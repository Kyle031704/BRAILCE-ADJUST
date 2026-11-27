#!/usr/bin/env python3
"""
Printable Braille test sheets for the Scanner (colour + black/white)
===================================================================
Writes A4 pages at 300 dpi (PNG per page + one combined PDF):

  page 1  colour, large cells (like a 3D-printed alphabet plate)
  page 2  colour, standard cells (Marburg Medium spacing)
  page 3  black on white, large cells   (same text as page 1, for comparison)
  page 4  black on white, standard cells (same text as page 2)

Colour pages use red dots on a green background whose grayscale brightness is
almost the same, which is exactly the case plain grayscale scanning could not read.
No print letters sit near the dots; the answer key is in the bottom margin.

Usage:
    python tools/make_color_test_sheet.py [OUT_DIR]     (default: tools/test_sheets)
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

DPI = 300
MM = DPI / 25.4
A4 = (int(round(210 * MM)), int(round(297 * MM)))

# RGB. Grayscale (0.299R + 0.587G + 0.114B): green ~104, red ~102.
GREEN = (20, 150, 110)
RED = (235, 50, 60)
WHITE = (255, 255, 255)
BLACK = (20, 20, 20)

# Grade 1 Braille (dots 1..6)
LETTER_DOTS = {
    "a": "1", "b": "12", "c": "14", "d": "145", "e": "15", "f": "124", "g": "1245",
    "h": "125", "i": "24", "j": "245", "k": "13", "l": "123", "m": "134", "n": "1345",
    "o": "135", "p": "1234", "q": "12345", "r": "1235", "s": "234", "t": "2345",
    "u": "136", "v": "1236", "w": "2456", "x": "1346", "y": "13456", "z": "1356",
}

# (dot diameter, dot pitch, cell pitch, line pitch) in mm
# Line pitch is wider than in books so the camera's center box catches one line only.
STANDARD = (1.5, 2.5, 6.2, 30.0)
LARGE = (2.6, 4.2, 10.5, 40.0)

LINES_LARGE = ["hello world", "braille is fun", "ang bata ay", "masaya ako", "salamat po"]
LINES_STANDARD = [
    "hello world braille",
    "ang bata ay masaya",
    "mahal ko ang pilipinas",
    "the quick brown fox",
    "jumps over the lazy dog",
    "salamat po sa inyo",
]


def _font(size_px: int) -> ImageFont.ImageFont:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "brailce", "display", "fonts", "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(path, size_px)
    except Exception:
        return ImageFont.load_default()


def render_page(lines: List[str], spec: Tuple[float, float, float, float],
                bg: Tuple[int, int, int], dot: Tuple[int, int, int], title: str) -> Image.Image:
    d_mm, p_mm, cell_mm, line_mm = spec
    img = Image.new("RGB", A4, WHITE)
    draw = ImageDraw.Draw(img)
    margin = int(15 * MM)

    # Coloured board sized to the content (white margin around it, like a printed card)
    top = int(28 * MM)
    height = int((18 + line_mm * (len(lines) - 1) + 2 * p_mm + 18) * MM)
    board = (margin, top, A4[0] - margin, min(A4[1] - int(40 * MM), top + height))
    draw.rectangle(board, fill=bg)

    # Header and answer key stay far away from the dots
    text_col = (90, 90, 90)
    draw.text((margin, int(10 * MM)), title, font=_font(int(4.2 * MM)), fill=text_col)
    draw.text((margin, int(17 * MM)),
              "Aim the camera so ONE line of dots fills the center box. Press ENTER to scan.",
              font=_font(int(3.0 * MM)), fill=text_col)
    key_y = A4[1] - int(34 * MM)
    draw.text((margin, key_y), "Answer key:", font=_font(int(3.0 * MM)), fill=text_col)
    for i, line in enumerate(lines):
        draw.text((margin + int(4 * MM), key_y + int((5 + 4.2 * i) * MM)), f"{i + 1}. {line}",
                  font=_font(int(3.0 * MM)), fill=text_col)

    r = d_mm * MM / 2
    x_start = board[0] + int(12 * MM)
    y = board[1] + int(18 * MM)
    max_x = board[2] - int(10 * MM)
    for line in lines:
        x = x_start
        for ch in line:
            if ch != " ":
                for n in LETTER_DOTS[ch]:
                    k = int(n)
                    col = 0 if k <= 3 else 1
                    row = (k - 1) % 3
                    cx = x + col * p_mm * MM
                    cy = y + row * p_mm * MM
                    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot)
            x += cell_mm * MM
        if x > max_x:
            raise ValueError(f"Line too long for the page: {line!r}")
        if y + 2 * p_mm * MM > board[3] - int(8 * MM):
            raise ValueError("Too many lines for the page")
        y += line_mm * MM
    return img


def main(out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    pages = [
        ("1_color_large.png", render_page(LINES_LARGE, LARGE, GREEN, RED,
                                          "Brailce test 1: COLOUR, large dots (red on green)")),
        ("2_color_standard.png", render_page(LINES_STANDARD, STANDARD, GREEN, RED,
                                             "Brailce test 2: COLOUR, standard dots (red on green)")),
        ("3_bw_large.png", render_page(LINES_LARGE, LARGE, WHITE, BLACK,
                                       "Brailce test 3: BLACK/WHITE, large dots (compare with 1)")),
        ("4_bw_standard.png", render_page(LINES_STANDARD, STANDARD, WHITE, BLACK,
                                          "Brailce test 4: BLACK/WHITE, standard dots (compare with 2)")),
    ]
    paths = []
    for name, img in pages:
        p = os.path.join(out_dir, name)
        img.save(p, dpi=(DPI, DPI))
        paths.append(p)
    pdf = os.path.join(out_dir, "brailce_scanner_test_sheets.pdf")
    first, rest = pages[0][1], [im for _, im in pages[1:]]
    first.save(pdf, save_all=True, append_images=rest, resolution=DPI)
    paths.append(pdf)
    return paths


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "test_sheets")
    for p in main(target):
        print("Saved", p)
