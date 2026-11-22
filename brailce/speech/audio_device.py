"""
Brailce Audio Device Coordinator
===============================

Coordinates exclusive access to the I2S DAC (MAX98357A) and ALSA sound card.
Ensures seamless, clean handoff between speech playback (Piper Neural TTS,
cached keystrokes) and tactile sound effects without ALSA device collisions,
DMA buffer underruns, or loud static noise.

Safe Playback Rules:
1. Always acquire exclusive device lock before opening the audio device.
2. Only one playback process runs at any time.
3. Playback uses the standard mode-introduction pipeline: aplay -q <file.wav>.
   Never stream raw bytes or piped WAV data to aplay via stdin.
4. No file is ever modified before playback — the ORIGINAL wav path is
   handed to aplay. No resampling, upsampling, gain staging, limiting, or
   temp WAV rewriting happens here.
5. Allow hardware DMA clock to settle after interrupting any active process.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional
from pathlib import Path

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
      (plug -> softvol "Master" -> dmixer -> hw); on nonzero exit, timeout,
      or launch failure it retries exactly once on the best hardware card
      (``plughw:<N>,0``).
    - The WAV file is played whole and unmodified: the ORIGINAL path is
      passed to aplay. No temp files, no resampling, no gain stage.
    - ``set_volume()`` is the single volume control: it drives the ALSA
      softvol control "Master" via best-effort ``amixer sset`` in a daemon
      thread. Nothing else touches volume.
    - Never stream raw bytes or piped WAV data to aplay via stdin.
    """

    _lock = threading.RLock()
    _active_process: Optional[subprocess.Popen] = None
    _active_owner: Optional[str] = None

    @classmethod
    def set_volume(cls, volume: int) -> None:
        """System volume via ALSA softvol control 'Master' (defined in ~/.asoundrc)."""
        try:
            pct = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            return
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
    def _run_aplay(cls, cmd: list, owner: str, timeout: float) -> bool:
        """Run one aplay invocation, registering the process. Returns True on clean exit 0."""
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cls.register_process(proc, owner)
            proc.wait(timeout=timeout)
            if proc.returncode == 0:
                time.sleep(0.12)  # Allow ALSA hardware ring buffer to completely drain to DAC
                return True
            logger.debug("aplay exited %s for: %s", proc.returncode, cmd[-1])
            return False
        except subprocess.TimeoutExpired:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            logger.debug("Playback timeout for: %s", cmd[-1])
            return False
        except Exception as exc:
            logger.debug("aplay failed for %s: %s", cmd[-1], exc)
            return False
        finally:
            cls.unregister_process(proc)

    @classmethod
    def play_wav_file(
        cls,
        wav_path: str,
        owner: str = "audio",
        timeout: float = 15.0,
    ) -> bool:
        """
        Safely play a WAV file using the exact standard mode-introduction pipeline:
            aplay -q <wav_path>

        Acquires exclusive device lock, registers the process, waits for completion,
        and unregisters safely. The file is played whole and unmodified — the
        ORIGINAL path is passed to aplay. On failure of the ALSA default device
        (nonzero exit, timeout, or launch error) it retries exactly once on the
        best hardware card (plughw:<N>,0). Never streams raw bytes or uses stdin.
        """
        if not wav_path or not os.path.isfile(wav_path):
            logger.debug("Audio file does not exist: %r", wav_path)
            return False

        if sys.platform != "win32":
            with cls._lock:
                if cls._run_aplay(["aplay", "-q", wav_path], owner, timeout):
                    return True
                best_dev = _find_best_alsa_device()
                if best_dev:
                    if cls._run_aplay(["aplay", "-D", best_dev, "-q", wav_path], owner, timeout):
                        return True
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
