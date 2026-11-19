"""
Standardize all 26 Filipino character WAVs (A-Z) and digits (0-9) to exactly 0.800s (17,640 samples).
Preserves BlessicaNeural voice, natural audio quality, and RMS level.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
import wave
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIL_DIR = PROJECT_ROOT / "brailce" / "audio" / "filipino"

TARGET_SR = 22050
TARGET_SAMPLES = int(TARGET_SR * 0.800)  # 17,640 samples = 0.800s


def process_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        ch = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())

    samples = np.frombuffer(frames, dtype=np.int16)
    x = samples.astype(np.float64)

    # Active voice boundary detection
    nz = np.where(np.abs(x) > 120)[0]
    if nz.size > 0:
        pad = int(sr * 0.025)  # 25ms margin
        start = max(0, nz[0] - pad)
        end = min(len(x), nz[-1] + pad)
        core = x[start:end]
    else:
        core = x

    # Edge fade to avoid pops
    fade_len = min(int(sr * 0.005), len(core) // 6)
    if fade_len > 1:
        fade_in = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fade_len)))
        fade_out = 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, fade_len)))
        core[:fade_len] *= fade_in
        core[-fade_len:] *= fade_out

    # Resample or time-compress if slightly longer than 17640
    if len(core) > TARGET_SAMPLES:
        core = np.interp(
            np.linspace(0, len(core), TARGET_SAMPLES, endpoint=False),
            np.arange(len(core)),
            core,
        )

    rem = TARGET_SAMPLES - len(core)
    head_pad = min(int(sr * 0.040), rem // 3)
    tail_pad = rem - head_pad
    padded = np.pad(core, (head_pad, tail_pad), mode="constant")
    assert len(padded) == TARGET_SAMPLES

    final_pcm = np.round(padded).astype(np.int16).tobytes()

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SR)
        wf.writeframes(final_pcm)

    # Also make sure lowercase file exists on Linux
    name = path.name
    if name.startswith("char_") and len(name) == 10:  # char_X.wav
        c = name[5]
        if c.isupper():
            low_p = FIL_DIR / f"char_{c.lower()}.wav"
            with wave.open(str(low_p), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(TARGET_SR)
                wf.writeframes(final_pcm)


def main():
    targets = []
    # All letters A-Z
    for code in range(ord("A"), ord("Z") + 1):
        p = FIL_DIR / f"char_{chr(code)}.wav"
        if p.exists():
            targets.append(p)
    # Digits 0-9
    for d in range(10):
        p = FIL_DIR / f"char_{d}.wav"
        if p.exists():
            targets.append(p)

    for p in targets:
        process_wav(p)
        print(f"[OK] Standardized {p.name} to 0.800s (17640 samples)")

    print(f"Standardized {len(targets)} Filipino character files to exactly 0.800s!")


if __name__ == "__main__":
    main()
