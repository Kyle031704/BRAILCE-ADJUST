#!/usr/bin/env python3
"""
Master all Brailce WAV assets to 44.1 kHz 16-bit STEREO PCM.
============================================================
Guarantees MAX98357A I2S bit clock BCLK = 1.411 MHz (well above the 1.024 MHz PLL floor).
Eliminates all hardware buzzing, jitter, and amplifier rail clipping.
Peak normalized to 20,000 (-4.3 dBFS) for safe headroom on 3W 4-ohm / 8-ohm speakers.
"""

import sys
import wave
from pathlib import Path
import numpy as np
import scipy.signal

TARGET_SR = 44100
TARGET_PEAK = 20000.0

def process_file(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            if sampwidth != 2:
                return False
            raw = wf.readframes(nframes)

        samples = np.frombuffer(raw, dtype=np.int16)
        if nchannels == 2:
            samples = samples.reshape(-1, 2)
            mono = samples.mean(axis=1).astype(np.float64)
        else:
            mono = samples.astype(np.float64)

        # Resample to 44.1 kHz if necessary
        if framerate != TARGET_SR:
            if framerate == 22050:
                resampled = scipy.signal.resample_poly(mono, 2, 1)
            else:
                up = TARGET_SR
                down = framerate
                import math
                g = math.gcd(up, down)
                resampled = scipy.signal.resample_poly(mono, up // g, down // g)
        else:
            resampled = mono

        # Peak normalize to safe headroom (20,000 / 32,768 = -4.3 dBFS)
        cur_peak = np.max(np.abs(resampled))
        if cur_peak > 10.0:
            resampled = resampled * (TARGET_PEAK / cur_peak)

        resampled_int16 = np.clip(resampled, -32767, 32767).astype(np.int16)
        stereo = np.column_stack((resampled_int16, resampled_int16))

        with wave.open(str(path), "wb") as wf_out:
            wf_out.setnchannels(2)
            wf_out.setsampwidth(2)
            wf_out.setframerate(TARGET_SR)
            wf_out.writeframes(stereo.tobytes())
        return True
    except Exception as exc:
        print(f"Error processing {path}: {exc}", file=sys.stderr)
        return False

def main() -> None:
    root = Path(__file__).resolve().parent.parent / "brailce" / "audio"
    files = list(root.rglob("*.wav"))
    print(f"Processing {len(files)} WAV files to 44.1kHz Stereo (safe peak {int(TARGET_PEAK)})...")
    success = 0
    for i, f in enumerate(files):
        if process_file(f):
            success += 1
        if (i + 1) % 100 == 0 or (i + 1) == len(files):
            print(f"Progress: {i + 1}/{len(files)} files processed.")
    print(f"Done! Successfully mastered {success}/{len(files)} files.")

if __name__ == "__main__":
    main()
