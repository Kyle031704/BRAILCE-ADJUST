#!/usr/bin/env python3
"""
Add Filipino word recordings to the audio bank (brailce/audio/filipino/word_<word>.wav).

Same voice and processing as the existing bank (tools/rebuild_filipino_blessica.py +
the later bank re-master): edge-tts fil-PH-BlessicaNeural reading "<word>.", 22050 Hz
mono 16-bit, silence trimmed with a 20 ms margin, 5 ms fades, active RMS matched to the
current bank median (~3950), peak <= 30000, 120 ms lead / 180 ms tail silence.

Words that already have a recording are skipped. Needs internet (run on the PC).

    python tools/add_filipino_words.py tools/filipino_vocabulary_source.txt
    python tools/add_filipino_words.py tools/filipino_vocabulary_source.txt --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import re
import sys
import wave
from typing import List, Optional, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIL_DIR = os.path.join(ROOT, "brailce", "audio", "filipino")
VOICE = "fil-PH-BlessicaNeural"
SR = 22050
PRE_MS, POST_MS = 120, 180


def slug(word: str) -> str:
    raw = word.strip().lower().rstrip(".,!?:;").replace("'", "")
    return re.sub(r"[^a-z0-9\-]", "", raw)


def read_list(path: str) -> List[Tuple[str, str]]:
    out, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            word, _, spoken = line.partition("|")
            s = slug(word)
            if s and s not in seen:
                seen.add(s)
                out.append((s, (spoken or word).strip()))
    return out


def bank_target_rms() -> float:
    vals = []
    for f in glob.glob(os.path.join(FIL_DIR, "word_*.wav")):
        with wave.open(f, "rb") as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)
        a = x[np.abs(x) > 500]
        if a.size:
            vals.append(float(np.sqrt((a ** 2).mean())))
    return float(np.median(vals)) if vals else 3950.0


def process(pcm: np.ndarray, sr_in: int, target_rms: float) -> np.ndarray:
    x = pcm.astype(np.float64)
    if sr_in != SR:
        n = int(round(len(x) * SR / sr_in))
        x = np.interp(np.linspace(0, len(x), n, endpoint=False), np.arange(len(x)), x)
    nz = np.where(np.abs(x) > 150)[0]
    if nz.size:
        m = int(SR * 0.02)
        x = x[max(0, nz[0] - m): min(len(x), nz[-1] + m)]
    a = x[np.abs(x) > 500]
    if a.size:
        x *= target_rms / float(np.sqrt((a ** 2).mean()))
    peak = float(np.abs(x).max()) if x.size else 0.0
    if peak > 30000:
        x *= 30000.0 / peak
    fade = min(int(SR * 0.005), len(x) // 8)
    if fade > 1:
        x[:fade] *= 0.5 * (1 - np.cos(np.linspace(0, np.pi, fade)))
        x[-fade:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, fade)))
    pad = lambda ms: np.zeros(int(SR * ms / 1000.0))
    return np.clip(np.round(np.concatenate([pad(PRE_MS), x, pad(POST_MS)])), -32767, 32767).astype(np.int16)


def _audit_stats():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from audit_audio import MIN_RMS, compute_wav_stats  # noqa: E402
    return MIN_RMS, compute_wav_stats


def _audit_median() -> float:
    min_rms, stats = _audit_stats()
    vals = [stats(f)[3] for f in glob.glob(os.path.join(FIL_DIR, "*.wav"))]
    vals = [v for v in vals if v >= min_rms]
    return float(np.median(vals)) if vals else 0.0


def _restore_audit_median(target: float, new_files: List[str]) -> None:
    """Scale only the new recordings so tools/audit_audio.py's bank median (whole-file
    RMS of every Filipino WAV) stays exactly where it was: the old files near the
    +/-3 dB audit edge must not be pushed over it by a shifted median."""
    if target <= 0 or not new_files:
        return
    min_rms, stats = _audit_stats()
    new_set = set(new_files)
    old = np.array([stats(f)[3] for f in glob.glob(os.path.join(FIL_DIR, "*.wav")) if f not in new_set])
    new = np.array([stats(f)[3] for f in new_files])
    best_k, best_err = 1.0, float("inf")
    for k in np.linspace(0.80, 1.25, 901):
        allv = np.concatenate([old, new * k])
        m = float(np.median(allv[allv >= min_rms]))
        if abs(m - target) < best_err:
            best_k, best_err = float(k), abs(m - target)
    for f in new_files:
        with wave.open(f, "rb") as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)
        y = x * best_k
        peak = float(np.abs(y).max()) if y.size else 0.0
        if peak > 30000:
            y *= 30000.0 / peak
        with wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(np.round(y).astype("<i2").tobytes())
    print(f"Bank loudness kept at median {target:.1f} (new files scaled {20 * np.log10(best_k):+.2f} dB).")


async def synth(text: str) -> Optional[Tuple[np.ndarray, int]]:
    import edge_tts
    import miniaudio
    for attempt in range(3):
        try:
            buf = bytearray()
            async for chunk in edge_tts.Communicate(text, VOICE).stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if not buf:
                raise RuntimeError("empty audio")
            dec = miniaudio.mp3_read_s16(bytes(buf))
            x = np.frombuffer(dec.samples, dtype=np.int16)
            if dec.nchannels == 2:
                x = x.reshape(-1, 2).mean(axis=1)
            return x, dec.sample_rate
        except Exception:
            await asyncio.sleep(0.7 * (attempt + 1))
    return None


async def run(items: List[Tuple[str, str]], target_rms: float) -> Tuple[List[str], List[str]]:
    sem = asyncio.Semaphore(4)
    added, failed = [], []

    async def one(s: str, spoken: str) -> None:
        async with sem:
            res = await synth(spoken if spoken.endswith((".", "?", "!")) else spoken + ".")
        if res is None:
            failed.append(s)
            return
        pcm = process(res[0], res[1], target_rms)
        path = os.path.join(FIL_DIR, f"word_{s}.wav")
        tmp = path + ".part"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        os.replace(tmp, path)
        added.append(s)

    await asyncio.gather(*(one(s, sp) for s, sp in items))
    return sorted(added), sorted(failed)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("word_list")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    items = read_list(args.word_list)
    todo = [(s, sp) for s, sp in items if not os.path.isfile(os.path.join(FIL_DIR, f"word_{s}.wav"))]
    print(f"{len(items)} words in list, {len(items) - len(todo)} already recorded, {len(todo)} to add.")
    if args.dry_run or not todo:
        for s, sp in todo:
            print("  +", s, "" if sp == s else f"(spoken: {sp})")
        return 0
    target = bank_target_rms()
    print(f"Matching bank level: active RMS {target:.0f}")
    before_median = _audit_median()
    added, failed = asyncio.run(run(todo, target))
    print(f"Added {len(added)} recordings.")
    if added:
        _restore_audit_median(before_median, [os.path.join(FIL_DIR, f"word_{s}.wav") for s in added])
    if failed:
        print("FAILED:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
