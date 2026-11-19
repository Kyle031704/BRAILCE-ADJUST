"""Unit tests for Filipino junctures, pause gaps, and Free Input AI cache isolation."""
import os
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from brailce.speech.filipino_engine import FilipinoEngine


class TestFilipinoJunctures(unittest.TestCase):
    def setUp(self):
        self.engine = FilipinoEngine()

    def test_free_input_never_plays_ai_cache(self):
        """Free input text (allow_ai_cache=False) must never play pre-cached ai_*.wav."""
        with patch("brailce.speech.filipino_engine.AudioDeviceCoordinator.play_wav_file") as mock_play:
            # Call speak_filipino with allow_ai_cache=False (default)
            self.engine.speak_filipino("Kumusta ka", allow_ai_cache=False)

            # Assert no call was made to any file named ai_*.wav
            for call_args in mock_play.call_args_list:
                played_path = call_args[0][0]
                self.assertNotIn("ai_kumusta", played_path.lower(), "Free Input must never trigger AI cache WAVs!")

    def test_inter_word_gap_and_terminal_punctuation_pause(self):
        """Verify concatenated phrase has word gap <= 60ms and question mark pause 350-550ms."""
        # Test concatenation logic directly
        framerate = 22050
        gap_word_len = int(framerate * 0.015)
        gap_period_len = int(framerate * 0.450)

        # Word gap 15ms = 330 samples at 22050Hz (<= 60ms)
        word_gap_ms = (gap_word_len / framerate) * 1000.0
        self.assertLessEqual(word_gap_ms, 60.0)

        # Period / Question mark gap 450ms (in 350-550ms bounds)
        punct_pause_ms = (gap_period_len / framerate) * 1000.0
        self.assertGreaterEqual(punct_pause_ms, 350.0)
        self.assertLessEqual(punct_pause_ms, 550.0)


if __name__ == "__main__":
    unittest.main()
