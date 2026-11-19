"""
Verify all 26 brailce/audio/filipino/char_A.wav .. char_Z.wav:
- Exist
- Match word_ano.wav sample rate and channels (22050 Hz, 1 channel)
- Have duration >= 200 ms (0.20 s)
- Have non-silent vocal RMS energy (RMS > 500)
"""

from __future__ import annotations

import os
from pathlib import Path
import wave
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIL_DIR = PROJECT_ROOT / "brailce" / "audio" / "filipino"
REF_WAV = FIL_DIR / "word_ano.wav"


def audit_letters() -> bool:
    assert REF_WAV.exists(), f"Reference WAV missing: {REF_WAV}"
    with wave.open(str(REF_WAV), "rb") as wf:
        ref_sr = wf.getframerate()
        ref_ch = wf.getnchannels()
        ref_sw = wf.getsampwidth()

    print(f"Reference word_ano.wav: {ref_sr} Hz, {ref_ch} channel(s), {ref_sw*8}-bit")
    print(f"{'Letter':<6} | {'Status':<6} | {'Sample Rate':<11} | {'Ch':<2} | {'Duration (s)':<12} | {'RMS':<6} | {'Peak':<6}")
    print("-" * 65)

    all_passed = True
    letters = [chr(c) for c in range(ord("A"), ord("Z") + 1)]

    for l in letters:
        p = FIL_DIR / f"char_{l}.wav"
        if not p.exists():
            print(f"{l:<6} | MISSING")
            all_passed = False
            continue

        with wave.open(str(p), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            nframes = wf.getnframes()
            dur = nframes / sr
            raw = wf.readframes(nframes)

        arr = np.frombuffer(raw, dtype=np.int16)
        rms = float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)))
        peak = int(np.max(np.abs(arr))) if arr.size > 0 else 0

        ok = (
            sr == ref_sr
            and ch == ref_ch
            and sw == ref_sw
            and dur >= 0.200
            and rms >= 500
            and peak >= 1000
        )
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_passed = False

        print(f"{l:<6} | {status:<6} | {sr:<11} | {ch:<2} | {dur:<12.3f} | {rms:<6.0f} | {peak:<6}")

    print("-" * 65)
    print(f"Result: {'ALL 26 LETTERS PASSED' if all_passed else 'SOME LETTERS FAILED'}")
    return all_passed


if __name__ == "__main__":
    success = audit_letters()
    raise SystemExit(0 if success else 1)
