#!/usr/bin/env python3
"""
Clean Speech Mastering with Vocal Anti-Bass High-Pass Filter.
============================================================

Specifically engineered for mini speakers (e.g. 3W 4-ohm / 8-ohm 30-40mm
transducers):

1. High-Pass Filter (Butterworth 2nd order @ 250 Hz):
   - Removes muddy low-end frequencies (< 250 Hz) that cause tiny speaker
     cones to over-excurse, bottom out, and produce rattling "boomy bass"
     distortion. The filter runs at each file's OWN sample rate - never a
     resampled one.

2. Loudness re-normalization (brailce/speech/audio_normalize.py
   normalize_loudness_pcm semantics: active speech RMS 4200, peak < 30000,
   no clipping):
   - Keeps the bank passing tools/verify_playback_chain.py after a re-clean.

The WAV is written back at its INPUT sample rate, mono (channel mean),
16-bit PCM: 22050 Hz mono 16-bit files stay 22050 Hz mono 16-bit. Files
are never converted to 44.1 kHz stereo and never re-synthesized - only
the 250 Hz HPF and the active-RMS-4200 level are (re)applied.
"""

import os
import sys
import wave
from pathlib import Path

import numpy as np
import scipy.signal

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brailce.speech.audio_normalize import (  # noqa: E402
    TARGET_ACTIVE_RMS,
    normalize_loudness_pcm,
)

HP_CUTOFF = 250.0  # Eliminates bass rumble and cone over-excursion


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

        # 1. 2nd-order Butterworth high-pass at 250 Hz, at the file's own rate (no resample)
        sos = scipy.signal.butter(2, HP_CUTOFF, 'hp', fs=framerate, output='sos')
        filtered = scipy.signal.sosfilt(sos, mono)

        # 2. Normalize active speech to RMS 4200 with peak < 30000 (audio_normalize semantics)
        normalized = normalize_loudness_pcm(filtered, target_rms=TARGET_ACTIVE_RMS)

        # 3. Write back at the INPUT rate, mono, 16-bit
        out_int16 = np.clip(np.round(normalized), -32767, 32767).astype(np.int16)

        temp_out = path.with_suffix(".tmp.wav")
        with wave.open(str(temp_out), "wb") as wf_out:
            wf_out.setnchannels(1)
            wf_out.setsampwidth(2)
            wf_out.setframerate(framerate)
            wf_out.writeframes(out_int16.tobytes())
        temp_out.replace(path)
        return True
    except Exception as exc:
        print(f"Error processing {path}: {exc}", file=sys.stderr)
        return False


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "brailce" / "audio"
    files = list(root.rglob("*.wav"))
    print(f"Applying Anti-Bass Vocal Filter ({HP_CUTOFF:.0f}Hz HPF at each file's own rate + "
          f"active-RMS-{int(TARGET_ACTIVE_RMS)} re-normalization) to {len(files)} files...")
    success = 0
    for i, f in enumerate(files):
        if process_file(f):
            success += 1
        if (i + 1) % 150 == 0 or (i + 1) == len(files):
            print(f"Progress: {i + 1}/{len(files)} files processed.")
    print(f"Done! Cleaned {success}/{len(files)} files (format preserved: input rate, mono, 16-bit).")


if __name__ == "__main__":
    main()
