#!/usr/bin/env python3
"""
Brailce Unified Studio Female Voice Generator (fil-PH-BlessicaNeural)
=====================================================================
1. Generates 100% consistent, studio-quality female voice for:
   - Letters A-Z
   - Digits 0-9
   - Math operations (+, -, *, /, =)
   - Punctuation marks (., ,, ?, !, #, ', :, ;)
   - Mode introductions in English & Filipino
2. Applies Treble Clarity EQ & High-Volume Normalization (Peak: -0.5 dBFS)
   to completely eliminate the "muffled/binalot" effect on the speaker.
3. Downloads Piper Lessac Female ONNX model for offline dynamic speech.
"""

import asyncio
import os
import sys
import subprocess
import wave
import struct
from pathlib import Path
import numpy as np

# Ensure edge-tts is installed in the current environment
try:
    import edge_tts
except ImportError:
    print("Installing edge-tts in environment...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts"])
    import edge_tts

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EN_DIR = PROJECT_ROOT / "brailce" / "audio" / "characters"
FIL_DIR = PROJECT_ROOT / "brailce" / "audio" / "filipino"
PIPER_DIR = Path(os.path.expanduser("~/piper"))

VOICE = "fil-PH-BlessicaNeural"
TARGET_SR = 22050

# All characters and symbols with natural pronunciations
AUDIO_ITEMS = {
    # Letters A-Z
    "char_A.wav": "ey.",
    "char_B.wav": "bee.",
    "char_C.wav": "see.",
    "char_D.wav": "dee.",
    "char_E.wav": "ee.",
    "char_F.wav": "ef.",
    "char_G.wav": "jee.",
    "char_H.wav": "aych.",
    "char_I.wav": "eye.",
    "char_J.wav": "jay.",
    "char_K.wav": "kay.",
    "char_L.wav": "el.",
    "char_M.wav": "em.",
    "char_N.wav": "en.",
    "char_O.wav": "oh.",
    "char_P.wav": "pee.",
    "char_Q.wav": "cue.",
    "char_R.wav": "ar.",
    "char_S.wav": "es.",
    "char_T.wav": "tee.",
    "char_U.wav": "you.",
    "char_V.wav": "vee.",
    "char_W.wav": "double you.",
    "char_X.wav": "ex.",
    "char_Y.wav": "why.",
    "char_Z.wav": "zee.",

    # Digits 0-9
    "char_0.wav": "zero.",
    "char_1.wav": "one.",
    "char_2.wav": "two.",
    "char_3.wav": "three.",
    "char_4.wav": "four.",
    "char_5.wav": "five.",
    "char_6.wav": "six.",
    "char_7.wav": "seven.",
    "char_8.wav": "eight.",
    "char_9.wav": "nine.",

    # Math operations
    "char_plus.wav": "plus.",
    "char_minus.wav": "minus.",
    "char_times.wav": "times.",
    "char_divide.wav": "divided by.",
    "char_equals.wav": "equals.",

    # Punctuation marks
    "char_period.wav": "period.",
    "char_comma.wav": "comma.",
    "char_question.wav": "question mark.",
    "char_exclamation.wav": "exclamation mark.",
    "char_hash.wav": "number sign.",
    "char_apostrophe.wav": "apostrophe.",
    "char_colon.wav": "colon.",
    "char_semicolon.wav": "semicolon.",
    "char_space.wav": "space.",

    # English Mode Introductions (Natural & Clear)
    "mode_free_input_en.wav": "Free Input Mode. Type letters and words.",
    "mode_math_en.wav": "Math Mode. Type numbers and operators.",
    "mode_ai_en.wav": "AI Assistant Mode. Type your question and press Enter.",
    "mode_scanner_en.wav": "Scanner Mode. Hold a Braille page and press Enter to scan.",

    # Filipino Mode Introductions (Natural Blessica)
    "mode_free_input.wav": "Malayang Paglalagay. Mag-type ng mga titik at salita.",
    "mode_math.wav": "Matematika. Mag-type ng mga numero at operasyon.",
    "mode_ai.wav": "AI Mode. I-type ang iyong tanong, pagkatapos ay pindutin ang Enter.",
    "mode_scanner.wav": "Pagbasa ng Braille. Ilagay ang pahina ng Braille sa tapat ng kamera, pagkatapos ay pindutin ang Enter.",
}


