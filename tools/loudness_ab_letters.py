#!/usr/bin/env python3
"""
A/B listening test for the loudness processing on specific Filipino letters
(run ON the Pi with the app stopped).

  "one" = previous processing (fast limiter, no squeeze cap)  - the raspy one
  "two" = current processing (phase rotation + gentle limiter, squeeze cap)

    ../venv/bin/python3 tools/loudness_ab_letters.py               # o s f x m 1 0
    ../venv/bin/python3 tools/loudness_ab_letters.py --letters S,X --boost 12
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brailce.speech.speaker_master import master_samples, read_wav, write_wav  # noqa: E402

AUDIO = os.path.join(ROOT, "brailce", "audio")
VARIANTS = {
    1: dict(phase_rotation=False, max_reduction_db=1000.0, lookahead_ms=2.0, release_ms=60.0),
    2: dict(),  # tuned defaults
}


def _play(path: str) -> None:
    subprocess.run(["aplay", "-q", path], check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--letters", default="O,S,F,X,M,1,0")
    ap.add_argument("--boost", type=float, default=12.0)
    args = ap.parse_args()
    letters = [c.strip().upper() for c in args.letters.split(",") if c.strip()]

    with tempfile.TemporaryDirectory(prefix="brailce_ab_") as tmp:
        print("Preparing ...", flush=True)
        rounds = []
        for num, opts in VARIANTS.items():
            files = []
            for c in letters:
                src = os.path.join(AUDIO, "filipino", f"char_{c}.wav")
                if not os.path.isfile(src):
                    continue
                x, sr, _ = read_wav(src)
                dst = os.path.join(tmp, f"v{num}_{c}.wav")
                write_wav(dst, master_samples(x, sr, args.boost, **opts), sr)
                files.append(dst)
            rounds.append((num, files))

        for num, files in rounds:
            print(f"\n>>> NUMBER {num}: {'dating proseso' if num == 1 else 'bagong proseso'}", flush=True)
            _play(os.path.join(AUDIO, "characters", f"char_{num}.wav"))
            time.sleep(0.8)
            for f in files:
                _play(f)
                time.sleep(0.6)
            time.sleep(1.5)
    print("\nTapos. Alin ang mas malinis: one o two?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
