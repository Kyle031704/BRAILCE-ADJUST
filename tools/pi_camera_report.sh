#!/bin/bash
# ==============================================================================
# Brailce - Raspberry Pi camera / driver report (one command, paste the output back)
# ==============================================================================
# Collects everything needed to debug "scanner does not read" WITHOUT guessing:
#   OS + kernel, config.txt camera lines, libcamera camera list, V4L2 nodes and
#   their names, user groups, which Python/venv runs the app, whether picamera2
#   and cv2 import inside that Python, which node UsbWebcamDevice picks, a live
#   frame's brightness/sharpness, and one full scan through the real decoder.
#
# Usage (on the Pi, from the project folder):
#   sudo systemctl stop brailce.service        # only one program can hold the camera
#   bash tools/pi_camera_report.sh             # writes pi_camera_report_<date>.txt too
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}" || exit 1

REPORT="pi_camera_report_$(date +%Y%m%d_%H%M%S).txt"
exec > >(tee "$REPORT") 2>&1

section() { echo; echo "=================== $1 ==================="; }

section "SYSTEM"
cat /etc/os-release 2>/dev/null | grep -E "PRETTY_NAME|VERSION_ID"
uname -a
cat /proc/device-tree/model 2>/dev/null; echo
echo "user: $(id -un)  groups: $(id -nG)"
echo "service brailce.service: $(systemctl is-active brailce.service 2>/dev/null) (must be inactive while this runs)"

section "config.txt camera lines"
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
grep -nE "camera|dtoverlay=(imx|ov|arducam)|start_x|gpu_mem" "$CFG" 2>/dev/null || echo "(no camera lines in $CFG)"

section "libcamera (CSI Pi Camera Module)"
if command -v rpicam-hello >/dev/null 2>&1; then
    timeout 15 rpicam-hello --list-cameras 2>&1 | head -20
elif command -v libcamera-hello >/dev/null 2>&1; then
    timeout 15 libcamera-hello --list-cameras 2>&1 | head -20
else
    echo "rpicam-hello / libcamera-hello not installed (fine if you use a USB webcam)"
fi

section "POWER (USB webcam on a Pi 5 needs a 5 A supply or usb_max_current_enable=1)"
if command -v vcgencmd >/dev/null 2>&1; then
    T=$(vcgencmd get_throttled 2>/dev/null)
    echo "$T   (0x0 = OK; bit0/16 under-voltage now/before, bit1/17 current-capped, bit2/18 throttled)"
fi
grep -nE "usb_max_current_enable|max_usb_current" "$CFG" 2>/dev/null || echo "usb_max_current_enable not set in $CFG (USB ports limited to 600 mA on a non-PD supply)"
echo "--- dmesg (under-voltage / usb disconnect) ---"
dmesg 2>/dev/null | grep -iE "under-?voltage|usb .*disconnect|not enough (bus )?power|over-?current" | tail -10

section "V4L2 video nodes (name per node)"
for n in /dev/video*; do
    b=$(basename "$n")
    printf "%-14s %s\n" "$n" "$(cat /sys/class/video4linux/$b/name 2>/dev/null)"
done
echo "--- /dev/v4l/by-id ---"; ls -l /dev/v4l/by-id 2>/dev/null || echo "(none: no USB webcam enumerated)"
if command -v v4l2-ctl >/dev/null 2>&1; then
    echo "--- v4l2-ctl --list-devices ---"; v4l2-ctl --list-devices 2>&1 | head -40
fi
echo "--- USB devices ---"; lsusb 2>/dev/null
echo "--- dmesg (camera/uvc/imx) ---"; dmesg 2>/dev/null | grep -iE "uvc|imx|ov5647|camera|cfe|pisp" | tail -15

section "USB WEBCAM capabilities (first capture node)"
CAP_NODE=""
for n in /dev/v4l/by-id/*index0 /dev/video0 /dev/video1 /dev/video2; do
    [ -e "$n" ] && CAP_NODE="$(readlink -f "$n")" && break
done
if [ -n "$CAP_NODE" ] && command -v v4l2-ctl >/dev/null 2>&1; then
    echo "node: $CAP_NODE"
    echo "--- supported formats / resolutions (app asks MJPG 1280x720) ---"
    v4l2-ctl -d "$CAP_NODE" --list-formats-ext 2>&1 | grep -E "^\s*\[|Size:" | head -30
    echo "--- controls (focus/exposure; 'focus' missing = fixed-focus webcam) ---"
    v4l2-ctl -d "$CAP_NODE" --list-ctrls 2>&1 | grep -iE "focus|exposure|brightness|gain|white" | head -15
else
    echo "no capture node or v4l2-ctl missing (sudo apt install v4l-utils)"
fi

section "Python used by the app"
PY=""
for c in "$PROJECT_ROOT/venv/bin/python3" "$HOME/venv/bin/python3" "$(command -v python3)"; do
    [ -x "$c" ] && PY="$c" && break
done
echo "python: $PY"; "$PY" --version
SVC=/etc/systemd/system/brailce.service
[ -f "$SVC" ] && { echo "--- service ExecStart ---"; grep -E "ExecStart|User|WorkingDirectory" "$SVC"; }
"$PY" - <<'EOF'
import importlib, sys
print("sys.prefix:", sys.prefix)
for m in ("cv2", "numpy", "picamera2", "libcamera"):
    try:
        mod = importlib.import_module(m)
        print(f"  import {m:10} OK  version={getattr(mod, '__version__', '?')}")
    except Exception as e:
        print(f"  import {m:10} FAILED: {e}")
EOF

section "config.json vision section"
"$PY" - <<'EOF'
import json
for p in ("config.json", "brailce/config/config.json"):
    try:
        print(p, "->", json.dumps(json.load(open(p)).get("vision", {})))
    except Exception as e:
        print(p, "unreadable:", e)
EOF

section "UsbWebcamDevice probe (what the app does at startup)"
"$PY" - <<'EOF'
import logging, time, sys
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
import numpy as np, cv2
from brailce.vision.camera import UsbWebcamDevice, PiCameraDevice, compute_sharpness
t0 = time.time()
cam = UsbWebcamDevice(device=0, resolution=(1280, 720))
print(f"probe took {time.time()-t0:.1f}s -> device={cam.device} available={cam.is_available()} error={cam.init_error}")
if not cam.is_available():
    print("USB path failed; trying PiCameraDevice (Picamera2)...")
    cam = PiCameraDevice()
    print(f"PiCameraDevice available={cam.is_available()} error={cam.init_error}")
if cam.is_available():
    time.sleep(1.5)
    f = cam.flush_and_capture()
    if f is None:
        print("LIVE FRAME: None  <-- camera opened but gives no frames")
    else:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f
        print(f"LIVE FRAME: {f.shape[1]}x{f.shape[0]} mean={g.mean():.0f} (want 120-210) std={g.std():.1f} sharpness={compute_sharpness(f):.1f} (want >15)")
        cv2.imwrite("pi_camera_report_frame.jpg", f)
        print("saved pi_camera_report_frame.jpg")
        from brailce.vision.obr_engine import OBREngine
        r = OBREngine().scan_frame(f)
        print(f"SCAN: readable={r.get('readable')} reason={r.get('reason')} text={r.get('text')!r} strategy={r.get('strategy')}")
        print("annotated result: debug_scan/last_scan.jpg")
cam.close()
EOF

echo
echo "Report saved to: $REPORT"
echo "Send back: $REPORT, pi_camera_report_frame.jpg and debug_scan/last_scan.jpg"
