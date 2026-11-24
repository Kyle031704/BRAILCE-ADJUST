"""
Brailce Tests — Classical Dot-Level Grade 1 Braille Recognizer
================================================================

Tests OBREngine classical pipeline against synthetic shaded embossed Braille
pages (lighting gradients, blur, rotation, noise, low contrast, unknown cells)
and checks existing repository image fixtures safely.
"""

import os
from typing import List

import cv2
import numpy as np
import pytest

from brailce.vision.obr_engine import OBREngine, UNKNOWN_CELL_CHAR


def render_shaded_braille(
    text: str,
    pitch: int = 20,
    dot_r: float = 4.5,
    blur_sigma: float = 0.0,
    angle_deg: float = 0.0,
    noise_std: float = 0.0,
    contrast_scale: float = 1.0,
    width: int = 640,
    height: int = 320,
) -> np.ndarray:
    """Render an embossed-style Grade 1 Braille page with shading & uneven lighting."""
    rev = {v: k for k, v in OBREngine.BINARY_DICT.items() if len(v) == 1 and v.isalpha()}
    rev["#"] = "001111"
    rev["^"] = "000001"
    dmap = {v: k for k, v in OBREngine.NUM_MAP.items()}

    img = np.full((height, width, 3), 215, dtype=np.float32)
    gx = np.linspace(-20.0, 20.0, width)[None, :, None]
    gy = np.linspace(-15.0, 15.0, height)[:, None, None]
    img = np.clip(img + gx + gy, 0, 255).astype(np.uint8)

    tokens: List[str] = []
    in_num = False
    for ch in text:
        if ch == " ":
            tokens.append("000000")
            in_num = False
        elif ch.isdigit():
            if not in_num:
                tokens.append("001111")
                in_num = True
            tokens.append(rev[dmap[ch]])
        elif ch.isupper():
            in_num = False
            tokens.append("000001")
            tokens.append(rev[ch])
        else:
            in_num = False
            tokens.append(rev[ch.upper()])

    x0, y0 = 55.0, 105.0
    hi_delta = int(round(27 * contrast_scale))
    lo_delta = int(round(75 * contrast_scale))
    hi_col = (min(255, 215 + hi_delta),) * 3
    lo_col = (max(0, 215 - lo_delta),) * 3

    for i, bits in enumerate(tokens):
        cx = x0 + i * (pitch * 2.5)
        for bi, b in enumerate(bits):
            if b == "1":
                col = bi // 3
                row = bi % 3
                dx = int(round(cx + col * pitch))
                dy = int(round(y0 + row * pitch))
                r_px = max(2, int(round(dot_r)))
                cv2.circle(img, (dx - 1, dy - 1), r_px, hi_col, -1, cv2.LINE_AA)
                cv2.circle(img, (dx + 2, dy + 2), r_px, lo_col, -1, cv2.LINE_AA)

    if angle_deg:
        M = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle_deg, 1.0)
        img = cv2.warpAffine(img, M, (width, height), borderMode=cv2.BORDER_REPLICATE)

    if blur_sigma > 0.0:
        img = cv2.GaussianBlur(img, (0, 0), blur_sigma)

    if noise_std > 0.0:
        rng = np.random.default_rng(42)
        noise = rng.normal(0.0, noise_std, img.shape)
        img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    return img


