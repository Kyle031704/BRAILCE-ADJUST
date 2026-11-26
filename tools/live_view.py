#!/usr/bin/env python3
"""
Brailce - Live Camera Viewfinder (Tkinter + Pillow GUI)
======================================================
Gumagamit ng native Windows Tkinter window para 100% gumana
kahit opencv-python-headless ang naka-install.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk

from brailce.vision.camera import compute_sharpness
from brailce.vision.obr_engine import OBREngine, ROI_W_FRAC, ROI_H_FRAC


class LiveViewApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("BRAILCE - Live Camera Viewfinder")
        self.root.geometry("860x650")
        self.root.configure(bg="#0F172A")

        os.makedirs("debug_scan", exist_ok=True)

        # Open Webcam
        self.cap = None
        for dev in [0, 1]:
            if os.name == "nt":
                c = cv2.VideoCapture(dev, cv2.CAP_DSHOW)
                if c.isOpened():
                    self.cap = c
                    break
            c = cv2.VideoCapture(dev)
            if c.isOpened():
                self.cap = c
                break

        self.obr = OBREngine()
        self.last_result = None
        self.last_text = ""
        self.last_dots_count = 0
        self.last_scan_time = 0.0
        self.last_roi_offset = (0, 0)

        # UI Layout
        # 1. Top Status Header
        self.header_frame = tk.Frame(root, bg="#1E293B", pady=8)
        self.header_frame.pack(fill=tk.X)

        self.telemetry_label = tk.Label(
            self.header_frame,
            text="Sharpness: -- | Brightness: --",
            font=("Arial", 11, "bold"),
            fg="#38BDF8",
            bg="#1E293B",
        )
        self.telemetry_label.pack()

        # 2. Video Canvas
        self.canvas_w = 800
        self.canvas_h = 450
        self.canvas = tk.Canvas(root, width=self.canvas_w, height=self.canvas_h, bg="#000000", highlightthickness=0)
        self.canvas.pack(pady=10)

        # 3. Decoded Result Banner
        self.result_label = tk.Label(
            root,
            text="Itapat ang Braille sa kahon at pindutin ang ENTER",
            font=("Arial", 14, "bold"),
            fg="#F8FAFC",
            bg="#0F172A",
        )
        self.result_label.pack(pady=6)

        # 4. Control Buttons
        self.btn_frame = tk.Frame(root, bg="#0F172A")
        self.btn_frame.pack(pady=6)

        self.scan_btn = tk.Button(
            self.btn_frame,
            text="📸  SCAN / BASAHIN ANG BRAILLE (Enter)",
            font=("Arial", 12, "bold"),
            bg="#10B981",
            fg="white",
            activebackground="#059669",
            activeforeground="white",
            padx=16,
            pady=8,
            cursor="hand2",
            relief=tk.FLAT,
            command=self.do_scan,
        )
        self.scan_btn.pack(side=tk.LEFT, padx=10)

        # Keyboard bindings
        self.root.bind("<Return>", lambda e: self.do_scan())
        self.root.bind("<KP_Enter>", lambda e: self.do_scan())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("q", lambda e: self.root.destroy())
        self.root.bind("Q", lambda e: self.root.destroy())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.current_frame = None
        self.frame_counter = 0

        if self.cap is None or not self.cap.isOpened():
            self.result_label.config(text="HINDI MABUKSAN ANG WEBCAM. Pakisuri ang USB cable.", fg="#EF4444")
        else:
            self.update_video()

    def do_scan(self):
        if self.current_frame is None:
            return

        frame = self.current_frame.copy()
        h, w = frame.shape[:2]

        # Strictly crop only inside the green guide box
        roi_w = int(w * ROI_W_FRAC)
        roi_h = int(h * 0.35)
        x0 = (w - roi_w) // 2
        y0 = (h - roi_h) // 2
        roi_crop = frame[y0 : y0 + roi_h, x0 : x0 + roi_w].copy()

        result = self.obr.process_image(roi_crop, full_frame=True, embossed_only=True)

        self.last_result = result
        self.last_text = result.get("text", "").strip()
        self.last_dots_count = len(result.get("dots", []))
        self.last_scan_time = time.time()
        self.last_roi_offset = (x0, y0)

        readable = result.get("readable", False)
        reason = result.get("reason", "unknown")

        # Save snapshot
        cv2.imwrite("debug_scan/last_scan.jpg", roi_crop)

        if readable and self.last_text:
            self.result_label.config(
                text=f"NABASA: '{self.last_text}'  ({self.last_dots_count} dots)",
                fg="#34D399",
            )
            self._speak_async(self.last_text)
        else:
            self.result_label.config(
                text=f"HINDI MABASA: {reason}  ({self.last_dots_count} dots)",
                fg="#F87171",
            )

    def _speak_async(self, text: str) -> None:
        """Speak decoded Braille letters and word in a background thread on Windows."""
        clean = "".join(ch for ch in text if ch.isalnum() or ch.isspace()).strip()
        if not clean:
            return
        letters = ", ".join(ch for ch in clean if not ch.isspace())
        spoken = f"{letters}. {clean}" if len(clean) > 1 and " " not in clean else clean

        def _worker():
            if os.name == "nt":
                try:
                    safe = spoken.replace("'", "''")
                    cmd = [
                        "powershell", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Speech; "
                        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        f"$s.Speak('{safe}')",
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def update_video(self):
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                self.current_frame = frame
                self.frame_counter += 1

                h, w = frame.shape[:2]
                draw_frame = frame.copy()

                # Center Guide Box
                roi_w = int(w * ROI_W_FRAC)
                roi_h = int(h * 0.35)
                x0 = (w - roi_w) // 2
                y0 = (h - roi_h) // 2

                # Sharpness and Brightness
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                sharpness = compute_sharpness(frame)
                brightness = float(np.mean(gray))

                guide_color = (0, 255, 0) if (sharpness >= 15.0 and 80 <= brightness <= 220) else (0, 165, 255)
                cv2.rectangle(draw_frame, (x0, y0), (x0 + roi_w, y0 + roi_h), guide_color, 2)
                cv2.putText(
                    draw_frame,
                    "ILAGAY ANG BRAILLE DITO",
                    (x0 + 10, y0 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    guide_color,
                    2,
                )

                # Show detected dots if recently scanned within 3 seconds
                if self.last_result and (time.time() - self.last_scan_time) < 3.5:
                    off_x, off_y = getattr(self, "last_roi_offset", (0, 0))
                    for d in self.last_result.get("dots", []):
                        px = int(round(d["x"])) + off_x
                        py = int(round(d["y"])) + off_y
                        cv2.circle(draw_frame, (px, py), 5, (0, 255, 0), -1)

                # Update telemetry header
                self.telemetry_label.config(
                    text=f"Linaw (Sharpness): {sharpness:.1f}  |  Liwanag (Brightness): {brightness:.0f}  |  Webcam: Active"
                )

                # Resize to fit canvas
                draw_rgb = cv2.cvtColor(draw_frame, cv2.COLOR_BGR2RGB)
                resized = cv2.resize(draw_rgb, (self.canvas_w, self.canvas_h))
                img = Image.fromarray(resized)
                self.photo = ImageTk.PhotoImage(image=img)
                self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)

        self.root.after(30, self.update_video)

    def on_close(self):
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = LiveViewApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
