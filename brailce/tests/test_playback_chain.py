"""
Playback chain regression tests.
================================

Guards the reference-simple playback pipeline restored in JOB 2:
  (a) play_wav_file() hands the ORIGINAL wav path to aplay (`aplay -q <file>`)
      and never creates or rewrites a temp WAV;
  (b) no source file under brailce/speech contains "np.repeat"
      (the buzzy 22050->44100 upsampler must never come back);
  (c) bank WAV samples are 22050 Hz mono 16-bit.
"""

from __future__ import annotations

import glob
import os
import sys
import wave
from unittest.mock import MagicMock, patch

import pytest

from brailce.speech.audio_device import AudioDeviceCoordinator

AUDIO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "audio"))
SPEECH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "speech"))


def _write_tiny_wav(path: str) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 220)


class TestPlayWavFileSimpleAplay:
    """play_wav_file must run `aplay -q <original path>` with zero file rewriting."""

    @pytest.fixture
    def sample_wav(self, tmp_path):
        wav = tmp_path / "x.wav"
        _write_tiny_wav(str(wav))
        return str(wav)

    def _fake_popen(self, returncodes=(0,)):
        procs = [MagicMock(returncode=rc, poll=MagicMock(return_value=rc)) for rc in returncodes]
        popen = MagicMock(side_effect=procs)
        return popen

    def test_aplay_gets_original_path_and_no_temp_file(self, sample_wav, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        fake_popen = self._fake_popen((0,))

        with patch("brailce.speech.audio_device.subprocess.Popen", fake_popen), \
             patch("tempfile.mkstemp") as fake_mkstemp, \
             patch("wave.open") as fake_wave_open:
            ok = AudioDeviceCoordinator.play_wav_file(sample_wav, owner="test")

        assert ok is True
        fake_popen.assert_called_once()
        cmd = fake_popen.call_args[0][0]
        assert cmd == ["aplay", "-q", sample_wav]
        assert sample_wav in cmd  # ORIGINAL path, never a temp/rewritten file
        fake_mkstemp.assert_not_called()
        fake_wave_open.assert_not_called()

    def test_nonzero_exit_retries_once_on_plughw_with_original_path(self, sample_wav, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        fake_popen = self._fake_popen((1, 0))  # default fails, plughw retry succeeds

        with patch("brailce.speech.audio_device.subprocess.Popen", fake_popen), \
             patch("brailce.speech.audio_device._find_best_alsa_device", return_value="plughw:1,0"), \
             patch("tempfile.mkstemp") as fake_mkstemp:
            ok = AudioDeviceCoordinator.play_wav_file(sample_wav, owner="test")

        assert ok is True
        assert fake_popen.call_count == 2
        first_cmd = fake_popen.call_args_list[0][0][0]
        second_cmd = fake_popen.call_args_list[1][0][0]
        assert first_cmd == ["aplay", "-q", sample_wav]
        assert second_cmd == ["aplay", "-D", "plughw:1,0", "-q", sample_wav]
        assert sample_wav in first_cmd and sample_wav in second_cmd
        fake_mkstemp.assert_not_called()

    def test_missing_file_returns_false_without_aplay(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        fake_popen = self._fake_popen((0,))
        with patch("brailce.speech.audio_device.subprocess.Popen", fake_popen):
            assert AudioDeviceCoordinator.play_wav_file(str(tmp_path / "nope.wav")) is False
        fake_popen.assert_not_called()


def test_no_np_repeat_in_speech_sources():
    """The buzzy np.repeat upsampler must be absent from every brailce/speech source."""
    py_files = glob.glob(os.path.join(SPEECH_ROOT, "*.py"))
    assert py_files, f"No sources found under {SPEECH_ROOT}"
    offenders = []
    for path in py_files:
        with open(path, "r", encoding="utf-8") as fp:
            if "np.repeat" in fp.read():
                offenders.append(os.path.relpath(path, SPEECH_ROOT))
    assert not offenders, f"np.repeat found in brailce/speech: {offenders}"


def test_bank_sample_wavs_are_22050_mono_16bit():
    """Sample bank files must be 22050 Hz / 1 channel / 16-bit PCM."""
    samples = [
        os.path.join(AUDIO_ROOT, "characters", "char_A.wav"),
        os.path.join(AUDIO_ROOT, "filipino", "word_ano.wav"),
    ]
    for path in samples:
        assert os.path.isfile(path), f"Missing bank WAV: {path}"
        with wave.open(path, "rb") as wf:
            assert wf.getframerate() == 22050, f"{path}: {wf.getframerate()} Hz != 22050"
            assert wf.getnchannels() == 1, f"{path}: {wf.getnchannels()} channels != 1"
            assert wf.getsampwidth() == 2, f"{path}: {wf.getsampwidth() * 8}-bit != 16-bit"
