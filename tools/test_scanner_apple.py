"""
Synthetic 5-cell "apple" Braille dot image test.
Verifies Grade 1 Braille "apple":
  a: dot 1 (row 0, col 0)
  p: dots 1, 2, 3, 4 (row 0, col 0; row 1, col 0; row 2, col 0; row 0, col 1)
  p: dots 1, 2, 3, 4
  l: dots 1, 2, 3 (row 0, col 0; row 1, col 0; row 2, col 0)
  e: dots 1, 5 (row 0, col 0; row 1, col 1)
Decodes strictly as "APPLE" with 5 cells (0 collapse into 3 cells).
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from brailce.vision.obr_engine import OBREngine


def create_synthetic_apple_image(
    w: int = 640,
    h: int = 480,
    dx: float = 20.0,
    dy: float = 20.0,
    dot_radius: float = 3.5,
    embossed: bool = True,
) -> np.ndarray:
    """Create a 640x480 test image containing standard Grade 1 Braille 'apple'."""
    p_cell = 2.4 * dx
    # Background
    bg_color = (210, 210, 210) if embossed else (255, 255, 255)
    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)

    # Center the 5 cells horizontally and vertically
    total_w = 4 * p_cell + dx
    x0 = (w - total_w) / 2.0
    y0 = (h - 2.0 * dy) / 2.0

    # 5 cells for "apple":
    # a: [1] -> (row 0, col 0)
    # p: [1,2,3,4] -> (0,0), (1,0), (2,0), (0,1)
    # p: [1,2,3,4] -> (0,0), (1,0), (2,0), (0,1)
    # l: [1,2,3] -> (0,0), (1,0), (2,0)
    # e: [1,5] -> (0,0), (1,1)
    cells_dots = [
        [(0, 0)],
        [(0, 0), (1, 0), (2, 0), (0, 1)],
        [(0, 0), (1, 0), (2, 0), (0, 1)],
        [(0, 0), (1, 0), (2, 0)],
        [(0, 0), (1, 1)],
    ]

    for k, c_dots in enumerate(cells_dots):
        cell_x = x0 + k * p_cell
        for row, col in c_dots:
            px = cell_x + col * dx
            py = y0 + row * dy
            r = dot_radius
            if embossed:
                # Embossed dots: highlight crescent (bright) + shadow crescent (dark)
                draw.ellipse([px - 2 - r, py - 2 - r, px - 2 + r, py - 2 + r], fill=(255, 255, 255))
                draw.ellipse([px + 2 - r, py + 2 - r, px + 2 + r, py + 2 + r], fill=(130, 130, 130))
            else:
                # Solid ink dot
                draw.ellipse([px - r, py - r, px + r, py + r], fill=(20, 20, 20))

    return np.array(img)


def test_apple_decoding():
    engine = OBREngine()

    # 1. Test solid ink dots
    img_ink = create_synthetic_apple_image(embossed=False)
    res_ink = engine.process_image(img_ink, debug_dump=True)
    print("\n--- TEST 1: Solid Ink Braille 'apple' ---")
    print(f"Readable: {res_ink.get('readable')}")
    print(f"Text: {res_ink.get('text')!r}")
    print(f"Cells: {len(res_ink.get('cells', []))}")
    assert res_ink.get("readable") is True, "Ink image should be readable"
    assert res_ink.get("text") == "APPLE", f"Expected 'APPLE', got {res_ink.get('text')!r}"
    assert len(res_ink.get("cells", [])) == 5, f"Expected 5 cells, got {len(res_ink.get('cells', []))}"
    print("PASS: Ink 'apple' correctly decoded as 5 cells 'APPLE'!")

    # 2. Test embossed dots (highlight + shadow pairs)
    img_emb = create_synthetic_apple_image(embossed=True)
    res_emb = engine.process_image(img_emb, debug_dump=True)
    print("\n--- TEST 2: Embossed Braille 'apple' ---")
    print(f"Readable: {res_emb.get('readable')}")
    print(f"Text: {res_emb.get('text')!r}")
    print(f"Cells: {len(res_emb.get('cells', []))}")
    assert res_emb.get("readable") is True, "Embossed image should be readable"
    assert res_emb.get("text") == "APPLE", f"Expected 'APPLE', got {res_emb.get('text')!r}"
    assert len(res_emb.get("cells", [])) == 5, f"Expected 5 cells, got {len(res_emb.get('cells', []))}"
    print("PASS: Embossed 'apple' correctly decoded as 5 cells 'APPLE'!")

    print("\n==========================================")
    print("ALL APPLE SCANNER TESTS PASSED SUCCESSFULLY!")
    print("==========================================")
    return True


if __name__ == "__main__":
    test_apple_decoding()
