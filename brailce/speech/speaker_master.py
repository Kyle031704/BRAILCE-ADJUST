"""
Speaker loudness mastering (louder, still clean)
================================================
The MAX98357A / hifiberry-dac route has no hardware or ALSA volume control, so
loudness is decided by the WAV level alone. Raising plain gain would clip. This
module makes speech louder without clipping:

  1. High-pass filter (2nd-order Butterworth, default 180 Hz). Small speakers
     cannot reproduce deep bass; it only makes the cone over-travel and rattle,
     and it eats headroom. Speech intelligibility lives above 300 Hz.
  2. Gain of ``boost_db``.
  3. Look-ahead peak limiter with a smooth release, so peaks never pass
     ``ceiling``: no hard clipping, no "basag" from the DAC.

With ``boost_db <= 0`` the audio is returned untouched (exactly the old sound).

Playback policy is unchanged: AudioDeviceCoordinator writes the mastered result
to a cache file and passes that file whole to ``aplay -q`` (no stdin piping,
no resampling). Original bank WAVs are never modified.

numpy only (scipy is not installed on the Pi).
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import tempfile
import wave
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_CEILING = 28000.0      # int16 peak ceiling after limiting (full scale = 32767)
DEFAULT_HPF_HZ = 180.0
LOOKAHEAD_MS = 2.0
RELEASE_MS = 60.0


def _highpass(x: np.ndarray, sr: int, fc: float) -> np.ndarray:
    """2nd-order Butterworth high-pass (RBJ biquad), direct form I."""
    if fc <= 0 or fc >= sr / 2:
        return x
    w0 = 2.0 * math.pi * fc / sr
    q = 1.0 / math.sqrt(2.0)
    alpha = math.sin(w0) / (2.0 * q)
    cosw = math.cos(w0)
    b0 = (1 + cosw) / 2
    b1 = -(1 + cosw)
    b2 = (1 + cosw) / 2
    a0 = 1 + alpha
    a1 = -2 * cosw
    a2 = 1 - alpha
    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    # Feed-forward part vectorised, recursive part in a tight loop.
    xp = np.concatenate(([0.0, 0.0], x))
    ff = b0 * xp[2:] + b1 * xp[1:-1] + b2 * xp[:-2]
    y = np.empty_like(ff)
    y1 = y2 = 0.0
    for i in range(ff.size):
        v = ff[i] - a1 * y1 - a2 * y2
        y[i] = v
        y2, y1 = y1, v
    return y


def _limiter_gain(x: np.ndarray, sr: int, ceiling: float) -> np.ndarray:
    """Gain curve (<= 1) that keeps |x * gain| <= ceiling with smooth attack/release."""
    need = np.minimum(1.0, ceiling / (np.abs(x) + 1e-9))
    if float(need.min()) >= 1.0:
        return np.ones_like(x)
    look = max(1, int(sr * LOOKAHEAD_MS / 1000.0))
    # Look-ahead minimum over [n, n + look]
    gmin = need.copy()
    for k in range(1, look + 1):
        np.minimum(gmin[:-k], need[k:], out=gmin[:-k])
    # Smooth attack: average over [n - look, n] (never above the requirement at the peak)
    csum = np.cumsum(np.concatenate(([0.0], gmin)))
    idx = np.arange(gmin.size)
    lo = np.maximum(0, idx - look)
    gavg = (csum[idx + 1] - csum[lo]) / (idx + 1 - lo)
    gavg = np.minimum(gavg, gmin)
    # Release back towards 1 (one-pole), never above the attack curve
    rc = math.exp(-1.0 / (sr * RELEASE_MS / 1000.0))
    g = np.empty_like(gavg)
    prev = 1.0
    for i in range(gavg.size):
        rel = prev + (1.0 - prev) * (1.0 - rc)
        v = gavg[i] if gavg[i] < rel else rel
        g[i] = v
        prev = v
    return g


def master_samples(samples: np.ndarray, sr: int, boost_db: float,
                   ceiling: float = DEFAULT_CEILING, hpf_hz: float = DEFAULT_HPF_HZ) -> np.ndarray:
    """Return int16 samples made louder by boost_db without exceeding ceiling."""
    s = np.asarray(samples)
    if boost_db <= 0 or s.size == 0:
        return s.astype(np.int16, copy=False)
    x = s.astype(np.float64)
    x = _highpass(x, sr, hpf_hz)
    x *= 10.0 ** (boost_db / 20.0)
    x *= _limiter_gain(x, sr, ceiling)
    return np.clip(np.round(x), -ceiling, ceiling).astype(np.int16)


def read_wav(path: str) -> Tuple[np.ndarray, int, int]:
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("only 16-bit PCM supported")
    data = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
        ch = 1
    return data, sr, ch


def write_wav(path: str, samples: np.ndarray, sr: int) -> None:
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    os.replace(tmp, path)


def master_file(src: str, dst: str, boost_db: float,
                ceiling: float = DEFAULT_CEILING, hpf_hz: float = DEFAULT_HPF_HZ) -> None:
    data, sr, _ = read_wav(src)
    write_wav(dst, master_samples(data, sr, boost_db, ceiling, hpf_hz), sr)


class SpeakerMaster:
    """Process-wide loudness setting + on-disk cache of mastered WAVs."""

    boost_db: float = 0.0
    ceiling: float = DEFAULT_CEILING
    hpf_hz: float = DEFAULT_HPF_HZ
    cache_dir: Optional[str] = None

    @classmethod
    def configure(cls, boost_db: float = 0.0, ceiling: float = DEFAULT_CEILING,
                  hpf_hz: float = DEFAULT_HPF_HZ, cache_dir: Optional[str] = None) -> None:
        env = os.environ.get("BRAILCE_LOUDNESS_BOOST_DB")
        if env not in (None, ""):
            try:
                boost_db = float(env)
            except ValueError:
                pass
        cls.boost_db = max(0.0, min(15.0, float(boost_db)))
        cls.ceiling = max(8000.0, min(32000.0, float(ceiling)))
        cls.hpf_hz = max(0.0, float(hpf_hz))
        cls.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "brailce_loud")
        if cls.boost_db > 0:
            logger.info("Speaker loudness: +%.1f dB, ceiling %d, high-pass %d Hz.",
                        cls.boost_db, int(cls.ceiling), int(cls.hpf_hz))

    @classmethod
    def active(cls) -> bool:
        return cls.boost_db > 0

    @classmethod
    def mastered_path(cls, src: str) -> str:
        """Path of the mastered copy of src (created on first use). Falls back to src on error."""
        if not cls.active():
            return src
        try:
            st = os.stat(src)
            key = f"{os.path.abspath(src)}|{st.st_mtime_ns}|{st.st_size}|{cls.boost_db}|{cls.ceiling}|{cls.hpf_hz}"
            name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".wav"
            os.makedirs(cls.cache_dir, exist_ok=True)
            dst = os.path.join(cls.cache_dir, name)
            if not os.path.isfile(dst):
                master_file(src, dst, cls.boost_db, cls.ceiling, cls.hpf_hz)
            return dst
        except Exception as exc:
            logger.debug("Loudness mastering failed for %s: %s", src, exc)
            return src

    @classmethod
    def warm_cache(cls, root: str) -> int:
        """Pre-master every bank WAV under root (run in a background thread)."""
        if not cls.active() or not os.path.isdir(root):
            return 0
        n = 0
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".wav"):
                    cls.mastered_path(os.path.join(dirpath, f))
                    n += 1
        return n
