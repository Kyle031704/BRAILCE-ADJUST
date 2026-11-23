"""
Brailce Tests - Scanner Spell-Then-Word Readout
===============================================

Verifies the Scanner Mode readout contract:
- Each decoded character of a completed word is spoken IN ORDER (letters AND
  digits), then the whole word is spoken.
- Number-sign (dots 3-4-5-6) digit sequences decode to digits 1-0 and are
  spoken digit by digit, then as the completed number.
- Genuine decoded punctuation (e.g. "?") is spelled like any other character;
  only true unknown cells are announced as unknown.
- Filipino readout routes letters and the completed word through the Filipino
  bank path; English through the characters/ bank / Piper path.
- STOP_PRESSED (Esc) still cancels mid-readout (JOB 1 behavior).
"""

import time
from unittest.mock import MagicMock

import pytest

from brailce.core.app import BrailceApp
from brailce.core.events import EventType, InputEvent
from brailce.core.state import ApplicationState
from brailce.i18n.strings import get_string
from brailce.modes.scanner_mode import ScannerMode
from brailce.vision.obr_engine import OBREngine


@pytest.fixture
def mock_app() -> BrailceApp:
    """Create a mock BrailceApp with real state and mocked subsystems."""
    app = MagicMock(spec=BrailceApp)
    app.state = ApplicationState()
    app.display = MagicMock()
    app.speech = MagicMock()
    app.camera = MagicMock()
    app.obr = MagicMock()
    app.config = MagicMock()
    app.config.get.return_value = False
    return app


@pytest.fixture
def scanner_mode(mock_app: BrailceApp) -> ScannerMode:
    mode = ScannerMode(mock_app)
    mode.CHAR_PACE_S = 0.01
    mode.WORD_PAUSE_S = 0.01
    mode.LINE_PAUSE_S = 0.02
    return mode


def _cell(char: str, kind: str = "char", word_index: int = 0, line_index: int = 0) -> dict:
    return {
        "char": char,
        "kind": kind,
        "dots_str": "",
        "word_index": word_index,
        "line_index": line_index,
    }


