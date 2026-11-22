#!/bin/bash
# ==============================================================================
# Brailce – Speaker Output Settings Diagnostic (default vs direct route)
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}      Brailce: Speaker Output Settings Diagnostic                 ${NC}"
echo -e "${BLUE}==================================================================${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# [a] Detected playback cards
echo ""
echo -e "${YELLOW}[1/6] Playback cards (aplay -l):${NC}"
aplay -l 2>/dev/null || echo "(aplay not available)"
echo ""
echo -e "${YELLOW}       /proc/asound/cards:${NC}"
cat /proc/asound/cards 2>/dev/null || echo "(no /proc/asound/cards)"

DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "snd_rpi_hifiberry|hifiberry|max98357|pcm5102a" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
if [ -z "$DAC_CARD" ]; then
    echo -e "${RED}No I2S DAC detected by name — testing the DEFAULT route only (the app's route).${NC}"
else
    echo -e "${GREEN}I2S DAC detected on card ${DAC_CARD} (direct route: plughw:${DAC_CARD},0)${NC}"
fi

# [b] Effective ALSA configs actually used by the app route
echo ""
echo -e "${YELLOW}[2/6] Effective ~/.asoundrc:${NC}"
if [ -f "$HOME/.asoundrc" ]; then
    cat "$HOME/.asoundrc"
else
    echo -e "${RED}(no ~/.asoundrc)${NC}"
fi
echo ""
echo -e "${YELLOW}       Effective /etc/asound.conf:${NC}"
if [ -f /etc/asound.conf ]; then
    cat /etc/asound.conf
else
    echo -e "${RED}(no /etc/asound.conf)${NC}"
fi

# [c] Mixer controls
echo ""
echo -e "${YELLOW}[3/6] ALSA mixer controls (amixer scontrols):${NC}"
amixer scontrols 2>/dev/null || echo "(amixer not available)"
echo -e "${YELLOW}       NOTE: the softvol Master control appears only while the default PCM is open (during playback).${NC}"

# [d] Configured app volume
echo ""
echo -e "${YELLOW}[4/6] Configured app volume (brailce/config/config.json):${NC}"
grep '"volume"' brailce/config/config.json || echo "(volume key not found)"

# [e] Same test files through BOTH routes: app default route vs direct hardware
CHAR_FILE="brailce/audio/characters/char_A.wav"
WORD_FILE="brailce/audio/filipino/word_ano.wav"

echo ""
echo -e "${YELLOW}[5/6] Playing each test file TWICE – app route vs direct hardware route...${NC}"
for TEST_FILE in "$CHAR_FILE" "$WORD_FILE"; do
    echo ""
    if [ ! -f "$TEST_FILE" ]; then
        echo -e "${RED}Test file missing: ${TEST_FILE}${NC}"
        continue
    fi
    echo -e "${BLUE}--- ${TEST_FILE} ---${NC}"
    echo -e "${YELLOW}App route (default -> softvol -> dmixer):${NC}"
    if aplay -q "$TEST_FILE"; then
        echo -e "${GREEN}  played via default route${NC}"
    else
        echo -e "${RED}  default route FAILED (exit $?)${NC}"
    fi
    if [ -n "$DAC_CARD" ]; then
        echo -e "${YELLOW}Direct hardware route (plughw:${DAC_CARD},0):${NC}"
        if aplay -D "plughw:${DAC_CARD},0" -q "$TEST_FILE"; then
            echo -e "${GREEN}  played via direct hardware route${NC}"
        else
            echo -e "${RED}  direct hardware route FAILED (exit $?)${NC}"
        fi
    fi
done

# [f] English + Filipino summary
echo ""
echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}How to read this result / Paano basahin:${NC}"
echo "  1. App route (default) basag, direct route clean -> the ~/.asoundrc /"
echo "     /etc/asound.conf dmixer+softvol chain is the problem (re-run"
echo "     tools/fix_buzzing_audio.sh to rewrite it)."
echo "  2. BOTH routes basag -> hardware cause: MAX98357A gain pin, 5V supply,"
echo "     speaker wiring, or volume set too high."
echo "  3. Both routes clean but the APP sounds basag -> report WHICH sound is"
echo "     basag: letra (letter), salita (word), or sagot ng AI (AI answer)."
echo ""
echo -e "${GREEN}Tapus na ang diagnostic! Alin sa mga sitwasyon (1/2/3) ang tumugma sa narinig mo?${NC}"
echo -e "${BLUE}==================================================================${NC}"
