"""
Brailce Tests - LCD Typed-Text Lowercase Rendering
======================================================

FIX 1: letters/words typed by the user must render lowercase on the LCD
(while typing and after typing), for EN and FIL, with numbers/punctuation
unaffected. Labels, mode names, AI response headers, and braille-dot
visuals must stay as-is.

Covers:
  - ConsoleDisplay (real display, the testable console path)
  - ILI9488Display (canvas draw recorder, no hardware needed)
  - Mode call sites (mock display): typed show_character content is
    lowercased at the mode call sites because show_character also
    renders labels ("Space", "Backspace", "Cleared").
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from brailce.core.app import BrailceApp
from brailce.core.events import AppMode, EventType, InputEvent
from brailce.braille.models import BrailleMapping
from brailce.display.console_display import ConsoleDisplay
from brailce.display.ili9488_display import ILI9488Display
from brailce.modes.ai_assistant import AIAssistantMode
from brailce.modes.free_input import FreeInputMode
from brailce.modes.math_mode import MathMode
from brailce.speech.service import SpeechService


# ---------------------------------------------------------------------------
# ConsoleDisplay (real display driver, output captured)
# ---------------------------------------------------------------------------

@pytest.fixture
def console() -> ConsoleDisplay:
    display = ConsoleDisplay(use_colors=False)
    display._use_colors = False
    return display


class TestConsoleTypedContentLowercase:
    def test_en_word_lowercase(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("APPLE")
        out = capsys.readouterr().out
        assert "apple" in out
        assert "APPLE" not in out

    def test_fil_word_lowercase(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("BAHAY")
        out = capsys.readouterr().out
        assert "bahay" in out
        assert "BAHAY" not in out

    def test_sentence_lowercase(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("WHAT IS RAIN?")
        out = capsys.readouterr().out
        assert "what is rain?" in out

    def test_numbers_unaffected(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("120")
        out = capsys.readouterr().out
        assert "120" in out

    def test_math_buffer_unaffected(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("5 + 3")
        out = capsys.readouterr().out
        assert "5 + 3" in out

    def test_punctuation_unaffected(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("#1,024?")
        out = capsys.readouterr().out
        assert "#1,024?" in out

    def test_mixed_letters_digits_punct(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("Hello World 42!")
        out = capsys.readouterr().out
        assert "hello world 42!" in out

    def test_empty_buffer_placeholder_is_a_label(self, console: ConsoleDisplay, capsys) -> None:
        console.show_text_buffer("")
        out = capsys.readouterr().out
        assert "(empty)" in out

    def test_ai_processing_typed_query_lowercase(self, console: ConsoleDisplay, capsys) -> None:
        console.show_ai_processing("WHAT IS RAIN?")
        out = capsys.readouterr().out
        assert "what is rain?" in out
        assert "WHAT IS RAIN?" not in out

    def test_ai_response_query_lowercase_but_answer_untouched(
        self, console: ConsoleDisplay, capsys
    ) -> None:
        console.show_ai_response("The Quick Brown Fox jumps.", query="WHAT IS RAIN?")
        out = capsys.readouterr().out
        assert "what is rain?" in out            # typed query echo -> lowercase
        assert "The Quick Brown Fox jumps." in out  # AI response -> unchanged

    def test_math_equation_digits_unaffected(self, console: ConsoleDisplay, capsys) -> None:
        console.show_math_result(
            equation="5 + 3",
            result="8",
            explanation="Start at 5, and count up 3 more: 6, 7, 8.",
        )
        out = capsys.readouterr().out
        assert "5 + 3 = 8" in out
        assert "Start at 5, and count up 3 more" in out


class TestConsoleLabelsNotLowercased:
    def test_control_labels_untouched(self, console: ConsoleDisplay, capsys) -> None:
        console.show_character("Space")
        console.show_character("Backspace")
        console.show_character("Cleared")
        console.show_character("# (Number Sign)")
        out = capsys.readouterr().out
        assert "[ Space ]" in out
        assert "[ Backspace ]" in out
        assert "[ Cleared ]" in out
        assert "# (Number Sign)" in out

    def test_braille_dot_visual_untouched(self, console: ConsoleDisplay, capsys) -> None:
        console.show_braille("BRAILLE-UNICODE-PLACEHOLDER", "Dots 1-2", "")
        out = capsys.readouterr().out
        assert "Dots 1-2" in out

    def test_mode_name_untouched(self, console: ConsoleDisplay, capsys) -> None:
        console.show_mode(AppMode.FREE_INPUT)
        out = capsys.readouterr().out
        assert "Free Input Mode" in out


# ---------------------------------------------------------------------------
# ILI9488Display (LCD driver, draw recorder instead of hardware)
# ---------------------------------------------------------------------------

class FakeDraw:
    """Records every text drawn onto the LCD canvas."""

    def __init__(self) -> None:
        self.texts = []

    def rectangle(self, *args, **kwargs) -> None:
        pass

    def line(self, *args, **kwargs) -> None:
        pass

    def ellipse(self, *args, **kwargs) -> None:
        pass

    def text(self, pos, s, **kwargs) -> None:
        self.texts.append(s)


@pytest.fixture
def lcd() -> ILI9488Display:
    display = ILI9488Display()
    display._draw = FakeDraw()
    display._image = object()  # truthy canvas marker; no hardware flush
    display._current_mode = AppMode.FREE_INPUT
    return display


class TestILI9488TypedContentLowercase:
    def test_en_word_lowercase(self, lcd: ILI9488Display) -> None:
        lcd.show_text_buffer("APPLE")
        assert "apple" in lcd._draw.texts
        assert "APPLE" not in lcd._draw.texts

    def test_fil_word_lowercase(self, lcd: ILI9488Display) -> None:
        lcd.show_text_buffer("BAHAY")
        assert "bahay" in lcd._draw.texts
        assert "BAHAY" not in lcd._draw.texts

    def test_numbers_punctuation_unaffected(self, lcd: ILI9488Display) -> None:
        lcd.show_text_buffer("120")
        assert "120" in lcd._draw.texts
        lcd._draw.texts.clear()
        lcd.show_text_buffer("#1,024?")
        assert "#1,024?" in lcd._draw.texts

    def test_empty_placeholder_is_a_label(self, lcd: ILI9488Display) -> None:
        lcd.show_text_buffer("")
        assert "Type letters on the keypad to practice" in lcd._draw.texts

    def test_ai_query_input_lowercase_and_stored(self, lcd: ILI9488Display) -> None:
        lcd._current_mode = AppMode.AI_ASSISTANT
        lcd.show_ai_query_input("WHAT IS RAIN?")
        assert lcd._current_query == "what is rain?"
        assert "what is rain?" in lcd._draw.texts

    def test_ai_processing_typed_query_lowercase(self, lcd: ILI9488Display) -> None:
        lcd.show_ai_processing("WHAT IS RAIN?")
        assert "what is rain?" in lcd._draw.texts

    def test_ai_response_query_lowercase_answer_untouched(self, lcd: ILI9488Display) -> None:
        lcd.show_ai_response("The Quick Brown Fox jumps.", query="WHAT IS RAIN?")
        assert "what is rain?"[:20] in " ".join(lcd._draw.texts)
        assert "The Quick Brown Fox" not in lcd._draw.texts  # answer is wrapped separately
        assert "AI ANSWER" in lcd._draw.texts                # header label untouched

    def test_math_equation_digits_unaffected(self, lcd: ILI9488Display) -> None:
        lcd.show_math_result(
            equation="5 + 3",
            result="8",
            explanation="Start at 5, and count up 3 more: 6, 7, 8.",
        )
        assert "5 + 3 =" in lcd._draw.texts   # big equation, answer drawn in the accent colour
        assert "8" in lcd._draw.texts
        assert "COUNTING EXPLANATION" in lcd._draw.texts

    def test_status_labels_untouched(self, lcd: ILI9488Display) -> None:
        lcd.show_text_buffer("APPLE")
        # Mode pill + card headers stay uppercase; only the typed buffer is lowered.
        assert "TYPED TEXT" in lcd._draw.texts
        assert "apple" in lcd._draw.texts

    def test_scanner_buffer_has_scanner_label_and_no_free_input_hint(self, lcd: ILI9488Display) -> None:
        lcd._current_mode = AppMode.SCANNER
        lcd._last_status_msg = None
        lcd.show_text_buffer("LUPA")
        assert "SCANNED TEXT" in lcd._draw.texts
        assert "TYPED TEXT" not in lcd._draw.texts
        assert lcd._last_status_msg is None  # no "Letters: N | Press Space..." hint in Scanner

    def test_braille_dots_label_not_doubled(self, lcd: ILI9488Display) -> None:
        lcd.show_braille("V", "Dots 1-2-3-6", "")
        assert "1-2-3-6" in lcd._draw.texts
        assert not any("Dots" in t for t in lcd._draw.texts)


    def test_multi_cell_braille_shows_every_cell(self, lcd: ILI9488Display) -> None:
        # Math answer "14" in Nemeth: two cells, must not be merged into one cell.
        lcd.show_braille("⠂⠲", "Dot 2 Dots 2-5-6", "")
        assert "2 | 2-5-6" in lcd._draw.texts
        assert not any("Dot 2 Dots" in t for t in lcd._draw.texts)

    def test_math_result_braille_goes_into_result_panel(self, lcd: ILI9488Display) -> None:
        lcd._current_mode = AppMode.MATH
        lcd.show_math_result(equation="11 + 3", result="14", explanation="Count up 3 more.")
        lcd._draw.texts.clear()
        lcd.show_braille("⠂⠲", "Dot 2 Dots 2-5-6", "")
        assert lcd._layout == "math_result"          # result card stays on screen
        assert "CHARACTER" not in lcd._draw.texts      # typing cards are not drawn over it

    def test_math_typing_status_is_math_hint(self, lcd: ILI9488Display) -> None:
        lcd._current_mode = AppMode.MATH
        lcd.show_text_buffer("2+3")
        assert lcd._last_status_msg.startswith("Ready for math input")


class TestILI9488Fonts:
    def test_bundled_dejavu_font_is_used(self) -> None:
        display = ILI9488Display()
        font = display._f(15)
        assert font.getname()[0] == "DejaVu Sans"
        assert display._f(15, bold=True).getname()[1] == "Bold"


class TestILI9488BatteryIndicator:
    def test_battery_override_and_header_refresh(self, lcd: ILI9488Display) -> None:
        lcd.show_battery(75, False)
        assert lcd._get_battery_info() == (75, False)
        assert "75%" in lcd._draw.texts
        assert "FREE INPUT" in lcd._draw.texts  # header redrawn, labels intact

    def test_battery_charging_marker(self, lcd: ILI9488Display) -> None:
        lcd.show_battery(60, True)
        assert lcd._get_battery_info() == (60, True)
        assert "60%+" in lcd._draw.texts

    def test_battery_default_when_no_data(self, lcd: ILI9488Display) -> None:
        # No poller value and no monitor attached: default 100% plugged.
        assert lcd._get_battery_info() == (100, True)

    def test_battery_none_ignored(self, lcd: ILI9488Display) -> None:
        lcd.show_battery(None, False)
        assert lcd._battery_override is None


# ---------------------------------------------------------------------------
# Mode call sites (mock display): typed show_character content
# ---------------------------------------------------------------------------

def mock_app() -> MagicMock:
    app = MagicMock(spec=BrailceApp)
    app.state = MagicMock()
    app.state.ai_processing = False
    app.state.number_mode = False
    app.state.language = "en"
    app.display = MagicMock()
    app.speech = MagicMock(spec=SpeechService)
    app.encoder = MagicMock()
    app.config = MagicMock()
    app.config.get.return_value = True
    return app


def mapping_for(char: str) -> BrailleMapping:
    return BrailleMapping(
        character=char,
        display_label=char,
        braille_dots={1},
        braille_unicode="\u2801",
        speech=char,
    )


class TestFreeInputModeCallSites:
    def test_en_typed_letter_lowercase(self) -> None:
        app = mock_app()
        app.encoder.encode.return_value = mapping_for("A")
        app.state.text_buffer = "A"
        FreeInputMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="A"))
        app.display.show_character.assert_called_once_with("a")
        # Buffer is passed through untouched: the display layer lowercases it.
        app.display.show_text_buffer.assert_called_once_with("A")

    def test_fil_typed_letter_lowercase(self) -> None:
        app = mock_app()
        app.state.language = "fil"
        app.encoder.encode.return_value = mapping_for("B")
        app.state.text_buffer = "B"
        FreeInputMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="B"))
        app.display.show_character.assert_called_once_with("b")

    def test_number_mode_digit_annotation_lowercase(self) -> None:
        app = mock_app()
        app.state.number_mode = True
        app.state.text_buffer = "1"
        FreeInputMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="A"))
        app.display.show_character.assert_called_once_with("1 (from a)")

    def test_typed_digit_unaffected(self) -> None:
        app = mock_app()
        app.encoder.encode.return_value = mapping_for("5")
        app.state.text_buffer = "5"
        FreeInputMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="5"))
        app.display.show_character.assert_called_once_with("5")

    def test_typed_punctuation_unaffected(self) -> None:
        app = mock_app()
        app.encoder.encode.return_value = mapping_for(",")
        app.state.text_buffer = ","
        FreeInputMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character=","))
        app.display.show_character.assert_called_once_with(",")

    def test_labels_not_lowercased(self) -> None:
        app = mock_app()
        FreeInputMode(app).handle_event(InputEvent(EventType.SPACE_PRESSED))
        app.display.show_character.assert_called_once_with("Space")
        app.display.show_text_buffer.assert_called_once_with(app.state.text_buffer)

    def test_spoken_status_echoes_typed_content_lowercase(self) -> None:
        app = mock_app()
        app.state.text_buffer = "APPLE"
        FreeInputMode(app).handle_event(InputEvent(EventType.ENTER_PRESSED))
        app.display.show_status.assert_called_once_with('Spoken: "apple"')


class TestMathModeCallSites:
    def test_typed_digit_unaffected(self) -> None:
        app = mock_app()
        app.encoder.encode.return_value = mapping_for("5")
        app.state.math_buffer = "5"
        MathMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="5"))
        app.display.show_character.assert_called_with("5")

    def test_typed_operator_unaffected(self) -> None:
        app = mock_app()
        app.encoder.encode.return_value = mapping_for("+")
        app.state.math_buffer = "+"
        MathMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="+"))
        app.display.show_character.assert_called_with("+")


class TestAIAssistantModeCallSites:
    def test_en_typed_letter_lowercase(self) -> None:
        app = mock_app()
        app.encoder.encode.return_value = mapping_for("A")
        AIAssistantMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="A"))
        app.display.show_character.assert_called_once_with("a")

    def test_fil_typed_letter_lowercase(self) -> None:
        app = mock_app()
        app.state.language = "fil"
        app.encoder.encode.return_value = mapping_for("N")
        AIAssistantMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="N"))
        app.display.show_character.assert_called_once_with("n")

    def test_typed_digit_unaffected(self) -> None:
        app = mock_app()
        AIAssistantMode(app).handle_event(InputEvent(EventType.CHARACTER_PRESSED, character="7"))
        app.display.show_character.assert_called_once_with("7")

    def test_space_label_not_lowercased(self) -> None:
        app = mock_app()
        AIAssistantMode(app).handle_event(InputEvent(EventType.SPACE_PRESSED))
        app.display.show_character.assert_called_once_with("Space")


def test_lcd_space_only_buffer_does_not_crash(lcd: ILI9488Display) -> None:
    """Space as the first key gives an all-space buffer; textwrap returns [] (was IndexError)."""
    lcd.show_text_buffer(" ")
    lcd.show_ai_query_input("  ")


class TestILI9488BatchAndLock:
    def test_batch_sends_one_lcd_update(self, lcd: ILI9488Display, monkeypatch) -> None:
        flushes = []
        monkeypatch.setattr(lcd, "_device", type("Dev", (), {"display": lambda self, img: flushes.append(1)})())
        lcd._current_mode = AppMode.SCANNER
        with lcd.batch():
            lcd.show_character("b")
            lcd.show_braille("\u2803", "Dots 1-2", "")
            lcd.show_text_buffer("BAHAY")
        assert len(flushes) == 1

    def test_preview_skips_frame_while_another_thread_draws(self, lcd: ILI9488Display) -> None:
        import threading
        import numpy as np
        drawn = threading.Event()
        release = threading.Event()

        def hold_canvas():
            with lcd._canvas_lock:
                drawn.set()
                release.wait(2)

        t = threading.Thread(target=hold_canvas)
        t.start()
        drawn.wait(2)
        before = lcd._image
        lcd.show_camera_preview(np.zeros((48, 64, 3), np.uint8), status="x")
        assert lcd._image is before          # frame skipped, canvas untouched
        release.set()
        t.join()
