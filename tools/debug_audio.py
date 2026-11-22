#!/usr/bin/env python3
"""
Brailce Comprehensive Audio Diagnostic & Fix Tool for Raspberry Pi 5
=====================================================================
Diagnoses:
1. ALSA sound cards (/proc/asound/cards, aplay -l)
2. Raspberry Pi 5 config.txt for I2S DAC overlays (MAX98357A / HiFiBerry)
3. ALSA ~/.asoundrc and mixer mute states
4. PipeWire / PulseAudio sinks
5. Piper TTS binary and voice model integrity
6. Plays audio tests across all outputs to locate the working speaker
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


def main():
    print("=" * 66)
    print("      BRAILCE HARDWARE AUDIO DIAGNOSTIC & DEBUGGER (Pi 5)        ")
    print("=" * 66)
    print()

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
                # e.g.: " 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0"
                num = line.split("[")[0].strip()
                name = line.split("[")[1].split("]")[0].strip()
                desc = line.split(":")[-1].strip() if ":" in line else ""
                detected_cards.append((num, name, desc))
    else:
        print("  /proc/asound/cards not found (running on non-Linux platform)")

    print()
    print(f"Total cards detected: {len(detected_cards)}")
    for num, name, desc in detected_cards:
        print(f"  -> Card {num}: '{name}' ({desc})")

    # Analyze detected cards
    has_usb = any("usb" in (name + desc).lower() for _, name, desc in detected_cards)
    has_dac = any(k in (name + desc).lower() for k in ["dac", "hifiberry", "i2s", "max98357", "snd_rpi"] for _, name, desc in detected_cards)
    only_hdmi = len(detected_cards) > 0 and all("hdmi" in (name + desc).lower() for _, name, desc in detected_cards)

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
        print("  config.txt not found (non-Raspberry Pi or Windows).")

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
    model_json = Path(os.path.expanduser("~/piper/en_US-ryan-medium.onnx.json"))

    piper_ok = False
    if piper_bin.is_file() and os.access(piper_bin, os.X_OK):
        bin_sz = piper_bin.stat().st_size
        print(f"  Piper binary found: {piper_bin} ({bin_sz / (1024*1024):.1f} MB)")
        piper_ok = bin_sz > 1000000
    else:
        print(f"  Piper binary MISSING at: {piper_bin}")

    model_ok = False
    if model_onnx.is_file():
        mod_sz = model_onnx.stat().st_size
        print(f"  Ryan voice model found: {model_onnx} ({mod_sz / (1024*1024):.1f} MB)")
        model_ok = mod_sz > 50000000  # Must be >50MB
        if not model_ok:
            print(f"  WARNING: Model file is too small or incomplete ({mod_sz} bytes)!")
    else:
        print(f"  Ryan voice model MISSING at: {model_onnx}")

    # ---- 5. Check eSpeak NG fallback ----
    print()
    print("[5] Checking eSpeak NG fallback...")
    espeak_cmd = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak_cmd:
        print(f"  eSpeak NG available at: {espeak_cmd}")
    else:
        print("  eSpeak NG not found.")

    # ---- 6. Check pre-rendered audio assets ----
    print()
    print("[6] Checking pre-rendered WAV files...")
    proj_root = Path(__file__).resolve().parent.parent
    sample_wav = proj_root / "brailce" / "audio" / "characters" / "char_A.wav"
    fil_wav = proj_root / "brailce" / "audio" / "filipino" / "mode_free_input.wav"

    if sample_wav.is_file():
        print(f"  char_A.wav OK ({sample_wav.stat().st_size} bytes)")
    else:
        print(f"  char_A.wav MISSING at: {sample_wav}")

    if fil_wav.is_file():
        print(f"  mode_free_input.wav OK ({fil_wav.stat().st_size} bytes)")
    else:
        print(f"  mode_free_input.wav MISSING at: {fil_wav}")

    # ---- 7. Live Sound Test across all cards ----
    print()
    print("[7] Testing playback across all audio output devices...")

    test_targets = []
    # Test default
    test_targets.append(("default", "Default ALSA Device"))
    # Test each card
    for num, name, _ in detected_cards:
        test_targets.append((f"plughw:{num},0", f"Card {num} ({name})"))

    working_device = None
    if sample_wav.is_file():
        for dev, label in test_targets:
            print(f"  Testing {label} [aplay -D {dev}]...")
            rc, out, err = run(["aplay", "-D", dev, "-q", str(sample_wav)], timeout=4)
            if rc == 0:
                print(f"    -> Playback command SUCCEEDED on: {dev}")
                if working_device is None and "hdmi" not in dev.lower():
                    working_device = dev
            else:
                print(f"    -> Failed on {dev} (code {rc}): {err}")

    # ---- 8. Summary & Remediation Recommendation ----
    print()
    print("=" * 66)
    print("                     DIAGNOSTIC SUMMARY                          ")
    print("=" * 66)

    if only_hdmi:
        print("CRITICAL FINDING: ONLY HDMI AUDIO CARDS ARE DETECTED!")
        print("The Raspberry Pi 5 DOES NOT have a 3.5mm headphone/audio jack.")
        print()
        print("PAANO ITO AYUSIN:")
        print("A. Kung gumagamit ka ng I2S DAC Amplifier (halimbawa, MAX98357A):")
        print("   Kailangan i-enable ang overlay sa /boot/firmware/config.txt.")
        print("   Patakbuhin: sudo bash tools/enable_i2s_audio.sh")
        print("   at pagkatapos ay: sudo reboot")
        print()
        print("B. Kung gumagamit ka ng USB Speaker o USB-to-3.5mm Adapter:")
        print("   Isaksak ang USB speaker sa kahit anong USB port ng Raspberry Pi 5.")
        print("   Kusang lilitaw ang 'USB Audio' card.")
        print()
        print("C. Kung HDMI monitor na may built-in speaker ang gamit mo:")
        print("   I-configure ang HDMI bilang default output.")
    elif has_usb or has_dac:
        print(f"GOOD NEWS: Found dedicated speaker card! (USB={has_usb}, I2S DAC={has_dac})")
        # Auto-configure ~/.asoundrc
        best_card = None
        for num, name, desc in detected_cards:
            if any(k in (name + desc).lower() for k in ["usb", "dac", "hifiberry", "max98357"]):
                best_card = num
                break
        if best_card:
            asoundrc = Path(os.path.expanduser("~/.asoundrc"))
            asoundrc_content = f"""pcm.!default {{
    type plug
    slave.pcm {{
        type hw
        card {best_card}
        device 0
    }}
}}
ctl.!default {{
    type hw
    card {best_card}
}}
"""
            asoundrc.write_text(asoundrc_content)
            print(f"Configured ~/.asoundrc to point to card {best_card}.")

    print("=" * 66)


if __name__ == "__main__":
    main()
