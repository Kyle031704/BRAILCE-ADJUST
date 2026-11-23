#!/bin/bash
# ==============================================================================
# Brailce - Speaker Output Level Sweep (measure the clean-output ceiling)
# ==============================================================================
# Plays the reference letter (char_A) and word (word_ano) at rising PEAK levels
# so the clean ceiling of the speaker/amp can be MEASURED by ear on the device.
#   usage: bash tools/speaker_level_sweep.sh           (app route:  aplay -q)
#          bash tools/speaker_level_sweep.sh direct    (direct:     plughw:<DAC card>,0)
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ROUTE="${1:-app}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}  Brailce: Speaker Output Level Sweep (sukatin ang malinis na ceiling)${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""

# Activate the venv if present (same pattern as the other tools scripts)
if [ -f "$HOME/venv/bin/activate" ]; then
    source "$HOME/venv/bin/activate"
fi

OUT_DIR="speaker_sweep_out"
echo -e "${YELLOW}[1/3] Generating the sweep WAVs into ${OUT_DIR}/ ...${NC}"
python3 tools/make_speaker_sweep.py "$OUT_DIR"

echo ""
if [ "$ROUTE" = "direct" ]; then
    DAC_CARD=$(aplay -l 2>/dev/null | grep -iE "dac|hifiberry|max98357|pcm5102a" | head -n1 | sed -n 's/card \([0-9]\):.*/\1/p' || true)
    if [ -z "$DAC_CARD" ]; then
        echo -e "${RED}Walang DAC card na nakita sa aplay -l (dac|hifiberry|max98357|pcm5102a) - walang arbitrary numeric fallback.${NC}"
        echo -e "${YELLOW}Ginagamit na lang ang APP route (aplay -q).${NC}"
        play_wav() { aplay -q "$1" || echo -e "${RED}WARN: hindi na-play ang $1${NC}"; }
    else
        echo -e "${GREEN}Direct route: aplay -D plughw:${DAC_CARD},0${NC}"
        play_wav() { aplay -D "plughw:${DAC_CARD},0" -q "$1" || echo -e "${RED}WARN: hindi na-play ang $1${NC}"; }
    fi
else
    echo -e "${GREEN}App route: aplay -q (default -> softvol Master -> dmixer -> DAC)${NC}"
    play_wav() { aplay -q "$1" || echo -e "${RED}WARN: hindi na-play ang $1${NC}"; }
fi

echo ""
echo -e "${YELLOW}[2/3] Speech sweep - pakiramdaman kung MALINIS (clean) o BASAG (distorted)${NC}"
for PEAK in 4000 6000 8000 10000 12000 14000 16000 20000 24000 28000; do
    P=$(printf '%05d' "$PEAK")
    echo ""
    echo ">>> PEAK $PEAK - pakiramdaman kung malinis o basag"
    play_wav "$OUT_DIR/charA_p${P}.wav"
    play_wav "$OUT_DIR/word_ano_p${P}.wav"
    sleep 1
done

echo ""
echo -e "${YELLOW}[3/3] Pure 1000 Hz sine - kilalanin ang malinis na tone vs basag na overload${NC}"
for PEAK in 4000 12000 20000; do
    P=$(printf '%05d' "$PEAK")
    echo ""
    echo ">>> SINE 1000 Hz - PEAK $PEAK"
    play_wav "$OUT_DIR/sine1k_p${P}.wav"
    sleep 1
done

echo ""
echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}  SWEEP DONE - ano ang nakuha mo? (What did you hear?)${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""
echo "(a) Tandaan ang PINAKAMATAAS na peak na MALINIS (walang basag) sa parehong letra at salita."
echo "    Remember the HIGHEST peak that sounded CLEAN on BOTH the letter and the word."
echo ""
echo "(b) Kung basag na sa lahat mula PEAK 4000 - hindi level ang problema, iulat ito."
echo "    If it is distorted at EVERY level from PEAK 4000 up, the level is NOT the problem - report it."
echo ""
echo "(c) I-re-master ang bank sa measured ceiling, 2 hakbang mas mababa:"
echo "    python3 tools/set_bank_level.py --peak <malinis na peak na 2 hakbang mas mababa>"
echo "    Halimbawa: kung malinis hanggang PEAK 12000 tapos basag na sa PEAK 14000,"
echo "    gamitin: python3 tools/set_bank_level.py --peak 10000"
echo ""
echo "(d) Ang mga PINAKAMATAAS na level ay INAAASAHANG tunog basag/pangit - iyan ang layunin ng sweep."
echo "    The highest levels are SUPPOSED to sound ugly - that is the point of the sweep."
echo ""
echo "(e) Pagkatapos ng set_bank_level, i-restart ang app para mag-apply:"
echo "    sudo systemctl restart brailce.service"
echo ""
