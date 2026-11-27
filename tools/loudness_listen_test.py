#!/usr/bin/env python3
"""
Loudness listening test for the speaker (run ON the Pi, app stopped).

Plays the same speech at several loudness boosts. Before each level the number
is announced ("one", "two", ...) at the current, unchanged level:

    1 = current sound (0 dB)   2 = +3 dB   3 = +6 dB   4 = +9 dB   5 = +12 dB

Listen for the LOUDEST number that still sounds clean (no "basag", no rattle,
no buzzing). That number's boost goes into speech.loudness_boost_db.

    sudo systemctl stop brailce.service
    ../venv/bin/python3 tools/loudness_listen_test.py            # all levels
    ../venv/bin/python3 tools/loudness_listen_test.py --levels 6,9
    sudo systemctl start brailce.service
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brailce.speech.speaker_master import master_file  # noqa: E402

AUDIO = os.path.join(ROOT, "brailce", "audio")
NUMBER_WORDS = {1: "char_1.wav", 2: "char_2.wav", 3: "char_3.wav", 4: "char_4.wav",
                5: "char_5.wav", 6: "char_6.wav", 7: "char_7.wav"}


def _samples() -> list:
    """A Filipino sentence, a mode intro and two letters: long + short sounds."""
    picks = []
    sentences = sorted(glob.glob(os.path.join(AUDIO, "filipino", "sentence_*.wav")),
                       key=os.path.getsize, reverse=True)
    if sentences:
        picks.append(sentences[len(sentences) // 4])   # a fairly long sentence
    for rel in ("filipino/mode_free_input.wav", "characters/char_A.wav", "filipino/char_B.wav"):
        p = os.path.join(AUDIO, rel)
        if os.path.isfile(p):
            picks.append(p)
    return picks


def _play(path: str) -> None:
    subprocess.run(["aplay", "-q", path], check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="0,3,6,9,12", help="boosts in dB, comma separated")
    ap.add_argument("--ceiling", type=float, default=28000.0)
    ap.add_argument("--highpass", type=float, default=180.0)
    args = ap.parse_args()
    levels = [float(v) for v in args.levels.split(",") if v.strip()]
    samples = _samples()
    if not samples:
        print("No bank WAVs found under", AUDIO)
        return 1

    with tempfile.TemporaryDirectory(prefix="brailce_listen_") as tmp:
        print("Preparing ...", flush=True)
        prepared = []
        for i, boost in enumerate(levels, start=1):
            files = []
            for j, src in enumerate(samples):
                dst = os.path.join(tmp, f"L{i}_{j}.wav")
                master_file(src, dst, boost, args.ceiling, args.highpass)
                files.append(dst)
            prepared.append((i, boost, files))

        for i, boost, files in prepared:
            print(f"\n>>> NUMBER {i}  =  +{boost:g} dB", flush=True)
            num = os.path.join(AUDIO, "characters", NUMBER_WORDS.get(i, "char_0.wav"))
            if os.path.isfile(num):
                _play(num)
            time.sleep(0.6)
            for f in files:
                _play(f)
                time.sleep(0.35)
            time.sleep(1.2)

    print("\nTapos. Ano ang PINAKAMALAKAS na numero na malinis pa (walang basag)?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
