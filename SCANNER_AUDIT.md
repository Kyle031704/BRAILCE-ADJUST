# Brailce Scanner Mode — Software Audit

**Date:** 2026-11-17  
**Scope:** `brailce/vision/obr_engine.py`, `brailce/modes/scanner_mode.py`, and related config

---

## 1. What actually runs at runtime (factual inventory)

| Component | What the code actually uses |
|---|---|
| **Dot detection** | `OBREngine.detect_dots()` — multi-scale Difference-of-Gaussians (DoG) on a resized frame; no YOLO, no ML |
| **Braille presence** | `OBREngine.braille_presence_confidence()` → float [0,1]; `detect_braille_presence()` returns `True` when confidence >= threshold |
| **Character decode** | `OBREngine.decode_dots_to_text()` — classical 6-bit dot-grid cell assignment |
| **Camera** | USB webcam via OpenCV `VideoCapture(0)` |
| **TTS** | `fil-PH-BlessicaNeural` (pre-rendered WAV bank) + Piper Ryan (English) |

NOTE: YOLOv8 (`brailce/vision/yolov8_braille.pt`) is an **optional secondary path** (`use_yolo_secondary=False` by default). It is never downloaded at runtime. The default and only active runtime path is the classical OpenCV dot-grid recognizer.

---

## 2. Camera settings negotiated at startup

The USB webcam is opened with `cv2.VideoCapture(0)`. Brailce requests:

- `CAP_PROP_AUTOFOCUS = 1` (enabled)
- `CAP_PROP_FRAME_WIDTH = 1280`, `CAP_PROP_FRAME_HEIGHT = 720` (may be downscaled by driver)
- `CAP_PROP_BUFFERSIZE = 1` (minimize latency)

Actual negotiated values depend on the webcam hardware and are logged at startup.

---

## 3. Datasets / test images

| File | Purpose |
|---|---|
| `tools/enhanced_braille.jpg` | High-contrast scan of a real Grade 1 Braille page (~1294 dots) |
| `tools/test_real_cam.jpg` | Low-angle webcam shot (~35 dots, partial view) |

---

## 4. Configuration audit

| Key | Location | Value | Effect |
|---|---|---|---|
| `vision.confidence_threshold` | `config.json`, `defaults.py` | **0.60** | Minimum confidence for "Braille detected" |
| `vision.use_yolo_secondary` | `defaults.py` | `false` | YOLOv8 disabled |
| `vision.model_path` | `config.json` | `brailce/vision/yolov8_braille.pt` | Unused (secondary path off) |
| `speech.filipino_char_gap_ms` | both | 200 | Scanner spell-out pacing in Filipino |
| `speech.filipino_word_gap_ms` | both | 30 | Word gap in concatenated Filipino TTS |

---

## 5. Presence Detection Flow

```
Frame captured (preview loop, ~15 fps)
     |
     v
check_image_quality()  -- brightness p90 > 55, Laplacian var > 4
     | fail -> 0.0
     v
detect_dots()          -- multi-scale DoG, 640px resize
     | < 4 dots -> 0.0
     v
dot_coverage gate      -- total dot area / frame area > 10% -> noise -> 0.0
     |
     v
diameter consistency   -- score from std/median ratio
     |
     v
nearest-neighbor pitch -- must be in [1.2x, 7.0x] dot diameter
     |
     v
grid regularity        -- fraction within +/-40% of median pitch
     |
     v
polka-dot penalty      -- 4-way cross-neighbor ratio penalizes uniform grids
     |
     v
confidence = 0.25*diam + 0.50*regularity + 0.25*(1 - polka)
     |
     v
ScannerMode._detection_worker compares conf >= 0.60
Hysteresis: 3 consecutive positives -> "Braille detected" announcement
            4 consecutive negatives -> reset
```

---

## 6. Known false-positive cases and results

| Input | Conf | Passes 0.60? | Why |
|---|---|---|---|
| Blank white frame | 0.0 | No | Quality gate fails |
| Random pixel noise (seed 123, 320x320) | 0.0 | No | Dot coverage >10% |
| Polka-dot grid (15px spacing) | ~0.3 | No | Polka-dot cross-neighbor penalty |
| Real Braille page (`enhanced_braille.jpg`) | 0.64 | Yes | Real lattice |
| Synthetic 4-dot Braille grid | ~0.7 | Yes | Regular spacing |

