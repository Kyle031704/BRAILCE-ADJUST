# Brailce — Offline Bilingual Braille Learning Device

Brailce is a portable **Raspberry Pi 5** assistive learning device for visually impaired Grade 1 learners in **English (`en`)** and **Filipino (`fil`)**. It combines a dedicated physical Braille-embossed keypad, a `480x320` ILI9488 SPI LCD, 100% offline bilingual speech synthesis, a local Ollama AI tutor, and a Raspberry Pi Camera Module v2 Optical Braille Recognition (OBR) scanner.

---

## Core Capabilities & Modes

| Mode | Purpose | Controls |
|------|---------|----------|
| **Free Input** | Immediate letter feedback (`1 key = 1 letter`) + clean whole-word pronunciation on Space/Enter | `A–Z`, `0–9`, `Space`, `Enter`, `Backspace`, `Clear` |
| **Math Mode** | Safe Grade 1 arithmetic (`+`, `-`, `*`, `/`) with bilingual counting explanations and LCD solution card | Digits, operators, `Enter`, `Clear` |
| **AI Assistant** | Offline Ollama (`lfm2.5-1.2b-instruct`) Q&A with bilingual guardrails and clean spoken normalization | Question input + `Enter` |
| **Scanner Mode** | Offline classical dot-level Grade 1 Braille camera scanner with live presence detection and letter -> word readout | Position sheet, `Enter` to scan/repeat, any key to stop, `Clear` to reset |

Desktop/PuTTY controls: `A-Z`, `0-9`, `+ - * /`, `Space`, `Enter`, `Backspace`, `F1` (Mode), `F2` (Language EN/FIL), `F3` (Clear), `F11`/`F12` (Volume Down/Up), `Esc` (Stop speech/readout/AI request), `Ctrl+C` or `Ctrl+Q` (Quit), `Ctrl+P` (Safe shutdown). The SAME mapping applies to a USB keyboard plugged directly into the Pi - see below.

---

## Direct USB Keyboard (No SSH)

A USB keyboard plugged directly into the Pi drives the app **without any SSH/PuTTY session**. Power on the Pi with `brailce.service` enabled, plug the USB keyboard in (before or after boot - hotplug is detected within ~5 s), and type.

- Under the systemd service (no TTY) the input layer AUTO-selects the direct `/dev/input` reader (`python-evdev`, listed in `requirements-pi.txt`). Each keyboard is GRABbed (EVIOCGRAB) so keystrokes never leak into the Linux console, and the reader re-enumerates every 5 s so the keyboard can be plugged in before or after boot without restarting the service.
- PuTTY/SSH is now optional (debugging only); the terminal stdin path only engages when a real TTY is attached.

### Key -> Action Mapping (identical for USB keyboard, PuTTY, and laptop keyboard)

