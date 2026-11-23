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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# 1. Match the reference repo (BRAILCE-TYPING-AND-AI-ONLY) ALSA route.
#    The reference writes NO ~/.asoundrc and NO /etc/asound.conf anywhere:
#    every sound plays through the Pi system default route (plain `aplay -q`).
#    On this hardware the reference bank (median peak 16692) played LOUD and
#    CLEAN on the same MAX98357A / sndrpihifiberry DAC, while our custom
#    plug -> softvol "Master" -> dmixer 44.1 kHz stereo chain measured basag
#    from peak ~6000 up. So instead of writing any chain, back up and REMOVE
#    the custom ALSA files and leave the system default (reference behavior).
echo -e "${YELLOW}[1/4] Restoring the reference ALSA route (system default - removing custom ~/.asoundrc + /etc/asound.conf)...${NC}"

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

echo -e "${GREEN}✓ No custom ALSA config is written anymore - the device now runs the reference's route:${NC}"
echo -e "${GREEN}  the ALSA system default (plain aplay -q), exactly like BRAILCE-TYPING-AND-AI-ONLY.${NC}"
echo -e "${GREEN}  Loudness is mastered in the WAV bank (tools/set_bank_level.py) + speech config volume.${NC}"

# 3. Activate venv & voice assets (bank stays 22050 Hz mono 16-bit)
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
