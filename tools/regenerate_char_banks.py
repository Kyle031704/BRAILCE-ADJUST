#!/usr/bin/env python3
"""
Regenerate the English + Filipino character pronunciation banks (char_*.wav).

Voices (both female, per bank spec):
  - English  bank (brailce/audio/characters/): edge-tts "en-US-JennyNeural"
  - Filipino bank (brailce/audio/filipino/):   edge-tts "en-PH-RosaNeural"
    (Rosa = English with a natural Filipino accent)

Spoken texts (identical English letter-name texts for both banks):
  A "ay", B "bee", C "see", D "dee", E "ee", F "ef", G "gee", H "aitch",
  I "eye", J "jay", K "kay", L "el", M "em", N "en", O "oh", P "pee",
  Q "cue", R "ar", S "es", T "tee", U "you", V "vee", W "double you",
  X "ex", Y "why", Z "zee"; (Filipino bank only) enye for N-tilde;
  0-9 "zero".."nine"; "plus", "minus", "times", "divided by", "equals";
  "period", "comma", "question mark", "exclamation mark", "number sign",
  "apostrophe", "colon", "semicolon", "space".

Per-file pipeline (NO time forcing, NO high-pass filter, NO extra EQ):
  edge-tts mp3 -> decode (ffmpeg on PATH, else miniaudio, else soundfile)
  -> scipy resample_poly to 22050 Hz mono -> 16-bit WAV
  -> standardize_wav_file() from brailce.speech.audio_normalize
     (trim silence with 40 ms margins + 5 ms edge fades, normalize active
     speech RMS to TARGET_ACTIVE_RMS with the peak capped at 30000, then
     uniform 120 ms pre / 180 ms post digital-zero padding).

Letter/digit files in BOTH banks are duration-guarded so a keystroke stays
snappy and the frozen Filipino clarity bounds hold (total <= 0.90 s incl.
padding); other Filipino files are guarded to <= 1.45 s
(test_filipino_keystrokes bound 1.50 s). An over-bound file is
automatically re-synthesized at a slightly faster edge-tts rate until it fits.

After a fully successful run this tool regenerates
brailce/tests/english_audio_manifest.sha256 and the
FROZEN_ENGLISH_CHARACTERS_SHA256 inside brailce/tests/test_filipino_tts_v2.py
via the tools/set_bank_level.py manifest semantics.

Rate overrides: char_M.wav (Filipino) is pinned to +20% because the
natural-rate Rosa "em" makes the autocorrelation F0 estimator
(tools/audit_filipino_gender.py, gated by
test_pronunciation_regression.py) latch a ~124 Hz subharmonic on the short
nasal coda; the +20% rendering measures clean female windows (>= 160 Hz).

Usage:
  python tools/regenerate_char_banks.py                            (both banks)
  python tools/regenerate_char_banks.py --bank en                  (46 English files)
  python tools/regenerate_char_banks.py --bank fil                 (54 Filipino files)
  python tools/regenerate_char_banks.py --bank fil --only char_M.wav
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (PROJECT_ROOT, TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from brailce.speech.audio_normalize import (  # noqa: E402
    TARGET_SAMPLE_RATE,
    standardize_wav_file,
)

EN_CHAR_DIR = os.path.join(PROJECT_ROOT, "brailce", "audio", "characters")
FIL_CHAR_DIR = os.path.join(PROJECT_ROOT, "brailce", "audio", "filipino")

EN_VOICE = "en-US-JennyNeural"
FIL_VOICE = "en-PH-RosaNeural"

LETTER_TEXTS: Dict[str, str] = {
    "A": "ay", "B": "bee", "C": "see", "D": "dee", "E": "ee",
    "F": "ef", "G": "gee", "H": "aitch", "I": "eye", "J": "jay",
    "K": "kay", "L": "el", "M": "em", "N": "en", "O": "oh",
    "P": "pee", "Q": "cue", "R": "ar", "S": "es", "T": "tee",
    "U": "you", "V": "vee", "W": "double you", "X": "ex",
    "Y": "why", "Z": "zee",
}
ENYE_TEXT = "enye"
DIGIT_TEXTS: Dict[str, str] = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}
OPERATOR_TEXTS: Dict[str, str] = {
    "plus": "plus",
    "minus": "minus",
    "times": "times",
    "divide": "divided by",
    "equals": "equals",
}
PUNCTUATION_TEXTS: Dict[str, str] = {
    "period": "period",
    "comma": "comma",
    "question": "question mark",
    "exclamation": "exclamation mark",
    "hash": "number sign",
    "apostrophe": "apostrophe",
    "colon": "colon",
    "semicolon": "semicolon",
    "space": "space",
}
FIL_ALIAS_TEXTS: Dict[str, str] = {
    "divided": "divided by",
    "hyphen": "hyphen",
    "number_sign": "number sign",
}

FIL_LETTER_MAX_TOTAL_S = 0.88
FIL_KEYSTROKE_MAX_TOTAL_S = 1.45
MIN_TOTAL_S = 0.20

RATE_OVERRIDES: Dict[str, Dict[str, str]] = {
    FIL_VOICE: {
        "char_M.wav": "+20%",
    },
}


def build_english_bank() -> Dict[str, str]:
    """Exact existing English bank filenames (46 files) with spoken texts."""
    bank: Dict[str, str] = {}
    bank.update({f"char_{letter}.wav": text for letter, text in LETTER_TEXTS.items()})
    bank.update({f"char_{digit}.wav": text for digit, text in DIGIT_TEXTS.items()})
    bank.update({f"char_{stem}.wav": text for stem, text in OPERATOR_TEXTS.items()})
    for stem in ("period", "comma", "question", "exclamation", "hash"):
        bank[f"char_{stem}.wav"] = PUNCTUATION_TEXTS[stem]
    return bank


def build_filipino_bank() -> Dict[str, str]:
    """All existing Filipino bank char files plus char_N-tilde (enye)."""
    bank: Dict[str, str] = {}
    bank.update({f"char_{letter}.wav": text for letter, text in LETTER_TEXTS.items()})
    bank["char_\u00d1.wav"] = ENYE_TEXT
    bank.update({f"char_{digit}.wav": text for digit, text in DIGIT_TEXTS.items()})
    bank.update({f"char_{stem}.wav": text for stem, text in OPERATOR_TEXTS.items()})
    bank.update({f"char_{stem}.wav": text for stem, text in PUNCTUATION_TEXTS.items()})
    bank.update({f"char_{stem}.wav": text for stem, text in FIL_ALIAS_TEXTS.items()})
    return bank


def wav_file_stats(path: str) -> Optional[Tuple[float, float, int]]:
    """Return (duration_s, active_rms, peak) for a 16-bit WAV file."""
    try:
        with wave.open(path, "rb") as wf:
            sr = wf.getframerate()
            n = wf.getnframes()
            raw = wf.readframes(n)
        if wf.getsampwidth() != 2:
            return None
    except Exception:
        return None
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if x.size == 0:
        return None
    peak = float(np.max(np.abs(x)))
    active = x[np.abs(x) > max(40.0, peak * 0.03)]
    arms = float(np.sqrt(np.mean(active ** 2))) if active.size else 0.0
    return (n / float(sr), arms, int(peak))


def active_rms(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    active = samples[np.abs(samples) > max(40.0, peak * 0.03)]
    return float(np.sqrt(np.mean(active.astype(np.float64) ** 2))) if active.size else 0.0


def decode_mp3(mp3_path: str) -> Tuple[np.ndarray, int]:
    """Decode an MP3 file to (mono float64 samples at int16 scale, sample_rate)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        out_wav = mp3_path + ".decoded.wav"
        subprocess.run(
            [ffmpeg, "-y", "-i", mp3_path, "-ar", str(TARGET_SAMPLE_RATE),
             "-ac", "1", "-sample_fmt", "s16", out_wav],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True, timeout=60,
        )
        with wave.open(out_wav, "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        os.remove(out_wav)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float64), sr

    try:
        import miniaudio
    except ImportError:
        miniaudio = None
    if miniaudio is not None:
        with open(mp3_path, "rb") as fp:
            decoded = miniaudio.mp3_read_s16(fp.read())
        samples = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float64)
        if decoded.nchannels > 1:
            samples = samples.reshape(-1, decoded.nchannels).mean(axis=1)
        return samples, decoded.sample_rate

    try:
        import soundfile as sf
    except ImportError:
        sf = None
    if sf is not None:
        data, sr = sf.read(mp3_path, dtype="float64", always_2d=False)
        data = np.asarray(data)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data * 32768.0, int(sr)

    raise RuntimeError(
        "No MP3 decoder available: ffmpeg not on PATH and neither "
        "'miniaudio' nor 'soundfile' is importable. Install ffmpeg or "
        "pip install miniaudio (or soundfile)."
    )


