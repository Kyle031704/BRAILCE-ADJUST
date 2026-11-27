"""
Test-suite wide settings.

Integration tests build a real BrailceApp. Without this, app.setup() opens the
computer's real USB webcam and leaves its OpenCV reader thread running, which on
Windows ends in "Windows fatal exception: access violation" dumps mid-run.
Tests that exercise the camera code mock it explicitly and are not affected.
"""

import os

os.environ.setdefault("BRAILCE_VISION_DRIVER", "simulated")
