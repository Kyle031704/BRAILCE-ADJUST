"""
Camera interface for capturing physical Braille documents.
==========================================================

Supports:
  - PiCameraDevice: Raspberry Pi Camera Module V2 (Sony IMX219) via Picamera2
    (or OpenCV V4L2 fallback), configured with RGB888 stream layout (matching OpenCV BGR
    memory order without cyan/blue cast), settled AE/AWB locking, and sharpness selection.
    Raises / reports missing hardware cleanly instead of faking a synthetic Braille frame.
  - SimulatedCamera: Reads an image file from disk (or local webcam) for desktop testing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple, List, Dict

try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np

logger = logging.getLogger(__name__)

# Channel order toggle for preview display (Task 3)
# Set to True if red and blue channels need to be swapped on your hardware display.
SWAP_RED_BLUE: bool = False


class CameraNotConnectedError(RuntimeError):
    """Raised when camera hardware is missing or disconnected."""


def compute_sharpness(frame: Optional[np.ndarray]) -> float:
    """Return variance of Laplacian sharpness metric for a BGR/gray frame."""
    if frame is None or cv2 is None or frame.size == 0:
        return 0.0
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 0.0


class CameraDevice(ABC):
    """Abstract base class for camera capture."""

    @abstractmethod
    def capture_image(self) -> Optional[np.ndarray]:
        """Capture and return an image as a numpy array (BGR)."""
        pass

    def is_available(self) -> bool:
        """Return True if the camera source is connected and usable."""
        return True

    def capture_still(self) -> Optional[np.ndarray]:
        """Capture a high-quality still frame (defaults to capture_image)."""
        return self.capture_image()

    def flush_and_capture(self, num_grabs: int = 5) -> Optional[np.ndarray]:
        """Flush stale camera hardware frames and capture an instantaneous fresh frame."""
        return self.capture_still()

    def capture_sharpest(self, n: int = 5) -> Optional[np.ndarray]:
        """Capture N frames and return the one with the highest Laplacian sharpness."""
        best_frame = None
        best_score = -1.0
        for _ in range(max(1, n)):
            frame = self.capture_still()
            if frame is None:
                continue
            score = compute_sharpness(frame)
            if score > best_score:
                best_score = score
                best_frame = frame
            time.sleep(0.03)
        return best_frame


class SimulatedCamera(CameraDevice):
    """Simulates a camera by loading an image from disk or using a local webcam."""

    def __init__(self, image_path: Optional[str] = None):
        self.image_path = image_path
        self._cap = None
        if not image_path and cv2 is not None:
            try:
                cap = cv2.VideoCapture(0)
                if cap is not None and cap.isOpened():
                    self._cap = cap
                elif cap is not None:
                    cap.release()
            except Exception:
                self._cap = None

    def is_available(self) -> bool:
        if self.image_path and os.path.exists(self.image_path):
            return True
        return bool(self._cap and self._cap.isOpened())

    def capture_image(self) -> Optional[np.ndarray]:
        if self.image_path and os.path.exists(self.image_path) and cv2 is not None:
            try:
                img = cv2.imread(self.image_path)
                if img is not None:
                    return img
            except Exception:
                pass

        if self._cap and self._cap.isOpened():
            try:
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    return frame
            except Exception:
                pass

        return None

    def flush_and_capture(self, num_grabs: int = 5) -> Optional[np.ndarray]:
        """Flush camera buffer and capture fresh frame."""
        if self._cap and self._cap.isOpened():
            for _ in range(max(1, num_grabs)):
                self._cap.grab()
            ret, frame = self._cap.read()
            if ret and frame is not None and frame.size > 0:
                return frame
        return self.capture_image()

    def close(self) -> None:
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None


class PiCameraDevice(CameraDevice):
    """
    Hardware driver for Raspberry Pi Camera Module V2 (Sony IMX219) on Raspberry Pi 5.

    Uses Picamera2 ("RGB888" -> OpenCV BGR byte order without cyan cast) at 1640x1232
    full-FOV 2x2 binned sensor mode, settles AWB/exposure, and never fabricates a
    synthetic Braille image when hardware is disconnected.
    """

    def __init__(self, resolution: tuple[int, int] = (1640, 1232)):
        self.resolution = resolution
        self._picam2 = None
        self._still_config = None
        self._cap = None
        self._is_active = False
        self.init_error: Optional[str] = None
        self._init_camera()

    def _init_camera(self) -> bool:
        """Attempt to initialize Picamera2 (or V4L2 OpenCV fallback)."""
        import sys
        import glob

        dist_paths = ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3.*/dist-packages")
        for dp in dist_paths:
            if os.path.isdir(dp) and dp not in sys.path:
                sys.path.insert(0, dp)

        try:
            from picamera2 import Picamera2
            self._picam2 = Picamera2()
            # In Picamera2, format="RGB888" delivers B-G-R bytes in memory for OpenCV,
            # avoiding the cyan/blue channel swap seen with "BGR888".
            preview_config = self._picam2.create_preview_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
            self._still_config = self._picam2.create_still_configuration(
                main={"size": self.resolution, "format": "RGB888"}
            )
            self._picam2.configure(preview_config)
            self._picam2.start()

            # Keep standard natural color balance (AwbEnable=True, Contrast=1.0, Sharpness=1.0)
            try:
                self._picam2.set_controls({
                    "Sharpness": 1.0,
                    "Contrast": 1.0,
                    "AwbEnable": True,
                })
            except Exception:
                pass

            self._is_active = True
            self.init_error = None
            logger.info("PiCameraDevice: Picamera2 initialized cleanly (still=%s)", self.resolution)
            return True
        except Exception as exc:
            logger.warning("PiCameraDevice: Picamera2 unavailable (%s); trying OpenCV VideoCapture(0).", exc)
            # Fallback to OpenCV V4L2 webcam if physically present (Linux/USB only).
            # On a Pi 5, /dev/video0 is usually an rp1-cfe/pisp pipeline node that OPENS
            # but never delivers a usable frame, so an isOpened() check alone used to mark
            # the camera "active" and the app then saw only None/black frames. Require a
            # real, non-black frame before accepting the fallback.
            if cv2 is not None:
                cap = None
                try:
                    cap = cv2.VideoCapture(0)
                    if cap is not None and cap.isOpened():
                        got = False
                        t0 = time.time()
                        while time.time() - t0 < 2.5:
                            ret, frame = cap.read()
                            if ret and frame is not None and frame.size > 0 and float(np.mean(frame)) > 1.0 and float(np.std(frame)) > 1.0:
                                got = True
                                break
                            time.sleep(0.04)
                        if got:
                            self._cap = cap
                            self._is_active = True
                            self.init_error = None
                            logger.info("PiCameraDevice: Initialized via OpenCV VideoCapture(0).")
                            return True
                        logger.warning("PiCameraDevice: VideoCapture(0) opened but delivered no usable frame (ISP/CFE node?).")
                    if cap is not None:
                        cap.release()
                except Exception:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass

            self._picam2 = None
            self._cap = None
            self._is_active = False
            self.init_error = f"Camera not connected: {exc}"
            logger.warning("PiCameraDevice unavailable: %s", self.init_error)
            return False

    def is_available(self) -> bool:
        """Check if real camera hardware is connected and active."""
        return self._is_active

    def capture_image(self) -> Optional[np.ndarray]:
        """Capture a preview frame (raises CameraNotConnectedError if disconnected)."""
        if not self._is_active:
            raise CameraNotConnectedError(self.init_error or "Camera not connected")

        if self._picam2 is not None:
            try:
                return self._picam2.capture_array("main")
            except Exception as exc:
                logger.warning("Picamera2 preview capture error: %s", exc)
                return None

        if self._cap and self._cap.isOpened():
            try:
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    return frame
            except Exception as exc:
                logger.warning("OpenCV capture error: %s", exc)
                return None

        raise CameraNotConnectedError("Camera stream lost")

    def capture_still(self) -> Optional[np.ndarray]:
        """Capture a full-resolution still frame via Picamera2 still configuration."""
        if not self._is_active:
            raise CameraNotConnectedError(self.init_error or "Camera not connected")

        if self._picam2 is not None and self._still_config is not None:
            try:
                return self._picam2.switch_mode_and_capture_array(self._still_config, "main")
            except Exception as exc:
                logger.debug("switch_mode_and_capture_array fallback to main: %s", exc)
                return self.capture_image()

        return self.capture_image()

    def flush_and_capture(self, num_grabs: int = 5) -> Optional[np.ndarray]:
        """Flush stale camera hardware frames and capture an instantaneous fresh frame."""
        if not self._is_active:
            raise CameraNotConnectedError(self.init_error or "Camera not connected")
        if self._cap and self._cap.isOpened():
            for _ in range(max(1, num_grabs)):
                self._cap.grab()
            ret, frame = self._cap.read()
            if ret and frame is not None and frame.size > 0:
                return frame
        return self.capture_still()

    def close(self) -> None:
        """Safely release camera hardware."""
        if self._picam2 and self._is_active:
            try:
                self._picam2.stop()
                self._picam2.close()
            except Exception:
                pass
            self._picam2 = None
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._is_active = False


class UsbWebcamDevice(CameraDevice):
    """
    Hardware driver for USB Webcams on Raspberry Pi 5 & dev PCs using OpenCV + V4L2.

    Features:
    - Auto-detection across candidate /dev/video* nodes or device indices (skips metadata/ISP nodes).
    - MJPG hardware encoding at configurable resolution (default 1280x720).
    - Zero stale-frame latency: background capture thread with buffer size 1.
    - Autofocus and manual focus controls (CAP_PROP_AUTOFOCUS, CAP_PROP_FOCUS).
    - Automatic reconnection retry on disconnect without crashing.
    """

    def __init__(
        self,
        device: Any = 0,
        resolution: tuple[int, int] = (1280, 720),
        still_resolution: tuple[int, int] = (1920, 1080),
        fps: int = 30,
        autofocus: bool = True,
        focus_value: int = -1,
    ) -> None:
        self.device = os.environ.get("BRAILCE_CAMERA_DEVICE", device)
        self.resolution = resolution
        self.still_resolution = still_resolution
        self.fps = fps
        self.autofocus = autofocus
        self.focus_value = focus_value
        self._cap: Optional[Any] = None
        self._is_active = False
        self.init_error: Optional[str] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_ts: float = 0.0  # monotonic time the latest frame was read
        self._lock = threading.Lock()
        # cv2.VideoCapture is NOT thread-safe: every read/grab/release goes through this lock.
        self._cap_lock = threading.Lock()
        # Prevents overlapping reconnect attempts (preview loop + capture_image both trigger them).
        self._reconnect_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._init_camera()

    # A buffered frame older than this is treated as stale (camera unplugged / stream stalled).
    STALE_FRAME_S: float = 1.5
    # Probe budget. Opening a DirectShow/V4L2 device costs ~1 s and a webcam's auto-exposure
    # needs up to ~2 s before it delivers a non-black frame. The old 1.5 s / 6 s budget was
    # exhausted by a black first device (IR camera, covered lens, metadata node) before the
    # real camera got a full warm-up, ending in "No functional USB camera found".
    WARMUP_S: float = 2.5
    PROBE_TOTAL_S: float = 20.0

    def _reader_alive(self) -> bool:
        return bool(self._running and self._thread is not None and self._thread.is_alive())

    def _fresh_frame(self, newer_than: float = 0.0) -> Optional[np.ndarray]:
        """Return a copy of the buffered frame if it is not stale (and newer than `newer_than`)."""
        with self._lock:
            if self._latest_frame is None:
                return None
            ts = self._latest_ts
            if time.monotonic() - ts > self.STALE_FRAME_S or ts <= newer_than:
                return None
            return self._latest_frame.copy()

    def _wait_fresh_frame(self, newer_than: float, timeout: float) -> Optional[np.ndarray]:
        """Wait (up to timeout) for the reader thread to deliver a frame newer than `newer_than`."""
        end = time.monotonic() + timeout
        while True:
            frame = self._fresh_frame(newer_than)
            if frame is not None or time.monotonic() >= end or not self._reader_alive():
                return frame
            time.sleep(0.01)

    def _direct_read(self, num_grabs: int = 0) -> Optional[np.ndarray]:
        """Read straight from the device (only used when the reader thread is not running)."""
        with self._cap_lock:
            if not self._cap or not self._cap.isOpened():
                return None
            for _ in range(max(0, num_grabs)):
                self._cap.grab()
            ret, frame = self._cap.read()
        if ret and frame is not None and frame.size > 0:
            with self._lock:
                self._latest_frame = frame
                self._latest_ts = time.monotonic()
            return frame.copy()
        return None

    @staticmethod
    def probe_device_candidates() -> list[Any]:
        """Discover candidate webcam device nodes or indices (excluding Pi hardware ISP nodes)."""
        candidates: list[Any] = []
        env_dev = os.environ.get("BRAILCE_CAMERA_DEVICE")
        if env_dev is not None:
            candidates.append(int(env_dev) if env_dev.isdigit() else env_dev)

        # Linux V4L2 device nodes: symlinks in /dev/v4l/by-id and /dev/v4l/by-path are real USB cameras
        import glob
        for p in sorted(glob.glob("/dev/v4l/by-id/*")) + sorted(glob.glob("/dev/v4l/by-path/*")):
            try:
                candidates.append(os.path.realpath(p))
            except Exception:
                candidates.append(p)

        # Only check low-index video nodes (0 to 9). Skip ISP/codec hardware queues.
        ISP_FILTER = ("isp", "codec", "meta", "cfe", "pisp", "hevc", "rpi-hevc", "bcm2835")
        for idx in range(10):
            node = f"/dev/video{idx}"
            if os.path.exists(node):
                name_file = f"/sys/class/video4linux/video{idx}/name"
                if os.path.isfile(name_file):
                    try:
                        with open(name_file, "r") as f:
                            dname = f.read().lower()
                        if any(k in dname for k in ISP_FILTER):
                            continue
                    except Exception:
                        pass
                candidates.append(node)

        # Fallback numeric indices (only 0, 1, 2). On Linux an index IS /dev/videoN, so name
        # it that way: otherwise 0 and "/dev/video0" are probed twice (and a USB webcam's
        # metadata node, or a Pi 5 rp1-cfe node, would be opened again under its index).
        for idx in (0, 1, 2):
            if os.name != "nt" and os.path.exists(f"/dev/video{idx}"):
                dname = UsbWebcamDevice._v4l2_node_name(idx)
                if dname and any(k in dname for k in ISP_FILTER):
                    continue
                candidates.append(f"/dev/video{idx}")
            else:
                candidates.append(idx)

        # De-duplicate, resolve symlinks, and guard against high-index nodes
        seen = set()
        unique = []
        for c in candidates:
            resolved = c
            if isinstance(c, str) and os.path.isabs(c):
                try:
                    resolved = os.path.realpath(c)
                except Exception:
                    resolved = c
            if isinstance(resolved, str) and resolved.startswith("/dev/video"):
                try:
                    num = int(resolved.replace("/dev/video", ""))
                    if num >= 10:
                        continue
                except ValueError:
                    pass
            key = str(resolved)
            if key not in seen:
                seen.add(key)
                unique.append(resolved)

        return unique

    # V4L2 nodes that are never a camera: Pi 5 rp1-cfe / pisp pipeline queues, codecs, metadata.
    ISP_NODE_NAMES: tuple = ("isp", "codec", "meta", "cfe", "pisp", "hevc", "rpi-hevc", "bcm2835")

    @classmethod
    def _v4l2_node_name(cls, target: Any) -> str:
        """sysfs name of a Linux video node (index or /dev/videoN), '' when unknown."""
        if os.name == "nt":
            return ""
        node = f"/dev/video{target}" if isinstance(target, int) else str(target)
        if not node.startswith("/dev/video"):
            return ""
        name_file = f"/sys/class/video4linux/{os.path.basename(node)}/name"
        try:
            with open(name_file, "r") as f:
                return f.read().strip().lower()
        except Exception:
            return ""

    def _open_capture(self, dev: Any, deadline: Optional[float] = None) -> Optional[Any]:
        if cv2 is None:
            return None
        target = int(dev) if str(dev).isdigit() else str(dev)

        # The numeric fallbacks 0/1/2 bypassed the sysfs name filter: on a Pi 5 with a CSI
        # camera, index 0 is an rp1-cfe node that opens but never streams, eating the probe
        # budget. Apply the same filter to indices.
        node_name = self._v4l2_node_name(target)
        if node_name and any(k in node_name for k in self.ISP_NODE_NAMES):
            logger.info("UsbWebcamDevice: Skipping %s (%s is a pipeline/metadata node, not a camera)", target, node_name)
            return None

        # Never try high-index /dev/video nodes (Pi 5 ISP/codec hardware queues)
        if isinstance(target, str) and target.startswith("/dev/video"):
            try:
                num = int(target.replace("/dev/video", ""))
                if num >= 10:
                    logger.warning("UsbWebcamDevice: Skipping candidate %s (ISP/codec node >= 10)", target)
                    return None
            except ValueError:
                pass

        if os.name == "nt":
            backends = [getattr(cv2, "CAP_DSHOW", 700), cv2.CAP_ANY]
        else:
            backends = [getattr(cv2, "CAP_V4L2", 200), cv2.CAP_ANY]

        for backend in backends:
            if deadline is not None and time.time() >= deadline:
                logger.warning("UsbWebcamDevice: Whole probe cap reached before testing %s on backend %s", target, backend)
                break
            cap = None
            try:
                cap = cv2.VideoCapture(target, backend)
                if cap is None or not cap.isOpened():
                    logger.warning("UsbWebcamDevice: Candidate %s failed to open with backend %s", target, backend)
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass
                    continue

                # Request MJPG + 640x480 (retry default format if refused)
                try:
                    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                    cap.set(cv2.CAP_PROP_FOURCC, fourcc)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                except Exception:
                    pass

                # Warm-up: read until the auto-exposure delivers a non-black frame
                # (up to WARMUP_S per device; a frame-count cap alone ends too early on
                # cameras that stream black frames at 30 fps while exposure settles).
                warmup_s = float(getattr(self, "WARMUP_S", 2.5))
                start_time = time.time()
                valid = False
                black_frames = 0
                cut_by_deadline = False
                while True:
                    if deadline is not None and time.time() >= deadline:
                        cut_by_deadline = True
                        break
                    if time.time() - start_time > warmup_s:
                        break
                    ret, test_frame = cap.read()
                    if ret and test_frame is not None and test_frame.size > 0:
                        m = float(np.mean(test_frame))
                        s = float(np.std(test_frame))
                        if m > 1.0 and s > 1.0:
                            valid = True
                            break
                        black_frames += 1
                        # A camera that has streamed black at full rate for over a second is
                        # not warming up (IR/depth camera, covered lens): stop early.
                        if black_frames >= 25 and time.time() - start_time > 1.0:
                            break
                    time.sleep(0.04)

                if valid:
                    act_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    act_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    logger.info("UsbWebcamDevice: Opened device %s (%dx%d, backend %s, warm-up OK)", target, act_w, act_h, backend)
                    return cap

                cap.release()
                if cut_by_deadline:
                    logger.warning("UsbWebcamDevice: Candidate %s warm-up cut short by the probe deadline", target)
                    break
                if black_frames > 0:
                    # The device streams, but only black frames: an IR/depth camera, a covered
                    # lens, or a metadata node. Another backend will not change that, so move
                    # on to the next device instead of burning the probe budget here.
                    logger.warning(
                        "UsbWebcamDevice: Candidate %s streams only black frames (%d read) - skipping "
                        "(IR camera / covered lens?)", target, black_frames,
                    )
                    break
                logger.warning("UsbWebcamDevice: Candidate %s opened but delivered no frames on backend %s", target, backend)
            except Exception as exc:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                logger.warning("UsbWebcamDevice: Candidate %s failed on backend %s: %s", target, backend, exc)

        return None

    def _init_camera(self) -> bool:
        if cv2 is None:
            self.init_error = "OpenCV (cv2) is not installed."
            return False

        # Try user-configured device first, then probe candidates
        configured = self.device
        if os.name != "nt" and str(configured).isdigit() and os.path.exists(f"/dev/video{configured}"):
            configured = f"/dev/video{configured}"  # same node as the probe list, so it is not opened twice
        candidates = [configured] + self.probe_device_candidates()
        seen = set()
        unique_cands = []
        for c in candidates:
            s = str(c)
            if s not in seen:
                seen.add(s)
                unique_cands.append(c)

        deadline = time.time() + self.PROBE_TOTAL_S
        for dev in unique_cands:
            if time.time() >= deadline:
                logger.warning("UsbWebcamDevice: Whole probe cap ~%.0fs reached, stopping search.", self.PROBE_TOTAL_S)
                break
            cap = self._open_capture(dev, deadline=deadline)
            if cap is not None:
                self._cap = cap
                self.device = dev
                self._configure_camera()
                self._is_active = True
                self.init_error = None
                self._start_reader_thread()
                w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                logger.info("UsbWebcamDevice: Winner selected device %s (%dx%d @ %d fps)", dev, w, h, self.fps)
                return True

        self._is_active = False
        self.init_error = "No functional USB camera found."
        logger.warning("UsbWebcamDevice: %s", self.init_error)
        return False

    def _configure_camera(self) -> None:
        if not self._cap or cv2 is None:
            return
        try:
            # Set MJPG fourcc if supported
            try:
                fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                self._cap.set(cv2.CAP_PROP_FOURCC, fourcc)
            except Exception:
                pass

            w, h = self.resolution
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            self._cap.set(cv2.CAP_PROP_FPS, self.fps)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # Focus controls
            try:
                env_focus = os.environ.get("BRAILCE_WEBCAM_FOCUS")
                focus_target = int(env_focus) if (env_focus and env_focus.isdigit()) else self.focus_value
                if not self.autofocus and focus_target >= 0:
                    self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
                    self._cap.set(cv2.CAP_PROP_FOCUS, float(focus_target))
                elif self.autofocus:
                    self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            except Exception:
                pass

            # White balance & exposure stabilization for white paper under room light
            try:
                # Keep auto white-balance active
                for wb_prop in (getattr(cv2, "CAP_PROP_AUTO_WB_TEMP", 45), getattr(cv2, "CAP_PROP_AUTOWB", 44)):
                    try:
                        self._cap.set(wb_prop, 1)
                    except Exception:
                        pass

                # Exposure tuning: avoid over-exposing pure white paper
                env_exp = os.environ.get("BRAILCE_WEBCAM_EXPOSURE")
                if env_exp is not None:
                    try:
                        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if os.name == "nt" else 1)
                        self._cap.set(cv2.CAP_PROP_EXPOSURE, float(env_exp))
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("UsbWebcamDevice exposure/wb tuning skipped: %s", exc)
        except Exception as exc:
            logger.debug("UsbWebcamDevice configure warning: %s", exc)

    def _start_reader_thread(self) -> None:
        self._running = True
        self._reader_gen = getattr(self, "_reader_gen", 0) + 1
        self._thread = threading.Thread(
            target=self._capture_worker, args=(self._reader_gen,), daemon=True, name="usb-cam-reader"
        )
        self._thread.start()

    def _capture_worker(self, gen: int = 0) -> None:
        """Continuously grab latest frame to guarantee zero latency and discard queued frames."""
        # Exit if a reconnect has started a newer reader (old one may have been stuck in read()).
        while self._running and gen == getattr(self, "_reader_gen", gen):
            try:
                with self._cap_lock:
                    cap = self._cap
                    if cap is None or not cap.isOpened():
                        ret, frame = False, None
                        opened = False
                    else:
                        opened = True
                        ret, frame = cap.read()
                if not opened:
                    time.sleep(0.5)
                    continue
                if ret and frame is not None and frame.size > 0:
                    with self._lock:
                        self._latest_frame = frame
                        self._latest_ts = time.monotonic()
                else:
                    time.sleep(0.02)
            except Exception:
                time.sleep(0.05)

    def is_available(self) -> bool:
        return self._is_active and bool(self._cap and self._cap.isOpened())

    def capture_image(self) -> Optional[np.ndarray]:
        if not self._is_active:
            raise CameraNotConnectedError(self.init_error or "Camera not connected")

        frame = self._get_frame(timeout=0.5)
        if frame is not None:
            return frame

        # No fresh frame: stream stalled or camera unplugged -> reconnect once.
        logger.warning("USB webcam produced no fresh frame; attempting automatic reconnection...")
        if self.reconnect():
            frame = self._get_frame(timeout=1.0)
            if frame is not None:
                return frame

        raise CameraNotConnectedError("USB Camera stream lost")

    def _get_frame(self, timeout: float, newer_than: float = 0.0, num_grabs: int = 2) -> Optional[np.ndarray]:
        """Fresh frame from the reader thread, or a direct read when the thread is not running."""
        frame = self._fresh_frame(newer_than)
        if frame is not None:
            return frame
        if self._reader_alive():
            return self._wait_fresh_frame(newer_than, timeout)
        return self._direct_read(num_grabs=num_grabs)

    def capture_frame(self) -> Optional[np.ndarray]:
        """Alias for capture_image."""
        return self.capture_image()

    def flush_and_capture(self, num_grabs: int = 5) -> Optional[np.ndarray]:
        """
        Flush OpenCV hardware buffer (4-5 times cap.grab() before read())
        to guarantee zero stale-frame latency on ENTER scan trigger.
        """
        if not self._is_active:
            raise CameraNotConnectedError(self.init_error or "Camera not connected")

        # The reader thread keeps the driver buffer drained, so "flushing" means waiting
        # for a frame that was read *after* this call (guaranteed post-press content).
        # Grabbing directly here while the reader thread runs would race on VideoCapture.
        requested_at = time.monotonic()
        frame = self._get_frame(timeout=1.0, newer_than=requested_at, num_grabs=num_grabs)
        if frame is not None:
            return frame

        return self.capture_image()

    def capture_still(self) -> Optional[np.ndarray]:
        """Capture clean still frame directly, flushing ring buffer to ensure instantaneous freshness."""
        if not self._is_active:
            raise CameraNotConnectedError(self.init_error or "Camera not connected")

        try:
            return self.flush_and_capture(num_grabs=5)
        except CameraNotConnectedError:
            raise
        except Exception:
            frame = self._fresh_frame()
            if frame is not None:
                return frame
            return self.capture_image()

    def reconnect(self) -> bool:
        """Attempt to re-initialize camera connection without crashing.

        Only one reconnect runs at a time; concurrent callers return False immediately
        instead of tearing down a camera another thread is in the middle of opening.
        """
        if not self._reconnect_lock.acquire(blocking=False):
            return False
        try:
            self.close()
            time.sleep(0.2)
            return self._init_camera()
        finally:
            self._reconnect_lock.release()

    def close(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        with self._cap_lock:
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
        with self._lock:
            # Drop the buffered frame so a disconnected camera can't keep serving a frozen image.
            self._latest_frame = None
            self._latest_ts = 0.0
        self._is_active = False
