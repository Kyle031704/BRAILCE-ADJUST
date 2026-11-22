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

# 1. Remove conflicting pinmux override from config.txt
echo -e "${YELLOW}[1/4] Removing pin conflict from Raspberry Pi config.txt...${NC}"
sudo sed -i '/gpio=18,19,21=a0/d' /boot/firmware/config.txt 2>/dev/null || true
sudo sed -i '/gpio=18,19,21=a0/d' /boot/config.txt 2>/dev/null || true

# 2. Configure official 44.1kHz Stereo ALSA dmixer (Locks MAX98357A PLL clock)
echo -e "${YELLOW}[2/4] Configuring 44.1 kHz Stereo ALSA dmixer in ~/.asoundrc...${NC}"
cat <<'EOF' > "$HOME/.asoundrc"
pcm.dmixer {
    type dmix
    ipc_key 1024
    ipc_perm 0666
    slave {
        pcm "hw:CARD=sndrpihifiberry,DEV=0"
        period_time 0
        period_size 1024
        buffer_size 8192
        rate 44100
        channels 2
    }
    bindings {
        0 0
        1 1
    }
}

pcm.softvol {
    type softvol
    slave.pcm "dmixer"
    control {
        name "Master"
    }
}

pcm.!default {
    type plug
    slave.pcm "softvol"
}

ctl.!default {
    type hw
    card sndrpihifiberry
}
EOF

sudo tee /etc/asound.conf > /dev/null <<'EOF'
pcm.dmixer {
    type dmix
    ipc_key 1024
    ipc_perm 0666
    slave {
        pcm "hw:CARD=sndrpihifiberry,DEV=0"
        period_time 0
        period_size 1024
        buffer_size 8192
        rate 44100
        channels 2
    }
    bindings {
        0 0
        1 1
    }
}

pcm.softvol {
    type softvol
    slave.pcm "dmixer"
    control {
        name "Master"
    }
}

pcm.!default {
    type plug
    slave.pcm "softvol"
}

ctl.!default {
    type hw
    card sndrpihifiberry
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
