#!/bin/bash
# ==============================================================================
# Brailce – Complete Fresh Setup Script for New MicroSD Card (Raspberry Pi 5)
# ==============================================================================
# Run this script on a newly flashed Raspberry Pi OS 64-bit to automatically
# install and configure all dependencies, audio, camera, AI, and permissions.
#
# Usage:
#   cd ~/BRAILCE-ADJUST
#   bash setup_new_sd.sh
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce – Raspberry Pi 5 Complete Setup for New SD Card      ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""

# 1. Update APT and install system dependencies
echo -e "${YELLOW}[1/6] Installing system packages and audio/camera libraries...${NC}"
sudo apt-get update -y
sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    git \
    espeak-ng \
    alsa-utils \
    libasound2-dev \
    libportaudio2 \
    libgl1 \
    v4l-utils \
    curl

# 2. Add user to hardware groups
echo -e "${YELLOW}[2/6] Configuring hardware permissions (video, audio, input, spi, i2c)...${NC}"
sudo usermod -aG video,audio,input,spi,i2c "$USER" 2>/dev/null || true

# 3. Create and activate Python Virtual Environment
echo -e "${YELLOW}[3/6] Setting up Python virtual environment (~/venv)...${NC}"
if [ ! -d "$HOME/venv" ]; then
    python3 -m venv "$HOME/venv"
fi
source "$HOME/venv/bin/activate"

# 4. Install Python dependencies
echo -e "${YELLOW}[4/6] Installing Python packages (OpenCV, Piper, Sherpa, etc.)...${NC}"
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# 5. Install and setup Ollama with Liquid AI model
echo -e "${YELLOW}[5/6] Setting up local Ollama AI server and LFM model...${NC}"
if ! command -v ollama >/dev/null 2>&1; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Ensure ollama service is running
sudo systemctl enable ollama || true
sudo systemctl start ollama || true

# Wait 3 seconds for service startup
sleep 3

echo "Downloading Liquid AI LFM model (liquidai/lfm2.5-1.2b-instruct)..."
ollama pull liquidai/lfm2.5-1.2b-instruct || echo -e "${YELLOW}Warning: Could not pull model now. Run 'ollama pull liquidai/lfm2.5-1.2b-instruct' later.${NC}"

# 6. Test configuration and verify camera diagnostic
echo -e "${YELLOW}[6/6] Verifying Camera & Audio Diagnostic...${NC}"
python tools/camera_live_diagnostic.py || echo -e "${YELLOW}Camera not plugged in or needs reboot.${NC}"

echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   ✓ SETUP COMPLETE! All Brailce functions are ready to use.     ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo ""
echo "Upang patakbuhin ang Brailce:"
echo "  source ~/venv/bin/activate && python main.py"
echo ""
echo "(Opsyonal) Para mag-autostart ang Brailce tuwing bubuksan ang Pi nang walang PuTTY:"
echo "  sudo bash tools/setup_autostart.sh"
echo ""
