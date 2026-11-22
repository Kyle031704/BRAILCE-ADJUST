#!/usr/bin/env python3
"""
Brailce Comprehensive Audio Diagnostic & Fix Tool for Raspberry Pi 5
=====================================================================
Diagnoses:
1. ALSA sound cards (/proc/asound/cards, aplay -l)
2. Distinguishes PLAYBACK devices from CAPTURE-ONLY (microphones/webcams)
3. Raspberry Pi 5 config.txt for I2S DAC overlays (MAX98357A / HiFiBerry)
4. ALSA ~/.asoundrc and mixer mute states
5. Piper TTS binary and voice model integrity
6. Plays audio tests across all valid playback outputs
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


def run(cmd, shell=False, timeout=10):
    try:
        res = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def has_playback_stream(card_num: str) -> bool:
    """Check if the card has a playback PCM (pcm*p)."""
    card_dir = Path(f"/proc/asound/card{card_num}")
    if not card_dir.is_dir():
        return False
    # Check for playback PCM directories like pcm0p, pcm1p
    return any(p.is_dir() and p.name.endswith("p") for p in card_dir.glob("pcm*"))


def has_capture_stream(card_num: str) -> bool:
    """Check if the card has a capture PCM (pcm*c)."""
    card_dir = Path(f"/proc/asound/card{card_num}")
    if not card_dir.is_dir():
        return False
    return any(p.is_dir() and p.name.endswith("c") for p in card_dir.glob("pcm*"))


def main():
    print("=" * 66)
    print("      BRAILCE HARDWARE AUDIO DIAGNOSTIC & DEBUGGER (Pi 5)        ")
    print("=" * 66)
    print()

    # Clean up broken ~/.asoundrc if it points to a non-playback device
    asoundrc_path = Path(os.path.expanduser("~/.asoundrc"))
    if asoundrc_path.is_file():
        asound_text = asoundrc_path.read_text()
        # If it points to card 2 (which was the webcam mic)
        if "card 2" in asound_text:
            asoundrc_path.unlink()
            print("  [FIX] Removed invalid ~/.asoundrc (it was pointing to Webcam Microphone).")

    # ---- 1. Check sound cards in kernel ----
    print("[1] Checking Linux ALSA sound cards (/proc/asound/cards)...")
    cards_file = Path("/proc/asound/cards")
    detected_cards = []
    if cards_file.is_file():
        content = cards_file.read_text()
        print(content)
        for line in content.splitlines():
            line = line.strip()
            if line and line[0].isdigit() and "[" in line:
                num = line.split("[")[0].strip()
                name = line.split("[")[1].split("]")[0].strip()
                desc = line.split(":")[-1].strip() if ":" in line else ""
                can_play = has_playback_stream(num)
                can_rec = has_capture_stream(num)
                detected_cards.append((num, name, desc, can_play, can_rec))
    else:
        print("  /proc/asound/cards not found (running on non-Linux platform)")

    print()
    print(f"Total cards detected: {len(detected_cards)}")
    playback_cards = []
    for num, name, desc, can_play, can_rec in detected_cards:
        types = []
        if can_play:
            types.append("SPEAKER/PLAYBACK")
            playback_cards.append((num, name, desc))
        if can_rec:
            types.append("MICROPHONE/CAPTURE")
        type_str = " + ".join(types) if types else "UNKNOWN"
        print(f"  -> Card {num}: '{name}' ({desc}) => [{type_str}]")

    print()
    print(f"Valid Sound Output (Playback) Devices found: {len(playback_cards)}")
    for num, name, desc in playback_cards:
        print(f"     * Card {num}: {name} ({desc})")

    has_real_speaker = any("hdmi" not in (name + desc).lower() for num, name, desc in playback_cards)

    # ---- 2. Check /boot/firmware/config.txt for I2S DAC overlay ----
    print()
    print("[2] Checking Raspberry Pi config.txt for I2S Audio DAC overlay...")
    cfg_paths = [
        Path("/boot/firmware/config.txt"),
        Path("/boot/config.txt")
    ]
    cfg_file = None
    for p in cfg_paths:
        if p.is_file():
            cfg_file = p
            break

    i2s_configured = False
    if cfg_file:
        cfg_text = cfg_file.read_text(errors="ignore")
        print(f"  Found config file: {cfg_file}")
        for line in cfg_text.splitlines():
            line = line.strip()
            if line.startswith("dtoverlay=") and any(d in line for d in ["hifiberry-dac", "max98357a", "i2s-mmap"]):
                print(f"  Active overlay: {line}")
                i2s_configured = True
        if not i2s_configured:
            print("  NO I2S DAC overlay found in config.txt.")
    else:
        print("  config.txt not found.")

    # ---- 3. Unmute ALSA & check volumes ----
    print()
    print("[3] Unmuting all ALSA audio mixers to 100%...")
    mixers = ["Master", "PCM", "Speaker", "Headphone", "Digital"]
    for m in mixers:
        rc, out, _ = run(["amixer", "set", m, "100%", "unmute"])
        if rc == 0:
            print(f"  Unmuted mixer: {m}")

    run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"])
    run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])

    # ---- 4. Check Piper TTS installation ----
    print()
    print("[4] Checking Piper Neural TTS installation...")
    piper_bin = Path(os.path.expanduser("~/piper/piper"))
    model_onnx = Path(os.path.expanduser("~/piper/en_US-ryan-medium.onnx"))

    if piper_bin.is_file() and os.access(piper_bin, os.X_OK):
        print(f"  ✓ Piper binary found ({piper_bin.stat().st_size / (1024*1024):.1f} MB)")
    else:
        print(f"  ✗ Piper binary MISSING")

    if model_onnx.is_file() and model_onnx.stat().st_size > 50000000:
        print(f"  ✓ Ryan voice model found ({model_onnx.stat().st_size / (1024*1024):.1f} MB)")
    else:
        print(f"  ✗ Ryan voice model MISSING or incomplete")

    # ---- 5. Live Sound Test across real playback cards ----
    print()
    print("[5] Testing playback on real playback devices...")
    proj_root = Path(__file__).resolve().parent.parent
    sample_wav = proj_root / "brailce" / "audio" / "characters" / "char_A.wav"

    if playback_cards and sample_wav.is_file():
        for num, name, desc in playback_cards:
            dev = f"plughw:{num},0"
            print(f"  Testing Card {num} ({name}) [aplay -D {dev}]...")
            rc, out, err = run(["aplay", "-D", dev, "-q", str(sample_wav)], timeout=4)
            if rc == 0:
                print(f"    -> SUCCESS on {dev}!")
            else:
                print(f"    -> Failed on {dev}: {err}")
    else:
        print("  NO usable playback hardware available to test.")

    # ---- 6. Summary & Action Plan ----
    print()
    print("=" * 66)
    print("                    EKSPLANASYON & SOLUSYON                      ")
    print("=" * 66)

    if not has_real_speaker:
        print("DAHILAN KUNG BAKIT WALANG BOSES NA LUMALABAS:")
        print("1. Ang nakasaksak na USB device (EMEET SmartCam C60E) ay WEBCAM.")
        print("   Ang audio nito ay MICROPHONE LAMANG (capture), HINDI SPEAKER.")
        print("2. Ang natitirang audio ports (vc4hdmi0, vc4hdmi1) ay Micro-HDMI.")
        print("   Wala kang nakakabit na HDMI TV/Monitor na may speaker.")
        print("3. Ang Raspberry Pi 5 ay WALANG built-in 3.5mm headphone jack.")
        print()
        print("PAANO MAGKAKAROON NG BOSES:")
        print("PUMILI SA DALAWA:")
        print()
        print("OPTION 1 (KUNG MAY I2S DAC MODULE KA, HAL. MAX98357A):")
        print("  Ito yung maliit na amplifier board na kinakabit sa GPIO pins (18, 19, 21).")
        print("  I-enable ang driver nito sa pamamagitan ng pagtakbo ng:")
        print("     sudo bash tools/enable_i2s_audio.sh")
        print("     sudo reboot")
        print()
        print("OPTION 2 (KUNG GAGAMIT KA NG USB SPEAKER O USB AUDIO DONGLE):")
        print("  Isaksak ang iyong USB Speaker sa alinmang USB port ng Pi 5.")
        print("  Pagkasaksak, patakbuhin muli ang:")
        print("     python3 tools/debug_audio.py")
        print("=" * 66)


if __name__ == "__main__":
    main()
