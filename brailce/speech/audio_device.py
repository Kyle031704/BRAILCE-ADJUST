"""
Brailce Audio Device Coordinator
================================

Coordinates exclusive access to the I2S DAC (MAX98357A) and ALSA sound card.
Ensures seamless, clean handoff between speech playback (Piper Neural TTS,
cached keystrokes) and tactile sound effects without ALSA device collisions,
DMA buffer underruns, or loud static noise.

Safe Playback Rules:
1. Always acquire exclusive device lock before opening the audio device.
2. Only one playback process runs at any time.
3. Playback uses the standard mode-introduction pipeline: aplay -q <file.wav>.
   Never stream raw bytes or piped WAV data to aplay via stdin.
4. Allow hardware DMA clock to settle after interrupting any active process.
"""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections import OrderedDict
from typing import Optional
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _find_best_alsa_device() -> Optional[str]:
    """Find the best hardware playback device, prioritizing I2S DAC (MAX98357A) and USB speakers."""
    cards_path = Path("/proc/asound/cards")
    if not cards_path.is_file():
        return None
    try:
        content = cards_path.read_text()
        dac_card = None
        usb_card = None
        for line in content.splitlines():
            line = line.strip()
            if line and line[0].isdigit() and "[" in line:
                num = line.split("[")[0].strip()
                name = line.split("[")[1].split("]")[0].strip()
                desc = line.split(":")[-1].strip() if ":" in line else ""
                card_dir = Path(f"/proc/asound/card{num}")
                # Verify card has playback subdevice (pcm*p), not capture-only microphone
                if any(p.is_dir() and p.name.endswith("p") for p in card_dir.glob("pcm*")):
                    full = (name + " " + desc).lower()
                    if any(k in full for k in ["dac", "hifiberry", "max98357", "snd_rpi", "i2s"]):
                        dac_card = num
                    elif "usb" in full and not any(k in full for k in ["cam", "webcam"]):
                        usb_card = num
        chosen = dac_card or usb_card
        if chosen is not None:
            return f"plughw:{chosen},0"
    except Exception:
        pass
    return None


