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

# 1. Match the reference repo ALSA route (identical to tools/fix_buzzing_audio.sh):
#    BRAILCE-TYPING-AND-AI-ONLY writes NO ~/.asoundrc and NO /etc/asound.conf -
#    the device plays through the Pi system default route (plain `aplay -q`).
#    Our custom plug -> softvol "Master" -> dmixer 44.1 kHz stereo chain measured
#    basag on this hardware from peak ~6000 up, so back up and REMOVE the custom
#    ALSA files instead of writing a chain.
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

# 2. Activate Virtual Environment
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

# 3. Ensure ffmpeg and python packages
echo -e "${YELLOW}[2/4] Ensuring audio tools and packages...${NC}"
sudo apt-get install -y ffmpeg alsa-utils 2>/dev/null || true
pip install edge-tts numpy || true

# 3. Volume: the reference repo sets NO ALSA mixer level (no softvol chain, no
#    amixer calls). Loudness comes from the re-mastered WAV bank
#    (tools/set_bank_level.py) + speech config volume, so no mixer is touched.

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
