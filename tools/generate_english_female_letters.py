"""
Generate all 46 English character WAVs using en-PH-RosaNeural (Female voice).
Standardized to 22050 Hz, 16-bit mono PCM, total duration exactly 0.800s (17,640 samples).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import wave
import edge_tts
import miniaudio
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAR_DIR = PROJECT_ROOT / "brailce" / "audio" / "characters"

VOICE = "en-PH-RosaNeural"
TARGET_SR = 22050
TARGET_SAMPLES = int(TARGET_SR * 0.800)  # 17,640 samples = 0.800s

CHARACTERS = {
    # Letters A-Z
    "A": ("char_A.wav", "ay."),
    "B": ("char_B.wav", "bee."),
    "C": ("char_C.wav", "see."),
    "D": ("char_D.wav", "dee."),
    "E": ("char_E.wav", "e."),
    "F": ("char_F.wav", "ef."),
    "G": ("char_G.wav", "gee."),
    "H": ("char_H.wav", "aitch."),
    "I": ("char_I.wav", "eye."),
    "J": ("char_J.wav", "jay."),
    "K": ("char_K.wav", "kay."),
    "L": ("char_L.wav", "el."),
    "M": ("char_M.wav", "em."),
    "N": ("char_N.wav", "en."),
    "O": ("char_O.wav", "oh."),
    "P": ("char_P.wav", "pee."),
    "Q": ("char_Q.wav", "cue."),
    "R": ("char_R.wav", "ar."),
    "S": ("char_S.wav", "es."),
    "T": ("char_T.wav", "tee."),
    "U": ("char_U.wav", "you."),
    "V": ("char_V.wav", "vee."),
    "W": ("char_W.wav", "double you."),
    "X": ("char_X.wav", "ex."),
    "Y": ("char_Y.wav", "why."),
    "Z": ("char_Z.wav", "zee."),
    # Digits 0-9
    "0": ("char_0.wav", "zero."),
    "1": ("char_1.wav", "one."),
    "2": ("char_2.wav", "two."),
    "3": ("char_3.wav", "three."),
    "4": ("char_4.wav", "four."),
    "5": ("char_5.wav", "five."),
    "6": ("char_6.wav", "six."),
    "7": ("char_7.wav", "seven."),
    "8": ("char_8.wav", "eight."),
    "9": ("char_9.wav", "nine."),
    # Math Operators
    "+": ("char_plus.wav", "plus."),
    "-": ("char_minus.wav", "minus."),
    "*": ("char_times.wav", "times."),
    "/": ("char_divide.wav", "divided by."),
    "=": ("char_equals.wav", "equals."),
    # Punctuation
    ".": ("char_period.wav", "period."),
    ",": ("char_comma.wav", "comma."),
    "?": ("char_question.wav", "question mark."),
    "!": ("char_exclamation.wav", "exclamation mark."),
    "#": ("char_hash.wav", "number sign."),
}


def fit_to_exact_samples(samples: np.ndarray, target_samples: int = TARGET_SAMPLES, sr: int = TARGET_SR) -> bytes:
    """Trim silence and pad to exactly target_samples (0.800s)."""
    x = samples.astype(np.float64)

    # Trim digital silence
    nz = np.where(np.abs(x) > 120)[0]
    if nz.size > 0:
        pad = int(sr * 0.025)
        start = max(0, nz[0] - pad)
        end = min(len(x), nz[-1] + pad)
        core = x[start:end]
    else:
        core = x

    # RMS normalization
    rms = np.sqrt(np.mean(core ** 2)) if len(core) > 0 else 0.0
    if rms > 1e-3:
        core = core * (3400.0 / rms)

    core = np.clip(core, -32000, 32000)

    # 5ms edge fade
    fade_len = min(int(sr * 0.005), len(core) // 6)
    if fade_len > 1:
        fade_in = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fade_len)))
        fade_out = 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, fade_len)))
        core[:fade_len] *= fade_in
        core[-fade_len:] *= fade_out

    # Fit to target length
    if len(core) > target_samples:
        core = np.interp(
            np.linspace(0, len(core), target_samples, endpoint=False),
            np.arange(len(core)),
            core,
        )
    
    rem = target_samples - len(core)
    head_pad = min(int(sr * 0.040), rem // 3)
    tail_pad = rem - head_pad
    padded = np.pad(core, (head_pad, tail_pad), mode="constant")
    assert len(padded) == target_samples

    return np.round(padded).astype(np.int16).tobytes()


async def synthesize_char(label: str, filename: str, spoken: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        out_path = CHAR_DIR / filename
        rate = "+15%" if len(spoken) > 10 else "+0%"
        for attempt in range(3):
            try:
                comm = edge_tts.Communicate(spoken, VOICE, rate=rate)
                mp3_bytes = bytearray()
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        mp3_bytes.extend(chunk["data"])

                if not mp3_bytes:
                    await asyncio.sleep(0.5)
                    continue

                decoded = miniaudio.mp3_read_s16(bytes(mp3_bytes))
                raw_samples = np.frombuffer(decoded.samples, dtype=np.int16)
                if decoded.nchannels == 2:
                    raw_samples = raw_samples.reshape(-1, 2).mean(axis=1).astype(np.int16)

                # Resample to 22050 Hz
                target_len = int(len(raw_samples) * TARGET_SR / decoded.sample_rate)
                resampled = np.interp(
                    np.linspace(0, len(raw_samples), target_len, endpoint=False),
                    np.arange(len(raw_samples)),
                    raw_samples,
                ).astype(np.int16)

                final_pcm = fit_to_exact_samples(resampled, TARGET_SAMPLES, TARGET_SR)

                with wave.open(str(out_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(TARGET_SR)
                    wf.writeframes(final_pcm)

                print(f"[OK] {filename} ({label}) -> {len(final_pcm)} bytes ({len(final_pcm)//2/TARGET_SR:.3f}s)")
                return
            except Exception as e:
                print(f"[RETRY {attempt+1}] {label}: {e}")
                await asyncio.sleep(1.0)
        raise RuntimeError(f"Failed to synthesize {label}")


async def main():
    CHAR_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(5)
    tasks = [
        synthesize_char(label, filename, spoken, sem)
        for label, (filename, spoken) in CHARACTERS.items()
    ]
    await asyncio.gather(*tasks)
    print("Done generating all English female character files!")


if __name__ == "__main__":
    asyncio.run(main())
