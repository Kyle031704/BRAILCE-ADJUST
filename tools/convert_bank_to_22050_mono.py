#!/usr/bin/env python3
"""
Convert the Brailce WAV bank to 22050 Hz mono 16-bit PCM.
==========================================================

Restores the reference audio format (BRAILCE-TYPING-AND-AI-ONLY):
22050 Hz / 1 channel / 16-bit, so `aplay -q <file.wav>` plays the files
directly with no runtime resampling, upsampling, or gain staging.

For every ``*.wav`` under ``brailce/audio`` (characters, filipino, any
cache/words directories present):
  1. Stereo -> mono by averaging channels (no sample dropping).
  2. Resample to 22050 Hz using ONLY ``scipy.signal.resample_poly``
     (for 44100 -> 22050 that is up=1, down=2; other rates via gcd).
     Never sample-dropping, never ``np.repeat``.
  3. Normalize loudness with ``brailce.speech.audio_normalize.
     normalize_loudness_pcm`` semantics (active RMS target 4200,
     peak below 30000, no clipping).
  4. Bank-level whole-file RMS equalization (same philosophy as
     ``standardize_wav_file``'s RMS floor, applied symmetrically) so
     every file stays inside the audio audit's +/-3 dB bank-median band
     while active RMS is kept within 3500-5000.

This is a format + level conversion ONLY: clip duration, voice, and
timing are never changed (no re-synthesis, no trim, no padding).

Usage:
    python tools/convert_bank_to_22050_mono.py [--dry-run]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import wave
from typing import Dict, List, Tuple

import numpy as np
from scipy.signal import resample_poly

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brailce.speech.audio_normalize import (  # noqa: E402
    TARGET_ACTIVE_RMS,
    TARGET_SAMPLE_RATE,
    normalize_loudness_pcm,
)

MAX_PEAK = 30000.0
MIN_ACTIVE_RMS = 3500.0
MAX_ACTIVE_RMS = 5000.0


def _read_wav(path: str) -> Tuple[int, int, np.ndarray]:
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        n = wf.getnframes()
        raw = wf.readframes(n)
    if sw != 2:
        raise ValueError(f"not 16-bit PCM (sampwidth={sw})")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return sr, ch, data


def _write_wav(path: str, sr: int, data: np.ndarray) -> None:
    pcm = np.clip(np.round(data), -32767, 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _active_rms(data: np.ndarray) -> float:
    if data.size == 0:
        return 0.0
    peak = float(np.max(np.abs(data)))
    if peak < 10.0:
        return 0.0
    active = data[np.abs(data) > max(40.0, peak * 0.03)]
    if active.size == 0:
        active = data
    return float(np.sqrt(np.mean(active.astype(np.float64) ** 2)))


def _whole_rms(data: np.ndarray) -> float:
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))


def _resample_to_22050(data: np.ndarray, src_sr: int) -> np.ndarray:
    if src_sr == TARGET_SAMPLE_RATE or data.size <= 1:
        return data
    g = math.gcd(TARGET_SAMPLE_RATE, src_sr)
    return resample_poly(data, TARGET_SAMPLE_RATE // g, src_sr // g)


def _equalize_bank(entries: List[dict]) -> None:
    """
    Keep every file's whole-file RMS inside the audit's +/-3 dB band around
    the bank median, with a safety margin, while keeping active RMS within
    3500-5000 and peak <= 30000 (mirrors the RMS-floor philosophy of
    ``standardize_wav_file`` but symmetric and capped).
    """
    rms_values = [e["whole_rms"] for e in entries if e["whole_rms"] > 0.0]
    if not rms_values:
        return
    median = float(np.median(rms_values))
    if median <= 0.0:
        return
    lo, hi = median / 1.30, median * 1.30
    target_lo, target_hi = median / 1.20, median * 1.20
    for e in entries:
        w = e["whole_rms"]
        if w <= 0.0:
            continue
        gain = 1.0
        if w < lo:
            gain = target_lo / w
        elif w > hi:
            gain = target_hi / w
        if gain == 1.0:
            continue
        # Caps: peak <= 30000 and active RMS within 3500-5000.
        peak = e["peak"]
        if peak > 0.0:
            gain = min(gain, MAX_PEAK / peak)
        act = e["active_rms"]
        if act > 0.0:
            # 10-count margins keep int16 quantization inside the strict bounds
            if gain > 1.0:
                gain = min(gain, (MAX_ACTIVE_RMS - 10.0) / act)
            else:
                gain = max(gain, (MIN_ACTIVE_RMS + 10.0) / act)
        if abs(gain - 1.0) < 1e-6:
            continue
        data = e["data"] * gain
        e["data"] = data
        e["peak"] = float(np.max(np.abs(data)))
        e["active_rms"] = _active_rms(data)
        e["whole_rms"] = _whole_rms(data)


def convert_bank(audio_root: str, dry_run: bool = False) -> int:
    banks: Dict[str, List[dict]] = {}
    for dirpath, _dirnames, filenames in os.walk(audio_root):
        rel_dir = os.path.relpath(dirpath, audio_root)
        for fname in sorted(filenames):
            if not fname.lower().endswith(".wav"):
                continue
            path = os.path.join(dirpath, fname)
            try:
                sr, ch, data = _read_wav(path)
            except Exception as exc:
                print(f"SKIP (unreadable): {os.path.join(rel_dir, fname)}: {exc}")
                continue
            data = _resample_to_22050(data, sr)
            data = normalize_loudness_pcm(data, target_rms=TARGET_ACTIVE_RMS)
            entry = {
                "path": path,
                "rel": os.path.join(rel_dir, fname) if rel_dir != "." else fname,
                "bank": rel_dir.split(os.sep)[0] if rel_dir != "." else ".",
                "data": data,
                "active_rms": _active_rms(data),
                "whole_rms": _whole_rms(data),
                "peak": float(np.max(np.abs(data))) if data.size else 0.0,
            }
            banks.setdefault(entry["bank"], []).append(entry)

    failures: List[str] = []
    for bank in sorted(banks):
        entries = banks[bank]
        _equalize_bank(entries)
        _equalize_bank(entries)  # second pass: re-converge median after gains
        for e in entries:
            if e["active_rms"] < MIN_ACTIVE_RMS or e["active_rms"] > MAX_ACTIVE_RMS:
                failures.append(f"{e['rel']}: active RMS {e['active_rms']:.0f} outside {MIN_ACTIVE_RMS}-{MAX_ACTIVE_RMS}")
            if e["peak"] > MAX_PEAK:
                failures.append(f"{e['rel']}: peak {e['peak']:.0f} > {MAX_PEAK}")
            if not dry_run:
                _write_wav(e["path"], TARGET_SAMPLE_RATE, e["data"])
        act = [e["active_rms"] for e in entries]
        wrm = [e["whole_rms"] for e in entries]
        print(
            f"{bank}: {len(entries)} files | active RMS {min(act):.0f}-{max(act):.0f} "
            f"(median {float(np.median(act)):.0f}) | whole RMS {min(wrm):.0f}-{max(wrm):.0f} "
            f"(median {float(np.median(wrm)):.0f})"
        )

    if failures:
        print(f"\n{len(failures)} level violations:")
        for f in failures[:20]:
            print("  -", f)
        return 1
    print(f"\n{'DRY RUN: ' if dry_run else ''}all banks converted to 22050 Hz mono 16-bit, active RMS {MIN_ACTIVE_RMS}-{MAX_ACTIVE_RMS}, peak <= {int(MAX_PEAK)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="check levels without writing files")
    args = parser.parse_args()
    audio_root = os.path.join(PROJECT_ROOT, "brailce", "audio")
    if not os.path.isdir(audio_root):
        print(f"audio root not found: {audio_root}", file=sys.stderr)
        return 1
    return convert_bank(audio_root, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
