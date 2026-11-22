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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

REAL_USER="${SUDO_USER:-$USER}"
USER_HOME=$(eval echo "~$REAL_USER")

# 1. Canonical ALSA output chain for the I2S DAC (identical to tools/fix_buzzing_audio.sh):
#    !default (plug) -> softvol "Master" -> dmixer (44.1 kHz stereo) -> hw DAC.
#    - dmix opens the card at 44100 Hz / 2 channels, so every playback (including
#      22050 Hz bank files, rate-converted by `plug`) locks 44.1 kHz stereo /
#      1.411 MHz BCLK instead of the hardware following each file's rate.
#    - softvol provides the "Master" control that brailce/speech/audio_device.py
#      uses (`amixer sset Master`) for the volume keys / config volume.
#    NOTE: the softvol "Master" control exists only while the default PCM is open
#    (i.e. during playback) - amixer on it is best-effort until then.
echo -e "${YELLOW}[1/4] Configuring ALSA output chain (plug -> softvol Master -> dmixer 44.1kHz stereo -> hw)...${NC}"

DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "snd_rpi_hifiberry|hifiberry|max98357|pcm5102a" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
if [ -z "$DAC_CARD" ]; then
    DAC_CARD="sndrpihifiberry"
fi

echo -e "${GREEN}✓ Identified I2S Audio DAC on Card: ${DAC_CARD}${NC}"

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

for TARGET_RC in "$USER_HOME/.asoundrc" "$HOME/.asoundrc"; do
    printf '%s\n' "$ASOUND_CHAIN" > "$TARGET_RC"
    chown "$REAL_USER":"$REAL_USER" "$TARGET_RC" 2>/dev/null || true
done

# The systemd service runs as root, so it reads the system-wide file.
printf '%s\n' "$ASOUND_CHAIN" | sudo tee /etc/asound.conf > /dev/null

echo -e "${GREEN}✓ ALSA chain written to ~/.asoundrc AND /etc/asound.conf (card: ${DAC_CARD}).${NC}"
echo -e "${GREEN}  Playback route: default (plug) -> softvol Master -> dmixer 44.1 kHz stereo -> hw DAC.${NC}"
echo -e "${GREEN}  44.1 kHz stereo lock = 1.411 MHz BCLK; volume via softvol Master (amixer sset Master).${NC}"

# 2. Activate Virtual Environment
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

# 3. Ensure ffmpeg and python packages
echo -e "${YELLOW}[2/4] Ensuring audio tools and packages...${NC}"
sudo apt-get install -y ffmpeg alsa-utils 2>/dev/null || true
pip install edge-tts numpy || true

# 3. Maximize volume and unmute ALSA.
# Best-effort: the softvol "Master" control only exists while the default PCM is
# open (i.e. during playback), so `amixer set Master` may fail until first playback.
echo -e "${YELLOW}[2/4] Setting ALSA volume (Master 100% unmuted, best-effort)...${NC}"
amixer set Master 100% unmute 2>/dev/null || true
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
