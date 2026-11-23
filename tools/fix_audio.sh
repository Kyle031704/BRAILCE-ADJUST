#!/bin/bash
# ==============================================================================
# Brailce – Audio Diagnostic & Auto-Fix Tool for Raspberry Pi 5
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Audio Diagnostic & Sound Fixer                      ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# 1. Unmute all ALSA mixers and set volume to 100%
echo -e "${YELLOW}[1/5] Unmuting ALSA volume controls...${NC}"
amixer set Master 100% unmute 2>/dev/null || true
amixer set PCM 100% unmute 2>/dev/null || true
amixer set Speaker 100% unmute 2>/dev/null || true
amixer set Headphone 100% unmute 2>/dev/null || true

# 2. Check detected sound cards
echo -e "${YELLOW}[2/5] Detecting sound cards (aplay -l)...${NC}"
aplay -l || true

# ALSA route: the reference repo (BRAILCE-TYPING-AND-AI-ONLY) writes NO
# ~/.asoundrc anywhere - playback uses the Pi system default. Back up and
# REMOVE any custom ALSA config instead of writing one (the custom plug/hw and
# softvol/dmixer chains measured basag on this hardware).
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


# 3. Verify Piper Neural TTS binary and voice model
echo -e "${YELLOW}[3/5] Checking Piper Neural TTS...${NC}"
mkdir -p "$HOME/piper"
cd "$HOME/piper"
if [ ! -f "$HOME/piper/piper" ]; then
    echo "Downloading Piper aarch64 binary..."
    curl -sL https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz | tar -xz --strip-components=1 2>/dev/null || true
    chmod +x "$HOME/piper/piper" 2>/dev/null || true
fi
sudo ln -sf "$HOME/piper/piper" /usr/local/bin/piper 2>/dev/null || true
sudo chmod +x /usr/local/bin/piper 2>/dev/null || true

if [ ! -f "$HOME/piper/en_US-ryan-medium.onnx" ]; then
    echo "Downloading Ryan voice model..."
    curl -sL -o "$HOME/piper/en_US-ryan-medium.onnx" https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx || true
    curl -sL -o "$HOME/piper/en_US-ryan-medium.onnx.json" https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json || true
fi

# 4. Direct sound playback test
echo -e "${YELLOW}[4/5] Testing direct audio playback (char_A.wav)...${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
if [ -f "brailce/audio/characters/char_A.wav" ]; then
    aplay -q "brailce/audio/characters/char_A.wav" || echo -e "${RED}aplay failed. Check speaker connection.${NC}"
fi

# 5. Piper TTS speech test
echo -e "${YELLOW}[5/5] Testing Piper voice synthesis...${NC}"
if [ -f "$HOME/piper/piper" ] && [ -f "$HOME/piper/en_US-ryan-medium.onnx" ]; then
    echo "Brailce audio is working." | "$HOME/piper/piper" --model "$HOME/piper/en_US-ryan-medium.onnx" --output_file /tmp/brailce_audio_test.wav 2>/dev/null || true
    if [ -f /tmp/brailce_audio_test.wav ]; then
        aplay -q /tmp/brailce_audio_test.wav || true
        echo -e "${GREEN}✓ Piper voice test complete!${NC}"
    fi
fi

echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   Audio setup complete. Now running Brailce...                   ${NC}"
echo -e "${GREEN}==================================================================${NC}"
source "$HOME/venv/bin/activate" 2>/dev/null || true
python main.py