| USB key | Action |
|---------|--------|
| `A`-`Z` | Type letter |
| `0`-`9` (top row + numpad) | Type digit |
| `.` `,` `!` `?` `'` `"` `:` `;` `#` `+` `-` `*` `/` `=` | Type punctuation / math symbol (Shift row included) |
| `Space` | Space / pronounce the completed word |
| `Enter` | Confirm / submit (top row + numpad) |
| `Backspace` | Delete last character |
| `F1` or `Tab` | Change mode (Free Input -> Math -> AI -> Scanner) |
| `F2`, `` ` `` or `~` | Toggle language (EN <-> FIL) |
| `F3` or `Delete` | Clear the buffer |
| `F11` / `F12` | Volume down / volume up |
| `Esc` | Stop speech / scanner readout / AI request |
| `Ctrl+C` or `Ctrl+Q` | Quit the app (back to the shell) |
| `Ctrl+P` | **Safe shutdown** - announce, clean up, then power the Pi off |

Quick language toggle: press `Backspace`+`Space` (or `Space`+`Backspace`) within 0.35 s.

### Config knobs (`brailce/config/config.json`, `input` section)

- `"device"`:
  - `"keyboard"` (default): AUTO - direct USB evdev input when available (headless-safe, never blocks on stdin), else the legacy keyboard-hook/terminal simulation.
  - `"evdev"`: force the direct `/dev/input` USB path.
  - `"terminal"`: force the legacy keyboard hook + terminal stdin path.
- `"usb_name_match"`: optional case-insensitive substring filter on the USB keyboard device name (e.g. `"Logitech"`); empty = accept any keyboard.

### Safe Shutdown

**Keypad-only safe shutdown:** press `Backspace` + `Enter` together; the device asks for confirmation; press `Backspace` + `Enter` again within 6 s to power off safely.

**Never cut power to a running Pi** - flipping a hard power switch while the app is running can corrupt the SD card. Power the device OFF with one of the safe paths instead; flip the hard power switch only AFTER the shutdown announcement has played and the Pi has powered itself off.

| Key / Button | Action |
|--------------|--------|
| `Ctrl+P` (USB keyboard, PuTTY, or laptop) | Safe shutdown: the device announces "Shutting down. Goodbye." (Filipino: "Pinapatay na ang Brailce. Paalam."), waits for the voice to finish, runs the normal app cleanup, then executes `systemctl poweroff`. Repeated presses are ignored. |

`Ctrl+C` / `Ctrl+Q` remain "quit the app back to the shell" (for debugging); only `Ctrl+P` powers the Pi off.

**Optional GPIO long-press button** - for users who wire their power switch to a GPIO pin:

1. Set `power.shutdown_gpio` in `brailce/config/config.json` to the BCM pin number (e.g. `17`); leave it `null` (default) when no button is wired.
2. Wire the button between that BCM pin and **GND** (the internal pull-up is enabled by default: `power.shutdown_pull_up: true`).
3. Hold the button for `power.shutdown_hold_seconds` (default `2.0` s) to trigger the exact same safe-shutdown flow as `Ctrl+P` (same announcement, cleanup, `systemctl poweroff`).
4. `gpiozero` (already in `requirements-pi.txt`) is imported lazily - when it is unavailable or the pin is `null`, the app logs one INFO line and continues without the button, so the service still boots fine on dev machines.

---

## Offline Speech & Audio Architecture

Brailce uses **one consistent offline voice per language**, normalized to `22050 Hz` 16-bit mono PCM with `120 ms` pre-silence / `180 ms` post-silence and routed through `AudioDeviceCoordinator.play_wav_file()` (`aplay -q` on Raspberry Pi):

1. **English (`en`) — Piper (`en_US-ryan-medium`):**
   - `PiperEngine` locks VITS synthesis to `length_scale=1.05`, `noise_scale=0.5`, `noise_w=0.6`.
   - `SpeechService.speak_word_clean()` uses cached `brailce/audio/words_en/<word>.wav` files or Option 1 carrier slicing (`"Please say: <word>."` via `brailce/speech/audio_normalize.py`) so short function words (`is`, `his`, `this`, `the`, `when`, `where`, `why`, `who`) are never clipped.
2. **Filipino (`fil`) — `sherpa-onnx` MMS-TTS Tagalog (`tgl`) VITS:**
   - `FilipinoEngine` synthesizes live Tagalog phrases completely offline from `brailce/models/filipino/model.onnx` + `tokens.txt` and plays pre-rendered WAVs from `brailce/audio/filipino/`.
3. **Bilingual Strings (`brailce/i18n/strings.py`):**
   - Single source of truth for `"en"` and `"fil"` prompts so LCD output and TTS output never diverge.

### Pre-building & Auditing Audio Banks
```bash
# Generate / normalize offline English word cache and Filipino audio bank
python tools/build_audio_bank.py

# Verify sample rate, mono PCM, peak headroom, active RMS consistency (±3 dB), and zero silent WAVs
python tools/audit_audio.py
```

---

## Raspberry Pi 5 Setup & Models (100% Offline Runtime)

Model binaries and audio caches are excluded from git (`*.onnx`, `*.pt`, `brailce/models/`). Place/install the following on the Raspberry Pi 5 before offline operation:

1. **System packages:**
   ```bash
   sudo apt update && sudo apt install -y alsa-utils python3-picamera2
   ```
2. **Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Piper English model:**
   - Place `piper` binary and `en_US-ryan-medium.onnx` (+ `.json`) under `~/piper/` (or configure `speech.piper_bin` / `speech.piper_model` in `brailce/config/config.json`).
4. **`sherpa-onnx` Filipino MMS-TTS (`tgl`) model:**
   - Download `vits-mms-tgl` (`model.onnx` and `tokens.txt`) from sherpa-onnx releases and place inside `brailce/models/filipino/`.
5. **Local Ollama LLM:**
   ```bash
   ollama pull lfm2.5-1.2b-instruct
   ```
6. *(Optional)* Secondary YOLO weights:
   - Not required; `OBREngine` uses the offline classical dot-level recognizer by default (`vision.use_yolo_secondary: false`).

---

## Braille Scanner Lighting & Focus Guide (Pi Camera v2 / IMX219)

Reliable OBR requires crisp dot shadows and highlands:
- **Camera color order:** Configured in `brailce/vision/camera.py` as `format="RGB888"` (matching OpenCV BGR memory layout without cyan tint), with AE/AWB settle and colour-gain lock.
- **Close focus adjustment:** Adjust the IMX219 stock manual focus lens ring carefully for a **12–15 cm** working distance over a printed/embossed page until dot diameters measure `>= 6 px`.
- **Raking LED lighting:** Mount a warm/neutral LED strip at a shallow **15–30° side angle** (raking light across the paper surface) rather than overhead flash, so each raised Braille dot casts a sharp highlight crest + dark crescent shadow.
- **Diagnostic inspection:**
  ```bash
  python tools/scan_debug.py --camera --out scan_debug.png
  python tools/scan_debug.py --image path/to/sample.jpg --out scan_debug.png
  ```

---

## Environment Configuration & Deployment

Copy `.env.example` to `.env` (never commit `.env` or personal IPs/passwords) when deploying from a development PC:

```bash
cp .env.example .env
# Edit PI_IP, PI_USER, PI_PASS, PI_REMOTE_DIR
python tools/deploy_to_pi.py
```

---

## Testing

Run the unit and integration test suite locally (laptop/headless, without Pi hardware):

```bash
python -m pytest brailce/tests -q --tb=short --maxfail=3
```