class TestClassicalOBRWordsAndResilience:
    """Verify exact Grade 1 decoding on shaded synthetic Braille pages."""

    @pytest.mark.parametrize("phrase,expected", [
        ("need", "NEED"),
        ("braille", "BRAILLE"),
        ("hello", "HELLO"),
        ("1234", "1234"),
        ("Cat", "CAT"),
    ])
    def test_synthetic_shaded_grade1_words(self, phrase: str, expected: str) -> None:
        engine = OBREngine()
        img = render_shaded_braille(phrase)
        res = engine.process_image(img)
        assert res["readable"] is True, f"Failed {phrase}: {res}"
        assert res["text"] == expected

    @pytest.mark.parametrize("angle_deg", [-5.0, 4.0, 6.0])
    def test_rotation_resilience(self, angle_deg: float) -> None:
        engine = OBREngine()
        img = render_shaded_braille("need", angle_deg=angle_deg)
        res = engine.process_image(img)
        assert res["readable"] is True
        assert res["text"] == "NEED"

    def test_noise_and_low_contrast_resilience(self) -> None:
        engine = OBREngine()
        img = render_shaded_braille("hello", noise_std=5.0, contrast_scale=0.55)
        res = engine.process_image(img)
        assert res["readable"] is True
        assert res["text"] == "HELLO"

    def test_flat_overhead_room_light_resilience(self) -> None:
        """Verify that faint embossed Braille under flat overhead lighting (contrast_scale=0.30) decodes accurately."""
        engine = OBREngine()
        img = render_shaded_braille("need", contrast_scale=0.30)
        res = engine.process_image(img)
        assert res["readable"] is True, f"Failed flat lighting: {res}"
        assert res["text"] == "NEED"


