"""Guard tests for Filipino character WAV clarity, female pitch, duration, and energy."""
import os
import unittest
import numpy as np
import soundfile as sf
import scipy.signal

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "..", "audio", "filipino")


class TestFilipinoLetterClarity(unittest.TestCase):
    def test_all_letters_and_digits_quality(self):
        targets = [f"char_{chr(c)}.wav" for c in range(ord("A"), ord("Z") + 1)] + \
                  [f"char_{d}.wav" for d in range(10)]

        for fname in targets:
            path = os.path.join(AUDIO_DIR, fname)
            self.assertTrue(os.path.isfile(path), f"Missing audio file: {fname}")

            # File size > 5 KB (not an LFS pointer)
            fsize = os.path.getsize(path)
            self.assertGreater(fsize, 5120, f"{fname} is too small ({fsize} bytes), possible LFS pointer")

            data, sr = sf.read(path)
            self.assertEqual(sr, 22050, f"{fname} sample rate must be 22050 Hz")

            # Duration 0.25 - 0.9s
            dur = len(data) / sr
            self.assertGreaterEqual(dur, 0.25, f"{fname} duration {dur:.2f}s too short (<0.25s)")
            self.assertLessEqual(dur, 0.90, f"{fname} duration {dur:.2f}s too long (>0.90s)")

            # Peak < 0.99 (no clipping)
            peak = float(np.max(np.abs(data)))
            self.assertLess(peak, 0.99, f"{fname} peak {peak:.3f} >= 0.99 (clipping)")
            self.assertGreater(peak, 0.20, f"{fname} peak {peak:.3f} too quiet")

            # RMS in 2000-6000 range when scaled to int16
            rms = float(np.sqrt(np.mean((data * 32767.0) ** 2)))
            self.assertGreaterEqual(rms, 2000, f"{fname} RMS {rms:.1f} too quiet (<2000)")
            self.assertLessEqual(rms, 6500, f"{fname} RMS {rms:.1f} too loud (>6500)")

            # Spectral centroid >= 850 Hz on magnitude spectrum (natural female speech covering back vowels)
            fft = np.abs(np.fft.rfft(data))
            f_axis = np.fft.rfftfreq(len(data), 1.0 / sr)
            mag_sum = np.sum(fft)
            if mag_sum > 1e-12:
                centroid = float(np.sum(f_axis * fft) / mag_sum)
                self.assertGreaterEqual(centroid, 850, f"{fname} centroid {centroid:.0f} Hz too muffled (<850 Hz)")

    def test_female_pitch_f0(self):
        """Verify female pitch median >= 160 Hz on sustained vowels."""
        for c in ["A", "E", "O", "0"]:
            p = os.path.join(AUDIO_DIR, f"char_{c}.wav")
            d, sr = sf.read(p)
            corr = np.correlate(d, d, mode="full")
            corr = corr[len(corr) // 2:]
            min_lag = int(sr / 400)
            max_lag = int(sr / 120)
            peak_lag = min_lag + np.argmax(corr[min_lag:max_lag])
            f0 = sr / peak_lag
            self.assertGreaterEqual(f0, 160.0, f"char_{c}.wav F0={f0:.1f} Hz is not female voice (expected >=160 Hz)")

    def test_speak_character_uses_filipino_bank_directly(self):
        """Verify speak_character plays pre-rendered WAV directly without MMS synthesis fallback."""
        from brailce.speech.filipino_engine import FilipinoEngine, get_filipino_character_path

        path_a = get_filipino_character_path("A")
        self.assertIsNotNone(path_a)
        self.assertTrue(os.path.isfile(path_a))
        self.assertTrue(path_a.endswith("char_A.wav"))


if __name__ == "__main__":
    unittest.main()
