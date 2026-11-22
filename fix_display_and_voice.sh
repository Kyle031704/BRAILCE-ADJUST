#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Fixing Display (TFT) & Voice (Piper TTS)            ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# 1. Hardware SPI & Permissions
echo -e "${YELLOW}[1/4] Enabling SPI interface & permissions...${NC}"
sudo raspi-config nonint do_spi 0 || true
sudo raspi-config nonint do_i2c 0 || true
sudo usermod -aG spi,gpio,video,audio,input "$USER" 2>/dev/null || true

# 2. Virtual environment & Display packages
echo -e "${YELLOW}[2/4] Setting up venv and installing display packages...${NC}"
if [ -f "$HOME/venv/pyvenv.cfg" ]; then
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' "$HOME/venv/pyvenv.cfg" 2>/dev/null || true
fi
source "$HOME/venv/bin/activate"
pip install spidev luma.lcd luma.core rpi-lgpio || true

# 3. Piper Neural TTS & Voice Model
echo -e "${YELLOW}[3/4] Ensuring Piper Neural TTS (Ryan voice) is installed...${NC}"
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

# Ensure audio is unmuted
amixer set Master 100% 2>/dev/null || true
amixer set PCM 100% 2>/dev/null || true

cd "$HOME/BRAILCE-ADJUST"
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   ✓ Ready! Starting Brailce...                                   ${NC}"
echo -e "${GREEN}==================================================================${NC}"
python main.py

