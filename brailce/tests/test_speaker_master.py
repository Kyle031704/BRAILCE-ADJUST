"""Loudness mastering: louder speech without clipping; 0 dB keeps the original sound."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import numpy as np
import pytest

from brailce.speech import speaker_master as sm
from brailce.speech.audio_device import AudioDeviceCoordinator


def _speech_like(sr: int = 22050, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2
    x = (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 1200 * t)
         + 0.4 * np.sin(2 * np.pi * 60 * t)) * env
    return (x / np.abs(x).max() * 19000).astype(np.int16)


def _active_rms(x: np.ndarray) -> float:
    x = x.astype(float)
    a = x[np.abs(x) > 0.03 * np.abs(x).max()]
    return float(np.sqrt((a ** 2).mean()))


@pytest.fixture(autouse=True)
def _reset_master():
    yield
    sm.SpeakerMaster.configure(boost_db=0.0)


def test_zero_boost_is_bit_identical():
    x = _speech_like()
    assert np.array_equal(sm.master_samples(x, 22050, 0.0), x)


@pytest.mark.parametrize("boost", [3.0, 6.0, 9.0, 12.0])
def test_boost_is_louder_and_never_passes_ceiling(boost):
    x = _speech_like()
    y = sm.master_samples(x, 22050, boost, ceiling=28000)
    assert int(np.abs(y.astype(int)).max()) <= 28000          # no clipping past the ceiling
    # clearly louder (part of a small boost is spent on the removed 60 Hz bass)
    assert 20 * np.log10(_active_rms(y) / _active_rms(x)) > min(1.5, 0.3 * boost)


def test_highpass_removes_deep_bass():
    sr = 22050
    t = np.arange(sr) / sr
    bass = (np.sin(2 * np.pi * 60 * t) * 10000).astype(np.int16)
    voice = (np.sin(2 * np.pi * 1000 * t) * 10000).astype(np.int16)
    yb = sm._highpass(bass.astype(float), sr, 180.0)
    yv = sm._highpass(voice.astype(float), sr, 180.0)
    assert np.abs(yb[sr // 2:]).max() < 0.15 * 10000   # 60 Hz mostly gone
    assert np.abs(yv[sr // 2:]).max() > 0.95 * 10000   # 1 kHz untouched


def test_mastered_copy_is_cached_and_original_untouched(tmp_path):
    src = tmp_path / "word.wav"
    sm.write_wav(str(src), _speech_like(), 22050)
    before = src.read_bytes()
    sm.SpeakerMaster.configure(boost_db=6.0, cache_dir=str(tmp_path / "cache"))
    p1 = sm.SpeakerMaster.mastered_path(str(src))
    p2 = sm.SpeakerMaster.mastered_path(str(src))
    assert p1 == p2 and p1 != str(src) and os.path.isfile(p1)
    assert src.read_bytes() == before


def test_disabled_returns_original_path(tmp_path):
    src = tmp_path / "a.wav"
    sm.write_wav(str(src), _speech_like(), 22050)
    sm.SpeakerMaster.configure(boost_db=0.0)
    assert sm.SpeakerMaster.mastered_path(str(src)) == str(src)


def test_env_override(monkeypatch):
    monkeypatch.setenv("BRAILCE_LOUDNESS_BOOST_DB", "4.5")
    sm.SpeakerMaster.configure(boost_db=0.0)
    assert sm.SpeakerMaster.boost_db == 4.5


def test_play_wav_file_plays_mastered_copy_and_cleans_temp(tmp_path, monkeypatch):
    live = tempfile.NamedTemporaryFile(prefix="brailce_piper_", suffix=".wav", delete=False)
    live.close()
    sm.write_wav(live.name, _speech_like(), 22050)
    sm.SpeakerMaster.configure(boost_db=6.0, cache_dir=str(tmp_path / "cache"))
    played = []
    monkeypatch.setattr("sys.platform", "linux")
    with patch.object(AudioDeviceCoordinator, "_run_aplay",
                      side_effect=lambda cmd, owner, timeout: played.append(cmd[-1]) or True):
        assert AudioDeviceCoordinator.play_wav_file(live.name, owner="piper")
    assert played and played[0] != live.name          # mastered copy was played
    assert not os.path.exists(played[0])              # one-off copy removed afterwards
    os.remove(live.name)
