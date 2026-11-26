"""
Regression tests for "scanner cannot read Braille (ink or embossed)".

Unlike test_obr_synthetic.py (which calls process_image() with defaults), these go
through OBREngine.scan_frame(): the exact path ScannerMode uses on the device, on
1280x720 camera-sized frames.

Root causes covered:
  1. ScannerMode forced embossed_only=True, which erases solid ink dots -> ink never read.
  2. detect_dots() size limit was relative to crop HEIGHT; on the slim center-strip ROI
     (~158px tall) every dot wider than ~13-19px was discarded -> close-up scans failed.
  3. Cell-pitch estimation took a median over wrong pair-distance divisors and capped
     at 3.15x dot pitch -> lattice drift / wide-spaced printed charts misread.
  4. Two-row lines (a-j) were fitted as rows 2-3 -> letters decoded as punctuation.
  5. Presence gate checked only a 640px downsample -> small dots rejected outright.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

from brailce.vision.obr_engine import OBREngine
from tools.synth_braille import generate_synthetic_braille_image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def engine():
    return OBREngine()


@pytest.fixture(autouse=True)
def _isolate_debug_dir(tmp_path, monkeypatch):
    # process_image() writes debug_scan/ into the CWD
    monkeypatch.chdir(tmp_path)


def _camera_frame(text: str, d: float, style: str, rot: float = 0.0) -> np.ndarray:
    """Render Braille on paper that fills a 1280x720 frame (no pasted page edges)."""
    page, _exp, _ = generate_synthetic_braille_image(
        text, dot_diameter=d, style=style, rotation_deg=rot,
        blur_sigma=0.8, noise_sigma=3.0, uneven_lighting=True, seed=7,
        margin_x=200, margin_y=330,
    )
    H, W = 720, 1280
    ph, pw = page.shape[:2]
    if pw < W:
        page = cv2.copyMakeBorder(page, 0, 0, (W - pw) // 2, W - pw - (W - pw) // 2, cv2.BORDER_REPLICATE)
        ph, pw = page.shape[:2]
    oy, ox = (ph - H) // 2, (pw - W) // 2
    return np.ascontiguousarray(page[oy:oy + H, ox:ox + W])


def _norm(t: str) -> str:
    return "".join(str(t).split()).upper()


@pytest.mark.parametrize("style,d,rot,text", [
    ("ink", 6.0, 0.0, "CAT DOG"),
    ("ink", 11.0, 3.0, "HELLO"),
    ("ink", 15.0, 0.0, "MAGANDA"),
    ("embossed", 6.0, 0.0, "HELLO"),
    ("embossed", 11.0, -4.0, "BRAILLE"),
    ("embossed", 15.0, 0.0, "CAT DOG"),
])
def test_scan_frame_reads_ink_and_embossed(engine, style, d, rot, text):
    frame = _camera_frame(text, d, style, rot)
    res = engine.scan_frame(frame)
    assert res.get("readable"), f"{style} d={d} rot={rot}: reason={res.get('reason')}"
    assert _norm(res["text"]) == _norm(text), f"{style} d={d}: got {res['text']!r}"


def test_center_strip_keeps_large_close_up_dots(engine):
    """Dots ~22px wide must survive detect_dots() on a slim 1000x158 ROI crop."""
    frame = _camera_frame("CAT", 11.0, "ink")
    h, w = frame.shape[:2]
    roi = frame[(h - 158) // 2:(h + 158) // 2, (w - 998) // 2:(w + 998) // 2]
    dots, diam = engine.detect_dots(roi)
    # CAT = C(2) + A(1) + T(4) = 7 dots; the old axis-relative size limit returned 0 here
    assert len(dots) >= 6 and diam > 13.0


def test_lattice_fit_handles_wide_cell_spacing():
    # printed chart: dot pitch 34, cell pitch 113.6 (3.34x), letters a-j columns
    xs = [113, 226, 340, 374, 453, 488, 567, 601, 680, 715, 794, 828, 907, 942, 1021, 1055, 1134, 1168]
    x0, pc, pcol = OBREngine._fit_cell_lattice([float(x) for x in xs], [1] * len(xs), 34.0, [])
    assert abs(pc - 113.5) < 2.0
    assert abs(pcol - 34.0) < 3.0
    assert abs(((113 - x0) / pc) - round((113 - x0) / pc)) < 0.05  # 113 is a left column


@pytest.mark.skipif(not os.path.isfile(os.path.join(ROOT, "123.webp")), reason="sample chart missing")
@pytest.mark.parametrize("box,expected", [
    ((30, 30, 1000, 170), "ABCDEFGHIJ"),
    ((30, 275, 1000, 415), "KLMNOPQRST"),
    ((30, 518, 620, 660), "UVWXYZ"),
])
def test_real_printed_ink_chart(engine, box, expected):
    img = cv2.imread(os.path.join(ROOT, "123.webp"))
    x0, y0, x1, y1 = box
    crop = img[y0:y1, x0:x1]
    s = min(1280 * 0.9 / crop.shape[1], 720 * 0.9 / crop.shape[0])
    crop = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)), interpolation=cv2.INTER_AREA)
    frame = np.full((720, 1280, 3), int(np.median(img)), np.uint8)
    oy, ox = (720 - crop.shape[0]) // 2, (1280 - crop.shape[1]) // 2
    frame[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    frame = cv2.GaussianBlur(frame, (3, 3), 0)
    res = engine.scan_frame(frame)
    assert _norm(res.get("text", "")) == expected


@pytest.mark.skipif(not os.path.isfile(os.path.join(ROOT, "123.webp")), reason="sample chart missing")
def test_real_printed_ink_chart_whole_page(engine, monkeypatch):
    """Whole chart (all-lines mode): crisp boxes/labels must not trip the noise gate; label rows must be ignored."""
    monkeypatch.setenv("BRAILCE_SCAN_ALL_LINES", "1")
    img = cv2.imread(os.path.join(ROOT, "123.webp"))
    res = engine.scan_frame(img)
    assert res.get("text", "").split() == ["ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ"]


@pytest.mark.skipif(not os.path.isfile(os.path.join(ROOT, "123.webp")), reason="sample chart missing")
def test_scan_reads_only_center_line(engine, monkeypatch):
    """Default scanner reads only the line in the center box, never the lines above/below it."""
    monkeypatch.delenv("BRAILCE_SCAN_ALL_LINES", raising=False)
    img = cv2.imread(os.path.join(ROOT, "123.webp"))
    res = engine.scan_frame(img)
    assert res.get("text", "").split() == ["KLMNOPQRST"]
    assert {c.get("line_index") for c in res.get("cells", [])} == {res["cells"][0]["line_index"]}
    assert all(b.get("label") in "KLMNOPQRST" for b in res.get("boxes", []))


# Real webcam frames from the device (EMEET C60E, side-lit embossed sheet with handwritten
# labels under each word). Each dot shows as a bright cap + long shadow; the classic DoG
# detector drowned in paper texture here ("too_small", 289 fake dots).
_REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _aim(frame: np.ndarray, line_center_y: int) -> np.ndarray:
    """Shift the frame so the given Braille line sits in the center box, like aiming the camera."""
    m = np.float32([[1, 0, 0], [0, 1, 360 - line_center_y]])
    return cv2.warpAffine(frame, m, (1280, 720), borderMode=cv2.BORDER_REPLICATE)


@pytest.mark.parametrize("fname,line_y,expected", [
    ("real_embossed_sidelit_1.jpg", 134, "PEN"),
    ("real_embossed_sidelit_1.jpg", 355, "QUIZ"),
    ("real_embossed_sidelit_1.jpg", 592, "ROAD"),
    ("real_embossed_sidelit_2.jpg", 175, "NEST"),
    ("real_embossed_sidelit_2.jpg", 540, "PEN"),
    # Phone/webcam photo of the same hand-embossed sheet (real_scan_1.jpg). Reading "open"
    # inside the center box used to give "OLEN": the strip-sized background kernel swallowed
    # dot 4 of the P. Detection now runs on the full frame, so the box and the page agree.
    ("real_embossed_sidelit_3.jpg", 180, "NEST"),
    ("real_embossed_sidelit_3.jpg", 365, "OPEN"),
    ("real_embossed_sidelit_3.jpg", 542, "PEN"),
])
def test_real_sidelit_embossed_lines(engine, fname, line_y, expected):
    frame = cv2.imread(os.path.join(_REAL, fname))
    assert frame is not None
    res = engine.scan_frame(_aim(frame, line_y))
    assert res.get("text") == expected, f"got {res.get('text')!r} via {res.get('strategy')}"


def test_shadow_detector_ignores_paper_texture(engine):
    frame = cv2.imread(os.path.join(_REAL, "real_embossed_sidelit_1.jpg"))
    dots, diam = engine.detect_shadow_dots(frame)
    # 3 visible words x ~10-15 dots; the classic detector found 289 texture specks here
    assert 25 <= len(dots) <= 45 and diam > 6.0
