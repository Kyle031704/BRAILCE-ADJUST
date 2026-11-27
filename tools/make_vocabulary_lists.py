#!/usr/bin/env python3
"""
Write the human-readable word and AI-question lists into listahan/:

  listahan/lahat_ng_salita.txt         every word with a ready recording (English + Filipino)
  listahan/mga_bagong_salita.txt       Filipino words that are not in filipino_cached_words.txt
                                       (the list as it was before the last vocabulary update)
  listahan/ai_assistant_mga_tanong.txt every question the AI Assistant answers from its
                                       built-in list, with the answer

    python tools/make_vocabulary_lists.py [--english-list FILE]

--english-list: a file listing English word_*.wav names (the English cache lives on the
Pi in brailce/audio/words_en/; export it with `ls`). Defaults to that folder locally.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "listahan")
FIL_DIR = os.path.join(ROOT, "brailce", "audio", "filipino")

FIL_QUESTION_START = ("ano ", "sino ", "ilang ", "ilan ", "kailan ", "saang ", "saan ", "paano ",
                      "kumusta", "magandang", "salamat", "tulong", "linggo", "mga araw")


def _words_from_names(names: List[str]) -> List[str]:
    out = set()
    for n in names:
        n = os.path.basename(n.strip())
        m = re.match(r"word_(.+)\.wav$", n)
        if m:
            out.add(m.group(1))
    return sorted(out)


def _columns(words: List[str], per_line: int = 6, width: int = 18) -> List[str]:
    lines = []
    for i in range(0, len(words), per_line):
        lines.append("".join(w.ljust(width) for w in words[i:i + per_line]).rstrip())
    return lines


def write_vocabulary(english_names: List[str]) -> None:
    fil = _words_from_names(glob.glob(os.path.join(FIL_DIR, "word_*.wav")))
    eng = _words_from_names(english_names)
    old_path = os.path.join(ROOT, "filipino_cached_words.txt")
    old = set()
    if os.path.isfile(old_path):
        with open(old_path, encoding="utf-8-sig") as f:
            old = {l.strip() for l in f if l.strip()}
    new = [w for w in fil if w not in old]

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "lahat_ng_salita.txt"), "w", encoding="utf-8") as f:
        f.write("BRAILCE - LAHAT NG SALITANG MAY HANDANG RECORDING\n")
        f.write("=" * 60 + "\n\n")
        f.write("Ang mga salitang ito ay agad na nabibigkas (naka-save na ang boses,\n")
        f.write("hindi na kailangan ng internet).\n\n")
        f.write("Paalala:\n")
        f.write("- ENGLISH: kahit anong English na salita ay kayang bigkasin ng Piper voice\n")
        f.write("  nang offline. Ang nasa listahan ay ang mga naka-save na (pinakamabilis).\n")
        f.write("- FILIPINO: ang salitang wala sa listahan ay kinukuha sa internet\n")
        f.write("  (Blessica voice) at sine-save para sa susunod. Kapag walang internet,\n")
        f.write("  English voice ang babasa nito.\n")
        f.write("- Lahat ng titik (A-Z, N-tilde) at numero (0-9) ay may sariling recording.\n\n")
        f.write(f"FILIPINO ({len(fil)} salita)\n" + "-" * 60 + "\n")
        f.write("\n".join(_columns(fil)) + "\n\n")
        f.write(f"ENGLISH ({len(eng)} salita)\n" + "-" * 60 + "\n")
        f.write("\n".join(_columns(eng)) + "\n")
    with open(os.path.join(OUT, "mga_bagong_salita.txt"), "w", encoding="utf-8") as f:
        f.write("BRAILCE - MGA BAGONG FILIPINO NA SALITA\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"{len(new)} bagong salitang may recording na (Blessica voice, parehong lakas\n")
        f.write("at proseso ng dating mga recording). Ginawa gamit ang\n")
        f.write("tools/add_filipino_words.py at tools/filipino_vocabulary_source.txt.\n\n")
        f.write("\n".join(_columns(new)) + "\n")
    print(f"Filipino {len(fil)} (bago: {len(new)}), English {len(eng)}")


def write_ai_questions() -> None:
    from brailce.ai.ollama_client import FACTUAL_QA_RESPONSES
    from brailce.modes.ai_assistant import FILIPINO_AI_OFFLINE_RESPONSES

    fil: Dict[str, str] = dict(FILIPINO_AI_OFFLINE_RESPONSES)
    eng: Dict[str, str] = {}
    for q, a in FACTUAL_QA_RESPONSES.items():
        if q.startswith(FIL_QUESTION_START) or q in fil:
            fil.setdefault(q, a)
        else:
            eng[q] = a

    def block(d: Dict[str, str]) -> List[str]:
        lines = []
        for i, (q, a) in enumerate(sorted(d.items()), start=1):
            lines.append(f"{i:3d}. {q}")
            lines.append(f"     -> {a}")
        return lines

    with open(os.path.join(OUT, "ai_assistant_mga_tanong.txt"), "w", encoding="utf-8") as f:
        f.write("BRAILCE AI ASSISTANT - MGA TANONG NA ALAM SAGUTIN\n")
        f.write("=" * 60 + "\n\n")
        f.write("PAANO GUMAGANA:\n")
        f.write("1. Mga tanong sa listahang ito: agad at laging tama ang sagot (naka-built-in).\n")
        f.write("   Dapat eksakto ang mga salita (hindi mahalaga ang malaki/maliit na titik\n")
        f.write("   at ang tandang pananong). Hal. \"What is Braille?\" = \"what is braille\".\n")
        f.write("2. Mga tanong sa matematika: kahit anong numero, hal. \"what is 7 + 8\",\n")
        f.write("   \"ano ang 12 - 5\", \"3 * 4\", \"10 / 2\" - sasagutin ng Math solver.\n")
        f.write("3. Ibang tanong: sinasagot ng offline AI model sa Pi\n")
        f.write("   (liquidai/lfm2.5-1.2b-instruct). Kaya nitong sagutin ang maraming\n")
        f.write("   pangkalahatang tanong, pero maliit na model ito: maaaring mali o\n")
        f.write("   hindi kumpleto ang sagot, at mas mabagal (ilang segundo).\n")
        f.write("4. Sa Filipino mode, ang AI ay sumasagot sa Filipino; kapag Ingles ang\n")
        f.write("   naging sagot, inuulit ito o nagbibigay ng paalala.\n\n")
        f.write(f"FILIPINO ({len(fil)} tanong)\n" + "-" * 60 + "\n")
        f.write("\n".join(block(fil)) + "\n\n")
        f.write(f"ENGLISH ({len(eng)} tanong)\n" + "-" * 60 + "\n")
        f.write("\n".join(block(eng)) + "\n")
    print(f"AI questions: Filipino {len(fil)}, English {len(eng)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--english-list", default=None)
    args = ap.parse_args()
    if args.english_list:
        with open(args.english_list, encoding="utf-8-sig") as f:
            names = [l for l in f if l.strip()]
    else:
        names = glob.glob(os.path.join(ROOT, "brailce", "audio", "words_en", "word_*.wav"))
    write_vocabulary(names)
    write_ai_questions()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
