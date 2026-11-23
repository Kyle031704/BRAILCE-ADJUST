"""
Synthetic Embossed 'tree' Braille Test
======================================
Tests OBREngine against a handheld camera capture frame containing:
  1. Embossed Grade 1 Braille "tree" punched with stylus & slate:
     - t: dots 2, 3, 4, 5
     - r: dots 1, 2, 3, 5
     - e: dots 1, 5
     - e: dots 1, 5
  2. Severe dark corner shadow (uneven lighting gradient).
  3. Dark printed/handwritten ink text ("tree") written directly below the Braille line.
  4. Centered slim single-line horizontal ROI window (78% width, 22% height).

Verifies that:
  - Corner shadows are flattened by local background subtraction.
  - Ink writing below the Braille line is excluded by the slim ROI and/or masked out.
  - Embossed dots with highlight+shadow dipoles are paired to single centroids without duplicate 'AA' or '???'.
  - Result decodes strictly as "TREE".
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
import numpy as np
import cv2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from brailce.vision.obr_engine import OBREngine, ROI_W_FRAC, ROI_H_FRAC


def create_embossed_tree_image(
    w: int = 640,
    h: int = 480,
    dx: float = 21.0,
    dy: float = 21.0,
    dot_radius: float = 4.0,
) -> np.ndarray:
    """
    Generate physically accurate 640x480 frame with:
      - Embossed Braille 'tree' centered inside the slim horizontal ROI
      - Dark corner shadow
      - Dark ink handwriting 'tree' below the Braille line
    """
    # 1. Base paper (off-white, 220)
    paper = np.full((h, w, 3), 220, dtype=np.float32)

    # 2. Add large corner shadow (dark vignette in top-left and top-right corners)
    # Lighting drops to ~80 in the corners
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # Top-left corner shadow
    dist_tl = np.hypot(xx, yy)
    shadow_tl = np.clip(1.0 - (dist_tl / (w * 0.70)), 0.0, 1.0) ** 1.5 * 130.0
    # Bottom-left corner shadow
    dist_bl = np.hypot(xx, yy - h)
    shadow_bl = np.clip(1.0 - (dist_bl / (w * 0.60)), 0.0, 1.0) ** 1.5 * 90.0
    paper = np.clip(paper - shadow_tl[:, :, None] - shadow_bl[:, :, None], 30.0, 255.0).astype(np.uint8)

    # 3. Braille cells for "tree":
    # t: dots 2, 3, 4, 5 -> col 0: row 1, 2; col 1: row 0, 1
    # r: dots 1, 2, 3, 5 -> col 0: row 0, 1, 2; col 1: row 1
    # e: dots 1, 5       -> col 0: row 0; col 1: row 1
    # e: dots 1, 5       -> col 0: row 0; col 1: row 1
    cells_dots = [
        [(1, 0), (2, 0), (0, 1), (1, 1)],  # 't'
        [(0, 0), (1, 0), (2, 0), (1, 1)],  # 'r'
        [(0, 0), (1, 1)],                  # 'e'
        [(0, 0), (1, 1)],                  # 'e'
    ]

    p_cell = 2.45 * dx
    total_w = 3 * p_cell + dx
    center_x = w / 2.0
    center_y = h / 2.0

    x0 = center_x - (total_w / 2.0)
    y0 = center_y - dy  # row 1 is at center_y

    # Collect dot center coordinates with slight stylus-and-slate spacing jitter (±0.8px)
    rng = np.random.default_rng(123)
    dots_list = []
    for k, c_dots in enumerate(cells_dots):
        cell_x = x0 + k * p_cell
        for row, col in c_dots:
            jx = rng.uniform(-0.8, 0.8)
            jy = rng.uniform(-0.8, 0.8)
            cx = cell_x + col * dx + jx
            cy = y0 + row * dy + jy
            dots_list.append((cx, cy))

    # Render embossed dots as 3D spherical domes lit from top-left (-135 deg)
    canvas = paper.astype(np.float32)
    radius = dot_radius
    light_dx = -0.55
    light_dy = -0.55

    for cx, cy in dots_list:
        min_x = max(0, int(math.floor(cx - radius * 1.5)))
        max_x = min(w, int(math.ceil(cx + radius * 1.5)))
        min_y = max(0, int(math.floor(cy - radius * 1.5)))
        max_y = min(h, int(math.ceil(cy + radius * 1.5)))

        sub_yy, sub_xx = np.mgrid[min_y:max_y, min_x:max_x].astype(np.float32)
        sub_dx = sub_xx - cx
        sub_dy = sub_yy - cy
        dist2 = sub_dx ** 2 + sub_dy ** 2
        in_dome = dist2 <= (radius * 1.30) ** 2
        if not np.any(in_dome):
            continue

        r_sub = np.sqrt(dist2)
        dome_mask = np.clip(1.0 - (r_sub - radius), 0.0, 1.0)
        shading = (sub_dx * (-light_dx) + sub_dy * (-light_dy)) / max(1.0, radius)
        effect = -shading * 48.0 * dome_mask
        canvas[min_y:max_y, min_x:max_x][in_dome] = np.clip(
            canvas[min_y:max_y, min_x:max_x][in_dome] + effect[in_dome, None], 0.0, 255.0
        )

    paper = canvas.astype(np.uint8)

    # 4. Add dark handwritten ink text "tree" below the Braille line (outside the slim ROI)
    # The slim ROI is 22% height (from y = 187 to y = 293).
    # Ink text is placed around y = 330 to y = 390.
    ink_color = (25, 25, 25)  # dark black pen/marker
    text_y = int(center_y + (h * ROI_H_FRAC / 2.0) + 40)
    text_x = int(center_x - 90)

    # Draw dark strokes for "tree" using cv2.putText with heavy thickness
    cv2.putText(
        paper,
        "tree",
        (text_x, text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.6,
        ink_color,
        4,
        cv2.LINE_AA,
    )
    # Add a horizontal dark underline stroke
    cv2.line(paper, (text_x - 10, text_y + 15), (text_x + 180, text_y + 15), ink_color, 3)

    return paper


def test_tree_decoding() -> bool:
    engine = OBREngine()
    img = create_embossed_tree_image()

    print("==================================================")
    print("TEST: Stylus-and-slate Embossed 'tree' Recognition")
    print("==================================================")

    # Test 1: Slim center ROI decode (primary handheld scan path)
    res_roi = engine.process_image(img, full_frame=False, debug_dump=True)
    print(f"\n[ROI Single-Line Scan]")
    print(f"Readable: {res_roi.get('readable')}")
    print(f"Reason:   {res_roi.get('reason')}")
    print(f"Text:     {res_roi.get('text')!r}")
    print(f"Cells:    {len(res_roi.get('cells', []))}")

    assert res_roi.get("readable") is True, f"ROI scan failed: reason={res_roi.get('reason')}"
    assert res_roi.get("text", "").upper() == "TREE", f"Expected 'TREE', got {res_roi.get('text')!r}"
    assert len(res_roi.get("cells", [])) == 4, f"Expected 4 cells, got {len(res_roi.get('cells', []))}"
    print("--> PASS: Slim ROI correctly isolated embossed Braille and decoded 'TREE'!")

    # Test 2: Full-frame with ink suppression & corner shadow rejection
    res_full = engine.process_image(img, full_frame=True, debug_dump=True)
    print(f"\n[Full-Frame Scan with Shadow & Ink Rejection]")
    print(f"Readable: {res_full.get('readable')}")
    print(f"Reason:   {res_full.get('reason')}")
    print(f"Text:     {res_full.get('text')!r}")
    print(f"Cells:    {len(res_full.get('cells', []))}")

    assert res_full.get("readable") is True, f"Full-frame scan failed: reason={res_full.get('reason')}"
    assert res_full.get("text", "").upper() == "TREE", f"Expected 'TREE', got {res_full.get('text')!r}"
    print("--> PASS: Ink rejection suppressed handwriting 'tree' and decoded 'TREE'!")

    print("\n==================================================")
    print("ALL TREE EMBOSSED BRAILLE TESTS PASSED (100% OK)!")
    print("==================================================")
    return True


if __name__ == "__main__":
    test_tree_decoding()
