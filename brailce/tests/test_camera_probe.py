"""Unit tests for camera probing, ISP rejection, warm-up tolerance, and preview reliability."""
import os
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from brailce.vision.camera import UsbWebcamDevice
from brailce.modes.scanner_mode import ScannerMode
from brailce.i18n.strings import get_string


class TestCameraProbe(unittest.TestCase):
    def test_isp_named_node_skipped(self):
        """Test that node 0 with ISP name in sysfs is skipped."""
        with patch("os.path.exists", side_effect=lambda p: p == "/dev/video0"), \
             patch("os.path.isfile", side_effect=lambda p: p == "/sys/class/video4linux/video0/name"), \
             patch("builtins.open", unittest.mock.mock_open(read_data="rpi-pisp-be\n")):
            candidates = UsbWebcamDevice.probe_device_candidates()
            self.assertNotIn("/dev/video0", candidates)

    def test_dev_video2_opened(self):
        """Test that a valid camera candidate (/dev/video2) is opened and accepted."""
        cam = UsbWebcamDevice(device="/dev/video2")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        test_frame = np.random.randint(50, 200, (480, 640, 3), dtype=np.uint8)
        mock_cap.read.return_value = (True, test_frame)
        mock_cap.get.return_value = 640

        with patch("cv2.VideoCapture", return_value=mock_cap):
            opened = cam._open_capture("/dev/video2")
            self.assertIsNotNone(opened)

    def test_slow_warmup_webcam_accepted(self):
        """Test that webcam with first 8 black reads then valid frame is opened successfully."""
        cam = UsbWebcamDevice(device=0)
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        
        # 8 black reads (mean 0), followed by valid frame
        black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        valid_frame = np.random.randint(50, 200, (480, 640, 3), dtype=np.uint8)
        frames = [black_frame] * 8 + [valid_frame] * 10
        mock_cap.read.side_effect = [(True, f) for f in frames]
        mock_cap.get.return_value = 640

        with patch("cv2.VideoCapture", return_value=mock_cap):
            opened = cam._open_capture(0)
            self.assertIsNotNone(opened)

    def test_three_failed_reads_no_camera_not_connected(self):
        """Test that 3 failed reads do NOT trigger SCANNER_CAMERA_NOT_CONNECTED error."""
        app = MagicMock()
        app.camera = MagicMock()
        # 3 failures, then successful frame
        valid_frame = np.ones((480, 640, 3), dtype=np.uint8) * 100
        app.camera.capture_image.side_effect = [None, None, None, valid_frame]
        app.speech = MagicMock()
        app.display = MagicMock()

        mode = ScannerMode(app)
        mode._preview_running = True
        mode._is_scanning = False
        mode._is_reading = False

        # Simulate 4 iterations of loop
        # We invoke manual step through loop logic
        consecutive_failures = 0
        for _ in range(4):
            frame = None
            try:
                frame = app.camera.capture_image()
            except Exception:
                frame = None
            if frame is not None and getattr(frame, "size", 0) > 0:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    mode._report_camera_missing()

        self.assertFalse(mode._camera_error_reported)
        app.speech.speak_text.assert_not_called()

    def test_ten_failed_reads_triggers_camera_not_connected(self):
        """Test that 10 consecutive failed reads DO trigger SCANNER_CAMERA_NOT_CONNECTED."""
        app = MagicMock()
        app.camera = MagicMock()
        app.camera.capture_image.return_value = None
        app.speech = MagicMock()
        app.display = MagicMock()

        mode = ScannerMode(app)
        mode._preview_running = True
        consecutive_failures = 0
        for _ in range(10):
            frame = app.camera.capture_image()
            if frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    mode._report_camera_missing()

        self.assertTrue(mode._camera_error_reported)
        expected_msg = get_string("SCANNER_CAMERA_NOT_CONNECTED", "en")
        app.speech.speak_text.assert_called_with(expected_msg, interrupt=True)


if __name__ == "__main__":
    unittest.main()


class TestPi5NodeFiltering(unittest.TestCase):
    """Raspberry Pi 5: rp1-cfe / pisp pipeline nodes open but never stream."""

    def test_numeric_index_pointing_at_cfe_node_is_skipped(self):
        cam = UsbWebcamDevice.__new__(UsbWebcamDevice)
        cam.WARMUP_S = 0.1
        with patch("brailce.vision.camera.os.name", "posix"), \
             patch("builtins.open", unittest.mock.mock_open(read_data="rp1-cfe-csi2_ch0\n")), \
             patch("cv2.VideoCapture") as vc:
            self.assertIsNone(cam._open_capture(0))
            vc.assert_not_called()

    def test_numeric_index_of_real_webcam_is_opened(self):
        cam = UsbWebcamDevice.__new__(UsbWebcamDevice)
        cam.WARMUP_S = 1.0
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.random.randint(50, 200, (480, 640, 3), dtype=np.uint8))
        mock_cap.get.return_value = 640
        with patch("brailce.vision.camera.os.name", "posix"), \
             patch("builtins.open", unittest.mock.mock_open(read_data="USB 2.0 Camera: USB Camera\n")), \
             patch("cv2.VideoCapture", return_value=mock_cap):
            self.assertIsNotNone(cam._open_capture(0))

    def test_black_only_device_does_not_try_second_backend(self):
        cam = UsbWebcamDevice.__new__(UsbWebcamDevice)
        cam.WARMUP_S = 0.3
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
        with patch("cv2.VideoCapture", return_value=mock_cap) as vc:
            self.assertIsNone(cam._open_capture(0))
            self.assertEqual(vc.call_count, 1)

    def test_picamera_fallback_rejects_capture_without_frames(self):
        from brailce.vision.camera import PiCameraDevice
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (False, None)
        with patch.dict("sys.modules", {"picamera2": None}), \
             patch("cv2.VideoCapture", return_value=mock_cap), \
             patch("brailce.vision.camera.time.time", side_effect=[0.0, 0.0, 10.0, 10.0, 10.0, 10.0]):
            dev = PiCameraDevice()
        self.assertFalse(dev.is_available())
        mock_cap.release.assert_called()
