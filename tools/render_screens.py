#!/usr/bin/env python3
"""
Brailce LCD Screen Layout Verification Tool
===========================================
Renders every operating-mode screen of the Brailce Grade 1 learning device
(Free Input, Math, AI Assistant, Scanner) to PNG screenshots at the exact
480x320 resolution of the ILI9488 TFT display.

Usage:
    python tools/render_screens.py            # writes lcd_test_*.png in the repo root
    python tools/render_screens.py --all OUT  # writes every screen variant into OUT/
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from brailce.display.ili9488_display import AppMode, ILI9488Display


def _display(language: str = "en") -> ILI9488Display:
    display = ILI9488Display()
    display.initialize()
    display.set_language(language)
    display.show_battery(89, True)
    return display


def render_all_modes(out_dir: str = ".", every: bool = False, language: str = "en") -> None:
    def save(display: ILI9488Display, name: str) -> None:
        path = os.path.join(out_dir, name)
        display.save_screenshot(path)
        print("Saved", path)

    d = _display(language)

    # 1. Free Input Mode
    d.show_mode(AppMode.FREE_INPUT)
    if every:
        save(d, "free_input_empty.png")
    d.show_character("a")
    d.show_braille("⠁", "Dot 1", "")
    d.show_text_buffer("HELLO WORLD A")
    save(d, "lcd_test_free_input.png")
    if every:
        d.show_character("5 (from e)")
        d.show_braille("⠑", "Dots 1-5", "")
        save(d, "free_input_number.png")
        d.show_character("Space")
        d.show_braille(" ", "No dots", "")
        save(d, "free_input_space.png")

    # 2. Math Mode
    d.show_mode(AppMode.MATH)
    if every:
        save(d, "math_welcome.png")
        d.show_character("3")
        d.show_braille("⠒", "Dots 2-5", "")
        d.show_text_buffer("11+3")
        save(d, "math_typing.png")
    d.show_math_result(
        equation="11 + 3",
        result="14",
        explanation="11 plus 3 equals 14. Let's count together. Start at 11, and count up 3 more: "
                    "12, 13, 14. The answer is 14.",
        short_step="Start 11 -> 12, 13, 14",
        language=language,
    )
    d.show_braille("⠂⠲", "Dot 2 Dots 2-5-6", "")
    save(d, "lcd_test_math_mode.png")

    # 3. AI Assistant Mode
    d.show_mode(AppMode.AI_ASSISTANT)
    if every:
        save(d, "ai_empty.png")
        d.show_ai_query_input("WHAT IS RAIN?")
        save(d, "ai_typing.png")
        d.show_ai_processing("WHAT IS RAIN?")
        save(d, "ai_thinking.png")
    d.show_ai_response(
        query="What is rain?",
        response="Rain is water falling in drops from clouds in the sky. Clouds collect tiny drops "
                 "of water until they become heavy, and then the water falls down as rain.",
    )
    save(d, "lcd_test_ai_mode.png")

    # 4. Scanner Mode
    d.show_mode(AppMode.SCANNER)
    save(d, "lcd_test_scanner_mode.png")
    if every:
        frame = np.full((480, 640, 3), 200, dtype=np.uint8)
        frame[::40, :, :] = 170
        d.show_camera_preview(frame, status="Braille detected. Press Enter to read.",
                              overlays=[{"char": "A", "bbox": [120, 210, 150, 260], "active": True},
                                        {"char": "B", "bbox": [170, 210, 200, 260]}],
                              distance_state="ok")
        save(d, "scanner_camera.png")
        d.show_mode(AppMode.SCANNER)
        d.show_character("b")
        d.show_braille("⠃", "Dots 1-2", "")
        d.show_text_buffer("BAHAY")
        save(d, "scanner_result.png")
        d.show_error("Camera not found")
        save(d, "error_bar.png")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--all":
        out = sys.argv[2]
        os.makedirs(out, exist_ok=True)
        lang = sys.argv[3] if len(sys.argv) >= 4 else "en"
        render_all_modes(out, every=True, language=lang)
    else:
        render_all_modes()