---

## 7. Documentation corrections

Previous versions of `HARDWARE_AND_FUNCTIONS.md` and `README.md` incorrectly claimed:
- YOLOv8 is the main detection engine -> **Corrected**: classical OpenCV dot-grid is the only runtime engine; YOLOv8 is disabled by default.
- eSpeak NG is the TTS engine -> **Corrected**: Piper Neural TTS (Ryan, en_US) for English; pre-rendered `fil-PH-BlessicaNeural` WAV bank for Filipino.

---

## 8. 2026-11-26 — "Scanner cannot read ink or embossed Braille" fixes

Measured on the **device decode path** (1280x720 frames, center-strip ROI first), 96 synthetic
frames (ink + embossed, dot diameter 6–15px, rotation −4..+3°): **17.7% → 97.9%** character accuracy.
Real printed alphabet chart (`123.webp`): rows a–j / k–t / u–z now decode exactly (previously nothing).

| Root cause | Fix |
|---|---|
| `ScannerMode` hard-coded `embossed_only=True`, which masks solid dark dots as "ink" → printed Braille was never readable | New `OBREngine.scan_frame()` tries ink+embossed detector (ROI, full frame) then embossed-only, scores results, keeps the best; ScannerMode uses it |
| `detect_dots()` blob size limit was relative to each crop axis → on the 158px-tall ROI, every dot wider than ~13–19px was dropped (all close-up scans) | Limit relative to `max(h, w/2)`; merged 2-dot blobs > 4× median area removed |
| Cell pitch = median of (pair distance / k) over many divisors, capped at 3.15× dot pitch → lattice drift, wide-spaced charts misread | `_fit_cell_lattice()`: grid search over 1.9–4.4× dot pitch + least-squares refinement |
| Lines using only dot rows 1–2 (a–j) fitted as rows 2–3 → letters decoded as punctuation | Near-tie row fits prefer topmost dots in row 1 |
| Presence gate only checked a 640px downsample → small/far dots rejected before decoding | Native-resolution re-check before rejecting |
| Stray 1–2 dot "lines" (dust, paper edge) added junk text | Dropped when real lines exist |
| O(n²) Python loops in pairing/NMS (up to ~7s per scan) | Vectorized pairing + grid NMS (~1s per scan on a desktop CPU) |

Regression tests: `brailce/tests/test_scanner_ink_and_embossed.py` (goes through `scan_frame()`).

---

## 9. 2026-11-26 (later) — Real embossed sheet + camera probe findings

Debugged on the saved photos of the user's hand-embossed (slate & stylus) sheet and on the
dev laptop's cameras. The Raspberry Pi itself was not reachable on the network, so nothing
below was verified on the device.

| Finding | Evidence | Fix |
|---|---|---|
| Only the "shadow" detector reads real embossed paper; classic `auto`/`embossed` DoG paths return `too_small` (hundreds of paper-texture specks) | `real_scan_1.jpg`, `tests/data/real_embossed_sidelit_*.jpg` | none needed; shadow path is first in `SCAN_STRATEGIES` |
| Center-box read of "open" gave **OLEN** while the full-frame read gave OPEN: `detect_shadow_dots()` sized its median-background kernel from the crop (21 px on the strip, i.e. one dot pitch), so the neighbouring dot's shadow became "background" and dot 4 of the P fell under threshold | 12 vs 13 dots in the same box | `process_shadow()` now always detects on the full frame (cached per `scan_frame()`) and keeps the dots inside the box |
| One missed dot = wrong letter, with no second chance | OLEN, ?AAO | `detect_shadow_dots_ex()` returns weak caps at half threshold; `_recover_grid_dots()` adds those that sit on the grid of the strong dots AND have >= 0.5x the median cap+shadow strength (a 0.35x speck turned QUIZ into QUSZ) |
| `UsbWebcamDevice` probe: 1.5 s warm-up per device, 6 s total, two backends per device. On the laptop, camera 0 is an IR camera streaming black frames; the budget ran out during camera 1's warm-up -> "No functional USB camera found" (config has `usb_device: 0`; `scan_1.png` is an all-black 1280x720 frame) | laptop probe log | `WARMUP_S = 2.5`, `PROBE_TOTAL_S = 20`, a device that only streams black frames is skipped after ~1 s without trying a second backend |
| `real_embossed_sidelit_2.jpg` line "open" still decodes as `?AAO` | 6 of 13 dots have a detectable shadow in that darker shot | open: needs brighter / lower-angle light at capture time |

