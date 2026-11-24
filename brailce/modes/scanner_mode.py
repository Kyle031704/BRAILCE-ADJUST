"""
Brailce Mode — Scanner Mode
===========================

Live viewfinder with cheap classical Braille presence detection (~3-5 Hz,
hysteresis + cooldown), sharpest still-frame capture on Enter, classical dot-grid
Grade 1 Braille decoding, and systematic letter-by-letter -> word-by-word readout
that cancels cleanly on any key.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from brailce.core.events import AppMode, EventType, InputEvent
from brailce.i18n.strings import get_mode_name, get_string
from brailce.modes.base_mode import BaseMode
from brailce.speech.audio_device import AudioDeviceCoordinator
from brailce.vision.camera import CameraNotConnectedError, compute_sharpness
from brailce.vision.obr_engine import UNKNOWN_CELL_CHAR

logger = logging.getLogger(__name__)


class ScannerMode(BaseMode):
    """Optical Braille Recognition (OBR) camera scanner mode."""

    # Presence detection tuning (~3-4 Hz)
    PRESENCE_POLL_S: float = 0.28
    PRESENCE_POSITIVE_FRAMES: int = 3
    PRESENCE_NEGATIVE_FRAMES: int = 4
    ANNOUNCEMENT_COOLDOWN_S: float = 6.0

    # Readout pacing (seconds; overridable in tests)
    CHAR_PACE_S: float = 0.80
    WORD_PAUSE_S: float = 0.35
    LINE_PAUSE_S: float = 0.60

    _REASON_TO_STRING_KEY = {
        "blurry": "SCANNER_BLURRY",
        "too_small": "SCANNER_MOVE_CLOSER",
        "too_large": "SCANNER_MOVE_FARTHER",
        "no_braille": "SCANNER_NO_BRAILLE",
    }

    _PUNCT_FILE_MAP = {
        ",": "char_comma.wav",
        ".": "char_period.wav",
        "?": "char_question.wav",
        "!": "char_exclamation.wav",
        "'": "char_apostrophe.wav",
        "-": "char_minus.wav",
        "+": "char_plus.wav",
        "=": "char_equals.wav",
        "/": "char_divide.wav",
        "*": "char_times.wav",
        "#": "char_hash.wav",
    }

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.scanned_text: Optional[str] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._scanned_frame: Optional[Any] = None
        self._is_scanning: bool = False
        self._is_reading: bool = False
        self._preview_running: bool = False
        self._preview_thread: Optional[threading.Thread] = None
        self._detector_thread: Optional[threading.Thread] = None
        self._readout_thread: Optional[threading.Thread] = None
        self._stop_readout_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._sharp_frames: Deque[Tuple[float, float, Any]] = deque(maxlen=15)
        self._latest_frame: Any = None
        self._braille_detected: Optional[bool] = None
        self._camera_error_reported: bool = False
        self._last_announce_time: float = 0.0
        self._distance_state: str = "no_braille"
        self._last_distance_announce_time: float = 0.0
        self._last_distance_announced_state: Optional[str] = None

    @property
    def mode_type(self) -> AppMode:
        return AppMode.SCANNER

    @property
    def display_name(self) -> str:
        lang = self.state.language if self.state else "en"
        return get_mode_name(self.mode_type, lang)

    def _lang(self) -> str:
        return self.state.language if self.state else "en"

    def _announce_capital_sign(self) -> bool:
        cfg = getattr(self.app, "config", None)
        if cfg and hasattr(cfg, "get"):
            return bool(cfg.get("vision", "announce_capital_sign", default=False))
        return False

    def _get_char_pace_s(self) -> float:
        cfg = getattr(self.app, "config", None)
        if cfg and hasattr(cfg, "get"):
            if self._lang() == "fil":
                gap_ms = cfg.get("speech", "filipino_char_gap_ms", default=None)
            else:
                gap_ms = cfg.get("speech", "char_gap_ms", default=None)
            if gap_ms is not None:
                return float(gap_ms) / 1000.0
        return self.CHAR_PACE_S

    def _get_word_pause_s(self) -> float:
        if self._lang() == "fil":
            cfg = getattr(self.app, "config", None)
            if cfg and hasattr(cfg, "get"):
                gap_ms = cfg.get("speech", "filipino_word_gap_ms", default=None)
                if gap_ms is not None:
                    return max(0.05, float(gap_ms) / 1000.0)
        return self.WORD_PAUSE_S

    def _stop_readout(self, announce_stopped: bool = False) -> None:
        """Stop active letter-by-letter/word-by-word readout immediately."""
        was_reading = self._is_reading
        self._stop_readout_event.set()
        self._is_reading = False
        if getattr(self.app, "speech", None):
            self.app.speech.stop()
        if announce_stopped and was_reading and getattr(self.app, "display", None):
            self.app.display.show_status(get_string("SCANNER_STOPPED", self._lang()))

    def handle_event(self, event: InputEvent) -> None:
        """Handle user input in Scanner Mode."""
        if event.event_type == EventType.STOP_PRESSED:
            # Esc: wind down readout/spelling via the existing stop flag (app already stopped speech)
            self._stop_readout(announce_stopped=self._is_reading)
            return

        if self._is_reading:
            # Any key cleanly interrupts active speech readout
            self._stop_readout(announce_stopped=True)
            if event.event_type == EventType.CLEAR_PRESSED:
                self._reset_scan_state()
            return

        if self._is_scanning or self.state.ai_processing:
            return

        if event.event_type == EventType.ENTER_PRESSED:
            # Always interrupt active readout, clear old state, and trigger a fresh scan
            self._stop_readout(announce_stopped=False)
            self._reset_scan_state()
            self._start_scan()
        elif event.event_type == EventType.CLEAR_PRESSED:
            self._reset_scan_state()

    def _reset_scan_state(self) -> None:
        """Clear previously scanned text and return to viewfinder ready state."""
        lang = self._lang()
        self.scanned_text = None
        self._last_result = None
        self._scanned_frame = None
        self._braille_detected = None
        self._distance_state = "no_braille"
        self._last_distance_announced_state = None
        if getattr(self.app, "display", None):
            ready = get_string("SCANNER_READY", lang)
            if hasattr(self.app.display, "show_scanner_viewfinder"):
                self.app.display.show_scanner_viewfinder(status=ready)
            else:
                self.app.display.show_status(ready)

    def _report_camera_missing(self) -> None:
        """Surface missing camera clearly via LCD + Voice once (no fake Braille)."""
        if self._camera_error_reported:
            return
        self._camera_error_reported = True
        msg = get_string("SCANNER_CAMERA_NOT_CONNECTED", self._lang())
        if getattr(self.app, "display", None):
            if hasattr(self.app.display, "show_error"):
                self.app.display.show_error(msg)
            else:
                self.app.display.show_status(msg)
        if getattr(self.app, "speech", None):
            self.app.speech.speak_text(msg, interrupt=True)

    def _detection_worker(self) -> None:
        """
        Cheap classical Braille presence monitor (~3-4 Hz) with hysteresis + cooldown.
        Uses braille_presence_confidence() >= confidence_threshold (default 0.60) so
        polka-dot grids, text pages, and noise are rejected before triggering the
        "Braille detected" announcement.
        """
        pos_streak = 0
        neg_streak = 0

        while self._preview_running:
            try:
                if self._is_scanning or self._is_reading or not getattr(self.app, "obr", None):
                    time.sleep(self.PRESENCE_POLL_S)
                    continue

                with self._frame_lock:
                    frame = self._latest_frame

                if frame is None:
                    time.sleep(self.PRESENCE_POLL_S)
                    continue

                # Read confidence threshold from config (default 0.42, was 0.60 — too strict)
                cfg = getattr(self.app, "config", None)
                conf_threshold = 0.42
                if cfg and hasattr(cfg, "get"):
                    try:
                        conf_threshold = float(cfg.get("vision", "confidence_threshold", default=0.42))
                    except Exception:
                        pass

                obr = self.app.obr
                distance_state = "no_braille"
                if hasattr(obr, "assess_braille_distance"):
                    # Evaluate strictly the center-strip ROI matching the LCD viewfinder reticle
                    h_f, w_f = frame.shape[:2]
                    from brailce.vision.obr_engine import ROI_W_FRAC, ROI_H_FRAC
                    roi_w = int(w_f * ROI_W_FRAC)
                    roi_h = int(h_f * ROI_H_FRAC)
                    rx0 = (w_f - roi_w) // 2
                    ry0 = (h_f - roi_h) // 2
                    roi_frame = frame[ry0 : ry0 + roi_h, rx0 : rx0 + roi_w]
                    dist_info = obr.assess_braille_distance(roi_frame, confidence_threshold=conf_threshold)
                    conf = dist_info.get("confidence", 0.0)
                    dot_count = dist_info.get("dot_count", 0)
                    # Must have at least 6 aligned dots inside ROI box before triggering distance guidance
                    if dot_count >= 6 and conf >= conf_threshold:
                        detected = True
                        distance_state = dist_info.get("state", "no_braille")
                    else:
                        distance_state = "no_braille"
                        detected = False
                    logger.debug("Braille distance assessment: %s conf=%.2f dots=%d", dist_info, conf, dot_count)
                elif hasattr(obr, "braille_presence_confidence"):
                    conf = obr.braille_presence_confidence(frame)
                    detected = conf >= conf_threshold
                    logger.debug("Braille presence confidence=%.2f threshold=%.2f detected=%s", conf, conf_threshold, detected)
                else:
                    detected = bool(obr.detect_braille_presence(frame))

                self._distance_state = distance_state

                if detected:
                    pos_streak += 1
                    neg_streak = 0
                    if pos_streak >= self.PRESENCE_POSITIVE_FRAMES:
                        became_detected = self._braille_detected is not True
                        self._braille_detected = True
                        now = time.monotonic()

                        # Intelligent, rate-limited voice distance & presence guidance
                        if not self.scanned_text and (now - self._last_distance_announce_time) >= 3.0:
                            prompt_key = None
                            if distance_state == "ok" and self._last_distance_announced_state != "ok":
                                prompt_key = "SCANNER_DISTANCE_OK"
                                self._last_distance_announced_state = "ok"
                            elif distance_state == "too_far" and self._last_distance_announced_state != "too_far":
                                prompt_key = "SCANNER_GUIDE_CLOSER"
                                self._last_distance_announced_state = "too_far"
                            elif distance_state == "too_close" and self._last_distance_announced_state != "too_close":
                                prompt_key = "SCANNER_GUIDE_FARTHER"
                                self._last_distance_announced_state = "too_close"
                            elif became_detected and (now - self._last_announce_time) >= self.ANNOUNCEMENT_COOLDOWN_S:
                                prompt_key = "SCANNER_BRAILLE_DETECTED"
                                self._last_announce_time = now

                            if prompt_key and getattr(self.app, "speech", None):
                                self._last_distance_announce_time = now
                                self.app.speech.speak_text(
                                    get_string(prompt_key, self._lang()),
                                    interrupt=False,
                                )
                else:
                    neg_streak += 1
                    pos_streak = 0
                    if neg_streak >= self.PRESENCE_NEGATIVE_FRAMES:
                        self._braille_detected = False
                        self._distance_state = "no_braille"
                        self._last_distance_announced_state = None
            except Exception as exc:
                logger.debug("Presence detection error: %s", exc)

            time.sleep(self.PRESENCE_POLL_S)

    def _preview_loop(self) -> None:
        """Live camera viewfinder loop maintaining a rolling buffer of sharpest frames."""
        consecutive_failures = 0
        last_reprobe_time = 0.0

        while self._preview_running:
            try:
                cam = getattr(self.app, "camera", None)
                if not self._is_scanning and cam is not None:
                    frame = None
                    try:
                        frame = cam.capture_image()
                    except CameraNotConnectedError:
                        frame = None
                    except Exception as exc:
                        logger.debug("Preview capture error: %s", exc)
                        frame = None

                    if frame is not None and getattr(frame, "size", 0) > 0:
                        consecutive_failures = 0
                        if self._camera_error_reported:
                            self._camera_error_reported = False
                            self._reset_scan_state()

                        sharpness = compute_sharpness(frame)
                        with self._frame_lock:
                            self._latest_frame = frame
                            self._sharp_frames.append((time.monotonic(), sharpness, frame))

                        if getattr(self.app, "display", None) and not self._is_reading:
                            lang = self._lang()
                            if self.scanned_text:
                                status = self.scanned_text
                            elif self._distance_state == "ok":
                                status = get_string("SCANNER_DISTANCE_OK", lang)
                            elif self._distance_state == "too_close":
                                status = get_string("SCANNER_MOVE_FARTHER", lang)
                            elif self._distance_state == "too_far":
                                status = get_string("SCANNER_MOVE_CLOSER", lang)
                            elif self._braille_detected is True:
                                status = get_string("SCANNER_BRAILLE_DETECTED", lang)
                            else:
                                status = get_string("SCANNER_READY", lang)
                            try:
                                self.app.display.show_camera_preview(
                                    frame,
                                    status=status,
                                    distance_state=self._distance_state,
                                )
                            except TypeError:
                                self.app.display.show_camera_preview(frame, status=status)
                            except Exception as disp_exc:
                                logger.debug("Display show_camera_preview error: %s", disp_exc)
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= 10:
                            self._report_camera_missing()
                            now = time.monotonic()
                            if now - last_reprobe_time >= 3.0:
                                last_reprobe_time = now
                                if hasattr(cam, "reconnect"):
                                    threading.Thread(target=cam.reconnect, daemon=True).start()
                        time.sleep(0.05)
                        continue
            except Exception as loop_exc:
                logger.warning("Preview loop iteration warning: %s", loop_exc)

            time.sleep(0.06)

    def _get_char_wav_path(self, ch: str, lang: str) -> Optional[str]:
        """Resolve path to pre-rendered char_<char>.wav in English or Filipino folder."""
        root = Path(__file__).resolve().parent.parent / "audio"
        folder = "filipino" if lang == "fil" else "characters"
        char_dir = root / folder

        ch_clean = ch.strip()
        if not ch_clean:
            return None

        if len(ch_clean) == 1 and ch_clean.isalpha():
            fname = f"char_{ch_clean.upper()}.wav"
        elif ch_clean.isdigit():
            fname = f"char_{ch_clean}.wav"
        elif ch_clean in self._PUNCT_FILE_MAP:
            fname = self._PUNCT_FILE_MAP[ch_clean]
        else:
            fname = f"char_{ch_clean}.wav"

        path = str(char_dir / fname)
        if os.path.isfile(path) and os.path.getsize(path) > 44:
            return path
        return None

    def _speak_letter(self, ch: str) -> None:
        """
        Step 1 (Spell): Pronounce each letter individually with a short pause between letters,
        using brailce/audio/characters/char_<char>.wav when in English mode and
        brailce/audio/filipino/char_<char>.wav when in Filipino mode.
        """
        speech = getattr(self.app, "speech", None)
        if not ch:
            return

        lang = self._lang()
        wav_path = self._get_char_wav_path(ch, lang)
        played = False
        if wav_path:
            try:
                played = AudioDeviceCoordinator.play_wav_file(wav_path, owner="scanner", timeout=3.0)
            except Exception as exc:
                logger.debug("Scanner char playback fallback: %s", exc)

        if not played and speech:
            speech.speak_character(ch)

    def _capture_candidate_frames(self) -> List[Any]:
        """
        Acquire candidate frames for OBR decode:
        1. Pre-press stable frame: sharpest frame captured 0.25s to 1.2s before ENTER
           (immune to the hand moving over the keyboard/device).
        2. Fresh camera capture right now (or capture_sharpest if available).
        3. Most recent sharp frame from the preview buffer.
        """
        cam = getattr(self.app, "camera", None)
        if cam is None or (hasattr(cam, "is_available") and not cam.is_available()):
            self._report_camera_missing()
            return []

        now = time.monotonic()
        candidates: List[Any] = []

        # 1. Fresh camera capture (with hardware buffer flush)
        try:
            fresh = None
            if hasattr(cam, "flush_and_capture"):
                try:
                    res = cam.flush_and_capture(num_grabs=5)
                    if isinstance(res, np.ndarray) and res.size > 0:
                        fresh = res
                except Exception:
                    pass
            if fresh is None:
                if hasattr(cam, "capture_sharpest"):
                    res = cam.capture_sharpest()
                    if isinstance(res, np.ndarray) and res.size > 0:
                        fresh = res
                elif hasattr(cam, "capture_still"):
                    res = cam.capture_still()
                    if isinstance(res, np.ndarray) and res.size > 0:
                        fresh = res
                else:
                    res = cam.capture_image()
                    if isinstance(res, np.ndarray) and res.size > 0:
                        fresh = res
            if fresh is not None:
                candidates.append(fresh)
        except CameraNotConnectedError:
            self._report_camera_missing()
            return candidates
        except Exception as exc:
            logger.debug("Candidate fresh capture failed: %s", exc)

        with self._frame_lock:
            buffer_items = list(self._sharp_frames)

        # 2. Look for pre-press stable frames (0.25s - 1.2s ago)
        pre_press = [
            item for item in buffer_items
            if isinstance(item, (tuple, list)) and len(item) >= 3 and (0.25 <= (now - item[0]) <= 1.20)
        ]
        if pre_press:
            best_pre = max(pre_press, key=lambda item: item[1])[2].copy()
            candidates.append(best_pre)

        # 3. Add overall sharpest buffered frame
        if buffer_items:
            def _get_sharp_frame(item):
                if len(item) == 3:
                    return item[1], item[2]
                return item[0], item[1]

            best_all = max(buffer_items, key=lambda item: _get_sharp_frame(item)[0])
            candidates.append(_get_sharp_frame(best_all)[1].copy())
        elif self._latest_frame is not None and getattr(self._latest_frame, "size", 0) > 0:
            candidates.append(self._latest_frame.copy())

        return candidates

    def _capture_best_still(self) -> Optional[Any]:
        """Acquire sharpest candidate frame for OBR scan."""
        cands = self._capture_candidate_frames()
        return cands[0] if cands else None

    def _start_scan(self) -> None:
        """Start full capture + classical OBR decode in background thread."""
        self._is_scanning = True
        self.scanned_text = None
        self._last_result = None
        self._stop_readout_event.clear()
        lang = self._lang()

        if getattr(self.app, "display", None):
            self.app.display.show_status(get_string("SCANNER_SCANNING", lang))
        if getattr(self.app, "speech", None):
            self.app.speech.speak_text(get_string("SCANNER_SCANNING", lang), interrupt=False)

        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        """Capture candidate frames, run OBR engine, report actionable error or start readout."""
        lang = self._lang()
        result: Optional[Dict[str, Any]] = None
        try:
            if not getattr(self.app, "obr", None):
                self._report_camera_missing()
                return

            candidates = self._capture_candidate_frames()
            if not candidates:
                if not self._camera_error_reported:
                    msg = get_string("SCANNER_NO_BRAILLE", lang)
                    if getattr(self.app, "display", None):
                        self.app.display.show_status(msg)
                    if getattr(self.app, "speech", None):
                        self.app.speech.speak_text(msg, interrupt=True)
                return

            best_result = None
            best_frame = None

            for cand in candidates:
                self._scanned_frame = cand
                # Strict Center-Strip ROI: process strictly the single-line window matching the LCD reticle
                res = self.app.obr.process_image(cand, full_frame=False)
                if res.get("readable") and str(res.get("text", "")).strip():
                    best_result = res
                    best_frame = cand
                    break

                if best_result is None:
                    best_result = res
                    best_frame = cand

            self._scanned_frame = best_frame
            result = best_result or {"readable": False, "reason": "no_braille"}

            # Log Scanner result to terminal on every scan for PuTTY visibility
            readable = bool(result.get("readable", False))
            text_res = str(result.get("text", "")).strip()
            reason_res = str(result.get("reason", "unknown"))
            cells_count = len(result.get("cells", []))
            print(f"Scanner result: readable={readable} text='{text_res}' cells={cells_count} reason={reason_res}", flush=True)
            logger.info("Scanner result: readable=%s text='%s' cells=%d reason=%s", readable, text_res, cells_count, reason_res)

            if self._stop_readout_event.is_set():
                # Esc pressed during scan: discard result without announcements or readout
                return

            if not readable or not text_res:
                logger.warning("Scanner mode: scan unreadable, result=%r", result)
                reason = str(result.get("reason", "no_braille"))
                key = self._REASON_TO_STRING_KEY.get(reason, "SCANNER_NO_BRAILLE")
                msg = get_string(key, lang)
                self._braille_detected = False
                if getattr(self.app, "display", None):
                    self.app.display.show_status(msg)

                speech = getattr(self.app, "speech", None)
                if speech:
                    if lang == "fil":
                        no_braille_wav = os.path.join("brailce", "audio", "filipino", "no_braille_fil.wav")
                        if os.path.isfile(no_braille_wav):
                            AudioDeviceCoordinator.play_wav_file(no_braille_wav, owner="scanner", timeout=4.0)
                        else:
                            speech.speak_text("Walang mabasang Braille.", interrupt=True)
                    else:
                        speech.speak_text("No Braille detected.", interrupt=True)
                return

            self.scanned_text = text_res
            self._last_result = result
            if getattr(self.app, "display", None):
                self.app.display.show_text_buffer(self.scanned_text)
        except Exception as exc:
            logger.exception("Scanner worker encountered error: %s", exc)
            print(f"Scanner result: readable=False error={exc}", flush=True)
        finally:
            self._is_scanning = False
            # If scan was unreadable or failed: clear scan locks so preview + distance guidance resume
            if result is None or not result.get("readable") or not str(result.get("text", "")).strip():
                self.scanned_text = None
                self._last_result = None
                self._scanned_frame = None
                self._braille_detected = None
                self._distance_state = "no_braille"
                self._last_distance_announced_state = None

        if self._last_result is not None and not self._stop_readout_event.is_set():
            self._start_readout_thread(self._last_result)

    def _start_readout_thread(self, result: Dict[str, Any]) -> None:
        """Launch interruptible letter-by-letter + word-by-word readout."""
        self._stop_readout(announce_stopped=False)
        self._stop_readout_event.clear()
        self._is_reading = True
        self._readout_thread = threading.Thread(
            target=self._readout_worker,
            args=(result,),
            daemon=True,
        )
        self._readout_thread.start()

    def _speak_completed_word(self, word_chars: List[str], has_unknown: bool) -> None:
        """
        Step 2 (Pronounce Word):
        Pause briefly, then pronounce the full word using the active language's audio pipeline:
        - In Filipino mode: prioritize matching brailce/audio/filipino/word_*.wav files
          before falling back to Filipino TTS.
        - In English mode: use existing English audio/TTS.
        """
        speech = getattr(self.app, "speech", None)
        if not speech or not word_chars or has_unknown:
            return
        word = "".join(word_chars)
        if len(word) <= 1:
            return

        lang = self._lang()
        if lang == "fil":
            try:
                from brailce.speech.filipino_engine import get_filipino_word_path
                fil_word_wav = get_filipino_word_path(word)
                if fil_word_wav and os.path.isfile(fil_word_wav) and os.path.getsize(fil_word_wav) > 44:
                    played = AudioDeviceCoordinator.play_wav_file(fil_word_wav, owner="scanner", timeout=5.0)
                    if played:
                        return
            except Exception as exc:
                logger.debug("Failed playing filipino word wav for '%s': %s", word, exc)

        if hasattr(speech, "speak_word_clean"):
            speech.speak_word_clean(word, interrupt=False)
        elif hasattr(speech, "speak_word"):
            speech.speak_word(word)
        else:
            speech.speak_text(word, interrupt=False)

    def _readout_worker(self, result: Dict[str, Any]) -> None:
        """
        Systematically read decoded cells:
          1. Each cell letter/digit spoken & displayed (show_braille + show_character)
          2. Completed word/number spoken cleanly at word end
          3. Unknown cell spoken as SCANNER_UNKNOWN_CELL
        Interrupts cleanly whenever _stop_readout_event is set.
        """
        lang = self._lang()
        speech = getattr(self.app, "speech", None)
        display = getattr(self.app, "display", None)
        cells: List[Dict[str, Any]] = list(result.get("cells") or [])

        # Build overlay entries for all cells
        overlays: List[Dict[str, Any]] = []
        for c in cells:
            ch_c = str(c.get("char", "")).strip()
            box_c = c.get("bbox") or c.get("box")
            if box_c is None and "center" in c and "pitch" in c:
                cx, cy = c["center"]
                p = c["pitch"]
                box_c = (cx - p * 0.7, cy - p * 1.1, p * 1.4, p * 2.2)
            if box_c and ch_c:
                overlays.append({"char": ch_c, "bbox": box_c, "active": False, "cell": c})

        try:
            if not cells:
                text = str(result.get("text", "")).strip()
                if text and speech:
                    speech.speak_text(text, interrupt=False)
                return

            announce_cap = self._announce_capital_sign()
            cur_word: List[str] = []
            cur_has_unknown = False
            prev_word_idx: Optional[int] = None
            prev_line_idx: Optional[int] = None

            for cell in cells:
                if self._stop_readout_event.is_set():
                    return

                word_idx = cell.get("word_index", 0)
                line_idx = cell.get("line_index", 0)

                if prev_word_idx is not None and (word_idx != prev_word_idx or line_idx != prev_line_idx):
                    if cur_word:
                        self._stop_readout_event.wait(self._get_word_pause_s())
                        self._speak_completed_word(cur_word, cur_has_unknown)
                    cur_word = []
                    cur_has_unknown = False
                    pause = self.LINE_PAUSE_S if (prev_line_idx is not None and line_idx != prev_line_idx) else self._get_word_pause_s()
                    if self._stop_readout_event.wait(pause):
                        return

                prev_word_idx = word_idx
                prev_line_idx = line_idx

                kind = cell.get("kind", "char")
                ch = str(cell.get("char", ""))
                dots_str = str(cell.get("dots_str", ""))

                if kind == "indicator":
                    continue

                ch = str(cell.get("char", "")).upper()

                if display:
                    if hasattr(display, "show_character"):
                        display.show_character(ch)
                    if dots_str and hasattr(display, "show_braille"):
                        display.show_braille(ch, dots_str, "")
                    if self._scanned_frame is not None and hasattr(display, "show_camera_preview") and overlays:
                        for ov in overlays:
                            ov["active"] = (ov.get("cell") is cell)
                        try:
                            display.show_camera_preview(self._scanned_frame, status=self.scanned_text or ch, overlays=overlays)
                        except Exception:
                            pass

                if kind == "unknown" or not ch.strip():
                    cur_has_unknown = True
                    if speech:
                        speech.speak_text(get_string("SCANNER_UNKNOWN_CELL", lang), interrupt=False)
                    if self._stop_readout_event.wait(self._get_char_pace_s()):
                        return
                    continue

                cur_word.append(ch)
                self._speak_letter(ch)

                if self._stop_readout_event.wait(self._get_char_pace_s()):
                    return

            if cur_word and not self._stop_readout_event.is_set():
                if not cur_has_unknown:
                    self._stop_readout_event.wait(self._get_word_pause_s())
                self._speak_completed_word(cur_word, cur_has_unknown)
                if self._scanned_frame is not None and hasattr(display, "show_camera_preview") and overlays:
                    for ov in overlays:
                        ov["active"] = False
                    try:
                        display.show_camera_preview(self._scanned_frame, status=self.scanned_text, overlays=overlays)
                    except Exception:
                        pass
        finally:
            self._is_reading = False
            self._is_scanning = False
            if not self._stop_readout_event.is_set():
                if display and self.scanned_text:
                    display.show_text_buffer(self.scanned_text)
                self._stop_readout_event.wait(0.6)
                # Auto-return to live preview and distance guidance
                self._reset_scan_state()
            else:
                # Cancelled by Esc or keypress: clear locks so preview resumes
                self.scanned_text = None
                self._last_result = None
                self._scanned_frame = None
                self._braille_detected = None
                self._distance_state = "no_braille"
                self._last_distance_announced_state = None

    def on_enter(self) -> None:
        logger.info("Entered Scanner Mode.")
        self.scanned_text = None
        self._last_result = None
        self._is_scanning = False
        self._is_reading = False
        self._braille_detected = None
        self._latest_frame = None
        self._sharp_frames.clear()
        self._camera_error_reported = False
        self._stop_readout_event.clear()

        if getattr(self.app, "display", None):
            self.app.display.clear()
            self.app.display.show_mode(self.mode_type)
            ready = get_string("SCANNER_READY", self._lang())
            if hasattr(self.app.display, "show_scanner_viewfinder"):
                self.app.display.show_scanner_viewfinder(status=ready)
            else:
                self.app.display.show_status(ready)

        self._preview_running = True
        if self._preview_thread is None or not self._preview_thread.is_alive():
            self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True, name="scanner-preview")
            self._preview_thread.start()
        if self._detector_thread is None or not self._detector_thread.is_alive():
            self._detector_thread = threading.Thread(target=self._detection_worker, daemon=True, name="scanner-detector")
            self._detector_thread.start()

    def on_exit(self) -> None:
        logger.info("Exited Scanner Mode.")
        self._preview_running = False
        self._is_scanning = False
        self._stop_readout(announce_stopped=False)
        self._braille_detected = None
        self._latest_frame = None
        self._sharp_frames.clear()
        if self._preview_thread and self._preview_thread.is_alive():
            self._preview_thread.join(timeout=0.2)
        if self._detector_thread and self._detector_thread.is_alive():
            self._detector_thread.join(timeout=0.2)
        if getattr(self.app, "display", None):
            self.app.display.close_camera_preview()