def resample_to_target(samples: np.ndarray, src_sr: int) -> np.ndarray:
    """Resample mono float64 samples to 22050 Hz (polyphase, anti-aliased)."""
    if src_sr == TARGET_SAMPLE_RATE:
        return samples
    frac = Fraction(TARGET_SAMPLE_RATE, src_sr).limit_denominator(10000)
    try:
        from scipy.signal import resample_poly
        return resample_poly(samples, frac.numerator, frac.denominator)
    except ImportError:
        from brailce.speech.audio_normalize import resample_mono_pcm
        return resample_mono_pcm(samples, src_sr, TARGET_SAMPLE_RATE)


def write_mono_wav(path: str, samples: np.ndarray) -> None:
    pcm = np.clip(np.round(samples), -32767, 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


async def synthesize_mp3(text: str, voice: str, rate: str, out_mp3: str) -> None:
    """Synthesize one text to an MP3 file via edge-tts."""
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate)
    mp3_bytes = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            mp3_bytes.extend(chunk["data"])
    if not mp3_bytes:
        raise RuntimeError(f"edge-tts returned no audio for {text!r} ({voice})")
    with open(out_mp3, "wb") as fp:
        fp.write(bytes(mp3_bytes))


async def generate_one(
    bank_label: str,
    out_dir: str,
    fname: str,
    text: str,
    voice: str,
    max_total_s: Optional[float],
    tmp_dir: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Synthesize, decode, master, and duration-guard one bank file."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, fname)
    before = wav_file_stats(out_path)
    override = RATE_OVERRIDES.get(voice, {}).get(fname)
    rates = (override,) if override else ("+0%", "+10%", "+20%", "+30%")
    last_err: Optional[Exception] = None

    for rate in rates:
        for attempt in range(3):
            mp3_path = os.path.join(tmp_dir, f"{bank_label}_{fname}.{rate.replace('+', '').replace('%', '')}.{attempt}.mp3")
            try:
                async with semaphore:
                    await synthesize_mp3(text, voice, rate, mp3_path)
                samples, src_sr = decode_mp3(mp3_path)
                if samples.size < 64:
                    raise RuntimeError(f"decoded audio too short ({samples.size} samples)")
                resampled = resample_to_target(samples, src_sr)
                write_mono_wav(out_path, resampled)
                if not standardize_wav_file(out_path):
                    raise RuntimeError("standardize_wav_file failed")
            except Exception as exc:
                last_err = exc
                await asyncio.sleep(1.0)
                continue
            finally:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)

            after = wav_file_stats(out_path)
            if after is None:
                last_err = RuntimeError(f"{fname}: unreadable output WAV")
                continue
            dur, arms, peak = after
            if dur < MIN_TOTAL_S:
                last_err = RuntimeError(f"{fname}: duration {dur:.3f}s < {MIN_TOTAL_S}s")
                continue
            if max_total_s is not None and dur > max_total_s:
                last_err = RuntimeError(f"{fname}: duration {dur:.3f}s > guard {max_total_s}s at rate {rate}")
                continue

            b_txt = ""
            if before is not None:
                b_txt = f" {before[0]:.3f}s/aRMS {before[1]:.0f}/peak {before[2]} ->"
            print(f"[{bank_label}] {fname} {text!r} rate {rate}:{b_txt} {dur:.3f}s/aRMS {arms:.0f}/peak {peak}", flush=True)
            return True

    print(f"[{bank_label}] FAILED {fname} {text!r} ({voice}): {last_err}", flush=True)
    return False


