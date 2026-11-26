#!/usr/bin/env python3
"""
Brailce Web Viewfinder (headless Pi, view from any browser on the same network)
==============================================================================
Live camera view + Scan button, for aiming the camera when the Pi has no monitor.

On the Pi (stop the app first, only one program can use the camera):
    sudo systemctl stop brailce.service
    source ~/venv/bin/activate
    python3 tools/web_viewfinder.py            # default port 8000

Then on the laptop open:  http://<PI-IP>:8000
  - Cyan box   = center strip the scanner reads first (put the Braille line inside it)
  - Status bar = live dot detection / distance hint (OK / move closer / move farther)
  - Scan       = runs the same decoder as Scanner Mode and shows the annotated result
Stop with Ctrl+C.

Uses only the Python standard library + OpenCV (no extra installs).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from brailce.vision.camera import PiCameraDevice, UsbWebcamDevice, compute_sharpness  # noqa: E402
from brailce.vision.obr_engine import ROI_H_FRAC, ROI_W_FRAC, OBREngine  # noqa: E402

logging.basicConfig(level=logging.WARNING)

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brailce viewfinder</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#111;color:#eee}
 main{max-width:1100px;margin:auto;padding:12px}
 .row{display:flex;gap:12px;flex-wrap:wrap}
 .col{flex:1 1 480px}
 img{width:100%;border-radius:6px;background:#000}
 button{font-size:20px;padding:12px 28px;border-radius:8px;border:0;background:#2b7;color:#fff;cursor:pointer}
 button:disabled{background:#555}
 #status{font-size:18px;margin:8px 0}
 #text{font-size:28px;font-weight:700;white-space:pre-wrap;margin:8px 0;color:#7f7}
 #meta{font-family:monospace;font-size:14px;color:#aaa;white-space:pre-wrap}
</style></head><body><main>
<h2>Brailce camera viewfinder</h2>
<div class="row">
 <div class="col"><h3>Live</h3><img src="/stream"><div id="status">...</div></div>
 <div class="col"><h3>Last scan</h3><img id="last" src="/last.jpg?0">
  <p><button id="btn" onclick="scan()">Scan (Enter)</button></p>
  <div id="text"></div><div id="meta"></div></div>
</div>
<script>
async function poll(){try{const r=await fetch('/status');const s=await r.json();
 document.getElementById('status').textContent=s.status;}catch(e){} setTimeout(poll,500);}
async function scan(){const b=document.getElementById('btn');b.disabled=true;b.textContent='Scanning...';
 try{const r=await fetch('/scan');const j=await r.json();
  document.getElementById('text').textContent=j.readable?j.text:('Unreadable: '+j.reason);
  document.getElementById('meta').textContent=JSON.stringify(j,null,1);
  document.getElementById('last').src='/last.jpg?'+Date.now();}
 finally{b.disabled=false;b.textContent='Scan (Enter)';}}
document.addEventListener('keydown',e=>{if(e.key==='Enter')scan();});
poll();
</script></main></body></html>"""