def _no_wav_playback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force _speak_letter/_speak_completed_word to the speech-service fallback."""
    monkeypatch.setattr(
        "brailce.speech.audio_device.AudioDeviceCoordinator.play_wav_file",
        lambda *args, **kwargs: False,
    )


class TestSpellThenWord:
    """(a) A decoded word is spelled letter by letter, then spoken as a word."""

    def test_apple_spells_each_letter_in_order_then_whole_word(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        result = {
            "readable": True,
            "text": "APPLE",
            "cells": [_cell(c) for c in "APPLE"],
        }
        scanner_mode.scanned_text = "APPLE"
        scanner_mode._readout_worker(result)

        char_calls = [c.args[0] for c in mock_app.speech.speak_character.call_args_list]
        assert char_calls == ["A", "P", "P", "L", "E"]
        mock_app.speech.speak_word_clean.assert_called_once_with("APPLE", interrupt=False)

        # Exact call order: five letters in order, then the whole word.
        relevant = [
            c for c in mock_app.speech.mock_calls
            if c[0] in ("speak_character", "speak_word_clean")
        ]
        names = [c[0] for c in relevant]
        assert names == ["speak_character"] * 5 + ["speak_word_clean"]
        assert relevant[-1][1][0] == "APPLE"

    def test_multi_word_line_spells_each_word_then_word(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        result = {
            "readable": True,
            "text": "CAT DOG",
            "cells": [
                _cell("C", word_index=0), _cell("A", word_index=0), _cell("T", word_index=0),
                _cell("D", word_index=1), _cell("O", word_index=1), _cell("G", word_index=1),
            ],
        }
        scanner_mode.scanned_text = "CAT DOG"
        scanner_mode._readout_worker(result)

        char_calls = [c.args[0] for c in mock_app.speech.speak_character.call_args_list]
        assert char_calls == ["C", "A", "T", "D", "O", "G"]
        word_calls = [c.args[0] for c in mock_app.speech.speak_word_clean.call_args_list]
        assert word_calls == ["CAT", "DOG"]


class TestDigitCoverage:
    """(b) Number-sign digit sequences decode to digits and are spelled."""

    GRADE1_LETTERS = {
        "100000": "A", "110000": "B", "100100": "C", "100110": "D", "100010": "E",
        "110100": "F", "110110": "G", "110010": "H", "010100": "I", "010110": "J",
        "101000": "K", "111000": "L", "101100": "M", "101110": "N", "101010": "O",
        "111100": "P", "111110": "Q", "111010": "R", "011100": "S", "011110": "T",
        "101001": "U", "111001": "V", "010111": "W", "101101": "X", "101111": "Y",
        "101011": "Z",
    }
    GRADE1_DIGITS = {
        "100000": "1", "110000": "2", "100100": "3", "100110": "4", "100010": "5",
        "110100": "6", "110110": "7", "110010": "8", "010100": "9", "010110": "0",
    }

    def test_grade1_lookup_covers_all_letters_and_digits(self) -> None:
        """Letters a-z decode directly; digits 1-0 decode via the number sign."""
        assert OBREngine.decode_cell_label("001111") == "#"  # dots 3-4-5-6
        for bits, letter in self.GRADE1_LETTERS.items():
            assert OBREngine.decode_cell_label(bits) == letter
        for bits, digit in self.GRADE1_DIGITS.items():
            letter = OBREngine.BINARY_DICT[bits]
            assert OBREngine.NUM_MAP[letter] == digit

    def test_number_sign_sequence_decodes_to_digits_and_spells_them(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        # "#", A, B, J -> number sign then 1, 2, 0 (binary Grade 1 schema)
        boxes = [
            {"x": 50, "y": 100, "w": 30, "h": 40, "name": "001111", "conf": 0.9},
            {"x": 85, "y": 100, "w": 30, "h": 40, "name": "100000", "conf": 0.9},
            {"x": 120, "y": 100, "w": 30, "h": 40, "name": "110000", "conf": 0.9},
            {"x": 155, "y": 100, "w": 30, "h": 40, "name": "010110", "conf": 0.9},
        ]
        result = OBREngine.decode_boxes(boxes)
        assert result["readable"] is True
        assert result["text"] == "120"
        assert [c["kind"] for c in result["cells"]] == ["indicator", "digit", "digit", "digit"]
        assert [c["char"] for c in result["cells"] if c["kind"] == "digit"] == ["1", "2", "0"]

        scanner_mode.scanned_text = result["text"]
        scanner_mode._readout_worker(result)

        char_calls = [c.args[0] for c in mock_app.speech.speak_character.call_args_list]
        assert char_calls == ["1", "2", "0"]
        mock_app.speech.speak_word_clean.assert_called_once_with("120", interrupt=False)


class TestUnknownCells:
    """Unknown cells are announced and suppress the whole-word readout;
    genuine decoded punctuation is spelled like any other character."""

    def test_decoded_question_mark_is_spelled_not_announced_unknown(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        result = {
            "readable": True,
            "text": "WHAT?",
            "cells": [_cell(c) for c in "WHAT?"],
        }
        scanner_mode.scanned_text = "WHAT?"
        scanner_mode._readout_worker(result)

        char_calls = [c.args[0] for c in mock_app.speech.speak_character.call_args_list]
        assert char_calls == ["W", "H", "A", "T", "?"]
        mock_app.speech.speak_word_clean.assert_called_once_with("WHAT?", interrupt=False)
        for call in mock_app.speech.speak_text.call_args_list:
            assert call.args[0] != get_string("SCANNER_UNKNOWN_CELL", "en")

    def test_true_unknown_cell_announced_and_word_skipped(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        result = {
            "readable": True,
            "text": "A?PLE",
            "cells": [
                _cell("A"), _cell("?", kind="unknown"),
                _cell("P"), _cell("L"), _cell("E"),
            ],
        }
        scanner_mode.scanned_text = "A?PLE"
        scanner_mode._readout_worker(result)

        char_calls = [c.args[0] for c in mock_app.speech.speak_character.call_args_list]
        assert char_calls == ["A", "P", "L", "E"]
        mock_app.speech.speak_text.assert_any_call(
            get_string("SCANNER_UNKNOWN_CELL", "en"), interrupt=False
        )
        mock_app.speech.speak_word_clean.assert_not_called()

    def test_unknown_word_skipped_midline_next_word_still_spoken(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        result = {
            "readable": True,
            "text": "A? CAT",
            "cells": [
                _cell("A", word_index=0),
                _cell("?", kind="unknown", word_index=0),
                _cell("C", word_index=1),
                _cell("A", word_index=1),
                _cell("T", word_index=1),
            ],
        }
        scanner_mode.scanned_text = "A? CAT"
        scanner_mode._readout_worker(result)

        word_calls = [c.args[0] for c in mock_app.speech.speak_word_clean.call_args_list]
        assert word_calls == ["CAT"]


class TestFilipinoRouting:
    """(c) Filipino readout routes letters and words through the Filipino path."""

    def test_filipino_word_uses_filipino_char_and_word_banks(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_app.state.language = "fil"
        played: list = []
        monkeypatch.setattr(
            "brailce.speech.audio_device.AudioDeviceCoordinator.play_wav_file",
            lambda path, owner="scanner", timeout=5.0: played.append(path) or True,
        )
        result = {
            "readable": True,
            "text": "BAHAY",
            "cells": [_cell(c) for c in "BAHAY"],
        }
        scanner_mode.scanned_text = "BAHAY"
        scanner_mode._readout_worker(result)

        char_paths = [p.replace("\\", "/") for p in played if "/char_" in p.replace("\\", "/")]
        assert [p.rsplit("/", 1)[-1] for p in char_paths] == [
            "char_B.wav", "char_A.wav", "char_H.wav", "char_A.wav", "char_Y.wav",
        ]
        assert all("/audio/filipino/" in p for p in char_paths)

        word_paths = [p.replace("\\", "/") for p in played if "word_bahay.wav" in p.replace("\\", "/")]
        assert word_paths, f"filipino word wav not played: {played}"
        assert "/audio/filipino/" in word_paths[0]
        # No English-bank fallbacks fired while the Filipino bank covered everything.
        mock_app.speech.speak_character.assert_not_called()
        mock_app.speech.speak_word_clean.assert_not_called()

    def test_speech_service_routes_completed_word_to_filipino_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from brailce.speech.service import SpeechService

        fil_cls = MagicMock()
        monkeypatch.setattr("brailce.speech.filipino_engine.FilipinoEngine", fil_cls)
        service = SpeechService(character_engine=MagicMock(), text_engine=MagicMock())
        service.language = "fil"

        service.speak_word_clean("BAHAY")

        assert service.filipino_engine is fil_cls.return_value
        fil_cls.return_value.enqueue_text.assert_called_once_with("bahay.", interrupt=False)

    def test_speech_service_routes_english_letter_through_piper_character_path(
        self,
    ) -> None:
        from brailce.speech.service import SpeechService

        engine = MagicMock()
        service = SpeechService(character_engine=engine, text_engine=engine)
        service.language = "en"

        service.speak_character("A")

        engine.speak.assert_called_once_with("a.", interrupt=False)


class TestStopCancelsReadout:
    """(d) STOP_PRESSED (Esc) still cancels mid-readout."""

    def test_stop_pressed_mid_readout_stops_spelling_and_skips_word(
        self, scanner_mode: ScannerMode, mock_app: BrailceApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_wav_playback(monkeypatch)
        # config.get -> None so _get_char_pace_s() falls back to CHAR_PACE_S pacing
        mock_app.config.get.return_value = None
        scanner_mode.CHAR_PACE_S = 0.15
        result = {
            "readable": True,
            "text": "SCANNER",
            "cells": [_cell(c) for c in "SCANNER"],
        }
        scanner_mode.scanned_text = "SCANNER"
        scanner_mode._start_readout_thread(result)
        time.sleep(0.30)  # ~1-2 letters into the 7-letter spelling
        assert scanner_mode._is_reading is True

        scanner_mode.handle_event(InputEvent(event_type=EventType.STOP_PRESSED))
        if scanner_mode._readout_thread is not None:
            scanner_mode._readout_thread.join(timeout=2.0)

        assert scanner_mode._is_reading is False
        mock_app.speech.stop.assert_called()
        mock_app.display.show_status.assert_called_with(get_string("SCANNER_STOPPED", "en"))
        char_calls = [c.args[0] for c in mock_app.speech.speak_character.call_args_list]
        assert 0 < len(char_calls) < 7
        mock_app.speech.speak_word_clean.assert_not_called()