#!/usr/bin/env python3
"""
Pre-render natural Filipino sentence audio (Blessica, whole sentence with real
intonation) for every built-in Filipino AI Assistant answer, so the device plays
them instantly and offline instead of stitching word recordings together.

Files go to brailce/audio/filipino/sentence_<sha1(text)>.wav, exactly the cache key
FilipinoEngine looks up for the text SpeechService.speak_response_text() speaks.
Uses FilipinoEngine._synthesize_edge_blessica() (same processing as the device
uses online). Needs internet + miniaudio (run on the PC).

    python tools/prerender_filipino_sentences.py            # render missing ones
    python tools/prerender_filipino_sentences.py --force    # re-render all
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

FIL_DIR = os.path.join(ROOT, "brailce", "audio", "filipino")
FIL_QUESTION_START = ("ano ", "sino ", "ilang ", "ilan ", "kailan ", "saang ", "saan ", "paano ",
                      "kumusta", "magandang", "salamat", "tulong", "linggo", "mga araw", "bakit ")


def spoken_texts() -> List[str]:
    from brailce.ai.ollama_client import FACTUAL_QA_RESPONSES, clean_ai_response
    from brailce.modes.ai_assistant import FILIPINO_AI_OFFLINE_RESPONSES
    from brailce.speech.service import normalize_response_text

    answers = list(FILIPINO_AI_OFFLINE_RESPONSES.values())
    answers += [a for q, a in FACTUAL_QA_RESPONSES.items() if q.startswith(FIL_QUESTION_START)]
    out, seen = [], set()
    for a in answers:
        # Same path as AIAssistantMode._finish_processing -> speak_response_text
        t = normalize_response_text(clean_ai_response(a) or a)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    from brailce.speech.filipino_engine import FilipinoEngine
    from add_filipino_words import _audit_median, _restore_audit_median

    texts = spoken_texts()
    eng = FilipinoEngine()
    before = _audit_median()
    made, failed = [], []
    for t in texts:
        path = os.path.join(FIL_DIR, f"sentence_{hashlib.sha1(t.encode('utf-8')).hexdigest()}.wav")
        if os.path.isfile(path) and not args.force:
            continue
        if eng._synthesize_edge_blessica(t, path) and os.path.getsize(path) > 1000:
            made.append(path)
        else:
            failed.append(t)
    print(f"{len(texts)} Filipino answers: rendered {len(made)}, failed {len(failed)}.")
    for t in failed:
        print("  FAILED:", t)
    if made:
        _restore_audit_median(before, made)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
