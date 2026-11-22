#!/usr/bin/env python3
"""
Verify the Brailce playback chain (numpy + wave only, no scipy).

Walks every ``*.wav`` under ``brailce/audio`` and asserts the reference
playback format and levels:
  - 22050 Hz sample rate
  - mono (1 channel)
  - 16-bit signed PCM
  - active RMS within 3500-5000 (audio_normalize active-window semantics)
  - peak <= 30000
  - no clipped samples (no |sample| >= 32767)

Also statically checks that no file under ``brailce/speech`` contains
"np.repeat" (the buzzy upsampler is gone for good).

Prints a per-directory summary. Exits 1 on any violation.
"""

from __future__ import annotations

import os
import sys
import wave
from typing import Tuple

import numpy as np

TARGET_SR = 22050
TARGET_CH = 1
TARGET_SW = 2
MIN_ACTIVE_RMS = 3500.0
MAX_ACTIVE_RMS = 5000.0
MAX_PEAK = 30000
CLIP_ABS = 32767

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIO_ROOT = os.path.join(PROJECT_ROOT, "brailce", "audio")
SPEECH_ROOT = os.path.join(PROJECT_ROOT, "brailce", "speech")


def active_rms(samples: np.ndarray) -> float:
    """RMS of active speech frames, same windowing as audio_normalize.normalize_loudness_pcm."""
    if samples.size == 0:
        return 0.0
    peak = float(np.max(np.abs(samples)))
    if peak < 10.0:
        return 0.0
    active = samples[np.abs(samples) > max(40.0, peak * 0.03)]
    if active.size == 0:
        active = samples
    return float(np.sqrt(np.mean(active.astype(np.float64) ** 2)))


def check_wav(path: str) -> Tuple[int, int, int, float, int, int]:
    """Return (sr, ch, sampwidth, active_rms, peak, clipped_count) for a WAV file."""
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) if sw == 2 else np.array([])
    peak = int(np.max(np.abs(samples))) if samples.size else 0
    clipped = int(np.count_nonzero(np.abs(samples) >= CLIP_ABS)) if samples.size else 0
    return sr, ch, sw, active_rms(samples), peak, clipped


def scan_np_repeat() -> list:
    """Return list of brailce/speech source files containing 'np.repeat'."""
    offenders = []
    for dirpath, _dirnames, filenames in os.walk(SPEECH_ROOT):
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            try:
                with open(full, "r", encoding="utf-8") as fp:
                    if "np.repeat" in fp.read():
                        offenders.append(os.path.relpath(full, PROJECT_ROOT))
            except Exception:
                offenders.append(os.path.relpath(full, PROJECT_ROOT) + " (unreadable)")
    return offenders


def main() -> int:
    violations: list = []
    per_dir: dict = {}

    for dirpath, _dirnames, filenames in os.walk(AUDIO_ROOT):
        rel_dir = os.path.relpath(dirpath, AUDIO_ROOT)
        checked = 0
        for fname in sorted(filenames):
            if not fname.lower().endswith(".wav"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.join(rel_dir, fname) if rel_dir != "." else fname
            checked += 1
            try:
                sr, ch, sw, arms, peak, clipped = check_wav(full)
            except Exception as exc:
                violations.append(f"{rel}: unreadable WAV ({exc})")
                continue
            if sr != TARGET_SR or ch != TARGET_CH or sw != TARGET_SW:
                violations.append(f"{rel}: wrong format {sr}Hz/{ch}ch/{sw * 8}bit (expected 22050Hz/1ch/16bit)")
            if arms < MIN_ACTIVE_RMS or arms > MAX_ACTIVE_RMS:
                violations.append(f"{rel}: active RMS {arms:.0f} outside {MIN_ACTIVE_RMS:.0f}-{MAX_ACTIVE_RMS:.0f}")
            if peak > MAX_PEAK:
                violations.append(f"{rel}: peak {peak} > {MAX_PEAK}")
            if clipped > 0:
                violations.append(f"{rel}: {clipped} clipped samples (|x| >= {CLIP_ABS})")
        if checked:
            per_dir[rel_dir] = checked

    for rel_dir in sorted(per_dir):
        print(f"{rel_dir}: {per_dir[rel_dir]} WAV files checked")

    repeat_offenders = scan_np_repeat()
    if repeat_offenders:
        for f in repeat_offenders:
            violations.append(f"{f}: contains 'np.repeat'")
    else:
        print(f"np.repeat scan: 0 occurrences in {SPEECH_ROOT}")

    if violations:
        print(f"\n{len(violations)} playback-chain violations:")
        for v in violations[:30]:
            print("  -", v)
        return 1
    print("\nPlayback chain OK: all WAVs 22050 Hz mono 16-bit, active RMS "
          f"{MIN_ACTIVE_RMS:.0f}-{MAX_ACTIVE_RMS:.0f}, peak <= {MAX_PEAK}, no clipped samples, no np.repeat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
