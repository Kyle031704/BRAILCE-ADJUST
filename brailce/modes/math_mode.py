"""
Brailce Mode – Math Mode (Method 1)
=====================================

Dedicated Math Mode for Grade 1 DepEd learners.

Allows learners to type arithmetic expressions (e.g. "5 + 3") on dedicated
Braille buttons with real-time auditory/Braille feedback, and press Enter
(or equals) to receive an immediate deterministic step-by-step counting
explanation tailored for Grade 1 visually impaired students.

Pipeline per key press:
    CHARACTER_PRESSED (digit/op) → Braille lookup → math buffer update →
    display update → spoken feedback
Enter / Equals:
    ENTER_PRESSED → SafeArithmetic solver → display solution card →
    spoken counting explanation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from brailce.braille.arithmetic import SafeArithmetic
from brailce.core.events import AppMode, EventType, InputEvent
from brailce.modes.base_mode import BaseMode

if TYPE_CHECKING:
    from brailce.core.app import BrailceApp

logger = logging.getLogger(__name__)

# Grade 1 Braille / UEB: Letters A-J correspond directly to numbers 1-0 under the # indicator
BRAILLE_LETTER_TO_NUMBER = {
    "A": ("1", "one", "⠁", "Dots 1", "● ○\n○ ○\n○ ○"),
    "B": ("2", "two", "⠃", "Dots 1-2", "● ○\n● ○\n○ ○"),
    "C": ("3", "three", "⠉", "Dots 1-4", "● ●\n○ ○\n○ ○"),
    "D": ("4", "four", "⠙", "Dots 1-4-5", "● ●\n○ ●\n○ ○"),
    "E": ("5", "five", "⠑", "Dots 1-5", "● ○\n○ ●\n○ ○"),
    "F": ("6", "six", "⠋", "Dots 1-2-4", "● ●\n● ○\n○ ○"),
    "G": ("7", "seven", "⠛", "Dots 1-2-4-5", "● ●\n● ●\n○ ○"),
    "H": ("8", "eight", "⠓", "Dots 1-2-5", "● ○\n● ●\n○ ○"),
    "I": ("9", "nine", "⠊", "Dots 2-4", "○ ●\n● ○\n○ ○"),
    "J": ("0", "zero", "⠚", "Dots 2-4-5", "○ ●\n● ●\n○ ○"),
}

# Valid characters allowed in Math Mode
MATH_VALID_CHARS = set("0123456789+-*/.=#")


def format_filipino_explanation(expr: str, solution: Any) -> Tuple[str, str]:
    """
    Format pedagogical Grade 1 Filipino math explanations:
    - Addition: "Ang sagot sa {a} dagdagan ng {b} ay {ans}. Pinagsama natin ang {a} at {b} kaya nakuha ang {ans}."
    - Subtraction: "Ang sagot sa {a} bawasan ng {b} ay {ans}. Binawas natin ang {b} mula sa {a} kaya natira ang {ans}."
    - Multiplication: "Ang sagot sa {a} ulit ng {b} ay {ans}. Ibig sabihin, pinarami natin ang {a} nang {b} beses, o {b} na pangkat ng {a}, kaya naging {ans}."
    - Division: "Ang sagot sa {a} hatiin sa {b} ay {ans}. Ibig sabihin, hinati natin nang pantay ang {a} sa {b} na bahagi, kaya bawat bahagi ay may {ans}."
      (with remainder: "... kaya bawat bahagi ay may {ans} at may natirang {r}.")
    """
    import re
    cleaned = expr.strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([\+\-\*\/×÷])\s*(\d+(?:\.\d+)?)$", cleaned)
    if not m or not solution.is_valid:
        return solution.explanation, solution.short_step

    a_raw, op, b_raw = m.groups()
    try:
        a_val = int(a_raw) if float(a_raw).is_integer() else float(a_raw)
        b_val = int(b_raw) if float(b_raw).is_integer() else float(b_raw)
    except Exception:
        return solution.explanation, solution.short_step

    ans_str = solution.result_str

    if op == "+":
        exp = f"Ang sagot sa {a_val} dagdagan ng {b_val} ay {ans_str}. Pinagsama natin ang {a_val} at {b_val} kaya nakuha ang {ans_str}."
        short = f"{a_val} + {b_val} = {ans_str}"
        return exp, short

    elif op in ("-", "–"):
        exp = f"Ang sagot sa {a_val} bawasan ng {b_val} ay {ans_str}. Binawas natin ang {b_val} mula sa {a_val} kaya natira ang {ans_str}."
        short = f"{a_val} - {b_val} = {ans_str}"
        return exp, short

    elif op in ("*", "×"):
        exp = f"Ang sagot sa {a_val} ulit ng {b_val} ay {ans_str}. Ibig sabihin, pinarami natin ang {a_val} nang {b_val} beses, o {b_val} na pangkat ng {a_val}, kaya naging {ans_str}."
        short = f"{a_val} x {b_val} = {ans_str}"
        return exp, short

    elif op in ("/", "÷"):
        if b_val == 0:
            return solution.explanation, solution.short_step
        if isinstance(a_val, int) and isinstance(b_val, int):
            quot = a_val // b_val
            rem = a_val % b_val
            if rem == 0:
                exp = f"Ang sagot sa {a_val} hatiin sa {b_val} ay {quot}. Ibig sabihin, hinati natin nang pantay ang {a_val} sa {b_val} na bahagi, kaya bawat bahagi ay may {quot}."
                short = f"{a_val} ÷ {b_val} = {quot}"
            else:
                exp = f"Ang sagot sa {a_val} hatiin sa {b_val} ay {quot}. Ibig sabihin, hinati natin nang pantay ang {a_val} sa {b_val} na bahagi, kaya bawat bahagi ay may {quot} at may natirang {rem}."
                short = f"{quot} R{rem}"
            return exp, short
        else:
            exp = f"Ang sagot sa {a_val} hatiin sa {b_val} ay {ans_str}. Ibig sabihin, hinati natin nang pantay ang {a_val} sa {b_val} na bahagi, kaya bawat bahagi ay may {ans_str}."
            short = f"{a_val} ÷ {b_val} = {ans_str}"
            return exp, short

    return solution.explanation, solution.short_step


class MathMode(BaseMode):
    """
    Dedicated Math Mode for Grade 1 DepEd arithmetic instruction.
    Supports '#' number sign to enter digits using keys A-J.
    """

    @property
    def filipino_engine(self):
        if not hasattr(self, "_filipino_engine") or self._filipino_engine is None:
            from brailce.speech.filipino_engine import FilipinoEngine
            self._filipino_engine = FilipinoEngine(fallback_speech=self.app.speech)
        return self._filipino_engine

    @property
    def mode_type(self) -> AppMode:
        return AppMode.MATH

    @property
    def display_name(self) -> str:
        return "Math Mode"

    def handle_event(self, event: InputEvent) -> None:
        """Process an input event in Math Mode."""
        if event.event_type == EventType.CHARACTER_PRESSED and event.character:
            self._handle_character(event.character)
        elif event.event_type == EventType.SPACE_PRESSED:
            self._handle_space()
        elif event.event_type == EventType.BACKSPACE_PRESSED:
            self._handle_backspace()
        elif event.event_type == EventType.CLEAR_PRESSED:
            self._handle_clear()
        elif event.event_type == EventType.ENTER_PRESSED:
            self._handle_enter()

    def _handle_character(self, character: str) -> None:
        """Handle numeric or operator key press."""
        # If student presses equals '=', trigger calculation
        if character == "=":
            self._handle_enter()
            return

        # 1. Number Indicator (#) in Math Mode
        lang = self.state.language if self.state else "en"
        from brailce.i18n.strings import get_string
        if character == "#":
            is_active = self.state.toggle_number_mode()
            dots_str = "Dots 3-4-5-6"
            unicode_char = "⠼"
            self.state.update_braille_info(dots_str, unicode_char)
            if self.app.display:
                self.app.display.show_character("# (Number Sign)")
                self.app.display.show_braille(unicode_char, dots_str, "○ ●\n○ ●\n● ●")
                self.app.display.show_status(get_string("NUMBER_MODE_ON", lang) if is_active else get_string("MATH_READY", lang))
            if self.app.speech:
                self.app.speech.speak(get_string("NUMBER_SIGN", lang))
            return

        # 2. Translate A-J to 1-0 when number mode is active
        if self.state.number_mode and character.upper() in BRAILLE_LETTER_TO_NUMBER:
            character = BRAILLE_LETTER_TO_NUMBER[character.upper()][0]

        if character not in MATH_VALID_CHARS:
            logger.warning("Character not permitted in Math Mode: %r", character)
            if self.app.display:
                self.app.display.show_status(get_string("MATH_ONLY_NUMBERS", lang))
            if self.app.speech:
                self.app.speech.speak(get_string("MATH_ERR_NUMBERS_ONLY", lang))
            return

        # 1. Look up Braille mapping
        mapping = None
        if self.app.encoder:
            mapping = self.app.encoder.encode(character)

        # 2. Update state
        self.state.append_math_character(character)
        if mapping:
            self.state.update_braille_info(mapping.dots_string(), mapping.braille_unicode)

        # 3. Update display
        if self.app.display:
            label = mapping.display_label if mapping else character
            self.app.display.show_character(label.lower())
            if mapping:
                self.app.display.show_braille(
                    mapping.braille_unicode,
                    mapping.dots_string(),
                    mapping.dots_visual()
                )
            self.app.display.show_text_buffer(self.state.math_buffer)

        # 4. Spoken audio feedback via unified speech facade
        if self.app.speech:
            self.app.speech.speak_character(character)

        logger.info("MathMode: CHAR=%s, BUFFER=%s", character, self.state.math_buffer)

    def _handle_space(self) -> None:
        """Handle space key press (adds visual separation between numbers)."""
        self.state.append_math_character(" ")
        if self.app.display:
            self.app.display.show_text_buffer(self.state.math_buffer)
        logger.info("MathMode: SPACE")

    def _handle_backspace(self) -> None:
        """Remove last character silently without clicks, beeps, or vocal announcements."""
        removed = self.state.backspace_math()
        lang = self.state.language if self.state else "en"
        from brailce.i18n.strings import get_string
        if self.app.display:
            if removed:
                self.app.display.show_character("Backspace")
                self.app.display.show_text_buffer(self.state.math_buffer)
            else:
                self.app.display.show_status(get_string("BUFFER_EMPTY", lang))

        if self.app.speech and self.app.config.get("speech", "speak_control_events", default=False):
            self.app.speech.speak(get_string("BUFFER_EMPTY", lang) if not removed else "backspace")

        logger.info("MathMode: BACKSPACE (removed: %r)", removed)

    def _handle_clear(self) -> None:
        """Clear the math buffer."""
        lang = self.state.language if self.state else "en"
        from brailce.i18n.strings import get_string
        self.state.clear_math_buffer()
        if self.app.display:
            self.app.display.show_character(get_string("CLEARED", lang))
            self.app.display.show_text_buffer("")
            self.app.display.show_status(get_string("MATH_CLEARED", lang))

        if self.app.speech and self.app.config.get("speech", "speak_control_events", default=False):
            self.app.speech.speak(get_string("CLEARED", lang).lower())

        logger.info("MathMode: CLEAR")

    def _handle_enter(self) -> None:
        """Calculate math problem and provide simple step-by-step counting explanation."""
        from brailce.i18n.strings import get_string

        lang = self.state.language
        expr = self.state.get_math_query()
        if not expr:
            status_msg = "Math buffer is empty" if lang == "en" else get_string("BUFFER_EMPTY", lang)
            empty_msg = get_string("MATH_EMPTY_BUFFER", lang)
            if self.app.display:
                self.app.display.show_status(status_msg)
            if self.app.speech:
                self.app.speech.speak(empty_msg)
            return

        # Solve deterministically using SafeArithmetic with language context
        solution = SafeArithmetic.solve(expr, language=lang)

        if solution.is_valid:
            explanation = solution.explanation
            short_step = solution.short_step

            self.state.math_last_result = solution.result_str
            self.state.math_last_explanation = explanation

            # Display solution card in appropriate language
            if self.app.display:
                self.app.display.show_math_result(
                    equation=solution.expression,
                    result=solution.result_str,
                    explanation=explanation,
                    short_step=short_step,
                    language=lang,
                )
                if self.app.encoder and solution.result_str:
                    mappings = self.app.encoder.encode_text(solution.result_str)
                    unicodes = "".join(m.braille_unicode for m in mappings if m)
                    dots = " ".join(m.dots_string() for m in mappings if m)
                    if unicodes:
                        self.app.display.show_braille(unicodes, dots, "")

            # Speak the full explanation through the unified SpeechService (same text as LCD)
            if self.app.speech:
                self.app.speech.speak(explanation)

            # Clear buffer so student can type the next problem immediately
            self.state.clear_math_buffer()
            logger.info("MathMode: SOLVED '%s' (%s) -> %s", solution.expression, lang, solution.result_str)

        else:
            err_msg = solution.error_message or get_string("MATH_ERR_MALFORMED", lang)
            if self.app.display:
                self.app.display.show_error(err_msg)
            if self.app.speech:
                if lang == "fil":
                    self.app.speech.speak_response_text(solution.explanation, interrupt=False, lang="fil")
                else:
                    self.app.speech.speak(solution.explanation)
            logger.warning("MathMode: SOLVE FAILED for '%s': %s", expr, solution.error_message)

    def on_enter(self) -> None:
        """Called when Math Mode becomes active."""
        lang = self.state.language if self.state else "en"
        from brailce.i18n.strings import get_string
        self.state.clear_math_buffer()
        if self.app.display:
            self.app.display.clear()
            self.app.display.show_mode(self.mode_type)
            self.app.display.show_status(get_string("MATH_READY", lang))
            self.app.display.show_text_buffer("")
        logger.info("Entered Math Mode.")

    def on_exit(self) -> None:
        """Called when exiting Math Mode."""
        self.state.clear_math_buffer()
        logger.info("Exited Math Mode.")
