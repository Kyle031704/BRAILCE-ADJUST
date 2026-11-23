#!/usr/bin/env python3
"""
Re-master the entire brailce audio bank to a measured clean-speaker peak level.

After the on-device sweep (tools/speaker_level_sweep.sh) finds the highest
PEAK that still sounds clean (walang basag) on both the letter and the word,
re-master the bank just under it:

    python3 tools/set_bank_level.py --peak <malinis na peak na 2 hakbang mas mababa>
    e.g. clean up to PEAK 12000, basag at PEAK 14000  ->  --peak 10000

What it does:
  1. Refuses to run unless EVERY WAV under brailce/audio is 22050 Hz / mono / 16-bit.
  2. Scales EVERY bank WAV so its own peak equals --peak (pure linear scale with a
     +/-32767 clip guard; no filtering, no resampling; 22050/mono/16-bit preserved).
  3. Re-baselines TARGET_ACTIVE_RMS in brailce/speech/audio_normalize.py to the
     post-scale median active RMS of the bank (active frames = amplitude above 500,
     same spirit as audio_normalize), rounded to the nearest 100 - so live
     Piper/Filipino synthesis stays at the SAME level as the re-mastered bank.
  4. Regenerates brailce/tests/english_audio_manifest.sha256 and the
     FROZEN_ENGLISH_CHARACTERS_SHA256 in brailce/tests/test_filipino_tts_v2.py
     to match the re-scaled files.
  5. Runs tools/verify_playback_chain.py automatically and prints its verdict.

--dry-run prints what WOULD happen (file count, projected median active RMS,
new TARGET_ACTIVE_RMS) and writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import wave

import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIO_ROOT = os.path.join(PROJECT_ROOT, "brailce", "audio")
CHARACTERS_DIR = os.path.join(AUDIO_ROOT, "characters")
NORMALIZE_PY = os.path.join(PROJECT_ROOT, "brailce", "speech", "audio_normalize.py")
VERIFY_TOOL = os.path.join(PROJECT_ROOT, "tools", "verify_playback_chain.py")
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "brailce", "tests", "english_audio_manifest.sha256")
TTS_V2_TEST = os.path.join(PROJECT_ROOT, "brailce", "tests", "test_filipino_tts_v2.py")

TARGET_SR = 22050
TARGET_CH = 1
TARGET_SW = 2
CLIP_GUARD = 32767
ACTIVE_FLOOR = 500.0
MIN_PEAK = 1000
MAX_PEAK = 30000
PROGRESS_EVERY = 150
TMP_SUFFIX = ".tmp_setbank"


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def bank_wav_paths() -> list:
    paths = []
    for dirpath, _dirnames, filenames in os.walk(AUDIO_ROOT):
        for fname in sorted(filenames):
            if fname.lower().endswith(".wav"):
                paths.append(os.path.join(dirpath, fname))
    return paths


def read_wav(path: str):
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16), sr, ch, sw


def atomic_write_bytes(path: str, data: bytes) -> None:
    tmp = path + TMP_SUFFIX
    with open(tmp, "wb") as fp:
        fp.write(data)
    os.replace(tmp, path)


def write_wav(path: str, samples: np.ndarray) -> None:
    tmp = path + TMP_SUFFIX
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(TARGET_CH)
        wf.setsampwidth(TARGET_SW)
        wf.setframerate(TARGET_SR)
        wf.writeframes(samples.tobytes())
    os.replace(tmp, path)


def clear_stale_tmps() -> None:
    for dirpath, _dirnames, filenames in os.walk(AUDIO_ROOT):
        for fname in filenames:
            if fname.endswith(TMP_SUFFIX):
                os.remove(os.path.join(dirpath, fname))


def gate_formats(paths: list) -> None:
    bad = []
    for path in paths:
        try:
            _samples, sr, ch, sw = read_wav(path)
        except Exception as exc:
            bad.append(f"{os.path.relpath(path, PROJECT_ROOT)}: unreadable WAV ({exc})")
            continue
        if (sr, ch, sw) != (TARGET_SR, TARGET_CH, TARGET_SW):
            bad.append(
                f"{os.path.relpath(path, PROJECT_ROOT)}: {sr} Hz / {ch} ch / {sw * 8}-bit"
                " (expected 22050 Hz / mono / 16-bit)"
            )
    if bad:
        print("ERROR: refusing to re-master - some bank WAVs are not 22050 Hz / mono / 16-bit:")
        for line in bad[:10]:
            print("  -", line)
        if len(bad) > 10:
            print(f"  ... and {len(bad) - 10} more")
        print("Run tools/convert_bank_to_22050_mono.py first.")
        sys.exit(1)


def active_rms(samples: np.ndarray, floor: float = ACTIVE_FLOOR) -> float:
    x = samples.astype(np.float64)
    active = x[np.abs(x) > floor]
    if active.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(active ** 2)))


def scale_to_peak(samples: np.ndarray, target_peak: int) -> np.ndarray:
    peak = float(np.max(np.abs(samples)))
    if peak < 1.0:
        return np.array(samples, dtype=np.int16, copy=True)
    scaled = np.round(samples.astype(np.float64) * (target_peak / peak))
    return np.clip(scaled, -CLIP_GUARD, CLIP_GUARD).astype(np.int16)


def current_target_rms() -> float:
    with open(NORMALIZE_PY, "r", encoding="utf-8") as fp:
        text = fp.read()
    m = re.search(r"TARGET_ACTIVE_RMS\s*=\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not m:
        fail("TARGET_ACTIVE_RMS not found in brailce/speech/audio_normalize.py")
    return float(m.group(1))


def update_target_rms(new_value: int) -> None:
    with open(NORMALIZE_PY, "r", encoding="utf-8") as fp:
        text = fp.read()
    new_text, count = re.subn(
        r"(TARGET_ACTIVE_RMS\s*=\s*)[0-9]+(?:\.[0-9]+)?",
        rf"\g<1>{new_value}.0",
        text,
        count=1,
    )
    if count != 1:
        fail("expected exactly one TARGET_ACTIVE_RMS assignment in audio_normalize.py")
    atomic_write_bytes(NORMALIZE_PY, new_text.encode("utf-8"))


def regenerate_characters_manifests():
    hashes = {}
    lines = []
    for dirpath, _dirnames, filenames in os.walk(CHARACTERS_DIR):
        for fname in sorted(filenames):
            if not fname.lower().endswith(".wav"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, CHARACTERS_DIR)
            with open(full, "rb") as fp:
                digest = hashlib.sha256(fp.read()).hexdigest()
            hashes[rel] = digest
            repo_rel = os.path.relpath(full, PROJECT_ROOT).replace(os.sep, "/")
            lines.append(f"{digest}  {repo_rel}")
    if not hashes:
        fail(f"no WAVs found under {CHARACTERS_DIR}")
    atomic_write_bytes(MANIFEST_PATH, ("\n".join(lines) + "\n").encode("utf-8"))
    frozen = hashlib.sha256(str(sorted(hashes.items())).encode("utf-8")).hexdigest()
    with open(TTS_V2_TEST, "r", encoding="utf-8") as fp:
        text = fp.read()
    new_text, count = re.subn(
        r'(FROZEN_ENGLISH_CHARACTERS_SHA256\s*=\s*")[0-9a-fA-F]{64}(")',
        rf"\g<1>{frozen}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        fail("FROZEN_ENGLISH_CHARACTERS_SHA256 not found in test_filipino_tts_v2.py")
    atomic_write_bytes(TTS_V2_TEST, new_text.encode("utf-8"))
    return len(hashes), frozen


def run_verify() -> int:
    proc = subprocess.run([sys.executable, VERIFY_TOOL], cwd=PROJECT_ROOT)
    return proc.returncode


def round_to_100(value: float) -> int:
    return int(value / 100.0 + 0.5) * 100


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-master the bank so every WAV peaks at a measured clean-speaker level."
    )
    parser.add_argument(
        "--peak",
        type=int,
        required=True,
        help=f"target peak for every bank WAV ({MIN_PEAK}-{MAX_PEAK}, int)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would happen without writing anything",
    )
    args = parser.parse_args()

    if not (MIN_PEAK <= args.peak <= MAX_PEAK):
        fail(f"--peak must be {MIN_PEAK}-{MAX_PEAK} (got {args.peak})")

    paths = bank_wav_paths()
    if not paths:
        fail(f"no WAVs found under {AUDIO_ROOT}")

    print(f"[1/4] Format gate: checking {len(paths)} bank WAVs (22050 Hz / mono / 16-bit)...")
    gate_formats(paths)

    old_target = current_target_rms()

    if args.dry_run:
        print(f"[2/4] DRY-RUN: projecting the bank at peak {args.peak} (nothing is written)...")
        projected = []
        for i, path in enumerate(paths, 1):
            samples, _sr, _ch, _sw = read_wav(path)
            peak = float(np.max(np.abs(samples)))
            if peak < 1.0:
                continue
            k = args.peak / peak
            x = samples.astype(np.float64)
            active = x[np.abs(x) > (ACTIVE_FLOOR / k)]
            if active.size:
                projected.append(float(np.sqrt(np.mean((active * k) ** 2))))
            if i % PROGRESS_EVERY == 0:
                print(f"  projected {i}/{len(paths)}")
        if not projected:
            fail("bank contains only silent WAVs; nothing to re-master")
        median = float(np.median(projected))
        new_target = round_to_100(median)
        print()
        print("DRY-RUN result (walang nabago - nothing was written):")
        print(f"  bank WAVs that would be re-mastered : {len(projected)}")
        print(f"  every WAV scaled to its own peak of  : {args.peak}")
        print(f"  projected median active RMS (>500)   : {median:.0f}")
        print(f"  current TARGET_ACTIVE_RMS            : {old_target:.0f}")
        print(f"  new TARGET_ACTIVE_RMS (round to 100) : {new_target}")
        print("  then: regenerate manifest + frozen hash, run tools/verify_playback_chain.py")
        print()
        print(f"Kung tama na, patakbuhin nang walang --dry-run: python3 tools/set_bank_level.py --peak {args.peak}")
        return 0

    clear_stale_tmps()
    print(f"[2/4] Scaling every bank WAV to its own peak of {args.peak} (linear, clip-guard +/-{CLIP_GUARD})...")
    arms_list = []
    for i, path in enumerate(paths, 1):
        samples, _sr, _ch, _sw = read_wav(path)
        scaled = scale_to_peak(samples, args.peak)
        write_wav(path, scaled)
        arms_list.append(active_rms(scaled))
        if i % PROGRESS_EVERY == 0 or i == len(paths):
            print(f"  scaled {i}/{len(paths)}")
    if not arms_list:
        fail("bank contains only silent WAVs; nothing was re-mastered")
    median = float(np.median(arms_list))
    new_target = round_to_100(median)

    print(f"[3/4] Re-baselining TARGET_ACTIVE_RMS in audio_normalize.py: {old_target:.0f} -> {new_target}")
    update_target_rms(new_target)

    count, frozen = regenerate_characters_manifests()
    print(f"  regenerated brailce/tests/english_audio_manifest.sha256 ({count} entries)")
    print(f"  updated FROZEN_ENGLISH_CHARACTERS_SHA256 -> {frozen}")

    print("[4/4] Running tools/verify_playback_chain.py ...")
    print()
    rc = run_verify()
    print()
    if rc == 0:
        print("VERIFY OK - the re-mastered bank passes the playback chain.")
    else:
        print("VERIFY reported violations (see output above) - review them before deploying.")
    print("Restart the app (sudo systemctl restart brailce.service) to apply.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
