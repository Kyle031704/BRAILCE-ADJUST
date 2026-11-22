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

# 1. Canonical ALSA output chain for the I2S DAC:
#    !default (plug) -> softvol "Master" -> dmixer (44.1 kHz stereo) -> hw DAC.
#    - dmix opens the card at 44100 Hz / 2 channels, so every playback (including
#      22050 Hz bank files, rate-converted by `plug`) locks 44.1 kHz stereo /
#      1.411 MHz BCLK instead of the hardware following each file's rate.
#    - softvol provides the "Master" control that brailce/speech/audio_device.py
#      uses (`amixer sset Master`) for the volume keys / config volume.
#    NOTE: the softvol "Master" control exists only while the default PCM is open
#    (i.e. during playback) - amixer on it is best-effort until then.
echo -e "${YELLOW}[1/4] Configuring ALSA output chain (plug -> softvol Master -> dmixer 44.1kHz stereo -> hw)...${NC}"

DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "dac|hifiberry|i2s|max98357" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
if [ -z "$DAC_CARD" ]; then
    DAC_CARD="sndrpihifiberry"
fi

ASOUND_CHAIN=$(cat <<EOF
pcm.dmixer {
    type dmix
    ipc_key 1024
    ipc_perm 0666
    slave {
        pcm "hw:${DAC_CARD},0"
        period_time 0
        period_size 1024
        buffer_size 8192
        rate 44100
        channels 2
    }
    bindings { 0 0; 1 1 }
}
pcm.softvol {
    type softvol
    slave.pcm "dmixer"
    control { name "Master" }
}
pcm.!default {
    type plug
    slave.pcm "softvol"
}
ctl.!default {
    type hw
    card ${DAC_CARD}
}
EOF
)

printf '%s\n' "$ASOUND_CHAIN" > "$HOME/.asoundrc"
# The systemd service runs as root, so it reads the system-wide file.
printf '%s\n' "$ASOUND_CHAIN" | sudo tee /etc/asound.conf > /dev/null

echo -e "${GREEN}✓ ALSA chain written to ~/.asoundrc AND /etc/asound.conf (card: ${DAC_CARD}).${NC}"
echo -e "${GREEN}  Playback route: default (plug) -> softvol Master -> dmixer 44.1 kHz stereo -> hw DAC.${NC}"
echo -e "${GREEN}  44.1 kHz stereo lock = 1.411 MHz BCLK; volume via softvol Master (amixer sset Master).${NC}"

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