class AudioDeviceCoordinator:
    """
    Singleton audio coordinator that manages DAC access and active playback
    processes across Piper TTS, cached character WAVs, and sound effects.

    Playback and volume policy:
    - Non-Windows playback opens the ALSA *default* device first
      (``aplay -q <file.wav>``) so the user's ~/.asoundrc chain applies
      (plug -> softvol "Master" -> dmixer -> hw); on nonzero exit OR timeout it
      falls back to one ``plughw:<N>,0`` attempt on the best hardware card.
      After 2 consecutive default failures playback is sticky on plughw and
      the default is not re-probed for 60 s.
    - ``set_gain_db()`` applies 0..24 dB output gain to 16-bit WAVs through a
      soft-knee limiter (knee starting at 0.60 of full scale, 0.945 ceiling
      held by a never-engaged guard cap), so quiet WAV banks sound full
      without hard digital clipping; gain-staged temp WAVs are memoized
      (last 32) and removed at process exit.
    - ``set_volume()`` drives the ALSA softvol control "Master" (defined in
      ~/.asoundrc) via best-effort ``amixer sset`` in a daemon thread; the
      stored percentage is the real volume channel, combined into the output
      gain (pct=100 -> unchanged, pct=1 -> -40 dB floor).
    - Never stream raw bytes or piped WAV data to aplay via stdin.
    """

    _lock = threading.RLock()
    _active_process: Optional[subprocess.Popen] = None
    _active_owner: Optional[str] = None
    _gain_db: float = 0.0   # class attribute
    _volume_pct: int = 100
    _default_failures: int = 0
    _default_retry_at: float = 0.0
    _gain_cache: "OrderedDict[tuple, str]" = OrderedDict()

    @classmethod
    def set_gain_db(cls, gain_db: float) -> None:
        try:
            cls._gain_db = max(0.0, min(24.0, float(gain_db)))
        except (TypeError, ValueError):
            cls._gain_db = 0.0

    @classmethod
    def set_volume(cls, volume: int) -> None:
        """System volume via ALSA softvol control 'Master' (defined in ~/.asoundrc)."""
        try:
            pct = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            return  # unparseable input: keep the last stored percentage
        # Store FIRST so the percentage is the real channel even without amixer
        cls._volume_pct = pct
        try:
            def _fire_amixer() -> None:
                try:
                    subprocess.run(["amixer", "sset", "Master", f"{pct}%", "unmute"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                except Exception:
                    pass  # missing amixer/control must never break speech (thread absorbs it)

            threading.Thread(target=_fire_amixer, daemon=True).start()
        except Exception:
            pass  # decorative best-effort: never block the caller (keyboard input thread)

    @classmethod
    def get_lock(cls) -> threading.RLock:
        """Get the shared re-entrant audio device lock."""
        return cls._lock

    @classmethod
    def register_process(cls, proc: subprocess.Popen, owner: str) -> None:
        """Register the currently playing audio process."""
        with cls._lock:
            cls._active_process = proc
            cls._active_owner = owner

    @classmethod
    def unregister_process(cls, proc: Optional[subprocess.Popen] = None) -> None:
        """Unregister the active audio process upon completion."""
        with cls._lock:
            if proc is None or cls._active_process is proc:
                cls._active_process = None
                cls._active_owner = None

    @classmethod
    def interrupt_active(cls, target_owner: Optional[str] = None) -> None:
        """
        Immediately terminate the currently playing audio process.
        Gives the hardware a moment to reset the I2S DMA buffer cleanly.
        """
        with cls._lock:
            proc = cls._active_process
            owner = cls._active_owner
            if proc is not None:
                if target_owner is None or owner == target_owner:
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=0.2)
                            except Exception:
                                proc.kill()
                                try:
                                    proc.wait(timeout=0.1)
                                except Exception:
                                    pass
                    except Exception as exc:
                        logger.debug("Error interrupting active audio process (%s): %s", owner, exc)
                    finally:
                        cls._active_process = None
                        cls._active_owner = None
                        # Allow ALSA I2S DMA clock to settle before the next audio stream opens
                        time.sleep(0.05)

    @classmethod
    def _apply_output_gain(cls, wav_path: str) -> tuple[str, bool]:
        """
        Apply the combined output gain (set_gain_db + stored volume pct) to a
        16-bit WAV through a soft-knee limiter: the knee starts at 0.60 of
        full scale and tanh-compresses toward the 0.945 ceiling; a hard cap
        at the ceiling is a never-engaged guard. Results are memoized in
        cls._gain_cache (path/mtime/gain/pct key, last 32 kept, evicted and
        atexit temp files removed best-effort).

        Returns (path_to_play, is_temp): a memoized or fresh temp WAV with
        gain applied, or (wav_path, False) when the combined gain is a no-op
        (|g| < 0.05 dB), the file is missing / not 16-bit PCM, or anything
        fails — gain staging must never block speech playback.
        """
        tmp_path: Optional[str] = None
        try:
            # Combined real volume channel: static gain + softvol percentage
            gain_db = cls._gain_db + float(np.log10(max(cls._volume_pct, 1) / 100.0)) * 20.0
            if abs(gain_db) < 0.05 or not wav_path or not os.path.isfile(wav_path):
                return (wav_path, False)
            key = (
                os.path.abspath(wav_path),
                os.path.getmtime(wav_path),
                round(cls._gain_db, 3),
                cls._volume_pct,
            )
            cached = cls._gain_cache.get(key)
            if cached is not None and os.path.isfile(cached):
                return (cached, True)
            with wave.open(wav_path, "rb") as wf:
                sampwidth = wf.getsampwidth()
                if sampwidth != 2:
                    return (wav_path, False)
                nchannels = wf.getnchannels()
                framerate = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
            out = samples * (10.0 ** (gain_db / 20.0))
            if gain_db > 0.05:
                # Limiter is ONLY engaged when positive software boost is applied and exceeds safe headroom
                KNEE_START = 0.88
                ceiling = 0.95
                over = np.abs(out) > KNEE_START
                if over.any():
                    oa = np.abs(out[over])
                    out[over] = np.sign(out[over]) * (
                        KNEE_START + (ceiling - KNEE_START) * np.tanh((oa - KNEE_START) / (ceiling - KNEE_START))
                    )
                out = np.sign(out) * np.minimum(np.abs(out), ceiling)
            else:
                out = np.clip(out, -1.0, 1.0)
            out_int16 = np.clip(out * 32767.0, -32767.0, 32767.0).astype(np.int16)
            fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="brailce_gain_")
            os.close(fd)
            with wave.open(tmp_path, "wb") as wf_out:
                wf_out.setnchannels(nchannels)
                wf_out.setsampwidth(sampwidth)
                wf_out.setframerate(framerate)
                wf_out.writeframes(out_int16.tobytes())
            cls._gain_cache[key] = tmp_path
            while len(cls._gain_cache) > 32:
                try:
                    _, evicted = cls._gain_cache.popitem(last=False)
                    os.remove(evicted)
                except Exception:
                    pass
            return (tmp_path, True)
        except Exception as exc:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            logger.debug("Output gain stage failed for %s: %s", wav_path, exc)
            return (wav_path, False)

    @classmethod
    def play_wav_file(
        cls,
        wav_path: str,
        owner: str = "audio",
        timeout: float = 15.0,
    ) -> bool:
        """
        Safely play a WAV file via aplay on the ALSA *default* device
        (aplay -q <wav_path>), which honors the user's ~/.asoundrc chain
        (plug -> softvol "Master" -> dmixer -> hw). On nonzero exit OR timeout
        it retries once on the best hardware card (plughw:<N>,0); after 2
        consecutive default failures playback goes straight to plughw and
        the default is not re-probed for 60 s. Output gain set via
        set_gain_db()/set_volume() is baked into a memoized temp WAV through
        a soft-knee limiter (kept for reuse, removed at process exit).
        
        Acquires exclusive device lock, registers the process, waits for completion,
        and unregisters safely. Never streams raw bytes or uses stdin.
        """
        if not wav_path or not os.path.isfile(wav_path):
            logger.debug("Audio file does not exist: %r", wav_path)
            return False

        if sys.platform != "win32":
            play_path, _is_temp = cls._apply_output_gain(wav_path)  # memoized temp: reused, never deleted here
            with cls._lock:
                proc = None
                try:
                    start = time.monotonic()
                    best_dev = _find_best_alsa_device()
                    # Sticky plughw: after 2 consecutive default failures, skip the
                    # default device until it is re-probed (at most once per 60 s).
                    use_default = best_dev is None or not (
                        cls._default_failures >= 2 and time.monotonic() < cls._default_retry_at
                    )
                    if use_default:
                        try:
                            proc = subprocess.Popen(
                                ["aplay", "-q", play_path],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            cls.register_process(proc, owner)
                            proc.wait(timeout=timeout)
                            if proc.returncode == 0:
                                cls._default_failures = 0
                                time.sleep(0.02)  # Fast buffer drain for snappy word/sentence response
                                return True
                        except subprocess.TimeoutExpired:
                            if proc and proc.poll() is None:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                            cls._default_failures += 1
                            if cls._default_failures >= 2:
                                cls._default_retry_at = time.monotonic() + 60.0
                        except Exception as exc:
                            logger.debug("aplay (default device) failed for %s: %s", wav_path, exc)
                        else:
                            # Attempt 1 exited nonzero: broken default device or .asoundrc
                            cls._default_failures += 1
                            if cls._default_failures >= 2:
                                cls._default_retry_at = time.monotonic() + 60.0
                    if best_dev:
                        try:
                            proc = subprocess.Popen(
                                ["aplay", "-D", best_dev, "-q", play_path],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            cls.register_process(proc, owner)
                            proc.wait(timeout=max(2.0, timeout - (time.monotonic() - start)))
                            if proc.returncode == 0:
                                time.sleep(0.02)  # Fast buffer drain for snappy word/sentence response
                                return True
                        except subprocess.TimeoutExpired:
                            if proc and proc.poll() is None:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                            logger.debug("Playback timeout for: %s", wav_path)
                            return False
                        except Exception as exc:
                            logger.debug("aplay (%s) failed for %s: %s", best_dev, wav_path, exc)
                finally:
                    cls.unregister_process(proc)

                # Secondary modern Linux audio backends (PipeWire / PulseAudio on Debian/Pi OS)
                import shutil
                for alt_bin in ["paplay", "pw-play"]:
                    alt_path = shutil.which(alt_bin)
                    if alt_path:
                        proc2 = None
                        try:
                            proc2 = subprocess.Popen(
                                [alt_path, wav_path],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            cls.register_process(proc2, owner)
                            proc2.wait(timeout=timeout)
                            if proc2.returncode == 0:
                                return True
                        except Exception:
                            pass
                        finally:
                            cls.unregister_process(proc2)
                return False
        else:
            # Windows fallback
            try:
                import winsound
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)
                return True
            except Exception as exc:
                logger.debug("winsound playback failed: %s", exc)
                return False


def _cleanup_gain_cache() -> None:
    """Best-effort removal of memoized gain temp WAVs at interpreter exit."""
    try:
        for tmp_path in list(AudioDeviceCoordinator._gain_cache.values()):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        AudioDeviceCoordinator._gain_cache.clear()
    except Exception:
        pass


atexit.register(_cleanup_gain_cache)
