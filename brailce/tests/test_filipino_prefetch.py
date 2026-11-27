"""Scanner word prefetch: a word's Filipino audio is prepared while letters are spelled,
and speaking it waits for that preparation instead of synthesizing a second time."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from brailce.speech.filipino_engine import FilipinoEngine
from brailce.modes.scanner_mode import ScannerMode


def test_prefetch_then_speak_synthesizes_once(tmp_path):
    eng = FilipinoEngine()
    calls = []
    started = threading.Event()

    def fake_synth(text, out):
        calls.append(text)
        started.set()
        time.sleep(0.3)               # slow online synthesis
        with open(out, "wb") as f:
            f.write(b"\0" * 2000)
        return True

    with patch("brailce.speech.filipino_engine._find_filipino_audio_dir", return_value=str(tmp_path)), \
         patch("brailce.speech.filipino_engine.get_filipino_word_path", return_value=None), \
         patch.object(eng, "synthesize_to_wav", side_effect=fake_synth), \
         patch("brailce.speech.filipino_engine.AudioDeviceCoordinator.play_wav_file", return_value=True) as play:
        eng.prefetch("bahay.")
        started.wait(2)
        assert eng.speak_filipino("bahay.") is True
    assert calls == ["bahay."]          # synthesized exactly once
    play.assert_called_once()


def test_scanner_prefetches_each_scanned_word():
    mode = ScannerMode.__new__(ScannerMode)
    speech = MagicMock()
    cells = [
        {"char": "a", "word_index": 0, "line_index": 0},
        {"char": "n", "word_index": 0, "line_index": 0},
        {"char": "g", "word_index": 0, "line_index": 0},
        {"char": "b", "word_index": 1, "line_index": 0},
        {"char": "a", "word_index": 1, "line_index": 0},
        {"char": "?", "kind": "unknown", "word_index": 2, "line_index": 0},
        {"char": "x", "word_index": 2, "line_index": 0},
        {"char": "i", "word_index": 3, "line_index": 0},
    ]
    mode._prefetch_words(cells, speech)
    assert [c.args[0] for c in speech.prefetch_word.call_args_list] == ["ANG", "BA"]
