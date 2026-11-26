"""
Optical Braille Recognition (OBR) Engine
========================================

Primary offline recognizer:
  Classical dot-level Grade 1 Braille pipeline (works on any embossed or printed
  Braille page without dataset/internet dependency):
    1. Image quality gates (paper contrast, Laplacian sharpness, dot resolution).
    2. Multi-scale Difference of Gaussians (DoG) for scale-adaptive dot detection
       (captures ink dots and merges embossed shadow/highlight pairs to dome centers).
    3. Grid orientation deskew + nearest-neighbor dot-pitch estimation.
    4. Data-driven text line clustering (prevents multi-line merge).
    5. Global 2x3 cell lattice fitting per text line (row y0 fit + column anchor fit).
    6. Exact Grade 1 Braille lookup with number sign (#) and capital sign (^) handling.

Optional secondary YOLO cell/dot detector:
  Available behind `use_yolo_secondary=True` if local weights exist (never downloads at runtime).
"""

from __future__ import annotations

import datetime
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np

logger = logging.getLogger(__name__)

UNKNOWN_CELL_CHAR = "?"

# Center ROI Constants (slim horizontal box for single-line Braille)
ROI_W_FRAC: float = 0.78
ROI_H_FRAC: float = 0.22
SHOW_ROI: bool = True


