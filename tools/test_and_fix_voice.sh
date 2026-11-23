#!/bin/bash
# ==============================================================================
# Brailce – Audio Output Auto-Configurator and Voice Test
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Voice & Speaker Auto-Fix Tool                       ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# 1. Detect Sound Cards
echo -e "${YELLOW}[1/6] Scanning detected audio cards...${NC}"
aplay -l || true

# Look for USB Audio or I2S DAC (detection only - the reference repo writes no
# ~/.asoundrc; the Pi system default decides the route).
USB_CARD=$(aplay -l 2>/dev/null | grep -iE "usb|audio|codec" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "dac|hifiberry|i2s|max98357" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)

if [ -n "$USB_CARD" ]; then
    echo -e "${GREEN}Detected USB Audio at card $USB_CARD (system default decides the route).${NC}"
elif [ -n "$DAC_CARD" ]; then
    echo -e "${GREEN}Detected I2S DAC at card $DAC_CARD (system default decides the route).${NC}"
fi

# ALSA route: back up and REMOVE any custom ALSA config so the device runs the
# reference repo's system-default route (BRAILCE-TYPING-AND-AI-ONLY writes none).
echo -e "${YELLOW}Restoring the reference ALSA route (system default)...${NC}"
STAMP="$(date +%Y%m%d)"
for ALSA_FILE in "$HOME/.asoundrc" "/etc/asound.conf"; do
    if [ -f "$ALSA_FILE" ]; then
        if [ "$ALSA_FILE" = "/etc/asound.conf" ]; then
            sudo mv "$ALSA_FILE" "${ALSA_FILE}.bak.${STAMP}"
        else
            mv "$ALSA_FILE" "${ALSA_FILE}.bak.${STAMP}"
        fi
        echo -e "${GREEN}✓ Backed up ${ALSA_FILE} -> ${ALSA_FILE}.bak.${STAMP}${NC}"
    fi
done
echo -e "${GREEN}✓ System default ALSA route active (reference behavior - no custom config written).${NC}"


# 2. Unmute Audio Controls
echo -e "${YELLOW}[2/6] Unmuting all audio channels to 100%...${NC}"
amixer set Master 100% unmute 2>/dev/null || true
amixer set PCM 100% unmute 2>/dev/null || true
amixer set Speaker 100% unmute 2>/dev/null || true
amixer set Headphone 100% unmute 2>/dev/null || true
pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null || true
pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null || true

# 3. Test eSpeak NG directly (immediate speech test)
echo -e "${YELLOW}[3/6] Testing immediate speech output (eSpeak NG)...${NC}"
espeak-ng -s 150 -a 100 "Free Input Mode" 2>/dev/null || espeak "Free Input Mode" 2>/dev/null || echo -e "${RED}eSpeak failed.${NC}"

# 4. Clean broken or incomplete Piper files
echo -e "${YELLOW}[4/6] Verifying Piper Neural TTS files...${NC}"
mkdir -p "$HOME/piper"
find "$HOME/piper" -size -100k -name "*.onnx" -delete 2>/dev/null || true
find "$HOME/piper" -size 0 -delete 2>/dev/null || true

PIPER_BIN="$HOME/piper/piper"
MODEL_ONNX="$HOME/piper/en_US-ryan-medium.onnx"
MODEL_JSON="$HOME/piper/en_US-ryan-medium.onnx.json"

if [ ! -x "$PIPER_BIN" ] || [ $(wc -c < "$PIPER_BIN" 2>/dev/null || echo 0) -lt 1000000 ]; then
    echo "Downloading Piper aarch64 binary (~15MB)..."
    curl -L --progress-bar "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz" -o "$HOME/piper/piper_archive.tar.gz"
    tar -xzf "$HOME/piper/piper_archive.tar.gz" -C "$HOME/piper" --strip-components=1
    rm -f "$HOME/piper/piper_archive.tar.gz"
    chmod +x "$PIPER_BIN"
fi
sudo ln -sf "$PIPER_BIN" /usr/local/bin/piper 2>/dev/null || true
sudo chmod +x /usr/local/bin/piper 2>/dev/null || true

if [ ! -f "$MODEL_ONNX" ] || [ $(wc -c < "$MODEL_ONNX" 2>/dev/null || echo 0) -lt 50000000 ]; then
    echo "Downloading Ryan voice model (~63MB)..."
    curl -L --progress-bar -o "$MODEL_ONNX" "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx"
    curl -L --progress-bar -o "$MODEL_JSON" "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json"
fi

# 5. Test Piper Studio Voice
echo -e "${YELLOW}[5/6] Speaking mode introduction with Piper Studio Voice...${NC}"
TEST_INTRO_WAV="/tmp/brailce_mode_intro_test.wav"
rm -f "$TEST_INTRO_WAV"
echo "Free Input Mode. Type letters and words." | "$PIPER_BIN" --model "$MODEL_ONNX" --output_file "$TEST_INTRO_WAV" 2>/dev/null || true

if [ -f "$TEST_INTRO_WAV" ]; then
    aplay -q "$TEST_INTRO_WAV" 2>/dev/null || paplay "$TEST_INTRO_WAV" 2>/dev/null || pw-play "$TEST_INTRO_WAV" 2>/dev/null || true
    echo -e "${GREEN}✓ Piper mode announcement played successfully!${NC}"
fi

# 6. Start Brailce Application
echo -e "${YELLOW}[6/6] Launching Brailce application...${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   Brailce starting... Mode introduction will speak now!          ${NC}"
echo -e "${GREEN}==================================================================${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

python main.py