class Viewfinder:
    def __init__(self) -> None:
        self.engine = OBREngine()
        self.cam = self._open_camera()
        self.lock = threading.Lock()
        self.frame: Optional[np.ndarray] = None
        self.status = "starting..."
        self.last_jpg: bytes = self._placeholder("No scan yet")
        self.running = True
        threading.Thread(target=self._grab_loop, daemon=True).start()
        threading.Thread(target=self._assess_loop, daemon=True).start()

    @staticmethod
    def _open_camera() -> Any:
        usb = UsbWebcamDevice()
        if usb.is_available():
            print(f"camera: USB webcam {usb.device}")
            return usb
        usb.close()
        pi = PiCameraDevice()
        if pi.is_available():
            print("camera: Raspberry Pi Camera")
            return pi
        raise SystemExit(
            f"No camera available (USB: {usb.init_error} | PiCam: {pi.init_error}).\n"
            "Is the Brailce app still running? Run: sudo systemctl stop brailce.service"
        )

    @staticmethod
    def _placeholder(msg: str) -> bytes:
        img = np.full((360, 640, 3), 30, np.uint8)
        cv2.putText(img, msg, (20, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
        return cv2.imencode(".jpg", img)[1].tobytes()

    def _grab_loop(self) -> None:
        while self.running:
            try:
                f = self.cam.capture_image()
                if f is not None and f.size:
                    with self.lock:
                        self.frame = f
            except Exception as exc:
                self.status = f"camera error: {exc}"
                time.sleep(0.5)
            time.sleep(0.03)

    def _assess_loop(self) -> None:
        """~3 Hz live hint, same check the Scanner Mode viewfinder uses."""
        while self.running:
            with self.lock:
                f = None if self.frame is None else self.frame
            if f is not None:
                try:
                    h, w = f.shape[:2]
                    rw, rh = int(w * ROI_W_FRAC), int(h * ROI_H_FRAC)
                    roi = f[(h - rh) // 2:(h - rh) // 2 + rh, (w - rw) // 2:(w - rw) // 2 + rw]
                    info = self.engine.assess_braille_distance(roi, confidence_threshold=0.42)
                    hint = {"ok": "Braille OK - press Scan", "too_far": "Move CLOSER",
                            "too_close": "Move FARTHER", "no_braille": "No Braille in the box"}
                    gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                    self.status = (f"{hint.get(info.get('state'), info.get('state'))} | dots={info.get('dot_count', 0)} "
                                   f"pitch={info.get('dot_pitch', 0):.0f}px conf={info.get('confidence', 0):.2f} | "
                                   f"sharp={compute_sharpness(f):.0f} bright={float(np.mean(gray)):.0f} | {w}x{h}")
                except Exception as exc:
                    self.status = f"assess error: {exc}"
            time.sleep(0.33)

    def preview_jpg(self) -> bytes:
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        if f is None:
            return self._placeholder("Waiting for camera...")
        h, w = f.shape[:2]
        rw, rh = int(w * ROI_W_FRAC), int(h * ROI_H_FRAC)
        x0, y0 = (w - rw) // 2, (h - rh) // 2
        cv2.rectangle(f, (x0, y0), (x0 + rw, y0 + rh), (255, 220, 0), 3)
        s = 640.0 / w
        small = cv2.resize(f, (640, int(h * s)))
        return cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()

    def scan(self) -> dict:
        t0 = time.time()
        try:
            img = self.cam.capture_sharpest(n=5)
        except Exception as exc:
            return {"readable": False, "reason": f"capture failed: {exc}"}
        if img is None:
            return {"readable": False, "reason": "capture failed"}
        res = self.engine.scan_frame(img)
        vis = img.copy()
        for d in res.get("dots", []):
            cv2.circle(vis, (int(d["x"]), int(d["y"])), max(3, int(d.get("d", 6) / 2)), (0, 220, 0), 2)
        for b in res.get("boxes", []):
            x, y, bw, bh = b["x"] - b["w"] / 2, b["y"] - b["h"] / 2, b["w"], b["h"]
            cv2.rectangle(vis, (int(x), int(y)), (int(x + bw), int(y + bh)), (0, 0, 255), 2)
            if b.get("label"):
                cv2.putText(vis, str(b["label"]), (int(x), max(15, int(y) - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        self.last_jpg = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        os.makedirs("debug_scan", exist_ok=True)
        cv2.imwrite(os.path.join("debug_scan", "web_last_raw.jpg"), img)
        # Keep every raw frame (lossless PNG) so real scans can be analysed offline
        stamp = time.strftime("%Y%m%d_%H%M%S")
        cv2.imwrite(os.path.join("debug_scan", f"web_raw_{stamp}.png"), img)
        out = {
            "readable": bool(res.get("readable")),
            "text": str(res.get("text", "")),
            "reason": str(res.get("reason", "")),
            "strategy": res.get("strategy"),
            "dots": len(res.get("dots", [])),
            "dot_pitch": round(float(res.get("dot_pitch", 0.0)), 1),
            "frame": f"{img.shape[1]}x{img.shape[0]}",
            "seconds": round(time.time() - t0, 2),
        }
        print("scan:", out, flush=True)
        return out


def make_handler(vf: Viewfinder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # keep the PuTTY console quiet
            pass

        def _send(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/":
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/status":
                self._send(json.dumps({"status": vf.status}).encode(), "application/json")
            elif path == "/last.jpg":
                self._send(vf.last_jpg, "image/jpeg")
            elif path == "/scan":
                self._send(json.dumps(vf.scan()).encode(), "application/json")
            elif path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    while vf.running:
                        jpg = vf.preview_jpg()
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404)

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="Brailce web viewfinder")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    vf = Viewfinder()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(vf))
    srv.daemon_threads = True
    print(f"Open in your laptop browser:  http://<PI-IP>:{args.port}   (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        vf.running = False
        srv.server_close()
        if hasattr(vf.cam, "close"):
            vf.cam.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