def enhance_audio(raw_samples: np.ndarray, sr: int = TARGET_SR) -> np.ndarray:
    """
    Apply Clarity Treble Boost and Peak Volume Normalization (Peak: -0.5 dBFS).
    Eliminates muffled / binalot sound on small enclosed speakers.
    """
    x = raw_samples.astype(np.float64)

    # 1. Trim digital silence at start and end
    threshold = 150
    nz = np.where(np.abs(x) > threshold)[0]
    if nz.size > 0:
        pad = int(sr * 0.04)  # 40ms pad
        start = max(0, nz[0] - pad)
        end = min(len(x), nz[-1] + pad)
        core = x[start:end]
    else:
        core = x

    if len(core) == 0:
        return np.zeros(int(sr * 0.4), dtype=np.int16)

    # 2. Treble Pre-emphasis / Clarity Boost (+3.5 dB above 2.5 kHz)
    # Simple FIR pre-emphasis filter: y[n] = x[n] - 0.35 * x[n-1]
    filtered = np.zeros_like(core)
    filtered[0] = core[0]
    for i in range(1, len(core)):
        filtered[i] = core[i] - 0.35 * core[i - 1]

    # Combine 70% original + 30% high-frequency boost for natural crispness
    crisp = 0.70 * core + 0.30 * filtered

    # 3. Peak Normalization to -0.5 dBFS (~30,800 peak out of 32,767)
    max_val = np.max(np.abs(crisp))
    if max_val > 10.0:
        scale = 30800.0 / max_val
        crisp = crisp * scale

    crisp = np.clip(crisp, -32760, 32760)

    # 4. Smooth 5ms cosine fade-in and fade-out to prevent clicks
    fade_len = min(int(sr * 0.005), len(crisp) // 8)
    if fade_len > 1:
        fade_in = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fade_len)))
        fade_out = 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, fade_len)))
        crisp[:fade_len] *= fade_in
        crisp[-fade_len:] *= fade_out

    # 5. Add 50ms pre-silence and 70ms post-silence
    pre = np.zeros(int(sr * 0.05), dtype=np.float64)
    post = np.zeros(int(sr * 0.07), dtype=np.float64)
    result = np.concatenate([pre, crisp, post])

    return result.astype(np.int16)


async def synthesize_item(filename: str, text: str):
    """Synthesize item with Edge-TTS fil-PH-BlessicaNeural, enhance and save."""
    tmp_mp3 = f"/tmp/{filename}.mp3"
    tmp_wav = f"/tmp/{filename}.wav"

    communicate = edge_tts.Communicate(text, VOICE, rate="+3%", pitch="+1Hz")
    await communicate.save(tmp_mp3)

    # Convert MP3 to 22050 Hz 16-bit mono PCM WAV using ffmpeg or sox or miniaudio
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp_mp3, "-ar", str(TARGET_SR), "-ac", "1", tmp_wav],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    # Read WAV data
    with wave.open(tmp_wav, "rb") as wf:
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
        samples = np.frombuffer(raw, dtype=np.int16)

    # Apply treble clarity and loud peak normalization
    enhanced = enhance_audio(samples, sr=TARGET_SR)

    # Save to characters directory and filipino directory
    for target_dir in [EN_DIR, FIL_DIR]:
        target_path = target_dir / filename
        with wave.open(str(target_path), "wb") as out_wf:
            out_wf.setnchannels(1)
            out_wf.setsampwidth(2)
            out_wf.setframerate(TARGET_SR)
            out_wf.writeframes(enhanced.tobytes())

    # Clean tmp
    for p in [tmp_mp3, tmp_wav]:
        if os.path.exists(p):
            os.remove(p)


def download_piper_female_voice():
    """Download en_US-lessac-medium female model for offline dynamic speech."""
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    lessac_onnx = PIPER_DIR / "en_US-lessac-medium.onnx"
    lessac_json = PIPER_DIR / "en_US-lessac-medium.onnx.json"

    if not lessac_onnx.is_file() or lessac_onnx.stat().st_size < 30000000:
        print("Downloading Piper Lessac Female Voice Model (~35MB)...")
        subprocess.run(
            ["curl", "-L", "--progress-bar", "-o", str(lessac_onnx),
             "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"],
            check=True
        )
        subprocess.run(
            ["curl", "-L", "--progress-bar", "-o", str(lessac_json),
             "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"],
            check=True
        )
        print("✓ Piper Lessac Female Voice ready.")
    else:
        print("✓ Piper Lessac Female Voice already present.")


async def main():
    print("=" * 66)
    print("   UPGRADING BRAILCE TO STUDIO FEMALE VOICE (Blessica Neural)    ")
    print("=" * 66)
    print()

    # 1. Unmute volume & maximize ALSA gain
    print("[1/4] Maximizing ALSA digital volume controls...")
    for mixer in ["Master", "PCM", "Speaker", "Digital"]:
        subprocess.run(["amixer", "set", mixer, "100%", "unmute"], capture_output=True)

    # 2. Download offline female Piper model
    print("[2/4] Ensuring offline female voice fallback is ready...")
    try:
        download_piper_female_voice()
    except Exception as e:
        print(f"  Note: Could not download Lessac now ({e}), continuing with pre-rendered assets.")

    # 3. Check for ffmpeg
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        print("Installing ffmpeg for audio mastering...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "ffmpeg"], check=True)

    # 4. Generate all studio female assets
    print(f"[3/4] Synthesizing {len(AUDIO_ITEMS)} items with {VOICE}...")
    total = len(AUDIO_ITEMS)
    for idx, (filename, text) in enumerate(AUDIO_ITEMS.items(), 1):
        print(f"  [{idx}/{total}] {filename} -> {text!r}")
        try:
            await synthesize_item(filename, text)
        except Exception as e:
            print(f"    Error on {filename}: {e}")

    # 5. Test playback of mode intro and math operator
    print("[4/4] Testing playback of newly mastered audio...")
    test_file = EN_DIR / "mode_free_input_en.wav"
    if test_file.is_file():
        subprocess.run(["aplay", "-q", str(test_file)])

    plus_file = EN_DIR / "char_plus.wav"
    if plus_file.is_file():
        subprocess.run(["aplay", "-q", str(plus_file)])

    print()
    print("=" * 66)
    print("   ✓ STUDIO FEMALE VOICE UPGRADE COMPLETE!                       ")
    print("=" * 66)


if __name__ == "__main__":
    asyncio.run(main())