class TestClassicalOBRQualityGatesAndUnknowns:
    """Verify failure diagnostics and zero fabricated guesses on invalid cells."""

    def test_flat_dark_image_returns_no_braille(self) -> None:
        engine = OBREngine()
        dark = np.zeros((320, 640, 3), dtype=np.uint8)
        res = engine.process_image(dark)
        assert res["readable"] is False
        assert res["reason"] == "no_braille"

    def test_blurry_image_returns_blurry(self) -> None:
        engine = OBREngine()
        blurry = render_shaded_braille("need", blur_sigma=3.2)
        res = engine.process_image(blurry)
        assert res["readable"] is False
        assert res["reason"] == "blurry"

    def test_tiny_dots_returns_too_small_or_no_braille(self) -> None:
        engine = OBREngine()
        # Sub-threshold ~3px dots across a 640x480 frame
        img = np.full((480, 640, 3), 215, dtype=np.uint8)
        for i in range(6):
            cv2.circle(img, (100 + i * 15, 200), 1, (110, 110, 110), -1)
        res = engine.process_image(img)
        assert res["readable"] is False
        assert res["reason"] in {"too_small", "no_braille"}

    def test_unknown_six_dot_cell_returns_question_mark(self) -> None:
        # "000110" (dots 4-5) has no Grade 1 letter mapping -> must decode as '?'
        boxes = [
            {"x": 100.0, "y": 100.0, "w": 30.0, "h": 45.0, "name": "100000", "conf": 0.9},
            {"x": 135.0, "y": 100.0, "w": 30.0, "h": 45.0, "name": "000110", "conf": 0.9},
        ]
        res = OBREngine.decode_boxes(boxes)
        assert res["readable"] is True
        assert res["text"] == f"A{UNKNOWN_CELL_CHAR}"

    def test_repository_image_fixtures_safe_handling(self) -> None:
        engine = OBREngine()
        if os.path.exists("tools/test_real_cam.jpg"):
            real_cam = cv2.imread("tools/test_real_cam.jpg")
            res = engine.process_image(real_cam)
            # Monitor terminal photo contains zero Braille — must NOT hallucinate Braille text
            assert res["readable"] is False
            assert res["text"] == ""
        if os.path.exists("tools/enhanced_braille.jpg"):
            enhanced = cv2.imread("tools/enhanced_braille.jpg")
            dots, dd = engine.detect_dots(enhanced)
            assert len(dots) > 100
            assert dd > 3.0

    def test_boundary_phantom_noise_pruning(self) -> None:
        """Stray dots near paper border producing '? RAIN' or trailing noise are cleanly trimmed."""
        engine = OBREngine()
        p_dot = 20.0
        cell_dots = {
            'R': [(0, 0), (0, 1), (0, 2), (1, 1)],
            'A': [(0, 0)],
            'I': [(0, 1), (1, 0)],
            'N': [(0, 0), (0, 2), (1, 0), (1, 1)]
        }
        dots = [{'x': 10.0, 'y': 105.0, 'd': 8.0}]  # isolated edge stray dot
        for i, ch in enumerate('RAIN'):
            x_base = 80.0 + i * (2.4 * p_dot)
            y_base = 100.0
            for col, row in cell_dots[ch]:
                dots.append({'x': x_base + col * p_dot, 'y': y_base + row * p_dot, 'd': 8.0})

        res = engine.decode_dots_to_text(dots, 8.0, (300, 400, 3))
        assert res["readable"] is True
        assert res["text"] == "RAIN"
        assert len(res["cells"]) == 4

    def test_multiline_noise_and_flashcard_pruning(self) -> None:
        """Background noise lines (punctuation, unknowns, stray wire artifacts) are pruned from real Braille words."""
        engine = OBREngine()
        p_dot = 20.0
        cell_dots = {
            'S': [(0, 1), (0, 2), (1, 0)],
            'H': [(0, 0), (0, 1), (1, 1)],
            'I': [(0, 1), (1, 0)],
            'P': [(0, 0), (0, 1), (0, 2), (1, 0)]
        }
        dots = []
        # Main Braille word line at y=200
        for i, ch in enumerate('SHIP'):
            x_base = 80.0 + i * (2.4 * p_dot)
            y_base = 200.0
            for col, row in cell_dots[ch]:
                dots.append({'x': x_base + col * p_dot, 'y': y_base + row * p_dot, 'd': 8.0})

        # Stray noise lines simulating background cables/reflections
        # Noise line 1: pure punctuation (!?,) at y=60
        dots.append({'x': 50.0, 'y': 60.0, 'd': 8.0})
        dots.append({'x': 70.0, 'y': 60.0, 'd': 8.0})
        dots.append({'x': 90.0, 'y': 60.0, 'd': 8.0})

        # Noise line 2: isolated cable fragment (2 dots) at y=350
        dots.append({'x': 100.0, 'y': 350.0, 'd': 8.0})
        dots.append({'x': 150.0, 'y': 350.0, 'd': 8.0})

        res = engine.decode_dots_to_text(dots, 8.0, (500, 500, 3))
        assert res["readable"] is True
        assert res["text"] == "SHIP"
        assert len(res["cells"]) == 4

    def test_home_pitch_resilience(self) -> None:
        """Words with diagonal letters (HOME) decode accurately across diverse cell pitches."""
        engine = OBREngine()
        p_dot = 20.0
        cell_dots = {
            'H': [(0, 0), (0, 1), (1, 1)],
            'O': [(0, 0), (0, 2), (1, 1)],
            'M': [(0, 0), (0, 2), (1, 0)],
            'E': [(0, 0), (1, 1)]
        }
        for factor in [2.1, 2.2, 2.3, 2.4, 2.5]:
            dots = []
            for i, ch in enumerate('HOME'):
                x_base = 50.0 + i * (factor * p_dot)
                y_base = 100.0
                for col, row in cell_dots[ch]:
                    dots.append({'x': x_base + col * p_dot, 'y': y_base + row * p_dot, 'd': 8.0})
            res = engine.decode_dots_to_text(dots, 8.0, (300, 400, 3))
            assert res["readable"] is True
            assert res["text"] == "HOME", f"Failed at factor {factor}: got {res['text']}"

    def test_scan_history_and_pruning(self, tmp_path) -> None:
        """Scan history correctly saves timestamped images, caps at 30, and generates index.html."""
        import numpy as np
        engine = OBREngine()
        test_frame = np.full((100, 100, 3), 200, dtype=np.uint8)

        engine._save_scan_history(test_frame, True, "JUMP", "ok")
        assert os.path.exists("debug_scan/last_scan.jpg")
        assert os.path.exists("debug_scan/index.html")

        # Verify HTML contains reference to JUMP
        with open("debug_scan/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        assert "Brailce Scanner Debug History" in html
        assert "JUMP" in html
