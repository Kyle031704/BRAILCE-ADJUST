#!/usr/bin/env python3
"""
Generate a speaker output level sweep (numpy + wave only, no scipy).

Creates linear-scaled copies of the two reference speech WAVs
(brailce/audio/characters/char_A.wav and brailce/audio/filipino/word_ano.wav)
at exact PEAK levels 4000..28000, plus 1000 Hz sine tones (0.5 s) at peaks
4000/12000/20000. Everything is 22050 Hz / mono / 16-bit. Scaling is pure
linear (samples * target_peak / current_peak) with a +/-32767 clip guard:
NO filtering, NO resampling, NO RMS re-normalization.

Play the sweep on the device with tools/speaker_level_sweep.sh to find the
highest peak that still sounds clean (walang basag), then re-master the bank
with tools/set_bank_level.py --peak <two steps below that ceiling>.
"""

from __future__ import annotations

import argparse
import os
import sys
import wave
from typing import List, Tuple

import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TARGET_SR = 22050
TARGET_CH = 1
TARGET_SW = 2
CLIP_GUARD = 32767

SPEECH_SOURCES: List[Tuple[str, str]] = [
    ("charA", os.path.join(PROJECT_ROOT, "brailce", "audio", "characters", "char_A.wav")),
    ("word_ano", os.path.join(PROJECT_ROOT, "brailce", "audio", "filipino", "word_ano.wav")),
]
SPEECH_PEAKS = [4000, 6000, 8000, 10000, 12000, 14000, 16000, 20000, 24000, 28000]
SINE_PEAKS = [4000, 12000, 20000]


def read_source_wav(path: str) -> np.ndarray:
    if not os.path.isfile(path):
        sys.exit(f"ERROR: source WAV not found: {path}")
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if (sr, ch, sw) != (TARGET_SR, TARGET_CH, TARGET_SW):
        sys.exit(
            f"ERROR: {path} must be 22050 Hz / mono / 16-bit to be swept, "
            f"but it is {sr} Hz / {ch} ch / {sw * 8}-bit.\n"
            "Fix the bank format first (tools/convert_bank_to_22050_mono.py)."
        )
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def write_wav(path: str, samples: np.ndarray) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(TARGET_CH)
        wf.setsampwidth(TARGET_SW)
        wf.setframerate(TARGET_SR)
        wf.writeframes(samples.astype(np.int16).tobytes())


def scale_to_peak(samples: np.ndarray, target_peak: int) -> np.ndarray:
    peak = float(np.max(np.abs(samples)))
    if peak < 1.0:
        sys.exit("ERROR: source WAV is silent (peak < 1); cannot build a sweep from it.")
    scaled = np.round(samples * (target_peak / peak))
    return np.clip(scaled, -CLIP_GUARD, CLIP_GUARD).astype(np.int16)


def sine_1k(target_peak: int) -> np.ndarray:
    n = int(round(0.5 * TARGET_SR))
    t = np.arange(n, dtype=np.float64) / TARGET_SR
    s = np.sin(2.0 * np.pi * 1000.0 * t)
    s = s * (target_peak / float(np.max(np.abs(s))))
    return np.round(s).astype(np.int16)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the speaker level sweep WAVs.")
    parser.add_argument(
        "outdir",
        nargs="?",
        default="./speaker_sweep_out",
        help="output directory (default: ./speaker_sweep_out)",
    )
    args = parser.parse_args()

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    outputs: List[str] = []
    for stem, src in SPEECH_SOURCES:
        samples = read_source_wav(src)
        for peak in SPEECH_PEAKS:
            out_path = os.path.join(outdir, f"{stem}_p{peak:05d}.wav")
            write_wav(out_path, scale_to_peak(samples, peak))
            outputs.append(out_path)
    for peak in SINE_PEAKS:
        out_path = os.path.join(outdir, f"sine1k_p{peak:05d}.wav")
        write_wav(out_path, sine_1k(peak))
        outputs.append(out_path)

    print(f"Speaker sweep written to {os.path.abspath(outdir)} ({len(outputs)} files):")
    print(f"{'file':<22} {'peak':>6} {'rms':>9}")
    for path in outputs:
        with wave.open(path, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        peak = int(np.max(np.abs(data))) if data.size else 0
        rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
        print(f"{os.path.basename(path):<22} {peak:>6} {rms:>9.1f}")
    print("Play them on the device: bash tools/speaker_level_sweep.sh [direct]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
