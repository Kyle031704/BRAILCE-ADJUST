"""Diagnostic utility to probe available cameras and verify capture quality."""
import os, sys, glob, time
try:
    import cv2
    import numpy as np
except ImportError:
    print("Error: cv2 or numpy not installed.")
    sys.exit(1)

from brailce.vision.camera import UsbWebcamDevice

print("=== Brailce Camera Diagnostic ===")
# 1. Video group check on Linux
if os.name != "nt":
    import subprocess
    try:
        groups = subprocess.check_output(["id", "-nG"]).decode().strip().split()
        if "video" not in groups:
            user = os.environ.get("USER", "raspberrypi5")
            print(f"HINT: User '{user}' is not in the 'video' group! Run: sudo usermod -aG video {user}")
        else:
            print("✓ User is in the 'video' group.")
    except Exception:
        pass

# 2. List /dev/video* devices
print("\n--- Detected Video Nodes ---")
video_nodes = sorted(glob.glob("/dev/video*"))
for node in video_nodes:
    name_file = f"/sys/class/video4linux/{os.path.basename(node)}/name"
    sys_name = "unknown"
    if os.path.isfile(name_file):
        try:
            with open(name_file, "r") as f:
                sys_name = f.read().strip()
        except Exception:
            pass
    print(f"  {node}: {sys_name}")

# 3. Probe candidates using UsbWebcamDevice
print("\n--- Probing Camera Candidates ---")
cam = UsbWebcamDevice()
candidates = cam.probe_device_candidates()
print(f"Candidates to test: {candidates}")

winner = None
frame = None
for dev in candidates:
    cap = cam._open_capture(dev, deadline=time.time() + 3.0)
    if cap is not None:
        ret, f = cap.read()
        cap.release()
        if ret and f is not None and f.size > 0:
            winner = dev
            frame = f
            break

if winner is not None and frame is not None:
    mean_rgb = [round(float(v), 2) for v in np.mean(frame, axis=(0, 1))]
    print(f"\n✓ Winning camera: {winner}")
    print(f"  Frame dimensions: {frame.shape[1]}x{frame.shape[0]}")
    print(f"  Mean BGR: {mean_rgb}")
    cv2.imwrite("camera_check.jpg", frame)
    print("  Saved test frame to camera_check.jpg")
else:
    print("\n✗ No functional camera device succeeded.")
