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
    python tools/scan_debug.py --camera --loop          # press Enter to scan again, q to quit

--camera uses the same camera selection as the app (USB webcam first, then Pi Camera)
and the same decode path as Scanner Mode (OBREngine.scan_frame: ink + embossed).
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
    group.add_argument("--camera", action="store_true", help="Capture from USB webcam (or Pi Camera fallback)")
    parser.add_argument("--loop", action="store_true", help="With --camera: scan again on every Enter (q to quit)")
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
        cam = _open_camera()
        if cam is None:
            return 1
        try:
            n = 0
            if args.loop and input("Aim the camera at the Braille, then [Enter] to scan ([q] quit): ").strip().lower().startswith("q"):
                return 0
            while True:
                image = _grab(cam)
                if image is None:
                    print("Failed to capture frame from camera.", file=sys.stderr)
                    return 1
                n += 1
                out = args.out
                if out and args.loop:
                    root, ext = os.path.splitext(out)
                    out = f"{root}_{n}{ext or '.png'}"
                _run_and_report(image, args.debug, out)
                if not args.loop:
                    return 0
                if input("\n[Enter] scan again, [q] quit: ").strip().lower().startswith("q"):
                    return 0
        finally:
            if hasattr(cam, "close"):
                cam.close()

    _run_and_report(image, args.debug, args.out)
    return 0


def _open_camera():
    from brailce.vision.camera import PiCameraDevice, UsbWebcamDevice
    usb = UsbWebcamDevice()
    if usb.is_available():
        print(f"camera       : USB webcam {usb.device}")
        return usb
    usb.close()
    pi = PiCameraDevice()
    if pi.is_available():
        print("camera       : Raspberry Pi Camera")
        return pi
    print(f"Camera unavailable: USB: {usb.init_error} | PiCam: {pi.init_error}", file=sys.stderr)
    return None


def _grab(cam):
    import time
    time.sleep(0.5)  # let exposure/autofocus settle
    try:
        return cam.capture_sharpest(n=5)
    except Exception as exc:
        print(f"capture error: {exc}", file=sys.stderr)
        return None


def _run_and_report(image, debug: bool, out) -> None:
    import time
    engine = OBREngine()
    t0 = time.time()
    result = engine.scan_frame(image)
    dt = time.time() - t0
    print(f"frame        : {image.shape[1]}x{image.shape[0]}")
    print(f"readable     : {result.get('readable')}")
    print(f"reason       : {result.get('reason')}")
    print(f"strategy     : {result.get('strategy')}")
    print(f"dot_pitch    : {result.get('dot_pitch', 0.0):.2f} px")
    print(f"dots         : {len(result.get('dots', []))}")
    print(f"cells        : {len(result.get('cells', []))}")
    print(f"time         : {dt:.2f} s")
    print(f"text         : {result.get('text')!r}")

    if debug:
        print("\n--- per-cell diagnostics ---")
        for i, cell in enumerate(result.get("cells", [])):
            print(f"  cell {i}: char={cell.get('char')!r}  {cell.get('dots_str', '')}  "
                  f"line={cell.get('line_index', 0)} word={cell.get('word_index', 0)}")

    if out:
        if cv2 is not None:
            cv2.imwrite(out, annotate_result(image, result))
        else:
            PILImage.fromarray(image).save(out)
        print(f"annotated    : {out}")
    print("debug image  : debug_scan/last_scan.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