def existing_char_files(directory: str) -> List[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.startswith("char_") and f.endswith(".wav"))


def check_bank_covers_existing(bank: Dict[str, str], directory: str, extra: Tuple[str, ...] = ()) -> None:
    existing = set(existing_char_files(directory))
    planned = set(bank)
    missing = existing - planned - set(extra)
    if missing:
        raise SystemExit(f"ERROR: existing {directory} files not covered by generator: {sorted(missing)}")
    new_files = planned - existing
    if new_files:
        print(f"NOTE: adding new bank files: {sorted(new_files)}")


async def run_bank(bank_label: str, out_dir: str, bank: Dict[str, str], voice: str, tmp_dir: str) -> bool:
    os.makedirs(out_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(5)
    letter_like = {f"char_{letter}.wav" for letter in LETTER_TEXTS}
    letter_like.add("char_\u00d1.wav")
    letter_like.update(f"char_{digit}.wav" for digit in DIGIT_TEXTS)

    async def one(fname: str) -> bool:
        if fname in letter_like:
            max_total = FIL_LETTER_MAX_TOTAL_S
        elif bank_label == "FIL":
            max_total = FIL_KEYSTROKE_MAX_TOTAL_S
        else:
            max_total = None
        return await generate_one(bank_label, out_dir, fname, bank[fname], voice, max_total, tmp_dir, semaphore)

    results = await asyncio.gather(*(one(fname) for fname in sorted(bank)))
    return all(results)


async def main_async(banks: List[str], only: Optional[List[str]]) -> int:
    tmp_dir = tempfile.mkdtemp(prefix="brailce_charbank_")
    ok = True
    try:
        if "en" in banks:
            en_bank = build_english_bank()
            if only:
                en_bank = {f: t for f, t in en_bank.items() if f in only}
            else:
                check_bank_covers_existing(en_bank, EN_CHAR_DIR)
            if en_bank:
                print(f"== English bank: {len(en_bank)} files -> {EN_CHAR_DIR} (voice {EN_VOICE}) ==", flush=True)
                ok &= await run_bank("EN", EN_CHAR_DIR, en_bank, EN_VOICE, tmp_dir)
        if "fil" in banks:
            fil_bank = build_filipino_bank()
            if only:
                fil_bank = {f: t for f, t in fil_bank.items() if f in only}
            if fil_bank:
                if not only:
                    check_bank_covers_existing(fil_bank, FIL_CHAR_DIR, extra=("char_\u00d1.wav",))
                print(f"== Filipino bank: {len(fil_bank)} files -> {FIL_CHAR_DIR} (voice {FIL_VOICE}) ==", flush=True)
                ok &= await run_bank("FIL", FIL_CHAR_DIR, fil_bank, FIL_VOICE, tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not ok:
        print(" regeneration FAILED - fix the errors above and re-run; manifests NOT touched", flush=True)
        return 1

    if "en" in banks:
        from set_bank_level import regenerate_characters_manifests
        count, frozen = regenerate_characters_manifests()
        print(f"== manifest regenerated: {count} entries, FROZEN_ENGLISH_CHARACTERS_SHA256 = {frozen} ==", flush=True)
    print("Done. Now run: python tools/verify_playback_chain.py && python tools/audit_audio.py", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the English/Filipino character pronunciation banks.")
    parser.add_argument("--bank", choices=("en", "fil", "both"), default="both")
    parser.add_argument("--only", default="", help="comma-separated char_*.wav filenames to regenerate (default: all)")
    args = parser.parse_args()
    banks = ["en", "fil"] if args.bank == "both" else [args.bank]
    only = [f.strip() for f in args.only.split(",") if f.strip()] or None
    return asyncio.run(main_async(banks, only))


if __name__ == "__main__":
    sys.exit(main())
