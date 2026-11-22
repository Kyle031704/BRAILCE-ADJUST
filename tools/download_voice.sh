#!/bin/bash
# ==============================================================================
# Brailce – High-Reliability Piper Voice Downloader & Audio Activator
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Voice Downloader & Audio Activator                  ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""

# 1. Unmute audio & fix PipeWire / ALSA
echo -e "${YELLOW}[1/5] Unmuting audio volume (100%)...${NC}"
amixer set Master 100% unmute 2>/dev/null || true
amixer set PCM 100% unmute 2>/dev/null || true
amixer set Speaker 100% unmute 2>/dev/null || true
amixer set Headphone 100% unmute 2>/dev/null || true
pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null || true
pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null || true

# 2. Clean up any 0-byte or corrupted partial downloads
echo -e "${YELLOW}[2/5] Cleaning up old partial files in ~/piper...${NC}"
mkdir -p "$HOME/piper"
rm -f "$HOME/piper"/*.tmp "$HOME/piper"/*.partial 2>/dev/null || true
find "$HOME/piper" -size -100k -name "*.onnx" -delete 2>/dev/null || true
find "$HOME/piper" -size 0 -delete 2>/dev/null || true

# 3. Download Piper binary (aarch64) with visible progress
echo -e "${YELLOW}[3/5] Downloading Piper TTS Engine (aarch64)...${NC}"
PIPER_BIN="$HOME/piper/piper"
if [ ! -x "$PIPER_BIN" ] || [ $(wc -c < "$PIPER_BIN" 2>/dev/null || echo 0) -lt 1000000 ]; then
    echo "Downloading Piper binary archive (~15 MB)..."
    curl -L --progress-bar "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz" -o "$HOME/piper/piper_archive.tar.gz"
    echo "Extracting Piper..."
    tar -xzf "$HOME/piper/piper_archive.tar.gz" -C "$HOME/piper" --strip-components=1
    rm -f "$HOME/piper/piper_archive.tar.gz"
    chmod +x "$PIPER_BIN"
    echo -e "${GREEN}✓ Piper binary installed!${NC}"
else
    echo -e "${GREEN}✓ Piper binary already present and valid.${NC}"
fi

# Ensure system path access
sudo ln -sf "$PIPER_BIN" /usr/local/bin/piper 2>/dev/null || true
sudo chmod +x /usr/local/bin/piper 2>/dev/null || true

# 4. Download Ryan Neural Voice Model with visible progress bar
echo -e "${YELLOW}[4/5] Downloading Studio Voice Model: en_US-ryan-medium (~63 MB)...${NC}"
MODEL_ONNX="$HOME/piper/en_US-ryan-medium.onnx"
MODEL_JSON="$HOME/piper/en_US-ryan-medium.onnx.json"

if [ ! -f "$MODEL_ONNX" ] || [ $(wc -c < "$MODEL_ONNX" 2>/dev/null || echo 0) -lt 50000000 ]; then
    echo "Downloading Ryan voice model (.onnx)..."
    curl -L --progress-bar -o "$MODEL_ONNX" "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx"
    echo "Downloading Ryan voice configuration (.json)..."
    curl -L --progress-bar -o "$MODEL_JSON" "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json"
    echo -e "${GREEN}✓ Voice model downloaded successfully!${NC}"
else
    echo -e "${GREEN}✓ Voice model already present and verified (>50MB).${NC}"
fi

# 5. Live Voice Test
echo -e "${YELLOW}[5/5] Testing Voice Output right now...${NC}"
TEST_WAV="/tmp/brailce_live_voice_test.wav"
rm -f "$TEST_WAV"

echo "Hello. Brailce voice is now active and ready." | "$PIPER_BIN" --model "$MODEL_ONNX" --output_file "$TEST_WAV" 2>/dev/null || true

if [ -f "$TEST_WAV" ] && [ -s "$TEST_WAV" ]; then
    echo -e "${GREEN}Playing test voice through speakers...${NC}"
    aplay -q "$TEST_WAV" 2>/dev/null || paplay "$TEST_WAV" 2>/dev/null || pw-play "$TEST_WAV" 2>/dev/null || true
    echo -e "${GREEN}✓ Piper Voice Synthesis OK!${NC}"
else
    echo -e "${YELLOW}Testing fallback eSpeak NG voice...${NC}"
    espeak-ng "Brailce voice ready." 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   VOICE ACTIVATED! Starting Brailce...                           ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo ""

cd "$HOME/BRAILCE-ADJUST"
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

python main.py