class OBREngine:
    """
    Universal engine for translating camera images of physical Braille into text.
    """

    # Exact Grade 1 Braille 6-bit Binary Dot Strings (bit 0 = dot 1 .. bit 5 = dot 6).
    # Zero fabricated/fuzzy guesses: any unrecognized pattern returns UNKNOWN_CELL_CHAR.
    BINARY_DICT: Dict[str, str] = {
        # Standard Alphabet (A-Z)
        "100000": "A", "110000": "B", "100100": "C", "100110": "D", "100010": "E",
        "110100": "F", "110110": "G", "110010": "H", "010100": "I", "010110": "J",
        "101000": "K", "111000": "L", "101100": "M", "101110": "N", "101010": "O",
        "111100": "P", "111110": "Q", "111010": "R", "011100": "S", "011110": "T",
        "101001": "U", "111001": "V", "010111": "W", "101101": "X", "101111": "Y",
        "101011": "Z",
        # Standard Grade 1 Punctuation
        "010000": ",", "011000": ";", "010010": ":", "010011": ".",
        "011010": "!", "011001": "?", "001000": "'", "001001": "-",
        "001011": "\"",
        # Indicators
        "000001": "^",  # Capital sign (Dot 6)
        "000101": "",   # Letter sign (Dots 4-6)
        "001111": "#",  # Number sign (Dots 3-4-5-6)
        "000000": " ",  # Blank cell
    }

    NAMED_DICT: Dict[str, str] = {
        "capital": "^", "cap": "^",
        "number_sign": "#", "number": "#", "num": "#", "hash": "#",
        "letter_sign": "",
        "period": ".", "comma": ",",
        "question_mark": "?", "question": "?",
        "exclamation_mark": "!", "exclamation": "!",
        "colon": ":", "semicolon": ";",
        "hyphen": "-", "dash": "-", "apostrophe": "'",
        "openquotation": "\"", "closequotation": "\"", "quote": "\"",
        "parenthesis": "(", "open_parenthesis": "(", "close_parenthesis": ")",
        "asterisk": "*", "ampersand": "&",
        "plus": "+", "minus": "-", "equals": "=", "slash": "/",
    }

    DIRECT_SYMBOLS: Dict[str, str] = {
        "#": "#", "^": "^", ".": ".", ",": ",", "?": "?", "!": "!",
        "-": "-", "'": "'", "\"": "\"", ":": ":", ";": ";",
        "+": "+", "*": "*", "/": "=", "(": "(", ")": ")", "=": "=",
    }

    NUM_MAP: Dict[str, str] = {
        "A": "1", "B": "2", "C": "3", "D": "4", "E": "5",
        "F": "6", "G": "7", "H": "8", "I": "9", "J": "0",
    }

    def __init__(
        self,
        model_path: str = "brailce/vision/yolov8_braille.pt",
        conf: float = 0.22,
        iou: float = 0.45,
        use_yolo_secondary: bool = False,
    ) -> None:
        self.model_path = model_path
        self.conf = conf
        self.iou = iou
        self.use_yolo_secondary = use_yolo_secondary
        self._model = None
        self.detected_schema = "classical_dot_grid"
        # When not None, process_image() appends debug frames here instead of writing them
        # (scan_frame() tries several strategies and only saves the winner).
        self._deferred_history: Optional[List[Tuple[Any, ...]]] = None
        # (frame, shape, detect_shadow_dots_ex result) reused by process_shadow() within one scan_frame()
        self._shadow_cache: Optional[Tuple[Any, Any, Any]] = None

    def _load_model(self) -> bool:
        """Load local YOLO weights only if use_yolo_secondary=True and weights exist locally."""
        if not self.use_yolo_secondary:
            return False
        if self._model is not None:
            return True
        if not self.model_path or not os.path.exists(self.model_path):
            logger.info("Optional secondary YOLO weights missing at %s (no runtime download).", self.model_path)
            return False
        try:
            if self.model_path.endswith(".onnx") and cv2 is not None:
                self._model = cv2.dnn.readNetFromONNX(self.model_path)
                self.detected_schema = "onnx_yolo"
                return True
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)
            self.detected_schema = self._detect_model_schema()
            return True
        except Exception as exc:
            logger.debug("Optional YOLO model load skipped: %s", exc)
            return False

    def _detect_model_schema(self) -> str:
        if not self._model or not hasattr(self._model, "names") or not self._model.names:
            return "universal"
        str_names = [str(n).strip() for n in self._model.names.values()]
        if sum(1 for n in str_names if len(n) == 6 and set(n).issubset({"0", "1"})) >= min(5, len(str_names)):
            return "binary_dots"
        if any(n.lower() in {"number_sign", "capital", "period"} for n in str_names):
            return "named_classes"
        return "direct_characters"

    def _run_yolo_inference(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Run secondary YOLO detection on image and return bounding boxes list."""
        if not self._model:
            return []
        boxes_out: List[Dict[str, Any]] = []
        try:
            if hasattr(self._model, "predict"):
                preds = self._model.predict(image, conf=self.conf, iou=self.iou, verbose=False)
                for r in preds:
                    if not hasattr(r, "boxes") or r.boxes is None:
                        continue
                    for b in r.boxes:
                        xywh = b.xywh[0].tolist()
                        cls_id = int(b.cls[0])
                        c_conf = float(b.conf[0])
                        label = r.names.get(cls_id, str(cls_id)) if hasattr(r, "names") else str(cls_id)
                        boxes_out.append({"x": xywh[0], "y": xywh[1], "w": xywh[2], "h": xywh[3], "name": label, "conf": c_conf})
        except Exception as exc:
            logger.debug("_run_yolo_inference error: %s", exc)
        return boxes_out

    @classmethod
    def decode_cell_label(cls, raw_name: str) -> str:
        """Decode a single Braille cell label across 6-bit binary, named, and direct schemas."""
        clean_name = str(raw_name).strip()
        if not clean_name:
            return ""

        if clean_name in cls.DIRECT_SYMBOLS:
            return cls.DIRECT_SYMBOLS[clean_name]

        if clean_name in cls.BINARY_DICT:
            return cls.BINARY_DICT[clean_name]

        lowered = clean_name.lower().replace(" ", "_")
        if lowered in cls.NAMED_DICT:
            return cls.NAMED_DICT[lowered]

        for prefix in ("cell_", "braille_", "char_", "letter_"):
            if lowered.startswith(prefix):
                remainder = lowered[len(prefix):]
                if remainder in cls.NAMED_DICT:
                    return cls.NAMED_DICT[remainder]
                if remainder in cls.BINARY_DICT:
                    return cls.BINARY_DICT[remainder]
                if len(remainder) == 1 and remainder.isalpha():
                    return remainder.upper()
                if remainder.isdigit():
                    return remainder

        if len(clean_name) == 6 and set(clean_name).issubset({"0", "1"}):
            return ""

        if len(clean_name) == 1 and clean_name.isalpha():
            return clean_name.upper()
        if clean_name.isdigit():
            return clean_name

        return ""

    @classmethod
    def filter_valid_detections(cls, boxes_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not boxes_data or len(boxes_data) < 2:
            return []

        valid_shape = []
        for b in boxes_data:
            w, h = float(b["w"]), float(b["h"])
            aspect = min(w, h) / max(1e-6, max(w, h))
            if aspect >= 0.35:
                valid_shape.append(b)

        if len(valid_shape) < 2:
            return []

        sizes = [(float(b["w"]) + float(b["h"])) / 2.0 for b in valid_shape]
        median_s = float(np.median(sizes))
        if median_s <= 0:
            return []

        consistent = [
            b for b in valid_shape
            if abs(((float(b["w"]) + float(b["h"])) / 2.0) - median_s) / median_s <= 0.55
        ]
        return consistent if len(consistent) >= 2 else []

    @classmethod
    def decode_boxes(cls, boxes_data: List[Dict[str, Any]], space_threshold: Optional[float] = None) -> Dict[str, Any]:
        """Decode cell boxes (from YOLO or synthetic boxes) into Grade 1 text."""
        valid_boxes = cls.filter_valid_detections(boxes_data)
        if not valid_boxes:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": []}

        widths = [float(b["w"]) for b in valid_boxes]
        median_w = float(np.median(widths)) if widths else 30.0
        if space_threshold is None:
            space_threshold = max(20.0, median_w * 1.30)

        sorted_by_y = sorted(valid_boxes, key=lambda b: b["y"])
        lines: List[List[Dict[str, Any]]] = []
        for b in sorted_by_y:
            b_top = b["y"] - b["h"] / 2.0
            b_bot = b["y"] + b["h"] / 2.0
            best_line = None
            max_overlap = 0.0
            for line in lines:
                line_top = min(item["y"] - item["h"] / 2.0 for item in line)
                line_bot = max(item["y"] + item["h"] / 2.0 for item in line)
                line_h = max(item["h"] for item in line)
                overlap = max(0.0, min(b_bot, line_bot) - max(b_top, line_top))
                if overlap > (line_h * 0.35) and overlap > max_overlap:
                    max_overlap = overlap
                    best_line = line
            if best_line is not None:
                best_line.append(b)
            else:
                lines.append([b])

        lines.sort(key=lambda ln: float(np.median([item["y"] for item in ln])))

        cleaned_lines: List[List[Dict[str, Any]]] = []
        for line in lines:
            line.sort(key=lambda b: b["x"])
            dedup: List[Dict[str, Any]] = []
            for b in line:
                if not dedup:
                    dedup.append(b)
                elif abs(b["x"] - dedup[-1]["x"]) < (median_w * 0.65):
                    if b.get("conf", 0) > dedup[-1].get("conf", 0):
                        dedup[-1] = b
                else:
                    dedup.append(b)
            if dedup:
                cleaned_lines.append(dedup)

        line_strings: List[str] = []
        cell_events: List[Dict[str, Any]] = []
        word_idx = 0

        for line_idx, line in enumerate(cleaned_lines):
            line_str = ""
            is_number = False
            is_capital = False
            last_x = None

            for b in line:
                if last_x is not None and (b["x"] - last_x) > space_threshold:
                    line_str += " "
                    is_number = False
                    is_capital = False
                    word_idx += 1
                last_x = b["x"]

                raw = str(b.get("name", ""))
                char = cls.decode_cell_label(raw)
                if not char and raw != "000101" and raw != "letter_sign":
                    char = UNKNOWN_CELL_CHAR

                if char == "#":
                    is_number = True
                    cell_events.append({"char": "#", "kind": "indicator", "word_index": word_idx, "line_index": line_idx})
                    continue
                if char == "^":
                    is_capital = True
                    cell_events.append({"char": "^", "kind": "indicator", "word_index": word_idx, "line_index": line_idx})
                    continue
                if char == "":
                    continue

                if char == UNKNOWN_CELL_CHAR:
                    line_str += UNKNOWN_CELL_CHAR
                    cell_events.append({"char": UNKNOWN_CELL_CHAR, "kind": "unknown", "word_index": word_idx, "line_index": line_idx})
                elif is_number:
                    out_c = cls.NUM_MAP[char] if char in cls.NUM_MAP else (char if (char.isdigit() or char in {".", ","}) else char)
                    if not (out_c.isdigit() or out_c in {".", ","}):
                        is_number = False
                    line_str += out_c
                    cell_events.append({"char": out_c, "kind": "digit" if out_c.isdigit() else "char", "word_index": word_idx, "line_index": line_idx})
                else:
                    if is_capital and char.isalpha():
                        out_c = char.upper()
                        is_capital = False
                        cap_flag = True
                    else:
                        out_c = char
                        cap_flag = False
                    line_str += out_c
                    cell_events.append({"char": out_c, "kind": "char", "capitalized": cap_flag, "word_index": word_idx, "line_index": line_idx})

            clean_l = " ".join(line_str.split()).strip()
            if clean_l:
                line_strings.append(clean_l)
            word_idx += 1

        final_text = " ".join(line_strings).strip().replace("\n", " ")
        if not final_text:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": []}

        return {"readable": True, "reason": "ok", "text": final_text, "cells": cell_events}

    # =========================================================================
    # Classical Scale-Adaptive Dot-Level Recognizer (Primary Offline Path)
    # =========================================================================

    @staticmethod
    def remove_background_shadows(gray: np.ndarray) -> np.ndarray:
        """
        Flatten corner shadows and uneven lighting using local Gaussian background subtraction.
        Subtracts large-scale illumination gradients (31x31 Gaussian blur) while preserving
        small dot/crescent structures (diameters 4px-16px).
        """
        if gray is None or cv2 is None or gray.size == 0:
            return gray
        kh = min(31, gray.shape[0] - (1 - gray.shape[0] % 2))
        kw = min(31, gray.shape[1] - (1 - gray.shape[1] % 2))
        kh = max(3, kh if kh % 2 == 1 else kh - 1)
        kw = max(3, kw if kw % 2 == 1 else kw - 1)
        bg = cv2.GaussianBlur(gray.astype(np.float32), (kw, kh), 0)
        diff = gray.astype(np.float32) - bg
        return np.clip(128.0 + diff, 0.0, 255.0).astype(np.uint8)

    @staticmethod
    def suppress_printed_ink(image: np.ndarray) -> np.ndarray:
        """
        Mask dark printed/handwritten ink strokes and reject them from Braille candidates:
        1. Flattens corner shadows via local background subtraction.
        2. Identifies dark connected components that are significantly larger or more
           elongated than normal Braille dots (area > 2.5x expected dot area or aspect > 2.0).
        3. Dilates ink mask slightly and fills with local paper background.
        Note: When paper has embossed characteristics (or dot_type is embossed), printed ink
        suppression is bypassed so embossed shadow/highlight crescents are never erased.
        """
        if image is None or cv2 is None or image.size == 0:
            return image

        # Bypass for embossed paper to preserve embossed dot shadows
        if getattr(image, "_is_embossed", False) or os.environ.get("BRAILCE_DISABLE_INK_SUPPRESSION", "1") == "1":
            return image

        clean = image.copy()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY) if len(clean.shape) == 3 else clean.copy()

        # 1. Local background subtraction (removes large corner shadows)
        flat = OBREngine.remove_background_shadows(gray)

        # 2. Identify candidate dark ink pixels relative to local paper background
        dark_mask = (flat < 118).astype(np.uint8) * 255
        paper_med = float(np.median(gray))
        abs_dark = (gray < paper_med - 18.0).astype(np.uint8) * 255
        combined_dark = cv2.bitwise_or(dark_mask, abs_dark)

        if np.any(combined_dark > 0):
            num, labels, stats, _ = cv2.connectedComponentsWithStats(combined_dark, connectivity=8)
            areas = [float(stats[i, cv2.CC_STAT_AREA]) for i in range(1, num)]
            candidate_dot_areas = [a for a in areas if 6.0 <= a <= 130.0]
            expected_dot_area = float(np.median(candidate_dot_areas)) if candidate_dot_areas else 40.0

            text_mask = np.zeros_like(gray, dtype=np.uint8)
            for i in range(1, num):
                w_comp = float(stats[i, cv2.CC_STAT_WIDTH])
                h_comp = float(stats[i, cv2.CC_STAT_HEIGHT])
                a_comp = float(stats[i, cv2.CC_STAT_AREA])

                max_dim = max(w_comp, h_comp)
                min_dim = max(1.0, min(w_comp, h_comp))
                aspect_ratio = max_dim / min_dim

                # Ink rejection criteria:
                # - Connected area > 2.5 * expected dot area
                # - Elongated stroke (pen stroke, line, letter stem)
                # - Very large component (max_dim > 22 or area > 180)
                is_ink = False
                if a_comp > max(160.0, 2.5 * expected_dot_area):
                    is_ink = True
                elif aspect_ratio > 2.0 and (max_dim > 16.0 or a_comp > 40.0):
                    is_ink = True
                elif max_dim > 22.0 or a_comp > 180.0:
                    is_ink = True

                if is_ink:
                    text_mask[labels == i] = 255

            if np.any(text_mask > 0):
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                dilated = cv2.dilate(text_mask, kernel, iterations=2)
                try:
                    clean = cv2.inpaint(clean, dilated, 7, cv2.INPAINT_TELEA)
                except Exception:
                    bg_only = gray.copy()
                    bg_only[dilated > 0] = int(round(paper_med))
                    bg_smooth = cv2.GaussianBlur(bg_only, (25, 25), 0)
                    if len(clean.shape) == 3:
                        for c in range(3):
                            clean[:, :, c][dilated > 0] = bg_smooth[dilated > 0]
                    else:
                        clean[dilated > 0] = bg_smooth[dilated > 0]

        return clean

    @staticmethod
    def enhance_contrast(image: np.ndarray) -> np.ndarray:
        """Flatten uneven paper lighting and enhance embossed dot contrast via CLAHE + Top-Hat / Black-Hat."""
        if image is None or cv2 is None or image.size == 0:
            return image
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
            # Guard against flat blank paper: if no local contrast features exist, do not amplify sensor noise
            gray_f = gray.astype(np.float32)
            g0 = cv2.GaussianBlur(gray_f, (0, 0), 1.0)
            g1 = cv2.GaussianBlur(gray_f, (0, 0), 2.0)
            if float(np.max(np.abs(g1 - g0))) < 1.0:
                return image

            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            cl = clahe.apply(gray)
            kh = min(15, cl.shape[0] - (1 - cl.shape[0] % 2))
            kw = min(15, cl.shape[1] - (1 - cl.shape[1] % 2))
            kh = max(5, kh if kh % 2 == 1 else kh - 1)
            kw = max(5, kw if kw % 2 == 1 else kw - 1)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kw, kh))
            tophat = cv2.morphologyEx(cl, cv2.MORPH_TOPHAT, kernel)
            blackhat = cv2.morphologyEx(cl, cv2.MORPH_BLACKHAT, kernel)
            enhanced = cv2.add(cl, tophat)
            enhanced = cv2.subtract(enhanced, blackhat)
            if len(image.shape) == 3:
                return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            return enhanced
        except Exception:
            return image

    @staticmethod
    def has_paper_background(image: np.ndarray) -> bool:
        """Verify frame is not an empty/black dead frame."""
        if image is None or image.size == 0:
            return False
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if (cv2 is not None and len(image.shape) == 3) else image
        return float(np.percentile(gray, 90)) > 35.0 and float(np.std(gray)) > 0.4

    @staticmethod
    def check_image_quality(image: np.ndarray) -> Tuple[bool, str, float]:
        """
        Check basic brightness and Laplacian sharpness.
        Returns (ok, reason, laplacian_var).
        """
        if image is None or image.size == 0 or cv2 is None:
            return False, "no_braille", 0.0

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        if float(np.percentile(gray, 90)) <= 55.0 or float(np.std(gray)) <= 3.5:
            return False, "no_braille", 0.0

        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if lap_var < 4.0:
            # Under flat room lighting without rake lighting, smooth white paper has low raw lap_var (1.0-4.0).
            # Evaluate if adaptive contrast (CLAHE) reveals in-focus micro-edges.
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            cl = clahe.apply(gray)
            enh_lap = float(cv2.Laplacian(cl, cv2.CV_64F).var())
            if lap_var < 1.0 or enh_lap < 11.0:
                return False, "blurry", lap_var
            lap_var = max(lap_var, enh_lap * 0.35)

        return True, "ok", lap_var

    @staticmethod
    def _nms_points(cands: List[Dict[str, float]], radius: float) -> List[Dict[str, float]]:
        """Greedy NMS on score-sorted points using a spatial grid (O(n) instead of O(n^2))."""
        kept: List[Dict[str, float]] = []
        if not cands:
            return kept
        cell = max(1.0, float(radius))
        grid: Dict[Tuple[int, int], List[Dict[str, float]]] = {}
        r2 = radius * radius
        for cand in cands:
            gx, gy = int(cand["x"] // cell), int(cand["y"] // cell)
            clash = False
            for ix in (gx - 1, gx, gx + 1):
                for iy in (gy - 1, gy, gy + 1):
                    for ex in grid.get((ix, iy), ()):
                        if (cand["x"] - ex["x"]) ** 2 + (cand["y"] - ex["y"]) ** 2 <= r2:
                            clash = True
                            break
                    if clash:
                        break
                if clash:
                    break
            if not clash:
                kept.append(cand)
                grid.setdefault((gx, gy), []).append(cand)
        return kept

    @staticmethod
    def detect_dots(image: np.ndarray, embossed_only: bool = False) -> Tuple[List[Dict[str, float]], float]:
        """
        Detect embossed or printed Braille dots across a wide scale range (4px to 20px)
        using multi-scale Difference of Gaussians (DoG). Robustly merges paired
        shadow-highlight crescents of embossed dots to the true geometric dome center,
        and retains solid ink dots.
        """
        if image is None or cv2 is None or image.size == 0:
            return [], 0.0

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
        h, w = gray.shape[:2]
        gray_f = gray.astype(np.float32)

        # Max blob size used to be relative to each axis, which on the slim center-strip ROI
        # (22% of frame height ~158px) silently dropped every dot wider than ~13-19px, i.e.
        # every close-up scan. Use max(h, w/2): unchanged for normal frames, but a wide thin
        # strip is no longer penalised. (Using the full long side let merged 2-dot blobs in.)
        ref = max(float(h), 0.5 * float(w))
        max_blob_e = 0.08 * ref
        max_blob = 0.12 * ref

        # 3-octave multi-scale Gaussian smoothing (covers 3px to 18px dot radius across wide distance range)
        g0 = cv2.GaussianBlur(gray_f, (0, 0), 1.0)
        g1 = cv2.GaussianBlur(gray_f, (0, 0), 2.0)
        g2 = cv2.GaussianBlur(gray_f, (0, 0), 4.0)
        g3 = cv2.GaussianBlur(gray_f, (0, 0), 8.0)

        # Dark response (center darker than surround: ink dots, shadow crescents)
        dog0_d = np.clip(g1 - g0, 0.0, 255.0)
        dog1_d = np.clip(g2 - g1, 0.0, 255.0)
        dog2_d = np.clip(g3 - g2, 0.0, 255.0)
        dark_resp = np.maximum(np.maximum(dog0_d, dog1_d), dog2_d)

        # Light response (center brighter than surround: embossed highlights)
        dog0_l = np.clip(g0 - g1, 0.0, 255.0)
        dog1_l = np.clip(g1 - g2, 0.0, 255.0)
        dog2_l = np.clip(g2 - g3, 0.0, 255.0)
        light_resp = np.maximum(np.maximum(dog0_l, dog1_l), dog2_l)

        # Robust thresholding: Braille dots typically occupy < 2% of the page
        p98_d = float(np.percentile(dark_resp, 98.0))
        p98_l = float(np.percentile(light_resp, 98.0))

        if embossed_only:
            # Erase handwritten/printed ink strokes first so DoG never rings on pencil/pen text
            ksize = max(15, min(41, (min(h, w) // 5) | 1))
            bg = cv2.medianBlur(gray, ksize).astype(np.float32)
            ink_dark = ((bg - gray_f) > 15.0).astype(np.uint8) * 255
            num_ink, labels_ink, stats_ink, _ = cv2.connectedComponentsWithStats(ink_dark, connectivity=8)
            ink_mask = np.zeros_like(gray, dtype=np.uint8)
            for i in range(1, num_ink):
                area_i = float(stats_ink[i, cv2.CC_STAT_AREA])
                bw_i = float(stats_ink[i, cv2.CC_STAT_WIDTH])
                bh_i = float(stats_ink[i, cv2.CC_STAT_HEIGHT])
                max_dim = max(bw_i, bh_i)
                min_dim = max(1.0, min(bw_i, bh_i))
                aspect_i = max_dim / min_dim
                x0_i, y0_i = int(stats_ink[i, cv2.CC_STAT_LEFT]), int(stats_ink[i, cv2.CC_STAT_TOP])
                pad = 6
                ry1, ry2 = max(0, y0_i - pad), min(h, y0_i + int(bh_i) + pad)
                rx1, rx2 = max(0, x0_i - pad), min(w, x0_i + int(bw_i) + pad)
                patch_diff = gray_f[ry1:ry2, rx1:rx2] - bg[ry1:ry2, rx1:rx2]
                bright_peak = float(np.max(patch_diff))
                dark_depth = float(-np.min(patch_diff))
                touches_edge = (y0_i <= 3) or (y0_i + int(bh_i) >= h - 3)
                if (
                    max_dim > 22.0
                    or (aspect_i > 2.1 and max_dim > 12.0)
                    or area_i > 250.0
                    or (touches_edge and dark_depth > 18.0 and bright_peak < 10.0)
                    or (dark_depth > 22.0 and bright_peak < max(6.0, dark_depth * 0.25))
                ):
                    ink_mask[labels_ink == i] = 255

            clean_gray = gray_f.copy()
            if np.any(ink_mask > 0):
                ink_mask = cv2.dilate(ink_mask, np.ones((3, 3), np.uint8), iterations=1)
                clean_gray[ink_mask > 0] = bg[ink_mask > 0]
                g0 = cv2.GaussianBlur(clean_gray, (0, 0), 1.0)
                g1 = cv2.GaussianBlur(clean_gray, (0, 0), 2.0)
                g2 = cv2.GaussianBlur(clean_gray, (0, 0), 4.0)
                g3 = cv2.GaussianBlur(clean_gray, (0, 0), 8.0)
                dark_resp = np.maximum(np.maximum(np.clip(g1 - g0, 0.0, 255.0), np.clip(g2 - g1, 0.0, 255.0)), np.clip(g3 - g2, 0.0, 255.0))
                light_resp = np.maximum(np.maximum(np.clip(g0 - g1, 0.0, 255.0), np.clip(g1 - g2, 0.0, 255.0)), np.clip(g2 - g3, 0.0, 255.0))
                p98_d = float(np.percentile(dark_resp, 98.0))
                p98_l = float(np.percentile(light_resp, 98.0))

            thresh_d = max(2.2, p98_d * 0.34)
            thresh_l = max(2.0, p98_l * 0.36)

            # 1. Collect dark shadow blobs and light highlight blobs separately
            dark_list: List[Dict[str, float]] = []
            mask_d = (dark_resp >= thresh_d).astype(np.uint8)
            num_d, _, stats_d, cents_d = cv2.connectedComponentsWithStats(mask_d, connectivity=8)
            for lbl in range(1, num_d):
                area = float(stats_d[lbl, cv2.CC_STAT_AREA])
                bw = float(stats_d[lbl, cv2.CC_STAT_WIDTH])
                bh = float(stats_d[lbl, cv2.CC_STAT_HEIGHT])
                if area < 6.0 or bw < 2.0 or bh < 2.0 or bw > max_blob_e or bh > max_blob_e:
                    continue
                if min(bw, bh) / max(1e-6, max(bw, bh)) < 0.35:
                    continue
                cx, cy = float(cents_d[lbl][0]), float(cents_d[lbl][1])
                diam = math.sqrt(4.0 * area / math.pi)
                dark_list.append({"x": cx, "y": cy, "d": diam, "area": area, "score": float(dark_resp[int(cy), int(cx)])})

            light_list: List[Dict[str, float]] = []
            mask_l = (light_resp >= thresh_l).astype(np.uint8)
            num_l, _, stats_l, cents_l = cv2.connectedComponentsWithStats(mask_l, connectivity=8)
            for lbl in range(1, num_l):
                area = float(stats_l[lbl, cv2.CC_STAT_AREA])
                bw = float(stats_l[lbl, cv2.CC_STAT_WIDTH])
                bh = float(stats_l[lbl, cv2.CC_STAT_HEIGHT])
                if area < 6.0 or bw < 2.0 or bh < 2.0 or bw > max_blob_e or bh > max_blob_e:
                    continue
                if min(bw, bh) / max(1e-6, max(bw, bh)) < 0.35:
                    continue
                cx, cy = float(cents_l[lbl][0]), float(cents_l[lbl][1])
                diam = math.sqrt(4.0 * area / math.pi)
                light_list.append({"x": cx, "y": cy, "d": diam, "area": area, "score": float(light_resp[int(cy), int(cx)])})

            # 2. Pair shadow + highlight of the SAME embossed dome to its true center
            all_d_vals = [b["d"] for b in (dark_list + light_list)]
            est_d0 = float(np.median(all_d_vals)) if all_d_vals else 8.0
            pair_r = max(10.0, est_d0 * 1.55)

            embossed_cands: List[Dict[str, float]] = []
            used_l_idx = set()
            for d_b in dark_list:
                best_li = None
                best_dist = float("inf")
                for li, l_b in enumerate(light_list):
                    if li in used_l_idx:
                        continue
                    dist = math.hypot(d_b["x"] - l_b["x"], d_b["y"] - l_b["y"])
                    if dist <= pair_r and dist < best_dist:
                        best_dist = dist
                        best_li = li
                if best_li is not None:
                    l_b = light_list[best_li]
                    used_l_idx.add(best_li)
                    cx = (d_b["x"] + l_b["x"]) / 2.0
                    cy = (d_b["y"] + l_b["y"]) / 2.0
                    diam = (d_b["d"] + l_b["d"]) / 2.0
                    area = max(d_b["area"], l_b["area"])
                    score = d_b["score"] + l_b["score"]
                else:
                    cx, cy, diam, area, score = d_b["x"], d_b["y"], d_b["d"], d_b["area"], d_b["score"]

                r_search = int(max(5, round(diam * 1.3)))
                y1, y2 = max(0, int(cy) - r_search), min(h, int(cy) + r_search + 1)
                x1, x2 = max(0, int(cx) - r_search), min(w, int(cx) + r_search + 1)
                local_l_max = float(np.max(light_resp[y1:y2, x1:x2]))
                bright_above_bg = float(np.max(clean_gray[y1:y2, x1:x2] - bg[y1:y2, x1:x2]))
                dark_below_bg = float(np.max(bg[y1:y2, x1:x2] - clean_gray[y1:y2, x1:x2]))
                # Reject flat handwriting edge artifacts (deep dark, weak highlight)
                if local_l_max >= 1.2 and bright_above_bg >= max(1.8, dark_below_bg * 0.12):
                    embossed_cands.append({"x": cx, "y": cy, "d": diam, "score": score, "area": area})

            # 3. Include remaining unpaired light-dominant domes (e.g. top-lit dots with faint shadow)
            for li, l_b in enumerate(light_list):
                if li in used_l_idx:
                    continue
                cx, cy, diam, area, score = l_b["x"], l_b["y"], l_b["d"], l_b["area"], l_b["score"]
                r_search = int(max(5, round(diam * 1.3)))
                y1, y2 = max(0, int(cy) - r_search), min(h, int(cy) + r_search + 1)
                x1, x2 = max(0, int(cx) - r_search), min(w, int(cx) + r_search + 1)
                local_d_max = float(np.max(dark_resp[y1:y2, x1:x2]))
                bright_above_bg = float(np.max(clean_gray[y1:y2, x1:x2] - bg[y1:y2, x1:x2]))
                if local_d_max >= 1.0 and bright_above_bg >= 2.0:
                    embossed_cands.append({"x": cx, "y": cy, "d": diam, "score": score + local_d_max, "area": area})

            if not embossed_cands:
                return [], 0.0
            if len(embossed_cands) >= 4:
                med_a = float(np.median([b["area"] for b in embossed_cands]))
                embossed_cands = [b for b in embossed_cands if 0.35 * med_a <= b["area"] <= 2.6 * med_a]
            if not embossed_cands:
                return [], 0.0
            est_d = float(np.median([b["d"] for b in embossed_cands]))
            embossed_cands.sort(key=lambda d: d["score"], reverse=True)
            # Ensure one dome never produces two dots (shadow + highlight are ~8-12px apart; real dots are ~22-30px apart)
            suppress_r = max(8.0, est_d * 1.15)
            final_dots_e = OBREngine._nms_points(embossed_cands, suppress_r)
            final_d_e = float(np.median([d["d"] for d in final_dots_e])) if final_dots_e else 0.0
            return final_dots_e, final_d_e

        thresh_d = max(4.0, p98_d * 0.42)
        thresh_l = max(4.0, p98_l * 0.42)

        mask_d = (dark_resp >= thresh_d).astype(np.uint8)
        mask_l = (light_resp >= thresh_l).astype(np.uint8)

        # Dark blobs (shadows / ink dots)
        num_d, _, stats_d, cents_d = cv2.connectedComponentsWithStats(mask_d, connectivity=8)
        dark_blobs = []
        for lbl in range(1, num_d):
            area = float(stats_d[lbl, cv2.CC_STAT_AREA])
            bw = float(stats_d[lbl, cv2.CC_STAT_WIDTH])
            bh = float(stats_d[lbl, cv2.CC_STAT_HEIGHT])
            if area < 3.0 or bw < 2.0 or bh < 2.0 or bw > max_blob or bh > max_blob:
                continue
            aspect = min(bw, bh) / max(1e-6, max(bw, bh))
            if aspect < 0.32:
                continue
            cx, cy = float(cents_d[lbl][0]), float(cents_d[lbl][1])
            diam = math.sqrt(4.0 * area / math.pi)
            score = float(dark_resp[int(cy), int(cx)]) if 0 <= int(cy) < h and 0 <= int(cx) < w else 1.0
            dark_blobs.append({"x": cx, "y": cy, "d": diam, "score": score, "area": area})

        # Light blobs (highlights)
        num_l, _, stats_l, cents_l = cv2.connectedComponentsWithStats(mask_l, connectivity=8)
        light_blobs = []
        for lbl in range(1, num_l):
            area = float(stats_l[lbl, cv2.CC_STAT_AREA])
            bw = float(stats_l[lbl, cv2.CC_STAT_WIDTH])
            bh = float(stats_l[lbl, cv2.CC_STAT_HEIGHT])
            if area < 3.0 or bw < 2.0 or bh < 2.0 or bw > max_blob or bh > max_blob:
                continue
            aspect = min(bw, bh) / max(1e-6, max(bw, bh))
            if aspect < 0.32:
                continue
            cx, cy = float(cents_l[lbl][0]), float(cents_l[lbl][1])
            diam = math.sqrt(4.0 * area / math.pi)
            score = float(light_resp[int(cy), int(cx)]) if 0 <= int(cy) < h and 0 <= int(cx) < w else 1.0
            light_blobs.append({"x": cx, "y": cy, "d": diam, "score": score, "area": area})

        if not dark_blobs:
            return [], 0.0

        # Filter out fringe artifacts / anti-aliasing edge crescents (< 30% of median dot area)
        if len(dark_blobs) >= 4:
            med_a_d = float(np.median([b["area"] for b in dark_blobs]))
            # also drop merged multi-dot blobs from the coarse DoG octave (> 4x median area)
            dark_blobs = [b for b in dark_blobs if (0.30 * med_a_d) <= b["area"] <= (4.0 * med_a_d)]

        if len(light_blobs) >= 4:
            med_a_l = float(np.median([b["area"] for b in light_blobs]))
            light_blobs = [b for b in light_blobs if (0.30 * med_a_l) <= b["area"] <= (4.0 * med_a_l)]

        if not dark_blobs:
            return [], 0.0

        est_d = float(np.median([b["d"] for b in dark_blobs]))
        max_pair_dist = max(4.0, est_d * 1.15)

        # Match paired shadow and highlight for embossed Braille
        paired_dots: List[Dict[str, float]] = []
        used_l = set()
        # Vectorized nearest-highlight lookup (was a pure-Python O(n*m) loop: several
        # seconds per frame when large dots produce thousands of faint candidate blobs).
        l_xy = np.array([[b["x"], b["y"]] for b in light_blobs], dtype=np.float64).reshape(-1, 2)
        l_free = np.ones(len(light_blobs), dtype=bool)
        for d_blob in dark_blobs:
            best_l = None
            best_dist = float("inf")
            if len(l_xy):
                dists = np.hypot(l_xy[:, 0] - d_blob["x"], l_xy[:, 1] - d_blob["y"])
                dists[~l_free] = np.inf
                j = int(np.argmin(dists))
                if dists[j] <= max_pair_dist:
                    best_l, best_dist = j, float(dists[j])

            if best_l is not None and best_dist >= (est_d * 0.25):
                l_match = light_blobs[best_l]
                diam_ratio = max(d_blob["d"], l_match["d"]) / max(1e-3, min(d_blob["d"], l_match["d"]))
                if diam_ratio <= 2.2:
                    used_l.add(best_l)
                    l_free[best_l] = False
                    # Center of embossed dot is midpoint between shadow and highlight
                    cx = (d_blob["x"] + l_match["x"]) / 2.0
                    cy = (d_blob["y"] + l_match["y"]) / 2.0
                    dot_d = (d_blob["d"] + l_match["d"]) / 2.0
                    paired_dots.append({"x": cx, "y": cy, "d": dot_d, "score": d_blob["score"] + l_match["score"]})
                elif not embossed_only:
                    paired_dots.append({"x": d_blob["x"], "y": d_blob["y"], "d": d_blob["d"], "score": d_blob["score"]})
            elif not embossed_only:
                # Solid ink dot or top-lit spot
                paired_dots.append({"x": d_blob["x"], "y": d_blob["y"], "d": d_blob["d"], "score": d_blob["score"]})

        # Non-maximum suppression / deduplicate any dots closer than 0.52 * est_d
        paired_dots.sort(key=lambda d: d["score"], reverse=True)
        suppress_r = max(3.0, est_d * 0.52)
        final_dots = OBREngine._nms_points(paired_dots, suppress_r)

        final_d = float(np.median([d["d"] for d in final_dots])) if final_dots else 0.0
        return final_dots, final_d

    @staticmethod
    def _estimate_skew_and_pitch(dots: List[Dict[str, float]], dot_diam: float) -> Tuple[float, float]:
        """
        Estimate grid rotation angle (degrees, in [-25, 25]) and within-cell
        dot-to-dot pitch p_dot directly from nearest-neighbor vectors.
        """
        if len(dots) < 4:
            return 0.0, max(8.0, dot_diam * 2.5)

        pts = np.array([[d["x"], d["y"]] for d in dots], dtype=np.float64)
        n = len(pts)

        # 1. Direct Nearest-Neighbor p_dot measurement (scale-invariant from 8px to 120px)
        nn_dists: List[float] = []
        nn_angles: List[float] = []
        for i in range(n):
            diffs = pts - pts[i]
            d = np.hypot(diffs[:, 0], diffs[:, 1])
            d_valid = d[d > 3.0]
            if len(d_valid):
                min_idx = int(np.where(d == np.min(d_valid))[0][0])
                dx, dy = diffs[min_idx]
                ang = math.degrees(math.atan2(dy, dx))
                folded = abs(((ang + 45.0) % 90.0) - 45.0)
                nn_dists.append(float(np.min(d_valid)))
                nn_angles.append(folded)

        nn_dists_arr = np.array(nn_dists, dtype=np.float64) if nn_dists else np.array([], dtype=np.float64)
        nn_angles_arr = np.array(nn_angles, dtype=np.float64) if nn_angles else np.array([], dtype=np.float64)

        # Priority 1: Non-diagonal nearest neighbors (filters out diagonal sqrt(2)*p_dot vectors)
        non_diag_mask = nn_angles_arr <= 22.0
        if np.sum(non_diag_mask) >= 2:
            p_dot = float(np.median(nn_dists_arr[non_diag_mask]))
        elif len(nn_dists) > 0:
            med_ang = float(np.median(nn_angles_arr))
            if med_ang > 22.0:
                p_dot = float(np.median(nn_dists_arr)) / math.sqrt(2.0)
            else:
                p_dot = float(np.median(nn_dists_arr))
        else:
            p_dot = max(12.0, dot_diam * 2.5)

        # 2. Skew estimation from orthogonal neighbor vectors around 1.0 * p_dot
        angles_deg: List[float] = []
        min_r = p_dot * 0.75
        max_r = p_dot * 1.35
        for i in range(n):
            diffs = pts - pts[i]
            dists = np.hypot(diffs[:, 0], diffs[:, 1])
            mask = (dists >= min_r) & (dists <= max_r)
            if not np.any(mask):
                continue
            for j in np.where(mask)[0]:
                dx, dy = diffs[j]
                ang = math.degrees(math.atan2(dy, dx))
                # Fold angle modulo 90 degrees into [-45, 45]
                ang_folded = ((ang + 45.0) % 90.0) - 45.0
                if abs(ang_folded) <= 22.0:
                    angles_deg.append(ang_folded)

        skew_deg = float(np.median(angles_deg)) if len(angles_deg) >= 3 else 0.0
        return skew_deg, p_dot

    @staticmethod
    def _fit_cell_lattice(
        col_xs: List[float],
        col_w: List[int],
        p_dot: float,
        pairs: List[Tuple[float, float]],
    ) -> Tuple[float, float, float]:
        """
        Fit the horizontal cell lattice of one Braille line.

        Each cell k has a left column at x0 + k*p_cell and a right column at
        x0 + k*p_cell + p_col. Searches p_cell over [1.9, 4.4] * p_dot (standard Braille
        is ~2.4; printed charts/signage are often wider) and every column-anchored x0,
        keeping the lattice that fits the most dot columns, then refines (x0, p_cell, p_col)
        by least squares. Returns (x0, p_cell, p_col).

        This replaces "median of pair-distance / k", which with only a couple of
        paired columns picked a wrong divisor and drifted ~1 column per few cells.
        """
        default_x0 = pairs[0][0] if pairs else (col_xs[0] if col_xs else 0.0)
        if len(col_xs) < 2 or p_dot <= 0:
            return default_x0, p_dot * 2.4, p_dot

        xs = np.asarray(col_xs, dtype=np.float64)
        ws = np.asarray(col_w, dtype=np.float64)
        tol = 0.30 * p_dot
        best = None  # (score, x0, pc)
        for ratio in np.arange(1.9, 4.401, 0.02):
            pc = ratio * p_dot
            # anchor lattice so that some column is a left (x0 = x) or right (x0 = x - p_dot) column
            x0_cands = np.concatenate([xs, xs - p_dot])
            phase = (xs[None, :] - x0_cands[:, None]) % pc
            dist = np.minimum(np.minimum(phase, np.abs(phase - p_dot)), pc - phase)
            fit = np.clip(1.0 - (dist / tol) ** 2, 0.0, None) * ws[None, :]
            scores = fit.sum(axis=1)
            # Mild prior toward the standard 2.4 ratio, and toward the first column being a left column
            scores = scores - 0.02 * abs(ratio - 2.4)
            scores[: len(xs)] += 0.01
            i = int(np.argmax(scores))
            if best is None or scores[i] > best[0] + 1e-9:
                best = (float(scores[i]), float(x0_cands[i]), float(pc))

        _, x0, pc = best
        p_col = p_dot

        # Least-squares refinement on inlier columns: x = x0 + k*pc + c*p_col
        for _ in range(2):
            rows_a, rows_b, rows_w = [], [], []
            for x, w in zip(xs, ws):
                k = math.floor((x - x0 + 0.5 * (pc - p_col)) / pc)
                rem = x - (x0 + k * pc)
                c = 0 if rem < 0.5 * p_col else 1
                if abs(rem - c * p_col) > tol:
                    continue
                rows_a.append([1.0, float(k), float(c)])
                rows_b.append(x)
                rows_w.append(w)
            if len(rows_a) < 3:
                break
            A = np.asarray(rows_a)
            b = np.asarray(rows_b)
            sw = np.sqrt(np.asarray(rows_w))
            if np.ptp(A[:, 1]) == 0:
                break
            if np.ptp(A[:, 2]) == 0:
                # no right columns among inliers: keep p_col fixed
                sol, *_ = np.linalg.lstsq((A[:, :2] * sw[:, None]), (b - A[:, 2] * p_col) * sw, rcond=None)
                nx0, npc, npcol = sol[0], sol[1], p_col
            else:
                sol, *_ = np.linalg.lstsq(A * sw[:, None], b * sw, rcond=None)
                nx0, npc, npcol = sol
            if 1.7 * p_dot <= npc <= 4.6 * p_dot and 0.7 * p_dot <= npcol <= 1.3 * p_dot:
                x0, pc, p_col = float(nx0), float(npc), float(npcol)
        return x0, pc, p_col

    @classmethod
    def decode_dots_to_text(
        cls,
        dots: List[Dict[str, float]],
        dot_diam: float,
        image_shape: Tuple[int, ...],
        single_line: bool = False,
    ) -> Dict[str, Any]:
        """
        Group detected dots into Braille lines, fit 2x3 cell grids, and translate
        exact 6-bit patterns using Grade 1 rules.
        """
        if len(dots) < 2:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": [], "boxes": []}

        h, w = image_shape[:2]
        skew_deg, p_dot = cls._estimate_skew_and_pitch(dots, dot_diam)

        cx0, cy0 = w / 2.0, h / 2.0
        rad = math.radians(-skew_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)

        rot_dots: List[Dict[str, float]] = []
        for d in dots:
            dx = d["x"] - cx0
            dy = d["y"] - cy0
            rx = dx * cos_a - dy * sin_a + cx0
            ry = dx * sin_a + dy * cos_a + cy0
            rot_dots.append({**d, "rx": rx, "ry": ry})

        # Single-line mode if explicitly requested (e.g. slim ROI scan) or if all dots span a single line height
        rot_dots.sort(key=lambda d: d["ry"])
        min_ry = rot_dots[0]["ry"]
        max_ry = rot_dots[-1]["ry"]
        span_y = max_ry - min_ry

        if single_line or span_y <= (p_dot * 3.4):
            rot_dots.sort(key=lambda d: d["rx"])
            lines: List[List[Dict[str, float]]] = [rot_dots]
        else:
            lines = []
            for d in rot_dots:
                if not lines:
                    lines.append([d])
                else:
                    line_min_y = min(item["ry"] for item in lines[-1])
                    if (d["ry"] - line_min_y) <= (p_dot * 2.80):
                        lines[-1].append(d)
                    else:
                        lines.append([d])
            for l in lines:
                l.sort(key=lambda d: d["rx"])

        # Drop stray "lines" made of 1-2 isolated dots (paper texture, dust, page edge)
        # when the frame clearly contains real Braille lines.
        if len(lines) > 1 and max(len(l) for l in lines) >= 6:
            lines = [l for l in lines if len(l) >= 3]

        # Drop "flat" lines (every dot on one horizontal row) such as the printed letter labels
        # under a Braille chart, when other lines show real multi-row cells.
        if len(lines) > 1:
            def _spread(l: List[Dict[str, float]]) -> float:
                return max(d["ry"] for d in l) - min(d["ry"] for d in l)
            if any(_spread(l) >= 0.8 * p_dot for l in lines):
                lines = [l for l in lines if not (len(l) >= 3 and _spread(l) < 0.35 * p_dot)]

        cell_pitch_default = p_dot * 2.4
        decoded_lines: List[Dict[str, Any]] = []
        word_idx = 0

        for line_idx, line_dots in enumerate(lines):
            if len(line_dots) < 1:
                continue

            cur_line_cells: List[Dict[str, Any]] = []
            cur_line_boxes: List[Dict[str, Any]] = []

            # 2. Fit best top-row baseline y0 such that y0, y0+p_dot, y0+2*p_dot maximize dot fit
            ys = np.array([d["ry"] for d in line_dots], dtype=np.float64)
            y_cands: List[Tuple[float, float]] = []
            for y_anchor in ys:
                for offset in (0, 1, 2):
                    cand_y0 = y_anchor - offset * p_dot
                    row_dists = np.min(
                        np.abs(ys[:, None] - (cand_y0 + np.array([0.0, p_dot, 2.0 * p_dot])[None, :])),
                        axis=1,
                    )
                    y_cands.append((float(np.sum(row_dists)), float(cand_y0)))
            min_err = min(e for e, _ in y_cands)
            # A line using only two dot rows fits "rows 1-2" and "rows 2-3" equally well, and
            # noise used to pick rows 2-3 (turning a-j into , ; : . punctuation). Almost every
            # letter has a top-row dot (1 or 4), so among near-equal fits prefer the one whose
            # topmost dots are row 1 (i.e. the largest y0).
            # Only break genuine ties: the alternative must fit essentially as well (and fit well).
            slack = 0.03 * p_dot * len(ys) + 1.0
            good_fit = min_err <= 0.15 * p_dot * len(ys)
            if good_fit:
                best_y0 = max(y0 for e, y0 in y_cands if e <= min_err + slack)
            else:
                best_y0 = min(y_cands)[1]

            # 3. Cluster horizontal dot positions into vertical sub-columns
            line_dots.sort(key=lambda d: d["rx"])
            sub_cols: List[List[Dict[str, float]]] = []
            for d in line_dots:
                if not sub_cols:
                    sub_cols.append([d])
                else:
                    mean_x = float(np.mean([item["rx"] for item in sub_cols[-1]]))
                    if abs(d["rx"] - mean_x) <= p_dot * 0.45:
                        sub_cols[-1].append(d)
                    else:
                        sub_cols.append([d])

            col_xs = [float(np.mean([d["rx"] for d in sc])) for sc in sub_cols]

            # Find paired columns (dx in [0.82 * p_dot, 1.18 * p_dot]) to anchor cell grid.
            # Intra-cell column gap is strictly 1.0 * p_dot, whereas inter-cell gap is >= 1.4 * p_dot.
            pairs: List[Tuple[float, float]] = []
            i = 0
            while i < len(col_xs) - 1:
                dx = col_xs[i + 1] - col_xs[i]
                if 0.82 * p_dot <= dx <= 1.18 * p_dot:
                    pairs.append((col_xs[i], col_xs[i + 1]))
                    i += 2
                else:
                    i += 1

            col_w = [len(sc) for sc in sub_cols]
            x0, p_cell, p_col = cls._fit_cell_lattice(col_xs, col_w, p_dot, pairs)

            # Assign each dot to (cell_k, col in {0, 1}, row in {0, 1, 2})
            # Decision boundary between cell k and k+1 is midway between col 1 and next col 0:
            # col 1 is at 1.0 * p_col, next col 0 is at p_cell. Midpoint = (p_cell + p_col) / 2
            half_gap = 0.5 * (p_cell - p_col)
            cells_bits: Dict[int, List[int]] = {}
            for d in line_dots:
                dx = d["rx"] - x0
                k = int(math.floor((dx + half_gap) / p_cell))
                x_cell = x0 + k * p_cell
                rem = d["rx"] - x_cell
                col = 0 if rem < (0.5 * p_col) else 1
                row = int(round((d["ry"] - best_y0) / p_dot))
                row = max(0, min(2, row))
                bits = cells_bits.setdefault(k, [0, 0, 0, 0, 0, 0])
                bits[col * 3 + row] = 1

            line_str = ""
            is_number = False
            is_capital = False
            prev_k: Optional[int] = None

            for k in sorted(cells_bits.keys()):
                if prev_k is not None and (k - prev_k) > 1:
                    line_str += " "
                    is_number = False
                    is_capital = False
                    word_idx += 1
                prev_k = k

                bits = cells_bits[k]
                bit_str = "".join(str(b) for b in bits)
                char = cls.BINARY_DICT.get(bit_str, UNKNOWN_CELL_CHAR)
                active_dots = [idx + 1 for idx, b in enumerate(bits) if b == 1]
                dots_str = "Dots " + "-".join(str(d) for d in active_dots) if active_dots else "No dots"

                # Compute unrotated visual bounding box center
                rx_c = x0 + k * p_cell + 0.5 * p_dot
                ry_c = best_y0 + p_dot
                dx_c = rx_c - cx0
                dy_c = ry_c - cy0
                rad_pos = math.radians(skew_deg)
                ux = dx_c * math.cos(rad_pos) - dy_c * math.sin(rad_pos) + cx0
                uy = dx_c * math.sin(rad_pos) + dy_c * math.cos(rad_pos) + cy0

                if char == "#":
                    is_number = True
                    cur_line_boxes.append({"x": ux, "y": uy, "w": p_dot * 1.6, "h": p_dot * 2.6, "label": "#"})
                    cur_line_cells.append({"char": "#", "kind": "indicator", "dots": active_dots, "dots_str": dots_str, "word_index": word_idx, "line_index": line_idx})
                    continue
                if char == "^":
                    is_capital = True
                    cur_line_boxes.append({"x": ux, "y": uy, "w": p_dot * 1.6, "h": p_dot * 2.6, "label": "^"})
                    cur_line_cells.append({"char": "^", "kind": "indicator", "dots": active_dots, "dots_str": dots_str, "word_index": word_idx, "line_index": line_idx})
                    continue
                if char == "" or char == " ":
                    continue

                if char == UNKNOWN_CELL_CHAR:
                    out_c = UNKNOWN_CELL_CHAR
                    kind = "unknown"
                    cap_flag = False
                elif is_number:
                    out_c = cls.NUM_MAP.get(char, char)
                    if not (out_c.isdigit() or out_c in {".", ","}):
                        is_number = False
                        kind = "char"
                    else:
                        kind = "digit" if out_c.isdigit() else "char"
                    cap_flag = False
                else:
                    if is_capital and char.isalpha():
                        out_c = char.upper()
                        is_capital = False
                        cap_flag = True
                    else:
                        out_c = char
                        cap_flag = False
                    kind = "char"

                line_str += out_c
                cur_line_boxes.append({"x": ux, "y": uy, "w": p_dot * 1.6, "h": p_dot * 2.6, "label": out_c})
                cur_line_cells.append({
                    "char": out_c,
                    "kind": kind,
                    "capitalized": cap_flag,
                    "dots": active_dots,
                    "dots_str": dots_str,
                    "word_index": word_idx,
                    "line_index": line_idx,
                    "x": ux,
                    "y": uy,
                })

            clean_l = " ".join(line_str.split()).strip()
            # Boundary phantom cell pruning (e.g. stray margin dot producing '? RAIN' or 'HOME ?'):
            words_in_line = clean_l.split()
            if len(words_in_line) >= 2:
                # Check leading word: if single-char unknown/punctuation and next word is a multi-letter word
                if words_in_line[0] in {UNKNOWN_CELL_CHAR, "?", "'", ",", ".", "!"} and len(words_in_line[1]) >= 2:
                    stray_char = words_in_line[0]
                    words_in_line = words_in_line[1:]
                    clean_l = " ".join(words_in_line)
                    for idx, ev in enumerate(cur_line_cells):
                        if ev.get("char") == stray_char:
                            cur_line_cells.pop(idx)
                            if idx < len(cur_line_boxes):
                                cur_line_boxes.pop(idx)
                            break
                # Check trailing word: if single-char unknown/punctuation and previous word is a multi-letter word
                if len(words_in_line) >= 2 and words_in_line[-1] in {UNKNOWN_CELL_CHAR, "?", "'", ",", ".", "!"} and len(words_in_line[-2]) >= 2:
                    stray_char = words_in_line[-1]
                    words_in_line = words_in_line[:-1]
                    clean_l = " ".join(words_in_line)
                    for idx in range(len(cur_line_cells) - 1, -1, -1):
                        ev = cur_line_cells[idx]
                        if ev.get("char") == stray_char:
                            cur_line_cells.pop(idx)
                            if idx < len(cur_line_boxes):
                                cur_line_boxes.pop(idx)
                            break

            if clean_l:
                decoded_lines.append({
                    "line_str": clean_l,
                    "cells": cur_line_cells,
                    "boxes": cur_line_boxes,
                    "dots": line_dots,
                })
            word_idx += 1

        # Multi-line noise filtering: discard non-Braille phantom lines
        valid_lines: List[Dict[str, Any]] = []
        for l_rec in decoded_lines:
            l_str = l_rec["line_str"]
            letters = sum(1 for c in l_str if c.isalnum())
            noise = sum(1 for c in l_str if not c.isalnum() and not c.isspace())
            # Rule 1: Must contain at least one letter or digit
            if letters == 0:
                continue
            # Rule 2: Cannot be dominated by noise / unknown cells
            if noise > letters:
                continue
            valid_lines.append({**l_rec, "letters": letters})

        # Rule 3: Dominant Braille line selection
        # If there is a dominant line (e.g. >= 3 letters), filter out stray 1-2 letter fragments from background noise
        if valid_lines:
            max_letters = max(vl["letters"] for vl in valid_lines)
            if max_letters >= 3:
                valid_lines = [vl for vl in valid_lines if vl["letters"] >= 3 or vl["letters"] >= (max_letters * 0.6)]

        line_strings = [vl["line_str"] for vl in valid_lines]
        cell_events = []
        debug_boxes = []
        for vl in valid_lines:
            cell_events.extend(vl["cells"])
            debug_boxes.extend(vl["boxes"])

        final_text = "\n".join(line_strings).strip()
        if not final_text:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": [], "boxes": debug_boxes}

        return {
            "readable": True,
            "reason": "ok",
            "text": final_text,
            "cells": cell_events,
            "boxes": debug_boxes,
            "skew_deg": skew_deg,
            "dot_pitch": p_dot,
            "dot_diameter": dot_diam,
        }

    def braille_presence_confidence(self, image: np.ndarray, max_side: Optional[int] = 640) -> float:
        """
        Fast heuristic confidence score [0.0, 1.0] for Braille page presence.

        Heuristics applied (each contributes independently):
          1. Image quality gate: brightness p90 > 55, Laplacian var > 4 — returns 0 immediately.
          2. Minimum 4 dots required — returns 0 immediately.
          3. Dot-count density gate: rejects frames where dot_count exceeds
             max(2000, sw*sh/50) — catches random-noise frames with tens of thousands
             of small blobs while ALLOWING large real Braille pages (1000-1500 dots).
          4. Dot-diameter consistency: score = max(0, 1 - std_d/med_d / 0.8).
             Polka-dot grids (perfectly uniform) score high here too, so this alone
             is not sufficient.
          5. Pitch-ratio gate: nearest-neighbor median / dot_diameter must be in
             [1.2, 7.0] — rejects blobs too tightly packed or too sparse.
          6. Braille-lattice asymmetry: Braille cells have 2 columns × 3 rows, so
             vertical neighbors appear at ~p and ~2p while horizontal intra-cell
             neighbors appear only at ~p inside a cell, with an inter-cell gap at
             ~1.5-2p. A polka-dot grid has 4-way cross neighbors (N, S, E, W) at
             exactly pitch p. We count the fraction of dots with ≥3 strict 4-way
             cross neighbors — high values flag polka-dot imposters.
          7. Grid regularity: fraction of nearest-neighbor distances within ±40%
             of median pitch. Contributes to final score.

        Returns confidence in [0.0, 1.0]. Caller decides threshold (default 0.60).
        """
        if image is None or cv2 is None or image.size == 0:
            return 0.0
        try:
            ok, _reason, _lap = self.check_image_quality(image)
            if not ok:
                return 0.0

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
            # Contrast and dark-pixel distribution gates:
            # 1. Reject computer screens / non-paper frames (e.g. test_real_cam.jpg) where dark pixels dominate
            if float(np.mean(gray < 90)) > 0.15:
                return 0.0

            # 2. Reject high-frequency noise frames where std(gray - blur) is abnormally high
            # Robust (median-based) noise estimate: sensor/random noise is high everywhere, but
            # crisp printed content (chart boxes, ink letters) only spikes at its edges. The old
            # std-based measure rejected sharp, high-contrast photos of printed Braille charts.
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            hf = np.abs(gray.astype(np.float32) - blur.astype(np.float32))
            if 1.4826 * float(np.median(hf)) > 8.0:
                return 0.0

            h, w = image.shape[:2]
            if max_side and max(w, h) > max_side:
                scale = float(max_side) / float(max(w, h))
                small = cv2.resize(image, (int(w * scale), int(h * scale)))
            else:
                small = image

            dots, dot_diam = self.detect_dots(small)
            if len(dots) < 6:
                dots_e, diam_e = self.detect_dots(self.enhance_contrast(small))
                if len(dots_e) > len(dots):
                    dots, dot_diam = dots_e, diam_e
            n = len(dots)
            if n < 4 or n > 2500:
                return 0.0

            diams = np.array([d["d"] for d in dots], dtype=np.float64)
            med_d = float(np.median(diams))
            if med_d < 1.8:
                return 0.0
            std_d = float(np.std(diams))
            diam_cv = std_d / max(1e-3, med_d)
            # Score 4: diameter consistency (high std → low score)
            diam_score = float(max(0.0, 1.0 - diam_cv / 0.8))

            pts = np.array([[d["x"], d["y"]] for d in dots], dtype=np.float64)

            # Subsample to 400 for speed on large images
            eval_n = min(n, 400)
            pts_eval = pts[:eval_n]

            nn_dists = []
            for i in range(eval_n):
                diffs = pts_eval - pts_eval[i]
                dists = np.hypot(diffs[:, 0], diffs[:, 1])
                valid = dists[dists > 3.0]
                if len(valid):
                    nn_dists.append(float(np.min(valid)))

            if len(nn_dists) < 4:
                return 0.0

            med_pitch = float(np.median(nn_dists))
            pitch_ratio = med_pitch / max(1.0, med_d)
            # Gate 5: pitch-ratio range (extended to 0.85 to accommodate diffuse flat-lit dot dilation)
            if not (0.85 <= pitch_ratio <= 7.5):
                return 0.0

            # Score 7: grid regularity (fraction within ±40% of median pitch)
            regular = sum(1 for d in nn_dists if 0.60 * med_pitch <= d <= 1.40 * med_pitch)
            regularity_score = regular / max(1, len(nn_dists))

            # Score 6: polka-dot cross-neighbor penalty
            # A polka-dot / regular grid has N, S, E, W neighbors all at exactly pitch p.
            # Braille never has E neighbour at pitch p (the inter-column gap is 1.5-2p).
            tol = 0.30 * med_pitch
            cross_count = 0
            for i in range(eval_n):
                diffs = pts_eval - pts_eval[i]
                dx = diffs[:, 0]
                dy = diffs[:, 1]
                has_e = bool(np.any((np.abs(dx - med_pitch) < tol) & (np.abs(dy) < tol)))
                has_w = bool(np.any((np.abs(dx + med_pitch) < tol) & (np.abs(dy) < tol)))
                has_n = bool(np.any((np.abs(dy - med_pitch) < tol) & (np.abs(dx) < tol)))
                has_s = bool(np.any((np.abs(dy + med_pitch) < tol) & (np.abs(dx) < tol)))
                if has_e and has_w and has_n and has_s:
                    cross_count += 1
            cross_ratio = cross_count / max(1, eval_n)
            # Polka dots: cross_ratio ~0.5-0.9; Braille: ~0.0
            polka_penalty = float(min(1.0, cross_ratio * 2.0))

            # Weighted confidence
            conf = (
                0.25 * diam_score
                + 0.50 * regularity_score
                + 0.25 * max(0.0, 1.0 - polka_penalty)
            )
            # Soft-gate: if regularity < 35% the detection is very weak regardless
            if regularity_score < 0.35:
                conf *= regularity_score / 0.35

            logger.debug(
                "braille_presence_confidence: n=%d diam_cv=%.2f pitch_ratio=%.1f "
                "regularity=%.2f cross_ratio=%.2f → conf=%.2f",
                n, diam_cv, pitch_ratio, regularity_score, cross_ratio, conf,
            )
            return float(min(1.0, max(0.0, conf)))
        except Exception as exc:
            logger.debug("braille_presence_confidence error: %s", exc)
            return 0.0

    def detect_braille_presence(self, image: np.ndarray, confidence_threshold: float = 0.28) -> bool:
        """
        Returns True when braille_presence_confidence() >= confidence_threshold (default 0.28).
        Lowered from 0.42 to reliably detect embossed Braille on physical paper under diffuse room light.
        Backward-compatible bool interface used by ScannerMode detection worker.
        """
        return self.braille_presence_confidence(image) >= confidence_threshold

    def assess_braille_distance(self, image: np.ndarray, confidence_threshold: float = 0.28) -> Dict[str, Any]:
        """
        Real-time distance and readiness assessment for handheld camera aiming:
        Evaluates dot count, median dot diameter, and pitch to guide user:
          - "no_braille": insufficient dots or low presence confidence
          - "too_close": dot diameter > 18px or pitch > 55px (camera closer than ~8cm)
          - "too_far": dot diameter < 2.5px or pitch < 11px (camera farther than ~24cm)
          - "ok": dot diameter 2.5px..18px and confidence >= threshold (nominally 10-22cm)
        """
        if image is None or cv2 is None or getattr(image, "size", 0) == 0:
            return {"state": "no_braille", "confidence": 0.0, "dot_diameter": 0.0, "dot_pitch": 0.0, "dot_count": 0}

        conf = self.braille_presence_confidence(image)
        if conf < confidence_threshold:
            return {"state": "no_braille", "confidence": conf, "dot_diameter": 0.0, "dot_pitch": 0.0, "dot_count": 0}

        h, w = image.shape[:2]
        if max(w, h) > 640:
            scale = 640.0 / float(max(w, h))
            small = cv2.resize(image, (int(w * scale), int(h * scale)))
        else:
            small = image

        dots, dot_diam = self.detect_dots(small)
        if len(dots) < 6:
            dots_e, diam_e = self.detect_dots(self.enhance_contrast(small))
            if len(dots_e) > len(dots) and len(dots_e) <= 2500 and diam_e >= 2.0:
                dots, dot_diam = dots_e, diam_e
        if len(dots) < 3 or len(dots) > 2500:
            return {"state": "no_braille", "confidence": conf, "dot_diameter": 0.0, "dot_pitch": 0.0, "dot_count": len(dots)}

        skew_deg, p_dot = self._estimate_skew_and_pitch(dots, dot_diam)

        # Distance thresholding based on physical Braille geometry (8px to 55px dot pitch)
        if dot_diam < 2.0 or p_dot < 8.0:
            state = "too_far"
        elif dot_diam > 18.0 or p_dot > 55.0:
            state = "too_close"
        else:
            state = "ok"

        return {
            "state": state,
            "confidence": conf,
            "dot_diameter": dot_diam,
            "dot_pitch": p_dot,
            "dot_count": len(dots),
        }

    def _record_history(self, frame: np.ndarray, readable: bool, text: str, reason: str) -> None:
        if self._deferred_history is not None:
            self._deferred_history.append((frame, readable, text, reason))
        else:
            self._save_scan_history(frame, readable, text, reason)

    @staticmethod
    def _score_result(res: Dict[str, Any]) -> float:
        """Rank a decode result: valid letters count, unknown cells are heavily penalised."""
        if not res or not res.get("readable") or not str(res.get("text", "")).strip():
            return -1.0
        chars = [c for c in str(res.get("text", "")) if not c.isspace()]
        valid = sum(1 for c in chars if c.isalnum())
        unknown = sum(1 for c in chars if c == UNKNOWN_CELL_CHAR)
        punct = len(chars) - valid - unknown
        return valid - 3.0 * unknown - 0.5 * punct

    # Strategy order for scan_frame(): (full_frame, detector)
    #   "shadow"   : side-lit embossed dots (bright cap + one-directional shadow), see detect_shadow_dots()
    #   "auto"     : classic DoG detector, ink + embossed
    #   "embossed" : classic DoG detector, embossed_only=True (rejects nearby handwriting/print)
    SCAN_STRATEGIES: Tuple[Tuple[bool, str], ...] = (
        (False, "shadow"),
        (False, "auto"),
        (True, "auto"),
        (True, "shadow"),
        (False, "embossed"),
        (True, "embossed"),
    )

    @staticmethod
    def _keep_center_line(res: Dict[str, Any], frame_h: int) -> Dict[str, Any]:
        """
        The user aims ONE line into the center box. When a read spans several lines
        (full-frame fallback, or a tall box catching a neighbour), keep only the readable
        line closest to the frame's vertical center and drop the rest, so lines above and
        below the box are never read out. Set BRAILCE_SCAN_ALL_LINES=1 to read every line.
        """
        if os.environ.get("BRAILCE_SCAN_ALL_LINES", "") == "1":
            return res
        if not res or not res.get("readable"):
            return res
        cells = list(res.get("cells") or [])
        lines: Dict[Any, List[Dict[str, Any]]] = {}
        for c in cells:
            if "y" not in c and c.get("kind") != "indicator":
                return res  # no positions (YOLO path): leave untouched
            lines.setdefault(c.get("line_index", 0), []).append(c)
        if len(lines) <= 1:
            return res

        def _line_text(line_cells: List[Dict[str, Any]]) -> str:
            out: List[str] = []
            prev_w = None
            for c in line_cells:
                if c.get("kind") == "indicator":
                    continue
                w = c.get("word_index", 0)
                if prev_w is not None and w != prev_w:
                    out.append(" ")
                out.append(str(c.get("char", "")))
                prev_w = w
            return " ".join("".join(out).split())

        center = 0.5 * float(frame_h)
        best_key, best_dist, best_text = None, None, ""
        for key, lc in lines.items():
            text = _line_text(lc)
            chars = [ch for ch in text if not ch.isspace()]
            if not chars or not any(ch.isalnum() for ch in chars):
                continue
            if sum(1 for ch in chars if ch == UNKNOWN_CELL_CHAR) / len(chars) > 0.30:
                continue
            ys = sorted(float(c["y"]) for c in lc if "y" in c)
            if not ys:
                continue
            dist = abs(ys[len(ys) // 2] - center)
            if best_dist is None or dist < best_dist:
                best_key, best_dist, best_text = key, dist, text
        if best_key is None:
            return res

        keep = lines[best_key]
        pts = [(float(c["x"]), float(c["y"])) for c in keep if "y" in c]
        res["all_lines_text"] = res.get("text", "")
        res["cells"] = keep
        res["boxes"] = [b for b in (res.get("boxes") or [])
                        if any(abs(float(b["x"]) - x) < 1.0 and abs(float(b["y"]) - y) < 1.0 for x, y in pts)]
        res["text"] = best_text
        res["words"] = [t for t in best_text.split() if t]
        res["center_line_only"] = True
        return res

    @staticmethod
    def _is_clean(res: Optional[Dict[str, Any]]) -> bool:
        if not res or not res.get("readable"):
            return False
        text = str(res.get("text", ""))
        return UNKNOWN_CELL_CHAR not in text and sum(c.isalnum() for c in text) >= 2

    def scan_frame(self, image: np.ndarray, strategies: Optional[Sequence[Tuple[bool, Any]]] = None) -> Dict[str, Any]:
        """
        Decode one camera frame, handling ink-printed AND embossed Braille.

        - Ink dots are solid dark blobs; embossed_only=True erases them, so the scanner
          used to be unable to read printed Braille.
        - Real embossed paper under a side light shows each dot as a small bright cap with
          a long shadow; the classic DoG detector drowns in paper texture there, so the
          "shadow" detector handles it.
        Tries the strategies, keeps the best-scoring result, prefers the line inside the
        center box over a multi-line full-frame read, and saves one debug image per scan.
        """
        strategies = tuple(strategies or self.SCAN_STRATEGIES)
        self._shadow_cache = None  # shadow detection is per full frame; reuse it across strategies
        results: List[Tuple[float, Dict[str, Any], Optional[Tuple[Any, ...]]]] = []
        first_fail: Optional[Dict[str, Any]] = None
        first_fail_hist: Optional[Tuple[Any, ...]] = None
        skip_classic = False
        for idx, (full, kind) in enumerate(strategies):
            if isinstance(kind, bool):  # legacy (full_frame, embossed_only)
                kind = "embossed" if kind else "auto"
            if skip_classic and kind != "shadow":
                continue
            self._deferred_history = []
            try:
                if kind == "shadow":
                    res = self.process_shadow(image, full_frame=full)
                else:
                    res = self.process_image(image, full_frame=full, embossed_only=(kind == "embossed"))
            finally:
                hist = self._deferred_history[-1] if self._deferred_history else None
                self._deferred_history = None
            res["strategy"] = {"full_frame": full, "embossed_only": kind == "embossed", "detector": kind}
            res = self._keep_center_line(res, image.shape[0])
            score = self._score_result(res)
            if score < 0:
                if first_fail is None or (first_fail.get("reason") == "no_braille" and res.get("reason") != "no_braille"):
                    first_fail, first_fail_hist = res, hist
                # Classic quality-gate failures are identical for every classic strategy.
                if kind != "shadow" and res.get("reason") == "blurry":
                    skip_classic = True
            else:
                results.append((score, res, hist))
            # The first three (shadow ROI, classic ROI, classic full frame) always compete;
            # after that stop as soon as we hold a clean read.
            if idx >= 2 and any(self._is_clean(r) for _, r, _ in results):
                break

        if not results:
            if first_fail_hist is not None:
                self._save_scan_history(*first_fail_hist)
            return first_fail or {"readable": False, "reason": "no_braille", "text": "", "cells": []}

        results.sort(key=lambda t: t[0], reverse=True)
        _, chosen, chosen_hist = results[0]

        # The user aims one line inside the center box. If the winner is a multi-line
        # full-frame read but a clean ROI read exists, read what was aimed at.
        def _lines(r: Dict[str, Any]) -> int:
            return len([ln for ln in str(r.get("text", "")).split("\n") if ln.strip()])

        def _core(t: str) -> str:
            return "".join(c for c in t if c.isalnum())

        if chosen.get("strategy", {}).get("full_frame"):
            roi_clean = [(sc, r, h) for sc, r, h in results
                         if not r.get("strategy", {}).get("full_frame") and self._is_clean(r)]
            if roi_clean and _lines(roi_clean[0][1]) < _lines(chosen):
                roi_core = _core(str(roi_clean[0][1].get("text", "")))
                full_lines = [_core(ln) for ln in str(chosen.get("text", "")).split("\n")]
                # ROI read is just a clipped piece of a longer full-frame line -> keep full frame
                clipped = any(roi_core in fl and len(fl) > len(roi_core) for fl in full_lines)
                if not clipped:
                    _, chosen, chosen_hist = roi_clean[0]

        if chosen_hist is not None:
            self._save_scan_history(*chosen_hist)
        self._shadow_cache = None
        return chosen

    # =========================================================================
    # Side-lit embossed Braille ("shadow" detector)
    # =========================================================================

    @staticmethod
    def _shadow_caps(d: np.ndarray, thr: float) -> List[Dict[str, float]]:
        """Bright caps above `thr` in the background-subtracted map `d`, with local shadow vector."""
        h, w = d.shape[:2]
        nb, _, sb, cb = cv2.connectedComponentsWithStats((d > thr).astype(np.uint8), connectivity=8)
        darkw = np.clip(-d - 0.5 * thr, 0, None)
        caps: List[Dict[str, float]] = []
        for i in range(1, nb):
            a = float(sb[i, cv2.CC_STAT_AREA])
            bw = float(sb[i, cv2.CC_STAT_WIDTH])
            bh = float(sb[i, cv2.CC_STAT_HEIGHT])
            if a < 4 or a > 0.002 * h * w or max(bw, bh) > 0.06 * max(h, w):
                continue
            x, y = float(cb[i][0]), float(cb[i][1])
            L = max(bw, bh)
            R = int(max(6, 0.9 * L))
            x0, x1 = max(0, int(x) - R), min(w, int(x) + R + 1)
            y0, y1 = max(0, int(y) - R), min(h, int(y) + R + 1)
            win = darkw[y0:y1, x0:x1]
            s = float(win.sum())
            vx = vy = 0.0
            if s > 0:
                yy, xx = np.mgrid[y0:y1, x0:x1]
                vx = float((win * xx).sum() / s - x)
                vy = float((win * yy).sum() / s - y)
            caps.append({"x": x, "y": y, "a": a, "L": L, "vx": vx, "vy": vy, "s": s})
        return caps

    @staticmethod
    def _shadow_keep(caps: List[Dict[str, float]], d: np.ndarray, thr: float, mx: float, my: float) -> List[Dict[str, float]]:
        """Keep caps with a deep shadow along the page-wide light direction (mx, my)."""
        h, w = d.shape[:2]
        dots: List[Dict[str, float]] = []
        for c in caps:
            ts = np.linspace(0.4 * c["L"], 1.6 * c["L"], 12)
            px = np.clip((c["x"] + ts * mx).astype(int), 0, w - 1)
            py = np.clip((c["y"] + ts * my).astype(int), 0, h - 1)
            if float(d[py, px].min()) < -thr:
                dots.append({"x": c["x"], "y": c["y"], "d": 2.0 * math.sqrt(c["a"] / math.pi), "score": c["s"] + c["a"]})
        return dots

    @classmethod
    def detect_shadow_dots_ex(cls, image: np.ndarray) -> Tuple[List[Dict[str, float]], float, List[Dict[str, float]]]:
        """
        detect_shadow_dots() plus a list of WEAK candidates: caps that pass the same test at
        half the threshold (and are not already strong dots). A real dot whose cap or shadow
        is partly swallowed by a neighbouring dot's shadow (e.g. dot 4 right after dot 1)
        lands here; process_shadow() keeps only the weak candidates that sit on the Braille
        grid of the strong dots, so paper texture never gets in.

        The background is a median over ~1/15 of the image's short side. On a slim center
        strip that kernel shrinks to one dot pitch and the dots' own shadows become
        "background", so callers must pass the FULL frame and crop the result afterwards.
        """
        if image is None or cv2 is None or getattr(image, "size", 0) == 0:
            return [], 0.0, []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        h, w = gray.shape[:2]
        k = max(21, (min(h, w) // 15) | 1)
        bg = cv2.medianBlur(gray, k).astype(np.float32)
        d = cv2.GaussianBlur(gray.astype(np.float32) - bg, (0, 0), 1.2)
        sigma = 1.4826 * float(np.median(np.abs(d - np.median(d))))
        thr = max(15.0, 6.0 * sigma)
        caps = cls._shadow_caps(d, thr)

        # Pass 1: page-wide shadow direction from caps with a clear one-sided shadow
        clear = [c for c in caps if c["s"] > 0 and math.hypot(c["vx"], c["vy"]) > 0.25 * c["L"]]
        if len(clear) < 3:
            return [], 0.0, []
        ux = np.array([c["vx"] for c in clear])
        uy = np.array([c["vy"] for c in clear])
        nrm = np.hypot(ux, uy)
        ux, uy = ux / nrm, uy / nrm
        wts = np.array([c["s"] for c in clear])
        mx = float((ux * wts).sum() / wts.sum())
        my = float((uy * wts).sum() / wts.sum())
        n = math.hypot(mx, my)
        if n < 0.5:  # shadows don't share one direction: not side-lit embossed Braille
            return [], 0.0, []
        mx, my = mx / n, my / n

        # Pass 2: keep caps with a deep shadow along that direction (a cap sitting between two
        # shadows, e.g. dot 4 right after dot 1's shadow, has no net local direction but passes here)
        dots = cls._shadow_keep(caps, d, thr, mx, my)
        if len(dots) >= 4:
            med = float(np.median([q["score"] for q in dots]))
            dots = [q for q in dots if q["score"] >= 0.25 * med]  # specks / stray marks have tiny shadows
        if not dots:
            return [], 0.0, []
        diam = float(np.median([q["d"] for q in dots]))

        # Weak pass at half threshold, same light direction; drop anything already found.
        weak_all = cls._shadow_keep(cls._shadow_caps(d, 0.5 * thr), d, 0.5 * thr, mx, my)
        weak: List[Dict[str, float]] = []
        if weak_all:
            sx = np.array([q["x"] for q in dots])
            sy = np.array([q["y"] for q in dots])
            for q in weak_all:
                if float(np.min(np.hypot(sx - q["x"], sy - q["y"]))) > 0.6 * diam * 1.8:
                    weak.append(q)
        return dots, diam, weak

    @classmethod
    def detect_shadow_dots(cls, image: np.ndarray) -> Tuple[List[Dict[str, float]], float]:
        """
        Detect embossed dots photographed under a side light (desk lamp / window), where
        each dot is a small bright cap followed by a long dark shadow, all shadows pointing
        the same way. Paper texture (+/-10 grey levels) and handwriting (dark, no bright cap)
        are rejected because a dot must be a strong bright cap WITH a deep shadow along the
        page-wide light direction.
        """
        dots, diam, _weak = cls.detect_shadow_dots_ex(image)
        return dots, diam

    @classmethod
    def _recover_grid_dots(
        cls, dots: List[Dict[str, float]], weak: List[Dict[str, float]], diam: float
    ) -> List[Dict[str, float]]:
        """
        Add weak candidates that lie exactly on the dot grid of the strong dots: same column
        or one dot pitch to the side, and 0/1/2 pitches up or down, of some strong dot within
        a cell's reach. One missed dot turns P into L, so this second chance matters; the
        grid test keeps paper specks out.
        """
        if not dots or not weak:
            return list(dots)
        _skew, p = cls._estimate_skew_and_pitch(dots, diam)
        if p <= 0:
            return list(dots)
        tol = 0.25 * p
        # A real dot photographed in the same light has a cap+shadow strength comparable to
        # its neighbours; a grid-aligned speck does not (QUIZ -> QUSZ came from a 0.35x one).
        min_score = 0.5 * float(np.median([q["score"] for q in dots]))
        sx = np.array([q["x"] for q in dots])
        sy = np.array([q["y"] for q in dots])
        out = list(dots)
        for q in sorted(weak, key=lambda z: z["score"], reverse=True):
            if q["score"] < min_score:
                continue
            dx = np.abs(sx - q["x"])
            dy = np.abs(sy - q["y"])
            near = np.hypot(dx, dy)
            if float(near.min()) < 0.6 * p:
                continue  # duplicate of a strong dot
            col_ok = (dx < tol) | (np.abs(dx - p) < tol)
            row_ok = (dy < tol) | (np.abs(dy - p) < tol) | (np.abs(dy - 2.0 * p) < tol)
            if bool(np.any(col_ok & row_ok & (near <= 2.5 * p))):
                out.append({"x": q["x"], "y": q["y"], "d": q["d"], "score": q["score"], "recovered": True})
                sx = np.append(sx, q["x"])
                sy = np.append(sy, q["y"])
        return out

    def process_shadow(self, image: np.ndarray, full_frame: bool = False) -> Dict[str, Any]:
        """
        Decode side-lit embossed Braille with the shadow detector.

        Detection always runs on the FULL frame (the background estimate needs more than
        a strip's worth of paper); the center-strip mode then keeps only the dots inside the
        box, so aiming one line into the box and reading the whole page use the same dots.
        """
        fail: Dict[str, Any] = {"readable": False, "reason": "no_braille", "text": "", "cells": [], "dots": []}
        if image is None or getattr(image, "size", 0) == 0 or cv2 is None:
            return fail
        h, w = image.shape[:2]
        if full_frame:
            x0, y0, crop = 0, 0, image
        else:
            rw, rh = int(w * ROI_W_FRAC), int(h * ROI_H_FRAC)
            x0, y0 = (w - rw) // 2, (h - rh) // 2
            crop = image[y0:y0 + rh, x0:x0 + rw]

        cache = getattr(self, "_shadow_cache", None)
        if cache is not None and cache[0] is image and cache[1] == image.shape:
            all_dots, diam, weak = cache[2]
        else:
            all_dots, diam, weak = self.detect_shadow_dots_ex(image)
            self._shadow_cache = (image, image.shape, (all_dots, diam, weak))
        all_dots = self._recover_grid_dots(all_dots, weak, diam)

        ch, cw = crop.shape[:2]
        dots = [
            {**q, "x": q["x"] - x0, "y": q["y"] - y0}
            for q in all_dots
            if x0 <= q["x"] < x0 + cw and y0 <= q["y"] < y0 + ch
        ]

        dbg = image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if not full_frame:
            cv2.rectangle(dbg, (x0, y0), (x0 + crop.shape[1], y0 + crop.shape[0]), (255, 200, 0), 2)
        for q in dots:
            cv2.circle(dbg, (int(q["x"] + x0), int(q["y"] + y0)), max(3, int(q["d"] / 2)), (0, 255, 0), 1)

        if len(dots) < 3:
            cv2.putText(dbg, "UNREADABLE: no_braille (shadow)", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2)
            self._record_history(dbg, False, "", "no_braille")
            fail["dots"] = [{"x": q["x"] + x0, "y": q["y"] + y0, "d": q["d"]} for q in dots]
            return fail

        res = self.decode_dots_to_text(dots, diam, crop.shape, single_line=False)
        text = str(res.get("text", "")).strip()
        chars = [c for c in text if not c.isspace()]
        unknown = sum(1 for c in chars if c == UNKNOWN_CELL_CHAR)
        if not text or not any(c.isalnum() for c in chars) or (chars and unknown / len(chars) > 0.30):
            res.update({"readable": False, "reason": "no_braille", "text": "", "cells": [], "boxes": []})
            text = ""
        readable = bool(res.get("readable")) and bool(text)

        for b in (res.get("boxes", []) if readable else []):
            b["x"] += x0
            b["y"] += y0
            bx, by = int(b["x"] - b["w"] / 2), int(b["y"] - b["h"] / 2)
            cv2.rectangle(dbg, (bx, by), (bx + int(b["w"]), by + int(b["h"])), (0, 0, 255), 2)
            if b.get("label"):
                cv2.putText(dbg, str(b["label"]), (bx, max(14, by - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        header = f"DECODED: {text}" if readable else "UNREADABLE: no_braille (shadow)"
        cv2.putText(dbg, header.replace("\n", " / "), (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                    (0, 255, 0) if readable else (0, 0, 255), 2)
        self._record_history(dbg, readable, text, "ok" if readable else "no_braille")

        for c in (res.get("cells", []) if readable else []):
            if "y" in c:
                c["x"] += x0
                c["y"] += y0
        for q in dots:
            q["x"] += x0
            q["y"] += y0
        res["dots"] = dots
        res["text"] = text
        res["words"] = [t for t in text.split() if t]
        res["readable"] = readable
        res["reason"] = "ok" if readable else "no_braille"
        if not readable:
            res["cells"] = []
        logger.info("Scanner result (shadow): dots=%d readable=%s text=%r", len(dots), readable, text)
        return res

    @staticmethod
    def _save_scan_history(target_frame: np.ndarray, readable: bool, text: str, reason: str) -> None:
        """
        Saves last_scan.jpg AND timestamped scan history file (scan_YYYYMMDD_HHMMSS_<tag>.jpg).
        Caps history at the latest 200 scans to protect Raspberry Pi storage.
        Updates debug_scan/index.html so users can view all scans via any web browser.
        """
        if target_frame is None or getattr(target_frame, "size", 0) == 0:
            return
        try:
            debug_dir = os.path.abspath("debug_scan")
            os.makedirs(debug_dir, exist_ok=True)

            # 1. Update last_scan.jpg
            last_scan_path = os.path.join(debug_dir, "last_scan.jpg")
            cv2.imwrite(last_scan_path, target_frame)
            if os.name != "nt":
                try:
                    cv2.imwrite("/tmp/last_scan_debug.jpg", target_frame)
                except Exception:
                    pass

            # 2. Timestamped scan history
            now = datetime.datetime.now()
            ts_str = now.strftime("%Y%m%d_%H%M%S")
            clean_tag = re.sub(r"[^a-zA-Z0-9]", "", text).lower() if readable and text else reason
            clean_tag = clean_tag[:16] if clean_tag else "scan"
            hist_filename = f"scan_{ts_str}_{clean_tag}.jpg"
            hist_path = os.path.join(debug_dir, hist_filename)
            cv2.imwrite(hist_path, target_frame)

            # 3. Retention pruning: keep newest 200 scans
            all_scans = sorted(
                [f for f in os.listdir(debug_dir) if f.startswith("scan_") and f.endswith(".jpg")],
                reverse=True,
            )
            for old_file in all_scans[200:]:
                try:
                    os.remove(os.path.join(debug_dir, old_file))
                except Exception:
                    pass

            # 4. Generate/update index.html gallery
            active_scans = [f for f in os.listdir(debug_dir) if f.startswith("scan_") and f.endswith(".jpg")]
            active_scans.sort(reverse=True)
            cards_html = []
            for fname in active_scans:
                parts = fname.replace(".jpg", "").split("_", 3)
                date_part = parts[1] if len(parts) > 1 else ""
                time_part = parts[2] if len(parts) > 2 else ""
                tag_part = parts[3].upper() if len(parts) > 3 else "SCAN"
                formatted_time = (
                    f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]} {time_part[:2]}:{time_part[2:4]}:{time_part[4:]}"
                    if len(date_part) == 8 and len(time_part) == 6
                    else fname
                )
                cards_html.append(f"""
                <div class="card">
                    <a href="{fname}" target="_blank"><img src="{fname}" alt="{tag_part}" /></a>
                    <div class="info">
                        <span class="tag">{tag_part}</span>
                        <span class="time">{formatted_time}</span>
                    </div>
                </div>""")

            html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Brailce Scanner History</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #121212; color: #eee; margin: 0; padding: 20px; }}
h1 {{ text-align: center; color: #4CAF50; }}
p.sub {{ text-align: center; color: #aaa; margin-bottom: 24px; }}
.latest-banner {{ max-width: 800px; margin: 0 auto 30px auto; background: #1e1e1e; border: 2px solid #4CAF50; border-radius: 8px; padding: 16px; text-align: center; }}
.latest-banner img {{ max-width: 100%; border-radius: 4px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; max-width: 1200px; margin: 0 auto; }}
.card {{ background: #1e1e1e; border: 1px solid #333; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; }}
.card img {{ width: 100%; height: 180px; object-fit: contain; background: #000; }}
.card .info {{ padding: 12px; display: flex; justify-content: space-between; align-items: center; }}
.card .tag {{ font-weight: bold; color: #FFEB3B; font-size: 1.1em; }}
.card .time {{ font-size: 0.8em; color: #888; }}
</style>
</head>
<body>
<h1>Brailce Scanner Debug History</h1>
<p class="sub">Latest 30 Scans Saved</p>
<div class="latest-banner">
    <h2>Pinakabagong Scan (last_scan.jpg)</h2>
    <a href="last_scan.jpg" target="_blank"><img src="last_scan.jpg?t={now.timestamp()}" alt="Latest Scan" /></a>
</div>
<div class="grid">
{''.join(cards_html)}
</div>
</body>
</html>"""
            with open(os.path.join(debug_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(html_content)
        except Exception as exc:
            logger.debug("_save_scan_history error: %s", exc)

    def process_image(
        self,
        image: np.ndarray,
        debug_dump: bool = False,
        full_frame: Optional[bool] = None,
        embossed_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Decode physical Braille image using quality gates + classical dot-grid recognizer:
        1. Image Quality Gate: brightness p90 > 55, Laplacian var > 4.
        2. ROI / Full-frame: use_full=True by default so whole words aren't cut off.
        3. Printed Ink Suppression: masks pure black text (gray < 70) and fills with paper median.
        4. Unified Detection: detect_dots() Difference-of-Gaussians dot centroid detection.
        5. Dot Grid Assignment: decode_dots_to_text() fits 2x3 cell lattice and Grade 1 mapping.
        6. Debug Visualization: always saves debug_scan/last_scan.jpg with green dot circles,
           tight red cell boxes, and yellow decoded labels.
        """
        if image is None or getattr(image, "size", 0) == 0:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": []}

        quality_ok, reason, lap_var = self.check_image_quality(image)
        if not quality_ok:
            self._record_history(image, False, "", reason)
            logger.info("Scanner result: quality gate failed reason=%s lap_var=%.2f", reason, lap_var)
            return {"readable": False, "reason": reason, "text": "", "cells": [], "lap_var": lap_var}

        # Braille presence confidence heuristic gate: reject computer screens, terminal text, and random noise
        gray_full = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        dark_frac = float(np.mean(gray_full < 90))
        presence = self.braille_presence_confidence(image) if dark_frac <= 0.15 else 0.0
        if dark_frac <= 0.15 and presence < 0.20 and max(image.shape[:2]) > 640:
            # The fast check downsamples to 640px, which shrinks small/far dots below the
            # detector's size floor. Re-check at native resolution before rejecting the scan.
            presence = self.braille_presence_confidence(image, max_side=None)
        if dark_frac > 0.15 or presence < 0.20:
            dbg_fail = image.copy()
            if len(dbg_fail.shape) == 2:
                dbg_fail = cv2.cvtColor(dbg_fail, cv2.COLOR_GRAY2BGR)
            cv2.putText(dbg_fail, "UNREADABLE: no_braille", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2)
            self._record_history(dbg_fail, False, "", "no_braille")
            logger.info("Scanner result: no Braille detected (dark=%.2f, conf=%.2f)", dark_frac, presence)
            return {"readable": False, "reason": "no_braille", "text": "", "cells": [], "lap_var": lap_var}

        h, w = image.shape[:2]
        use_full = full_frame if full_frame is not None else (
            os.environ.get("BRAILCE_FORCE_ROI", "") != "1"
        )
        if use_full:
            roi_w, roi_h = w, h
            x0, y0 = 0, 0
            crop = image.copy()
        else:
            roi_w = int(w * ROI_W_FRAC)
            roi_h = int(h * ROI_H_FRAC)
            x0 = (w - roi_w) // 2
            y0 = (h - roi_h) // 2
            crop = image[y0 : y0 + roi_h, x0 : x0 + roi_w].copy()

        if os.environ.get("BRAILCE_SCANNER_ROT180", "0") == "1":
            crop = np.rot90(crop, 2)
        if os.environ.get("BRAILCE_SCANNER_HFLIP", "0") == "1":
            crop = np.fliplr(crop)

        # 2. Suppress pure black printed ink
        clean_crop = self.suppress_printed_ink(crop)

        # 3. Detect dots directly (preserves true geometric crescent pairs)
        dots, dot_diam = self.detect_dots(clean_crop, embossed_only=embossed_only)

        # 3b. Adaptive contrast enhancement fallback for faint dots under diffuse lighting
        if len(dots) < 8:
            enhanced_crop = self.enhance_contrast(clean_crop)
            e_dots, e_diam = self.detect_dots(enhanced_crop, embossed_only=embossed_only)
            max_allowed = max(45, len(dots) * 6) if embossed_only else 2500
            if len(e_dots) > len(dots) and len(e_dots) <= max_allowed and e_diam >= 2.0:
                dots, dot_diam = e_dots, e_diam

        # Automatic center-crop fallback if full frame produced < 2 dots
        if len(dots) < 2 and use_full and full_frame is None:
            c_roi_w = int(w * ROI_W_FRAC)
            c_roi_h = int(h * ROI_H_FRAC)
            c_x0 = (w - c_roi_w) // 2
            c_y0 = (h - c_roi_h) // 2
            c_crop = image[c_y0 : c_y0 + c_roi_h, c_x0 : c_x0 + c_roi_w].copy()
            clean_c = self.suppress_printed_ink(c_crop)
            c_dots, c_diam = self.detect_dots(clean_c, embossed_only=embossed_only)
            if 0 < len(c_dots) < 8:
                c_dots_e, c_diam_e = self.detect_dots(self.enhance_contrast(clean_c), embossed_only=embossed_only)
                if len(c_dots_e) > len(c_dots):
                    c_dots, c_diam = c_dots_e, c_diam_e
            if len(c_dots) >= 2:
                crop = c_crop
                clean_crop = clean_c
                dots, dot_diam = c_dots, c_diam
                x0, y0 = c_x0, c_y0
                roi_w, roi_h = c_roi_w, c_roi_h

        # Prepare debug visual frame
        dbg_img = crop.copy()
        if len(dbg_img.shape) == 2:
            dbg_img = cv2.cvtColor(dbg_img, cv2.COLOR_GRAY2BGR)

        # Draw green circles on detected dots
        for d in dots:
            px, py = int(round(d["x"])), int(round(d["y"]))
            r = int(max(2, round(d.get("d", dot_diam) / 2.0)))
            cv2.circle(dbg_img, (px, py), r, (0, 255, 0), 1)

        # Always save debug image even when < 2 dots or too small
        if len(dots) < 2 or (0 < dot_diam < 3.8):
            res_reason = "too_small" if (0 < dot_diam < 3.8) else "no_braille"
            cv2.putText(dbg_img, f"UNREADABLE: {res_reason}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2)
            self._record_history(dbg_img, False, "", res_reason)
            logger.info("Scanner result: dot_count=%d dot_pitch=0.00 cells_found=0 readable=False reason=%s text=''", len(dots), res_reason)
            return {
                "readable": False,
                "reason": res_reason,
                "text": "",
                "words": [],
                "cells": [],
                "boxes": [],
                "dots": [{"x": float(x0 + d["x"]), "y": float(y0 + d["y"])} for d in dots],
                "dot_pitch": 0.0,
                "lap_var": lap_var,
            }

        res = self.decode_dots_to_text(dots, dot_diam, crop.shape, single_line=False)
        res["lap_var"] = lap_var
        res["dots"] = dots

        cells = res.get("cells", [])
        boxes = res.get("boxes", [])

        # Optional secondary YOLO evaluation if enabled and model available
        if self.use_yolo_secondary and self._load_model():
            try:
                yolo_boxes = self._run_yolo_inference(crop)
                if yolo_boxes:
                    yolo_res = self.decode_boxes(yolo_boxes)
                    if yolo_res.get("readable") and len(yolo_res.get("cells", [])) >= len(cells):
                        logger.info("Secondary YOLO produced readable Braille result: %s", yolo_res.get("text"))
                        res["text"] = yolo_res["text"]
                        res["cells"] = yolo_res["cells"]
                        res["readable"] = True
                        res["reason"] = "ok"
                        cells = res.get("cells", [])
                        boxes = res.get("boxes", [])
            except Exception as yolo_exc:
                logger.debug("Secondary YOLO inference skipped: %s", yolo_exc)
        dot_pitch = float(res.get("dot_pitch", 0.0))
        lines_count = len(set(c.get("line_index", 0) for c in cells)) if cells else 0
        unknown_count = sum(1 for c in cells if c.get("char") == UNKNOWN_CELL_CHAR)
        unknown_ratio = (unknown_count / len(cells)) if cells else 0.0

        conf = self.braille_presence_confidence(clean_crop)
        res["confidence"] = conf

        # Reject garbage: if >30% of decoded text is '?', or if no valid letters/digits exist
        text_chars = [ch for ch in str(res.get("text", "")) if not ch.isspace()]
        unknown_chars_count = sum(1 for ch in text_chars if ch == UNKNOWN_CELL_CHAR)
        unknown_text_ratio = (unknown_chars_count / len(text_chars)) if text_chars else 1.0
        has_valid_letter = any(ch.isalnum() for ch in text_chars)

        # Reject if no characters decoded or high unknown ratio (>30% unknown) or < 3 dots
        if not str(res.get("text", "")).strip():
            res["readable"] = False
            res["reason"] = "no_braille"
            res["text"] = ""
        elif len(dots) < 3:
            res["readable"] = False
            res["reason"] = "no_braille"
            res["text"] = ""
        elif not has_valid_letter:
            res["readable"] = False
            res["reason"] = "no_braille"
            res["text"] = ""
        elif unknown_text_ratio > 0.30:
            res["readable"] = False
            res["reason"] = "no_braille"
            res["text"] = ""
        elif unknown_ratio > 0.30:
            res["readable"] = False
            res["reason"] = "no_braille"
            res["text"] = ""
        elif lines_count >= 5 and (len(cells) / max(1, lines_count)) < 1.0:
            res["readable"] = False
            res["reason"] = "no_braille"
            res["text"] = ""

        readable = bool(res.get("readable", False))
        reason = str(res.get("reason", "no_braille" if not readable else "ok"))
        text = str(res.get("text", "")).strip()

        # Draw red boxes tightly around each 2x3 Braille cell ONLY if readable
        if readable:
            for b in boxes:
                bx = int(round(b["x"] - b["w"] / 2.0))
                by = int(round(b["y"] - b["h"] / 2.0))
                bw = int(round(b["w"]))
                bh = int(round(b["h"]))
                cv2.rectangle(dbg_img, (bx, by), (bx + bw, by + bh), (0, 0, 255), 2)
                lbl = str(b.get("label", ""))
                if lbl:
                    cv2.putText(dbg_img, lbl, (bx, max(14, by - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 255), 2)
        else:
            boxes = []
            cells = []
            res["boxes"] = []
            res["cells"] = []

        # Build full debug visual frame (including ROI border if cropped)
        if x0 != 0 or y0 != 0:
            dbg_full = image.copy()
            if len(dbg_full.shape) == 2:
                dbg_full = cv2.cvtColor(dbg_full, cv2.COLOR_GRAY2BGR)
            # Draw ROI border in cyan
            cv2.rectangle(dbg_full, (x0, y0), (x0 + roi_w, y0 + roi_h), (255, 200, 0), 2)
            # Paste rendered dbg_img into ROI
            dbg_full[y0 : y0 + roi_h, x0 : x0 + roi_w] = dbg_img
            target_dbg_frame = dbg_full
        else:
            target_dbg_frame = dbg_img

        # Draw header text: DECODED: <text> or UNREADABLE: <reason>
        header_txt = f"DECODED: {text}" if readable else f"UNREADABLE: {reason}"
        header_color = (0, 255, 0) if readable else (0, 0, 255)
        cv2.putText(target_dbg_frame, header_txt, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, header_color, 2)

        # Save debug image to debug_scan/last_scan.jpg and timestamped history
        self._record_history(target_dbg_frame, readable, text, reason)

        # Shift coordinates if ROI was used
        if x0 != 0 or y0 != 0:
            for b in boxes:
                b["x"] += x0
                b["y"] += y0
            for c in cells:
                if "y" in c:
                    c["x"] += x0
                    c["y"] += y0
            for d in dots:
                d["x"] += x0
                d["y"] += y0

        readable = bool(res.get("readable", False))
        reason = str(res.get("reason", "no_braille" if not readable else "ok"))
        text = str(res.get("text", "")).strip()
        words = [w for w in text.split() if w]
        res["words"] = words

        logger.info(
            "Scanner result: dot_count=%d dot_pitch=%.2f cells_found=%d readable=%s reason=%s text=%r",
            len(dots), dot_pitch, len(cells), readable, reason, text,
        )

        return res
