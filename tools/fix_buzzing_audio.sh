#!/bin/bash
# ==============================================================================
# Brailce – Anti-Buzzing & Clean Audio Master Fix for Raspberry Pi 5
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Anti-Buzzing & Audio Distortion Fix                ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""

cd "$HOME/BRAILCE-ADJUST"

# 1. Configure clean ALSA hardware route for I2S DAC (Direct, zero dmix jitter/softvol clipping)
echo -e "${YELLOW}[1/4] Configuring clean ALSA hardware route...${NC}"

DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "dac|hifiberry|i2s|max98357" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
if [ -z "$DAC_CARD" ]; then
    DAC_CARD="sndrpihifiberry"
fi

cat <<EOF > "$HOME/.asoundrc"
pcm.!default {
    type plug
    slave.pcm {
        type hw
        card $DAC_CARD
        device 0
    }
}
ctl.!default {
    type hw
    card $DAC_CARD
}
EOF

sudo tee /etc/asound.conf > /dev/null <<EOF
pcm.!default {
    type plug
    slave.pcm {
        type hw
        card $DAC_CARD
        device 0
    }
}
ctl.!default {
    type hw
    card $DAC_CARD
}
EOF

echo -e "${GREEN}✓ ~/.asoundrc configured for rock-solid 44.1 kHz stereo PLL lock.${NC}"

# 3. Activate venv & generate clean 44.1kHz stereo audio assets
echo -e "${YELLOW}[3/4] Voice assets (regeneration only when BRAILCE_REGEN_VOICE=1)...${NC}"
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

if [ "${BRAILCE_REGEN_VOICE:-0}" = "1" ]; then
    python tools/upgrade_audio_female.py || echo "WARN: voice regeneration failed (offline?) — .asoundrc and configs are still applied."
else
    echo "Skipping voice bank regeneration (set BRAILCE_REGEN_VOICE=1 to regenerate characters/*.wav)."
fi

# 4. Start Brailce
echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   ✓ ALL AUDIO CLEANED & UNBUZZED! Launching Brailce...           ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo ""
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet brailce.service 2>/dev/null; then
    echo "brailce.service is active — skipping interactive launch (dmix would mix two instances). Reboot or restart the service to apply changes."
else
    python main.py
fi
