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

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

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
        """
        if image is None or cv2 is None or image.size == 0:
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
            if float(np.max(np.abs(g1 - g0))) < 2.5:
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
        if float(np.percentile(gray, 90)) <= 35.0 or float(np.std(gray)) <= 0.4:
            return False, "no_braille", 0.0

        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if lap_var < 0.5:
            return False, "blurry", lap_var

        return True, "ok", lap_var

    @staticmethod
    def detect_dots(image: np.ndarray) -> Tuple[List[Dict[str, float]], float]:
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

        # Quick exit for flat blank paper with no local contrast features
        max_resp = max(float(dark_resp.max()), float(light_resp.max())) if dark_resp.size > 0 else 0.0
        if max_resp < 2.5:
            return [], 0.0

        # Robust thresholding: Braille dots typically occupy < 2% of the page
        p98_d = float(np.percentile(dark_resp, 98.0))
        p98_l = float(np.percentile(light_resp, 98.0))
        thresh_d = max(2.5, p98_d * 0.30)
        thresh_l = max(2.5, p98_l * 0.30)

        mask_d = (dark_resp >= thresh_d).astype(np.uint8)
        mask_l = (light_resp >= thresh_l).astype(np.uint8)

        # Dark blobs (shadows / ink dots)
        num_d, _, stats_d, cents_d = cv2.connectedComponentsWithStats(mask_d, connectivity=8)
        dark_blobs = []
        max_dim = max(45.0, min(w, h) * 0.35)
        for lbl in range(1, num_d):
            area = float(stats_d[lbl, cv2.CC_STAT_AREA])
            bw = float(stats_d[lbl, cv2.CC_STAT_WIDTH])
            bh = float(stats_d[lbl, cv2.CC_STAT_HEIGHT])
            if area < 3.0 or bw < 2.0 or bh < 2.0 or bw > max_dim or bh > max_dim:
                continue
            aspect = min(bw, bh) / max(1e-6, max(bw, bh))
            if aspect < 0.32:
                continue
            cx, cy = float(cents_d[lbl][0]), float(cents_d[lbl][1])
            # Reject border cutoff artifacts
            if cx < 3.5 or cx > (w - 4.5) or cy < 3.5 or cy > (h - 4.5):
                continue
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
            if area < 3.0 or bw < 2.0 or bh < 2.0 or bw > max_dim or bh > max_dim:
                continue
            aspect = min(bw, bh) / max(1e-6, max(bw, bh))
            if aspect < 0.32:
                continue
            cx, cy = float(cents_l[lbl][0]), float(cents_l[lbl][1])
            # Reject border cutoff artifacts
            if cx < 3.5 or cx > (w - 4.5) or cy < 3.5 or cy > (h - 4.5):
                continue
            diam = math.sqrt(4.0 * area / math.pi)
            score = float(light_resp[int(cy), int(cx)]) if 0 <= int(cy) < h and 0 <= int(cx) < w else 1.0
            light_blobs.append({"x": cx, "y": cy, "d": diam, "score": score, "area": area})

        if not dark_blobs:
            return [], 0.0

        # Filter out fringe artifacts (< 30% of median) and giant merged blobs (> 220% of median)
        if len(dark_blobs) >= 4:
            med_a_d = float(np.median([b["area"] for b in dark_blobs]))
            dark_blobs = [b for b in dark_blobs if (0.30 * med_a_d) <= b["area"] <= (2.40 * med_a_d)]

        if len(light_blobs) >= 4:
            med_a_l = float(np.median([b["area"] for b in light_blobs]))
            light_blobs = [b for b in light_blobs if (0.30 * med_a_l) <= b["area"] <= (2.00 * med_a_l)]

        if not dark_blobs:
            return [], 0.0

        est_d = float(np.median([b["d"] for b in dark_blobs]))
        max_pair_dist = max(7.0, est_d * 1.8)

        # Match paired shadow and highlight for embossed Braille
        paired_dots: List[Dict[str, float]] = []
        used_l = set()
        for d_blob in dark_blobs:
            best_l = None
            best_dist = float("inf")
            for l_idx, l_blob in enumerate(light_blobs):
                if l_idx in used_l:
                    continue
                dist = math.hypot(d_blob["x"] - l_blob["x"], d_blob["y"] - l_blob["y"])
                if dist <= max_pair_dist and dist < best_dist:
                    best_dist = dist
                    best_l = l_idx

            if best_l is not None and best_dist >= (est_d * 0.20):
                l_match = light_blobs[best_l]
                diam_ratio = max(d_blob["d"], l_match["d"]) / max(1e-3, min(d_blob["d"], l_match["d"]))
                if diam_ratio <= 1.8:
                    used_l.add(best_l)
                    # Center of embossed dot is midpoint between shadow and highlight
                    cx = (d_blob["x"] + l_match["x"]) / 2.0
                    cy = (d_blob["y"] + l_match["y"]) / 2.0
                    dot_d = (d_blob["d"] + l_match["d"]) / 2.0
                    paired_dots.append({"x": cx, "y": cy, "d": dot_d, "score": d_blob["score"] + l_match["score"]})
                else:
                    paired_dots.append({"x": d_blob["x"], "y": d_blob["y"], "d": d_blob["d"], "score": d_blob["score"]})
            else:
                # Solid ink dot or top-lit spot
                paired_dots.append({"x": d_blob["x"], "y": d_blob["y"], "d": d_blob["d"], "score": d_blob["score"]})

        # Non-maximum suppression / deduplicate any duplicate crescent fragments closer than suppress_r
        # Ensures that highlight and shadow crescents from a single embossed dot are never split into 2 dots
        paired_dots.sort(key=lambda d: d["score"], reverse=True)
        final_dots: List[Dict[str, float]] = []
        suppress_r = max(6.0, min(13.0, est_d * 1.35))
        for cand in paired_dots:
            if any(math.hypot(cand["x"] - ex["x"], cand["y"] - ex["y"]) <= suppress_r for ex in final_dots):
                continue
            final_dots.append(cand)

        final_d = float(np.median([d["d"] for d in final_dots])) if final_dots else 0.0
        return final_dots, final_d

    @staticmethod
    def _estimate_skew_and_pitch(dots: List[Dict[str, float]], dot_diam: float) -> Tuple[float, float]:
        """
        Estimate grid rotation angle (degrees, in [-25, 25]) and within-cell
        dot-to-dot pitch p_dot directly from nearest-neighbor vectors.
        """
        if len(dots) < 4:
            return 0.0, max(12.0, dot_diam * 2.5)

        pts = np.array([[d["x"], d["y"]] for d in dots], dtype=np.float64)
        n = len(pts)

        # 1. Direct Nearest-Neighbor p_dot measurement (scale-invariant from 15px to 80px+)
        nn_dists: List[float] = []
        for i in range(n):
            diffs = pts - pts[i]
            d = np.hypot(diffs[:, 0], diffs[:, 1])
            d = d[d > 3.0]
            if len(d):
                nn_dists.append(float(np.min(d)))

        if nn_dists:
            nns = np.array(sorted(nn_dists))
            # Dynamic scale-invariant dot pitch from 25th-40th percentile of valid nearest-neighbor distances
            p_dot = float(np.percentile(nns, 35))
            p_dot = max(12.0, min(120.0, p_dot))
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

        cell_pitch_default = p_dot * 2.4
        line_strings: List[str] = []
        cell_events: List[Dict[str, Any]] = []
        debug_boxes: List[Dict[str, Any]] = []
        word_idx = 0

        for line_idx, line_dots in enumerate(lines):
            if len(line_dots) < 1:
                continue

            # 2. Fit best top-row baseline y0 such that y0, y0+p_dot, y0+2*p_dot maximize dot fit
            ys = np.array([d["ry"] for d in line_dots], dtype=np.float64)
            best_y0 = float(np.min(ys))
            best_err = float("inf")
            for y_anchor in ys:
                for offset in (0, 1, 2):
                    cand_y0 = y_anchor - offset * p_dot
                    row_dists = np.min(
                        np.abs(ys[:, None] - (cand_y0 + np.array([0.0, p_dot, 2.0 * p_dot])[None, :])),
                        axis=1,
                    )
                    err = float(np.sum(row_dists))
                    if err < best_err:
                        best_err = err
                        best_y0 = float(cand_y0)

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

            p_cell = p_dot * 2.45
            if len(sub_cols) >= 2:
                # Estimate p_cell dynamically from neighbor column gaps
                diffs = [
                    float(np.mean([d["rx"] for d in sub_cols[j + 1]]))
                    - float(np.mean([d["rx"] for d in sub_cols[j]]))
                    for j in range(len(sub_cols) - 1)
                ]
                cands = [df / k for df in diffs for k in range(1, 15) if 1.9 * p_dot <= (df / k) <= 3.2 * p_dot]
                if cands:
                    p_cell = float(np.median(cands))

            # Group sub-columns into 2-column Braille cells using X-coordinate clustering
            cell_groups: List[Dict[str, Any]] = []
            i = 0
            while i < len(sub_cols):
                col_i_x = float(np.mean([d["rx"] for d in sub_cols[i]]))
                if i + 1 < len(sub_cols):
                    col_next_x = float(np.mean([d["rx"] for d in sub_cols[i + 1]]))
                    gap = col_next_x - col_i_x
                    if 0.60 * p_dot <= gap <= 1.38 * p_dot:
                        # 2-column cell (Column 0 and Column 1)
                        cell_groups.append({
                            "col0": sub_cols[i],
                            "col1": sub_cols[i + 1],
                            "x_start": col_i_x,
                            "x_end": col_next_x,
                            "center_x": (col_i_x + col_next_x) / 2.0,
                        })
                        i += 2
                        continue
                # 1-column cell: check if column 0 or column 1
                is_col1 = False
                if cell_groups:
                    prev_end = cell_groups[-1]["x_end"]
                    gap_from_prev = col_i_x - prev_end
                    if 1.75 * p_dot <= gap_from_prev <= 2.85 * p_dot:
                        is_col1 = True

                if is_col1:
                    cell_groups.append({
                        "col0": [],
                        "col1": sub_cols[i],
                        "x_start": col_i_x - p_dot,
                        "x_end": col_i_x,
                        "center_x": col_i_x - 0.5 * p_dot,
                    })
                else:
                    cell_groups.append({
                        "col0": sub_cols[i],
                        "col1": [],
                        "x_start": col_i_x,
                        "x_end": col_i_x + p_dot,
                        "center_x": col_i_x + 0.5 * p_dot,
                    })
                i += 1

            line_str = ""
            is_number = False
            is_capital = False

            def _decode_col_dots(col_dots: List[Dict[str, float]]) -> List[Tuple[Dict[str, float], int]]:
                sorted_d = sorted(col_dots, key=lambda d: d["ry"])
                n = len(sorted_d)
                if n == 0:
                    return []
                if n >= 3:
                    return [(sorted_d[0], 0), (sorted_d[1], 1), (sorted_d[2], 2)]
                if n == 2:
                    r0 = int(round((sorted_d[0]["ry"] - best_y0) / p_dot))
                    r1 = int(round((sorted_d[1]["ry"] - best_y0) / p_dot))
                    r0 = max(0, min(2, r0))
                    r1 = max(0, min(2, r1))
                    diff = sorted_d[1]["ry"] - sorted_d[0]["ry"]
                    if diff >= 1.45 * p_dot:
                        return [(sorted_d[0], 0), (sorted_d[1], 2)]
                    elif r0 == r1:
                        if r0 == 0:
                            r1 = 1
                        elif r0 == 2:
                            r0 = 1
                        else:
                            if sorted_d[0]["ry"] - best_y0 < 0.8 * p_dot:
                                r0, r1 = 0, 1
                            else:
                                r0, r1 = 1, 2
                    return [(sorted_d[0], r0), (sorted_d[1], r1)]
                r0 = int(round((sorted_d[0]["ry"] - best_y0) / p_dot))
                return [(sorted_d[0], max(0, min(2, r0)))]

            for c_idx, cell in enumerate(cell_groups):
                # Detect word space between cells
                if c_idx > 0:
                    space_gap = cell["x_start"] - cell_groups[c_idx - 1]["x_end"]
                    if space_gap >= max(p_dot * 1.85, p_cell * 0.85):
                        line_str += " "
                        is_number = False
                        is_capital = False
                        word_idx += 1

                bits = [0, 0, 0, 0, 0, 0]
                for d, row in _decode_col_dots(cell["col0"]):
                    bits[0 * 3 + row] = 1
                for d, row in _decode_col_dots(cell["col1"]):
                    bits[1 * 3 + row] = 1

                bit_str = "".join(str(b) for b in bits)
                char = cls.BINARY_DICT.get(bit_str, UNKNOWN_CELL_CHAR)
                active_dots = [idx + 1 for idx, b in enumerate(bits) if b == 1]
                dots_str = "Dots " + "-".join(str(d) for d in active_dots) if active_dots else "No dots"

                # Compute unrotated visual bounding box center
                rx_c = cell["center_x"]
                ry_c = best_y0 + p_dot
                dx_c = rx_c - cx0
                dy_c = ry_c - cy0
                rad_pos = math.radians(skew_deg)
                ux = dx_c * math.cos(rad_pos) - dy_c * math.sin(rad_pos) + cx0
                uy = dx_c * math.sin(rad_pos) + dy_c * math.cos(rad_pos) + cy0

                if char == "#":
                    is_number = True
                    debug_boxes.append({"x": ux, "y": uy, "w": p_dot * 1.6, "h": p_dot * 2.6, "label": "#"})
                    cell_events.append({"char": "#", "kind": "indicator", "dots": active_dots, "dots_str": dots_str, "word_index": word_idx, "line_index": line_idx})
                    continue
                if char == "^":
                    is_capital = True
                    debug_boxes.append({"x": ux, "y": uy, "w": p_dot * 1.6, "h": p_dot * 2.6, "label": "^"})
                    cell_events.append({"char": "^", "kind": "indicator", "dots": active_dots, "dots_str": dots_str, "word_index": word_idx, "line_index": line_idx})
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
                debug_boxes.append({"x": ux, "y": uy, "w": p_dot * 1.6, "h": p_dot * 2.6, "label": out_c})
                cell_events.append({
                    "char": out_c,
                    "kind": kind,
                    "capitalized": cap_flag,
                    "dots": active_dots,
                    "dots_str": dots_str,
                    "word_index": word_idx,
                    "line_index": line_idx,
                })



            clean_l = " ".join(line_str.split()).strip()
            if clean_l:
                line_strings.append(clean_l)
            word_idx += 1

        final_text = " ".join(line_strings).strip().replace("\n", " ")
        if not final_text:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": [], "boxes": debug_boxes}

        # Token filtering & edge-noise suppression:
        tokens = [t.strip() for t in final_text.split() if t.strip()]
        garbage_punct = {'?', '"', ',', ':', '!', ';', '.'}
        valid_tokens = []
        for token in tokens:
            if any(ch in garbage_punct for ch in token):
                continue
            if not any(ch.isalnum() for ch in token):
                continue
            valid_tokens.append(token)

        if not valid_tokens:
            return {"readable": False, "reason": "no_braille", "text": "", "cells": [], "boxes": debug_boxes}

        has_multi = any(len(t) >= 2 and any(c.isalnum() for c in t) for t in valid_tokens)
        if has_multi:
            while len(valid_tokens) > 1 and len(valid_tokens[0]) == 1:
                valid_tokens.pop(0)
            while len(valid_tokens) > 1 and len(valid_tokens[-1]) == 1:
                valid_tokens.pop(-1)

        cleaned_text = " ".join(valid_tokens).strip()
        if not cleaned_text or not any(c.isalnum() for c in cleaned_text):
            return {"readable": False, "reason": "no_braille", "text": "", "cells": [], "boxes": debug_boxes}

        return {
            "readable": True,
            "reason": "ok",
            "text": cleaned_text,
            "cells": cell_events,
            "boxes": debug_boxes,
            "skew_deg": skew_deg,
            "dot_pitch": p_dot,
            "dot_diameter": dot_diam,
        }

    def braille_presence_confidence(self, image: np.ndarray) -> float:
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

            h, w = image.shape[:2]
            if max(w, h) > 640:
                scale = 640.0 / float(max(w, h))
                small = cv2.resize(image, (int(w * scale), int(h * scale)))
            else:
                small = image

            dots, dot_diam = self.detect_dots(small)
            if len(dots) < 6:
                dots_e, diam_e = self.detect_dots(self.enhance_contrast(small))
                if len(dots_e) > len(dots):
                    dots, dot_diam = dots_e, diam_e
            n = len(dots)
            if n < 3 or n > 2500:
                return 0.0

            # Gate 3: dot coverage fraction — noise/static fills >10% of frame area with tiny blobs;
            # real Braille pages never exceed ~8% coverage (dots are sparse on white paper).
            # Also applies a generous absolute cap for extreme static (>>2000 dots on 320×320).
            sh, sw = small.shape[:2]
            frame_area = float(sw * sh)
            import math as _math
            dot_coverage = sum(_math.pi * (d["d"] * 0.5) ** 2 for d in dots) / frame_area
            if dot_coverage > 0.10:
                logger.debug("detect_braille_presence: dot coverage %.1f%% exceeds 10%% gate", dot_coverage * 100)
                return 0.0

            diams = np.array([d["d"] for d in dots], dtype=np.float64)
            med_d = float(np.median(diams))
            if med_d < 2.0:
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
            # Gate 5: pitch-ratio range
            if not (1.2 <= pitch_ratio <= 7.0):
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

        # Distance thresholding based on physical Braille geometry
        if dot_diam < 2.5 or p_dot < 11.0:
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

    def process_image(
        self,
        image: np.ndarray,
        debug_dump: bool = False,
        full_frame: Optional[bool] = None,
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
            try:
                os.makedirs("debug_scan", exist_ok=True)
                cv2.imwrite("debug_scan/last_scan.jpg", image)
            except Exception:
                pass
            logger.info("Scanner result: quality gate failed reason=%s lap_var=%.2f", reason, lap_var)
            return {"readable": False, "reason": reason, "text": "", "cells": [], "lap_var": lap_var}

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
        dots, dot_diam = self.detect_dots(clean_crop)

        # 3b. Adaptive contrast enhancement fallback for faint dots under diffuse lighting
        if 0 < len(dots) < 8:
            enhanced_crop = self.enhance_contrast(clean_crop)
            e_dots, e_diam = self.detect_dots(enhanced_crop)
            if len(e_dots) > len(dots):
                dots, dot_diam = e_dots, e_diam

        # Automatic center-crop fallback if full frame produced < 2 dots
        if len(dots) < 2 and use_full and full_frame is None:
            c_roi_w = int(w * ROI_W_FRAC)
            c_roi_h = int(h * ROI_H_FRAC)
            c_x0 = (w - c_roi_w) // 2
            c_y0 = (h - c_roi_h) // 2
            c_crop = image[c_y0 : c_y0 + c_roi_h, c_x0 : c_x0 + c_roi_w].copy()
            clean_c = self.suppress_printed_ink(c_crop)
            c_dots, c_diam = self.detect_dots(clean_c)
            if 0 < len(c_dots) < 8:
                c_dots_e, c_diam_e = self.detect_dots(self.enhance_contrast(clean_c))
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
        if len(dots) < 2 or (0 < dot_diam < 2.0):
            res_reason = "too_small" if (0 < dot_diam < 2.0) else "no_braille"
            cv2.putText(dbg_img, f"UNREADABLE: {res_reason}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2)
            for dbg_path in ("/tmp/last_scan_debug.jpg", "debug_scan/last_scan.jpg"):
                try:
                    os.makedirs(os.path.dirname(os.path.abspath(dbg_path)), exist_ok=True)
                    cv2.imwrite(dbg_path, dbg_img)
                except Exception:
                    pass
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

        res = self.decode_dots_to_text(dots, dot_diam, crop.shape, single_line=not use_full)
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

        # Ensure decoded text has strictly no \n (Single-Line mode only)
        res["text"] = str(res.get("text", "")).replace("\n", " ").strip()

        conf = self.braille_presence_confidence(clean_crop)
        res["confidence"] = conf

        # Reject garbage: if >30% of decoded text is '?', or if no valid letters/digits exist
        text_chars = [ch for ch in str(res.get("text", "")) if not ch.isspace()]
        unknown_chars_count = sum(1 for ch in text_chars if ch == UNKNOWN_CELL_CHAR)
        unknown_text_ratio = (unknown_chars_count / len(text_chars)) if text_chars else 1.0
        has_valid_letter = any(ch.isalnum() for ch in text_chars)

        conf = self.braille_presence_confidence(clean_crop)
        res["confidence"] = conf

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

        # Draw red boxes tightly around each 2x3 Braille cell
        for b in boxes:
            bx = int(round(b["x"] - b["w"] / 2.0))
            by = int(round(b["y"] - b["h"] / 2.0))
            bw = int(round(b["w"]))
            bh = int(round(b["h"]))
            cv2.rectangle(dbg_img, (bx, by), (bx + bw, by + bh), (0, 0, 255), 2)
            lbl = str(b.get("label", ""))
            if lbl:
                cv2.putText(dbg_img, lbl, (bx, max(14, by - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 255), 2)

        readable = bool(res.get("readable", False))
        reason = str(res.get("reason", "no_braille" if not readable else "ok"))
        text = str(res.get("text", "")).strip()

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

        # Save debug image to both /tmp/last_scan_debug.jpg and debug_scan/last_scan.jpg
        for dbg_path in ("/tmp/last_scan_debug.jpg", "debug_scan/last_scan.jpg"):
            try:
                os.makedirs(os.path.dirname(os.path.abspath(dbg_path)), exist_ok=True)
                cv2.imwrite(dbg_path, target_dbg_frame)
            except Exception as save_exc:
                logger.debug("Failed saving debug image to %s: %s", dbg_path, save_exc)

        # Shift coordinates if ROI was used
        if x0 != 0 or y0 != 0:
            for b in boxes:
                b["x"] += x0
                b["y"] += y0
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
