"""Re-render Filipino characters with BlessicaNeural and precise phonetic respellings."""
import os
import sys
import shutil
import asyncio
import numpy as np
import scipy.signal
import soundfile as sf
import edge_tts

RESPELLED_LETTERS = {
    "A": "ey", "B": "bi", "C": "si", "D": "di", "E": "i",
    "F": "ef", "G": "dyi", "H": "eych", "I": "ay", "J": "dyey",
    "K": "key", "L": "el", "M": "em", "N": "en", "O": "o",
    "P": "pi", "Q": "kyu", "R": "ar", "S": "es", "T": "ti",
    "U": "yu", "V": "vi", "W": "dobolyu", "X": "eks", "Y": "wai", "Z": "zi"
}

DIGITS = {
    "0": "sero", "1": "wan", "2": "tu", "3": "tri", "4": "for",
    "5": "fayv", "6": "siks", "7": "seben", "8": "eyt", "9": "nayn"
}

AUDIO_DIR = os.path.join("brailce", "audio", "filipino")
BACKUP_DIR = os.path.join("tools", "backup_fil_chars")
os.makedirs(BACKUP_DIR, exist_ok=True)

# 1. Back up existing character wavs
print("Backing up existing character WAVs...")
for f in os.listdir(AUDIO_DIR):
    if f.startswith("char_") and f.endswith(".wav"):
        src = os.path.join(AUDIO_DIR, f)
        dst = os.path.join(BACKUP_DIR, f)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)

TARGET_SR = 22050
VOICE = "fil-PH-BlessicaNeural"

async def render_clip(text: str, out_wav: str):
    tmp_mp3 = out_wav + ".tmp.mp3"
    comm = edge_tts.Communicate(text, VOICE)
    await comm.save(tmp_mp3)

    raw, sr = sf.read(tmp_mp3)
    try:
        os.remove(tmp_mp3)
    except Exception:
        pass

    if raw.ndim > 1:
        raw = raw[:, 0]

    # Resample to 22050 Hz
    if sr != TARGET_SR:
        num_target = int(round(len(raw) * TARGET_SR / sr))
        raw = scipy.signal.resample(raw, num_target)

    # Trim silence (threshold -45 dBFS = 0.0056)
    thresh = 0.006
    active = np.where(np.abs(raw) > thresh)[0]
    if len(active) > 0:
        start_idx = max(0, active[0] - int(0.015 * TARGET_SR))
        end_idx = min(len(raw), active[-1] + int(0.080 * TARGET_SR))
        trimmed = raw[start_idx:end_idx]
    else:
        trimmed = raw

    # RMS normalize to ~3800 (target ~0.116 in float32, peak ~0.45-0.55)
    current_rms = float(np.sqrt(np.mean(trimmed ** 2)))
    if current_rms > 1e-4:
        target_rms = 3800.0 / 32768.0
        norm = trimmed * (target_rms / current_rms)
    else:
        norm = trimmed

    # Ensure peak <= 0.85
    peak = float(np.max(np.abs(norm)))
    if peak > 0.85:
        norm = norm * (0.85 / peak)

    # Convert to int16 PCM
    pcm16 = np.clip(np.round(norm * 32767.0), -32767, 32767).astype(np.int16)
    sf.write(out_wav, pcm16, TARGET_SR, subtype="PCM_16")

async def main():
    print(f"Rendering 26 letters with voice {VOICE}...")
    for letter, text in RESPELLED_LETTERS.items():
        out_path = os.path.join(AUDIO_DIR, f"char_{letter}.wav")
        await render_clip(text, out_path)
        dur = len(sf.read(out_path)[0]) / TARGET_SR
        print(f"  char_{letter}.wav ({text}) -> {dur:.2f}s")

    print(f"Rendering 10 digits with voice {VOICE}...")
    for digit, text in DIGITS.items():
        out_path = os.path.join(AUDIO_DIR, f"char_{digit}.wav")
        await render_clip(text, out_path)
        dur = len(sf.read(out_path)[0]) / TARGET_SR
        print(f"  char_{digit}.wav ({text}) -> {dur:.2f}s")

    print("Done rendering characters.")

if __name__ == "__main__":
    asyncio.run(main())
