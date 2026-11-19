"""
Generate all 26 Filipino character WAVs (A-Z) using fil-PH-BlessicaNeural.
Conservative silence trimming with >= 20ms head/tail padding and 5ms edge fade.
Standardized to 22050 Hz, 16-bit mono PCM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import wave
import edge_tts
import miniaudio
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIL_DIR = PROJECT_ROOT / "brailce" / "audio" / "filipino"

VOICE = "fil-PH-BlessicaNeural"

# Clean phonetic letter-name spellings per Task 2
PHONETIC_LETTERS = {
    "A": "ey.",
    "B": "bee.",
    "C": "si.",
    "D": "di.",
    "E": "i.",
    "F": "ef.",
    "G": "dyi.",
    "H": "eyts.",
    "I": "ay.",
    "J": "dyey.",
    "K": "kay.",
    "L": "el.",
    "M": "em.",
    "N": "en.",
    "O": "o.",
    "P": "pi.",
    "Q": "kyu.",
    "R": "ar.",
    "S": "es.",
    "T": "ti.",
    "U": "yu.",
    "V": "vi.",
    "W": "dobolyu.",
    "X": "eks.",
    "Y": "way.",
    "Z": "zi.",
}


def process_audio_samples(samples: np.ndarray, sr: int = 22050, pre_ms: int = 25, post_ms: int = 35) -> bytes:
    """Conservative trimming: at least 20ms head/tail padding, 5ms fade in/out, RMS normalized."""
    x = samples.astype(np.float64)

    # Trim only quiet digital silence at edges
    nz = np.where(np.abs(x) > 120)[0]
    if nz.size > 0:
        pad = int(sr * 0.025)  # 25ms conservative margin
        x = x[max(0, nz[0] - pad) : min(len(x), nz[-1] + pad)]

    # RMS normalization to ~3400 (clear, crisp, unclipped)
    rms = np.sqrt(np.mean(x ** 2)) if len(x) > 0 else 0.0
    if rms > 1e-3:
        target_rms = 3400.0
        x = x * (target_rms / rms)

    x = np.clip(x, -32000, 32000)

    # 5ms edge fade to prevent clicks/pops
    fade_len = min(int(sr * 0.005), len(x) // 6)
    if fade_len > 1:
        fade_in = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fade_len)))
        fade_out = 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, fade_len)))
        x[:fade_len] *= fade_in
        x[-fade_len:] *= fade_out

    pcm = np.round(x).astype(np.int16).tobytes()
    pre_silence = b"\x00\x00" * int(sr * (pre_ms / 1000.0))
    post_silence = b"\x00\x00" * int(sr * (post_ms / 1000.0))
    return pre_silence + pcm + post_silence


async def synthesize_letter(letter: str, spoken: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        out_p = FIL_DIR / f"char_{letter}.wav"
        for attempt in range(3):
            try:
                comm = edge_tts.Communicate(spoken, VOICE)
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
                target_len = int(len(raw_samples) * 22050 / decoded.sample_rate)
                resampled = np.interp(
                    np.linspace(0, len(raw_samples), target_len, endpoint=False),
                    np.arange(len(raw_samples)),
                    raw_samples,
                ).astype(np.int16)

                final_pcm = process_audio_samples(resampled, sr=22050)

                with wave.open(str(out_p), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(22050)
                    wf.writeframes(final_pcm)

                # Also save lowercase copy for case-safe Linux lookup
                out_lower = FIL_DIR / f"char_{letter.lower()}.wav"
                with wave.open(str(out_lower), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(22050)
                    wf.writeframes(final_pcm)

                return True
            except Exception as exc:
                print(f"Error {letter} (attempt {attempt+1}): {exc}")
                await asyncio.sleep(0.5 * (attempt + 1))
        return False


async def main():
    FIL_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(5)
    tasks = [synthesize_letter(letter, spoken, sem) for letter, spoken in PHONETIC_LETTERS.items()]
    results = await asyncio.gather(*tasks)
    succeeded = sum(1 for r in results if r)
    print(f"Generated {succeeded}/26 character audio files with fil-PH-BlessicaNeural.")


if __name__ == "__main__":
    asyncio.run(main())
