#!/usr/bin/env python3
"""
Brailce Camera & Scanner Diagnostic Tool
Run on Raspberry Pi 5 to test USB webcam live, verify ceiling light visibility,
measure dot contrast and presence confidence, and inspect decoded Grade 1 Braille.

Usage:
    python tools/camera_live_diagnostic.py
    python tools/camera_live_diagnostic.py --loop  # Continuous live monitor
"""

import os
import sys
import time
import json
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from brailce.vision.camera import UsbWebcamDevice, PiCameraDevice, compute_sharpness
from brailce.vision.obr_engine import OBREngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CameraDiag")


def run_single_diagnostic(cam, obr, save_path="debug_scan/live_test.jpg"):
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    print("\n" + "=" * 60)
    print("Capturing live test frame from camera...")
    frame = cam.capture_image()
    if frame is None or frame.size == 0:
        print("[ERROR] Camera returned an empty frame! Check USB webcam connection.")
        return False

    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    mean_val = float(np.mean(gray))
    std_val = float(np.std(gray))
    min_val = int(np.min(gray))
    max_val = int(np.max(gray))
    sharpness = compute_sharpness(frame)

    print(f"Frame resolution: {w}x{h}")
    print(f"Lighting Analysis:")
    print(f"  - Average Brightness: {mean_val:.1f} / 255 (Optimal: 120 - 210)")
    print(f"  - Contrast (std dev): {std_val:.1f} (Optimal: > 25.0)")
    print(f"  - Dynamic Range: min={min_val}, max={max_val}")
    print(f"  - Sharpness (Laplacian var): {sharpness:.1f} (Optimal: > 15.0)")

    if mean_val < 80:
        print("  [ADVICE] Ceiling lighting appears dim or hand is casting a large shadow.")
    elif mean_val > 230:
        print("  [ADVICE] Image is overexposed/washed out. Tilt paper slightly away from direct reflection.")

    # Test Braille presence confidence
    conf = obr.braille_presence_confidence(frame)
    print(f"\nBraille Presence Confidence: {conf:.2f} (Detection threshold: 0.42)")
    if conf >= 0.42:
        print("  [STATUS] BRAILLE DETECTED! Viewfinder will signal READY.")
    else:
        print("  [STATUS] Waiting for Braille. Hold paper steady under the camera.")

    # Run full OBR process
    print("\nRunning Grade 1 OBR Engine...")
    t0 = time.time()
    result = obr.process_image(frame)
    dt = (time.time() - t0) * 1000.0

    dots = result.get("dots", [])
    cells = result.get("cells", [])
    dot_pitch = result.get("dot_pitch", 0.0)
    readable = result.get("readable", False)
    text = result.get("text", "").strip()
    reason = result.get("reason", "unknown")

    print(f"Processing time: {dt:.1f} ms")
    print(f"Detected dots: {len(dots)}")
    print(f"Estimated dot pitch: {dot_pitch:.1f} px")
    print(f"Cells recognized: {len(cells)}")
    print(f"Readable: {readable} (Reason: {reason})")
    print(f"Decoded text: {repr(text)}")

    # Copy debug visual
    if os.path.exists("debug_scan/last_scan.jpg"):
        import shutil
        shutil.copy("debug_scan/last_scan.jpg", save_path)
        print(f"\n[SAVED] Visual proof saved to: {save_path}")
        if readable:
            print("  - Green circles = detected dots")
            print("  - Tight Red boxes = 2x3 Braille cells")
            print("  - Yellow text = decoded Grade 1 character")
        else:
            print(f"  - UNREADABLE: {reason} (Walang embossed Braille na natagpuan sa harap ng camera)")

    print("=" * 60 + "\n")
    return readable


def get_cam_device_index():
    try:
        from brailce.config.loader import ConfigLoader
        cfg = ConfigLoader.load("brailce/config/config.json")
        return cfg.get("vision", "usb_device", default=0)
    except Exception:
        pass
    try:
        with open("config.json", "r") as f:
            return json.load(f).get("vision", {}).get("usb_device", 0)
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Brailce Live Camera Diagnostic")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    parser.add_argument("--interval", type=float, default=2.0, help="Interval between scans in loop mode")
    args = parser.parse_args()

    cam_index = get_cam_device_index()
    print(f"Initializing UsbWebcamDevice (device {cam_index})...")
    cam = None
    try:
        cam = UsbWebcamDevice(device=cam_index, resolution=(1280, 720))
    except Exception as exc:
        print(f"[WARN] USB webcam open with device={cam_index} failed: {exc}")

    if cam is None or not cam.is_available():
        print("[INFO] Trying PiCameraDevice fallback...")
        try:
            cam = PiCameraDevice()
        except Exception:
            pass

    if cam is None or not cam.is_available():
        print(f"[ERROR] Camera not available! Check USB webcam cable connection.")
        sys.exit(1)

    obr = OBREngine()

    if args.loop:
        print(f"Starting continuous diagnostic loop (every {args.interval}s). Press Ctrl+C to stop.")
        idx = 1
        try:
            while True:
                save_file = f"debug_scan/live_scan_{idx % 5}.jpg"
                run_single_diagnostic(cam, obr, save_path=save_file)
                idx += 1
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped diagnostic loop.")
        finally:
            if hasattr(cam, "close"):
                cam.close()
    else:
        run_single_diagnostic(cam, obr)
        if hasattr(cam, "close"):
            cam.close()


if __name__ == "__main__":
    main()
