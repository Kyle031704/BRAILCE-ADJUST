"""
Regression tests for camera/scanner bugs:
  1. USB webcam kept serving a frozen frame forever after unplug (stale buffer never expired).
  2. flush_and_capture() raced the reader thread on the same cv2.VideoCapture.
  3. Overlapping reconnect() calls.
  4. Distance gate made "move closer/farther" guidance unreachable for pitch-based cases.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from brailce.vision.camera import CameraNotConnectedError, UsbWebcamDevice


def _make_cam(read_fn) -> UsbWebcamDevice:
    with patch.object(UsbWebcamDevice, "_init_camera", return_value=True):
        cam = UsbWebcamDevice(device=0)
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.side_effect = read_fn
    cam._cap = cap
    cam._is_active = True
    return cam


def test_stale_frame_is_not_served_after_stream_dies():
    frame = np.full((48, 64, 3), 120, dtype=np.uint8)
    alive = {"on": True}

    def read():
        time.sleep(0.01)
        return (True, frame) if alive["on"] else (False, None)

    cam = _make_cam(read)
    cam.STALE_FRAME_S = 0.2
    cam._start_reader_thread()
    try:
        assert cam.capture_image() is not None
        alive["on"] = False  # simulate unplug
        time.sleep(0.3)
        with patch.object(cam, "reconnect", return_value=False) as rc:
            with pytest.raises(CameraNotConnectedError):
                cam.capture_image()
            rc.assert_called_once()
    finally:
        cam.close()


def test_flush_and_capture_never_touches_cap_concurrently():
    frame = np.full((48, 64, 3), 120, dtype=np.uint8)
    in_read = {"n": 0, "overlap": False}
    guard = threading.Lock()

    def read():
        with guard:
            in_read["n"] += 1
            if in_read["n"] > 1:
                in_read["overlap"] = True
        time.sleep(0.005)
        with guard:
            in_read["n"] -= 1
        return True, frame

    cam = _make_cam(read)
    cam._cap.grab.side_effect = lambda: read()[0]
    cam._start_reader_thread()
    try:
        t0 = time.monotonic()
        for _ in range(10):
            out = cam.flush_and_capture(num_grabs=5)
            assert out is not None
        assert cam._latest_ts >= t0
        assert not in_read["overlap"], "VideoCapture accessed from two threads at once"
    finally:
        cam.close()


def test_reconnect_is_not_reentrant():
    cam = _make_cam(lambda: (False, None))
    started = threading.Event()
    release = threading.Event()

    def slow_init():
        started.set()
        release.wait(2.0)
        return True

    with patch.object(cam, "_init_camera", side_effect=slow_init) as init:
        t = threading.Thread(target=cam.reconnect)
        t.start()
        assert started.wait(2.0)
        assert cam.reconnect() is False  # second caller bails out immediately
        release.set()
        t.join(2.0)
        assert init.call_count == 1


@pytest.mark.parametrize("state,pitch", [("too_far", 6.0), ("too_close", 70.0)])
def test_distance_guidance_reachable_for_pitch_out_of_range(state, pitch):
    from brailce.modes.scanner_mode import ScannerMode

    app = MagicMock()
    app.state.language = "en"
    app.state.ai_processing = False
    app.config.get.side_effect = lambda *a, default=None: default
    app.obr.assess_braille_distance.return_value = {
        "state": state, "confidence": 0.9, "dot_count": 40, "dot_pitch": pitch, "dot_diameter": 5.0,
    }
    mode = ScannerMode(app)
    mode.PRESENCE_POLL_S = 0.01
    mode._latest_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mode._preview_running = True
    t = threading.Thread(target=mode._detection_worker, daemon=True)
    t.start()
    time.sleep(0.15)
    mode._preview_running = False
    t.join(1.0)
    assert mode._braille_detected is True
    assert mode._distance_state == state
    assert app.speech.speak_text.called
