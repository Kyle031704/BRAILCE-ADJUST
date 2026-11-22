#!/bin/bash
set -e
echo "=== 1. Enabling SPI interface ==="
sudo raspi-config nonint do_spi 0 || true

echo "=== 2. Activating venv and installing display packages ==="
source ~/venv/bin/activate
pip install spidev luma.lcd luma.core rpi-lgpio || true

echo "=== 3. Downloading Piper Neural TTS and Ryan voice ==="
mkdir -p ~/piper
cd ~/piper
if [ ! -f ~/piper/piper ]; then
    echo "Downloading piper binary..."
    curl -sL https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz | tar -xz --strip-components=1 || true
    chmod +x ~/piper/piper || true
fi
if [ ! -f ~/piper/en_US-ryan-medium.onnx ]; then
    echo "Downloading voice model..."
    curl -sL -o en_US-ryan-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx || true
    curl -sL -o en_US-ryan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json || true
fi

cd ~/BRAILCE-ADJUST
echo "=== 4. Starting Brailce ==="
python main.py