Regression: `tests/data/real_embossed_sidelit_3.jpg` (= `real_scan_1.jpg`) lines NEST / OPEN / PEN
added to `test_scanner_ink_and_embossed.py`. Scanner + OBR + camera test set: 106 passed.

Still to verify ON THE PI: which `/dev/video*` node the probe selects, that live frames are not
black (`python3 tools/camera_live_diagnostic.py`), and a real scan's `debug_scan/last_scan.jpg`.

### 9b. Raspberry Pi / camera-driver check (same day)

The Pi could not be reached: its previous addresses in the laptop's SSH known_hosts are on
the home router (192.168.0.x) and the laptop's mobile hotspot (192.168.137.x), while the
laptop is now on a different Wi-Fi (172.18.109.x) with no hotspot active. Nothing below was
run on the device.

Driver review (README: Raspberry Pi Camera Module v2 / IMX219; config.json: `driver: "usb"`):

| Issue | Fix |
|---|---|
| `UsbWebcamDevice` numeric fallbacks 0/1/2 bypassed the sysfs name filter. On a Pi 5 with a CSI camera, index 0 is an `rp1-cfe` pipeline node that opens but never streams, burning the probe budget | `_v4l2_node_name()` + `ISP_NODE_NAMES` filter applied to indices too |
| `PiCameraDevice` fallback accepted `cv2.VideoCapture(0)` on `isOpened()` alone, so a CFE/ISP node made the camera "active" while every frame was None/black | fallback now requires one real non-black frame within 2.5 s |
| `setup_new_sd.sh` never installed `python3-picamera2`; the venv is created without system site packages, so the Pi Camera path depends on the `/usr/lib/python3/dist-packages` sys.path hack | apt package added; verify with `tools/pi_camera_report.sh` |
| On the laptop the black `usb_device: 0` is an **Iriun Webcam** virtual device (phone not connected), the integrated camera is index 1 | handled by the black-frame skip above |
| IMX219 (Camera v2) is fixed-focus at ~1 m from the factory; at 12-15 cm everything is blurred unless the lens ring is turned (README "Lighting & Focus Guide") | hardware: must be checked on the device |

New: `tools/pi_camera_report.sh` collects OS, config.txt camera lines, libcamera list, V4L2
node names, groups, venv imports (cv2/picamera2), the app's own probe result, a live frame's
brightness/sharpness and one real decode. Tests: `test_camera_probe.py::TestPi5NodeFiltering`.

### 9c. Correction: the Pi camera is a USB webcam (user, same day)

The relevant path is `UsbWebcamDevice` over V4L2. USB-webcam-specific points on a Pi 5:

| Point | Status |
|---|---|
| Probe list named the same node twice (`0` and `/dev/video0`), and the configured `usb_device: 0` once more | indices are now normalised to `/dev/videoN` on Linux (ISP/CFE names still skipped) |
| A UVC webcam exposes `/dev/video0` (capture) + `/dev/video1` (metadata, same name). The metadata node opens but never streams | probed after video0; the "no frames -> next" rule (9b) keeps it cheap |
| Black / dropped frames from USB power limits: a Pi 5 on a non-PD power bank caps the USB ports at 600 mA; a webcam + keyboard can exceed that | hardware; `tools/pi_camera_report.sh` now prints `vcgencmd get_throttled`, `usb_max_current_enable`, and under-voltage/disconnect dmesg lines |
| Fixed-focus webcams are sharp from ~30 cm; at the 10-15 cm scanning distance they blur. Autofocus webcams hunt there (`vision.autofocus=false` + `focus_value` locks them) | hardware; report prints the node's focus controls and supported MJPG sizes |

Nothing in 9b/9c was run on the device (not reachable). `test_camera_probe.py`: 9 passed.
