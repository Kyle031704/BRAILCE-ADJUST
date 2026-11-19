"""
Brailce Scanner Diagnostic Tool (`tools/scan_debug.py`)
=======================================================

Run the offline classical OBR pipeline on an image file or live Raspberry Pi
Camera still capture, print diagnostic quality metrics and decoded text, and
optionally save an annotated image showing detected dots and fitted cell boxes.

Usage:
    python tools/scan_debug.py --image tools/enhanced_braille.jpg --out debug_out.png
    python tools/scan_debug.py --image tools/enhanced_braille.jpg --debug
    python tools/scan_debug.py --camera --out cam_debug.png
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import cv2
except ImportError:
    cv2 = None

import numpy as np
from PIL import Image as PILImage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brailce.vision.obr_engine import OBREngine


def annotate_result(image: np.ndarray, result: dict) -> np.ndarray:
    """Draw detected dot circles (green) and fitted Braille cells (blue) on a copy."""
    vis = image.copy()
    if cv2 is not None:
        for d in result.get("dots", []):
            cx, cy = int(round(d["x"])), int(round(d["y"]))
            r = max(3, int(round(d.get("d", 6.0) / 2.0)))
            cv2.circle(vis, (cx, cy), r, (0, 200, 0), 1)

        for b in result.get("boxes", []):
            cx, cy = float(b["x"]), float(b["y"])
            hw, hh = float(b["w"]) / 2.0, float(b["h"]) / 2.0
            pt1 = (int(round(cx - hw)), int(round(cy - hh)))
            pt2 = (int(round(cx + hw)), int(round(cy + hh)))
            cv2.rectangle(vis, pt1, pt2, (220, 90, 20), 1)
            label = str(b.get("label", ""))
            if label:
                cv2.putText(vis, label, (pt1[0], max(12, pt1[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (20, 20, 220), 1)
    return vis


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Brailce OBR dot & cell detection.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="Path to input image file")
    group.add_argument("--camera", action="store_true", help="Capture sharpest frame from PiCameraDevice")
    parser.add_argument("--out", help="Optional path to save annotated debug image")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug_dump in process_image(): prints dot count, pitch, cell bounding boxes, per-cell bitmask and decoded char",
    )
    args = parser.parse_args()

    if args.image:
        if cv2 is not None:
            image = cv2.imread(args.image)
        else:
            try:
                pil_img = PILImage.open(args.image).convert("RGB")
                image = np.array(pil_img)
            except Exception as e:
                print(f"Could not read image {args.image}: {e}", file=sys.stderr)
                return 1
        if image is None:
            print(f"Could not read image: {args.image}", file=sys.stderr)
            return 1
    else:
        if cv2 is None:
            print("OpenCV (cv2) is required for camera capture.", file=sys.stderr)
            return 1
        from brailce.vision.camera import PiCameraDevice
        cam = PiCameraDevice()
        if not cam.is_available():
            print(f"Camera unavailable: {cam.init_error}", file=sys.stderr)
            return 1
        try:
            image = cam.capture_sharpest(n=5)
        finally:
            cam.close()
        if image is None:
            print("Failed to capture frame from camera.", file=sys.stderr)
            return 1

    engine = OBREngine()
    result = engine.process_image(image, debug_dump=args.debug)
    print(f"readable     : {result.get('readable')}")
    print(f"reason       : {result.get('reason')}")
    print(f"dot_pitch    : {result.get('dot_pitch', 0.0):.2f} px")
    print(f"dots         : {len(result.get('dots', []))}")
    print(f"cells        : {len(result.get('cells', []))}")
    print(f"text         : {result.get('text')!r}")

    if args.debug:
        print("\n--- per-cell diagnostics ---")
        for i, cell in enumerate(result.get("cells", [])):
            print(
                f"  cell {i}: char={cell['char']!r}  bitmask={cell['dots_str']}  "
                f"x={cell['x']:.1f}  y={cell['y']:.1f}"
            )

    if args.out:
        if cv2 is not None:
            annotated = annotate_result(image, result)
            cv2.imwrite(args.out, annotated)
        else:
            PILImage.fromarray(image).save(args.out)
        print(f"annotated    : {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
