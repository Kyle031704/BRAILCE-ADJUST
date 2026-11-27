"""
Coloured Braille regression tests
=================================
Red dots on a green board (like a 3D-printed alphabet plate) have almost the same
brightness, so a plain grayscale conversion makes the dots disappear. The OBR engine
must pick the colour axis instead, while plain white-paper pages stay on the
unchanged grayscale path.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

from brailce.vision.obr_engine import OBREngine
from tools.synth_braille import generate_synthetic_braille_image

# BGR colours chosen so their grayscale values are nearly equal (~102 vs ~104)
GREEN_BOARD = (110, 150, 20)
RED_DOT = (60, 50, 235)


def _recolor(img: np.ndarray, board=GREEN_BOARD, dot=RED_DOT) -> np.ndarray:
    """Turn a dark-ink-on-white synthetic page into coloured dots on a coloured board."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    ink = np.clip((255.0 - gray.astype(np.float32)) / 255.0, 0.0, 1.0)[..., None]
    out = np.array(board, np.float32) * (1.0 - ink) + np.array(dot, np.float32) * ink
    return np.clip(out, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def engine() -> OBREngine:
    return OBREngine()


@pytest.fixture(scope="module")
def colored_page():
    img, expected, _ = generate_synthetic_braille_image(
        "HELLO WORLD", dot_diameter=10.0, style="ink", rotation_deg=0.0,
        blur_sigma=0.4, noise_sigma=2.0, uneven_lighting=False,
    )
    ink_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return _recolor(img), expected, ink_gray


def test_red_and_green_look_alike_in_grayscale():
    board = cv2.cvtColor(np.uint8([[GREEN_BOARD]]), cv2.COLOR_BGR2GRAY)[0, 0]
    dot = cv2.cvtColor(np.uint8([[RED_DOT]]), cv2.COLOR_BGR2GRAY)[0, 0]
    assert abs(int(board) - int(dot)) < 12  # the reason colour handling is needed


def test_color_image_prefers_colour_for_red_on_green(engine, colored_page):
    img, _, _ = colored_page
    color_img, info = engine.color_contrast_image(img)
    assert color_img is not None
    assert info["prefer"] is True
    assert color_img.shape == img.shape


def test_color_dots_come_out_dark(engine, colored_page):
    img, _, ink_gray = colored_page
    color_img, _ = engine.color_contrast_image(img)
    gray = cv2.cvtColor(color_img, cv2.COLOR_BGR2GRAY)
    dot_mask = ink_gray < 80      # dot centres in the source ink page
    board_mask = ink_gray > 200
    assert dot_mask.any() and board_mask.any()
    assert gray[dot_mask].mean() + 80 < gray[board_mask].mean()


def test_scan_reads_red_on_green(engine, colored_page):
    img, expected, _ = colored_page
    res = engine.scan_frame(img)
    assert res.get("readable") is True, res.get("reason")
    assert " ".join(res["text"].split()) == " ".join(expected.split())
    assert res["color_channel"]["used"] is True


def test_color_path_can_be_disabled(engine, colored_page, monkeypatch):
    img, _, _ = colored_page
    monkeypatch.setenv("BRAILCE_COLOR_SCAN", "0")
    res = engine.scan_frame(img)
    assert "color_channel" not in res


@pytest.mark.parametrize("name", ["real_embossed_sidelit_1.jpg", "real_embossed_sidelit_2.jpg",
                                  "real_embossed_sidelit_3.jpg"])
def test_white_paper_stays_on_grayscale_path(engine, name):
    path = os.path.join(os.path.dirname(__file__), "data", name)
    img = cv2.imread(path)
    assert img is not None
    color_img, info = engine.color_contrast_image(img)
    assert color_img is None
    assert info["prefer"] is False


def test_grayscale_input_is_ignored(engine):
    gray = np.full((240, 320), 200, np.uint8)
    color_img, info = engine.color_contrast_image(gray)
    assert color_img is None and info["prefer"] is False
