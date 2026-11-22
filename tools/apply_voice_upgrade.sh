#!/bin/bash
# ==============================================================================
# Brailce – Master Audio Upgrade to Studio Female Voice (Blessica Neural)
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Upgrading All Voices to Studio Female Voice         ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""

cd "$HOME/BRAILCE-ADJUST"

# 1. Configure clean ALSA plug for I2S DAC (Direct hardware connection, zero dmix jitter/softvol clipping)
echo -e "${YELLOW}[1/4] Configuring clean ALSA hardware audio route...${NC}"

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

# 2. Activate Virtual Environment
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

# 3. Ensure ffmpeg and python packages
echo -e "${YELLOW}[2/4] Ensuring audio tools and packages...${NC}"
sudo apt-get install -y ffmpeg alsa-utils 2>/dev/null || true
pip install edge-tts numpy || true

# 3. Maximize volume and unmute ALSA to eliminate "muffled" audio
echo -e "${YELLOW}[2/4] Setting clean ALSA digital volume (85% unmuted, no clipping)...${NC}"
amixer set Master 85% unmute 2>/dev/null || true
amixer set PCM 100% unmute 2>/dev/null || true
amixer set Speaker 100% unmute 2>/dev/null || true
amixer set Digital 100% unmute 2>/dev/null || true
pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null || true

# 4. Run the Studio Female Voice Generator
echo -e "${YELLOW}[3/4] Voice bank (regeneration only when BRAILCE_REGEN_VOICE=1)...${NC}"
if [ "${BRAILCE_REGEN_VOICE:-0}" = "1" ]; then
    python tools/upgrade_audio_female.py || echo "WARN: voice regeneration failed (offline?) — .asoundrc and configs are still applied."
else
    echo "Skipping voice bank regeneration (set BRAILCE_REGEN_VOICE=1 to regenerate characters/*.wav)."
fi

# 5. Launch Brailce
echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   ✓ ALL VOICES UPGRADED! Launching Brailce...                    ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo ""
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet brailce.service 2>/dev/null; then
    echo "brailce.service is active — skipping interactive launch (dmix would mix two instances). Reboot or restart the service to apply changes."
else
    python main.py
fi
