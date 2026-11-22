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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo -e "${YELLOW}[1/4] Detecting playback cards...${NC}"
aplay -l

# Look strictly for HiFiBerry/MAX98357A DAC, explicitly excluding HDMI
DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "snd_rpi_hifiberry|hifiberry|max98357|pcm5102a" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)

if [ -z "$DAC_CARD" ]; then
    echo -e "${RED}Warning: Could not detect I2S DAC by name. Falling back to card 2...${NC}"
    DAC_CARD=2
fi

echo -e "${GREEN}✓ Correct I2S DAC found on: Card ${DAC_CARD} (plughw:${DAC_CARD},0)${NC}"

echo -e "${YELLOW}[2/4] Testing Direct Hardware Playback to I2S DAC (Letter 'A')...${NC}"
TEST_FILE="brailce/audio/characters/char_A.wav"

if [ -f "$TEST_FILE" ]; then
    echo "Playing directly to speaker via plughw:${DAC_CARD},0..."
    aplay -D "plughw:${DAC_CARD},0" -q "$TEST_FILE"
    echo -e "${GREEN}✓ Direct hardware playback to Card ${DAC_CARD} completed!${NC}"
else
    echo -e "${RED}Test file $TEST_FILE not found!${NC}"
fi

echo -e "${YELLOW}[3/4] Testing Word Playback (Kumusta)...${NC}"
WORD_FILE="brailce/audio/filipino/word_kumusta.wav"
if [ -f "$WORD_FILE" ]; then
    aplay -D "plughw:${DAC_CARD},0" -q "$WORD_FILE"
    echo -e "${GREEN}✓ Word playback completed!${NC}"
fi

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}Tapos na ang test! Narinig mo ba nang malinaw sa speaker?         ${NC}"
echo -e "${BLUE}==================================================================${NC}"
