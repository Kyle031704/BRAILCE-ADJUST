#!/bin/bash
# ==============================================================================
# Brailce – Quick Speaker Diagnostic & Direct Hardware Audio Test
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}      Brailce: Quick Speaker Diagnostic & Test                    ${NC}"
echo -e "${BLUE}==================================================================${NC}"

cd "$HOME/BRAILCE-ADJUST"

echo -e "${YELLOW}[1/4] Detecting playback cards...${NC}"
aplay -l

DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "dac|hifiberry|i2s|max98357" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
if [ -z "$DAC_CARD" ]; then
    echo -e "${RED}Warning: No I2S DAC found! Trying default...${NC}"
    DAC_CARD=0
fi

echo -e "${GREEN}Using I2S DAC on Card: ${DAC_CARD}${NC}"

echo -e "${YELLOW}[2/4] Unmuting all mixer channels to 100%...${NC}"
amixer set Master 100% unmute 2>/dev/null || true
amixer set PCM 100% unmute 2>/dev/null || true
amixer set Speaker 100% unmute 2>/dev/null || true
amixer set Digital 100% unmute 2>/dev/null || true

echo -e "${YELLOW}[3/4] Testing Direct Hardware Playback (Letter 'A')...${NC}"
TEST_FILE="brailce/audio/characters/char_A.wav"

if [ -f "$TEST_FILE" ]; then
    echo "Playing via direct hardware (plughw:${DAC_CARD},0)..."
    aplay -D "plughw:${DAC_CARD},0" -q "$TEST_FILE"
    sleep 0.5
    echo -e "${GREEN}✓ Direct hardware playback finished!${NC}"

    echo "Playing via ALSA default (aplay -q)..."
    aplay -q "$TEST_FILE"
    echo -e "${GREEN}✓ Default ALSA playback finished!${NC}"
else
    echo -e "${RED}Test file $TEST_FILE not found!${NC}"
fi

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}Test completed. Pakinggan kung malinaw at malakas ang boses!     ${NC}"
echo -e "${BLUE}==================================================================${NC}"
