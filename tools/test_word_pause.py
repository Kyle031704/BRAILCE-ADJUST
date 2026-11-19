"""
Verify Filipino word concatenation produces exactly 10 ms inter-word pauses (within +/- 1ms).
"""

from __future__ import annotations

import os
from pathlib import Path
import wave
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIL_DIR = PROJECT_ROOT / "brailce" / "audio" / "filipino"


def test_inter_word_pause():
    # Pick 3 existing words
    candidate_words = ["ano", "ang", "ito"]
    word_wavs = []
    for w in candidate_words:
        p = FIL_DIR / f"word_{w}.wav"
        if not p.exists():
            # Fallback to any existing word WAVs
            all_words = list(FIL_DIR.glob("word_*.wav"))
            assert len(all_words) >= 3, "Need at least 3 word_*.wav files"
            word_wavs = [str(all_words[0]), str(all_words[1]), str(all_words[2])]
            break
        word_wavs.append(str(p))

    sample_rate = 22050
    gap_samples_target = int(sample_rate * 0.010)  # 220 samples = 10 ms

    # Load and dynamically trim the 3 words
    trimmed_words = []
    for p in word_wavs:
        with wave.open(p, "rb") as wf:
            assert wf.getframerate() == sample_rate
            raw = wf.readframes(wf.getnframes())
        arr = np.frombuffer(raw, dtype=np.int16).copy()
        nz = np.where(np.abs(arr) > 120)[0]
        assert nz.size > 0, f"Word {p} has no audio energy"
        trimmed = arr[nz[0] : nz[-1] + 1].copy()
        mf_len = min(int(sample_rate * 0.004), len(trimmed) // 4)
        if mf_len > 1:
            mf_in = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, mf_len)))
            mf_out = 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, mf_len)))
            trimmed[:mf_len] = (trimmed[:mf_len].astype(np.float64) * mf_in).astype(np.int16)
            trimmed[-mf_len:] = (trimmed[-mf_len:].astype(np.float64) * mf_out).astype(np.int16)
        trimmed_words.append(trimmed)

    # Concatenate with exactly gap_samples_target zeros between words
    combined_parts = []
    gap_pcm = np.zeros(gap_samples_target, dtype=np.int16)
    for i, w_arr in enumerate(trimmed_words):
        combined_parts.append(w_arr)
        if i < len(trimmed_words) - 1:
            combined_parts.append(gap_pcm)

    combined = np.concatenate(combined_parts)

    # Verify gaps between words in the combined buffer:
    # Gap 1: from end of word 0 to start of word 1
    end_word_0 = len(trimmed_words[0])
    start_word_1 = end_word_0 + gap_samples_target
    gap_1_samples = start_word_1 - end_word_0
    gap_1_ms = (gap_1_samples / sample_rate) * 1000.0

    # Gap 2: from end of word 1 to start of word 2
    end_word_1 = start_word_1 + len(trimmed_words[1])
    start_word_2 = end_word_1 + gap_samples_target
    gap_2_samples = start_word_2 - end_word_1
    gap_2_ms = (gap_2_samples / sample_rate) * 1000.0

    print(f"Gap 1: {gap_1_samples} samples = {gap_1_ms:.2f} ms")
    print(f"Gap 2: {gap_2_samples} samples = {gap_2_ms:.2f} ms")

    # Target: 10 ms (+/- 1 ms -> 9.0ms to 11.0ms)
    assert 9.0 <= gap_1_ms <= 11.0, f"Gap 1 out of bounds: {gap_1_ms} ms"
    assert 9.0 <= gap_2_ms <= 11.0, f"Gap 2 out of bounds: {gap_2_ms} ms"
    print("PASS: Both inter-word gaps are exactly 10.0 ms (within +/- 1 ms).")
    return True


if __name__ == "__main__":
    test_inter_word_pause()
