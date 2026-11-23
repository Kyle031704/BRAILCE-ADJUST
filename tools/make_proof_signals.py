#!/usr/bin/env python3
"""
Generate the calibrated PROOF signals for the speaker/amplifier/voice-output
proof test (numpy + wave only, no scipy).

Everything is written 22050 Hz / mono / 16-bit (the reference playback
format) into the target directory (default: ./proof_signals/):

  - 1.0 s sine tones at the SAFE level PEAK 4000 for the frequency sweep:
    250, 500, 1000, 2000, 4000, 6000, 8000 Hz
    -> sine_0250_p4000.wav ... sine_8000_p4000.wav
  - level sweep of speech: linear-scaled copies of the two reference bank
    WAVs (brailce/audio/characters/char_A.wav +
    brailce/audio/filipino/word_ano.wav) at PEAK 4000, 8000, 12000, 16000,
    20000, 25000 -> charA_p04000.wav ... word_ano_p025000.wav.
    Scaling is the SAME pure linear approach as tools/make_speaker_sweep.py
    (round(samples * target_peak / current_peak) with a +/-32767 clip
    guard): NO filtering, NO resampling, NO RMS re-normalization.
  - a 3.0 s "pink-ish" noise burst at PEAK 3000 (noise_burst_p3000.wav)
    for the amp noise/rattle check: np.random white noise (FIXED seed)
    through a gentle low-pass (1/sqrt(1+(f/fc)^2), fc = 3000 Hz).

After writing, every file is read BACK and printed in a verification table
(expected peak vs actual peak, actual RMS, duration) plus a format check.
The table is part of the proof report produced by
tools/speaker_amp_proof_test.sh.
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

SINE_FREQS = [250, 500, 1000, 2000, 4000, 6000, 8000]
SINE_PEAK = 4000
SINE_SECONDS = 1.0

SPEECH_SOURCES: List[Tuple[str, str]] = [
    ("charA", os.path.join(PROJECT_ROOT, "brailce", "audio", "characters", "char_A.wav")),
    ("word_ano", os.path.join(PROJECT_ROOT, "brailce", "audio", "filipino", "word_ano.wav")),
]
SPEECH_PEAKS = [4000, 8000, 12000, 16000, 20000, 25000]

NOISE_PEAK = 3000
NOISE_SECONDS = 3.0
NOISE_SEED = 20261123
NOISE_FC = 3000.0


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
            f"ERROR: {path} must be {TARGET_SR} Hz / {TARGET_CH} ch / {TARGET_SW * 8}-bit "
            f"to be swept, but it is {sr} Hz / {ch} ch / {sw * 8}-bit.\n"
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
    """Pure linear scaling + clip guard (same as tools/make_speaker_sweep.py)."""
    peak = float(np.max(np.abs(samples)))
    if peak < 1.0:
        sys.exit("ERROR: source WAV is silent (peak < 1); cannot sweep it.")
    scaled = np.round(samples * (target_peak / peak))
    return np.clip(scaled, -CLIP_GUARD, CLIP_GUARD).astype(np.int16)


def sine_tone(freq: int, target_peak: int, seconds: float) -> np.ndarray:
    n = int(round(seconds * TARGET_SR))
    t = np.arange(n, dtype=np.float64) / TARGET_SR
    s = np.sin(2.0 * np.pi * float(freq) * t)
    s = s * (target_peak / float(np.max(np.abs(s))))
    return np.round(s).astype(np.int16)


def noise_burst(target_peak: int, seconds: float, seed: int) -> np.ndarray:
    """Fixed-seed white noise through a gentle low-pass = "pink-ish" burst."""
    n = int(round(seconds * TARGET_SR))
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / TARGET_SR)
    gain = 1.0 / np.sqrt(1.0 + (freqs / NOISE_FC) ** 2)
    y = np.fft.irfft(spec * gain, n)
    y = y * (target_peak / float(np.max(np.abs(y))))
    return np.round(y).astype(np.int16)


def verify_table(expected: List[Tuple[str, int]]) -> int:
    """Read every output BACK; print actual peak/RMS/duration; 0 = all good."""
    print(f"{'file':<26} {'exp-peak':>8} {'peak':>6} {'rms':>9} {'dur(s)':>7}")
    format_bad: List[str] = []
    for path, exp_peak in expected:
        with wave.open(path, "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        peak = int(np.max(np.abs(data))) if data.size else 0
        rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
        dur = nframes / float(sr) if sr else 0.0
        print(f"{os.path.basename(path):<26} {exp_peak:>8} {peak:>6} {rms:>9.1f} {dur:>7.2f}")
        if (sr, ch, sw) != (TARGET_SR, TARGET_CH, TARGET_SW):
            format_bad.append(os.path.basename(path))
    if format_bad:
        print(f"FORMAT MISMATCH ({len(format_bad)} files, expected "
              f"{TARGET_SR} Hz / {TARGET_CH} ch / {TARGET_SW * 8}-bit): " + ", ".join(format_bad))
        return 1
    print(f"format check: all {len(expected)} files are {TARGET_SR} Hz / mono / "
          f"{TARGET_SW * 8}-bit (reference playback format)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the calibrated speaker/amp proof signals.")
    parser.add_argument(
        "outdir",
        nargs="?",
        default="./proof_signals",
        help="target directory (default: ./proof_signals)",
    )
    args = parser.parse_args()

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    expected: List[Tuple[str, int]] = []

    for freq in SINE_FREQS:
        path = os.path.join(outdir, f"sine_{freq:04d}_p{SINE_PEAK}.wav")
        write_wav(path, sine_tone(freq, SINE_PEAK, SINE_SECONDS))
        expected.append((path, SINE_PEAK))

    for stem, src in SPEECH_SOURCES:
        samples = read_source_wav(src)
        for peak in SPEECH_PEAKS:
            path = os.path.join(outdir, f"{stem}_p{peak:05d}.wav")
            write_wav(path, scale_to_peak(samples, peak))
            expected.append((path, peak))

    noise_path = os.path.join(outdir, "noise_burst_p3000.wav")
    write_wav(noise_path, noise_burst(NOISE_PEAK, NOISE_SECONDS, NOISE_SEED))
    expected.append((noise_path, NOISE_PEAK))

    print(f"Proof signals written to {os.path.abspath(outdir)} ({len(expected)} files):")
    rc = verify_table(expected)
    print("Proof suite: bash tools/speaker_amp_proof_test.sh  (generates + plays + records the report)")
    return rc


if __name__ == "__main__":
    sys.exit(main())