#!/bin/bash
# ==============================================================================
# Brailce - SPEAKER + AMPLIFIER + VOICE-OUTPUT PROOF TEST (timestamped report)
# ==============================================================================
# Runs ON THE DEVICE (Raspberry Pi 5, MAX98357A I2S mono amp, ALSA card
# sndrpihifiberry, app = systemd brailce.service) and produces a timestamped
# evidence report in the repo root:
#     speaker_amp_proof_<YYYYmmdd_HHMMSS>.txt
#
# Sections (EVERY section prints to the console AND appends to the report):
#   [0] PREP    - generate the calibrated proof signals (proof_signals/)
#   [A] SYSTEM  - objective system identity (date/host/git/ALSA/mixer/bank/dmesg)
#   [B] HW      - objective: capture /proc/asound/*/pcm0p/sub0/hw_params + status
#                 WHILE a real bank file plays (PROVES the stream the
#                 MAX98357A amp actually received)
#   [C] XRUN    - objective: over/underrun count per played file
#   [D] FREQ    - interactive: 250..8000 Hz sines at SAFE PEAK 4000
#   [E] LEVEL   - interactive: speech level sweep PEAK 4000..25000
#   [F] VOICE   - interactive: 4 real bank sounds (letra/salita/mode/sentence)
#   [G] AMP     - noise-burst verdict + hw_params interpretation
#   [SUMMARY]   - system state / hw stream / XRUN totals / verdicts / guide
#
# NO set -e ON PURPOSE: every step is guarded (|| true / if) so the run ALWAYS
# reaches the FINAL SUMMARY, even when aplay/python/git are missing.
#
# usage:  bash tools/speaker_amp_proof_test.sh
#         (sudo bash tools/speaker_amp_proof_test.sh if dmesg needs root)
# ==============================================================================

GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR" || { echo "FATAL: cannot cd to repo root"; exit 1; }

# venv activation (numpy for the signal generator), same pattern as the tools
if [ -f "$HOME/venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$HOME/venv/bin/activate" 2>/dev/null || true
fi

REPORT="speaker_amp_proof_$(date +%Y%m%d_%H%M%S).txt"
REPORT_ABS="$(pwd)/$REPORT"
: > "$REPORT"
PROOF_DIR="proof_signals"
XRUN_ERR_FILE="$(mktemp 2>/dev/null || echo "/tmp/brailce_xrun_$$.err")"
XRUN_TOTAL=0
SUMMARY_VERDICTS=""
trap "rm -f \"$XRUN_ERR_FILE\" 2>/dev/null" EXIT

# --- report helpers: every line goes to console AND the report ----------------
say() {                       # plain line: console + report
    echo "$*" | tee -a "$REPORT"
}
say_c() {                     # colored console line; PLAIN copy in the report
    local color="$1"; shift
    echo -e "${color}${*}${NC}"
    echo "$*" >> "$REPORT"
}
add_verdict() {               # collect one line for the FINAL summary table
    SUMMARY_VERDICTS="${SUMMARY_VERDICTS}${1}"$'\n'
}
# play + ask: label -> aplay (default route) -> 8 s "malinis?" question
play_and_ask() {
    local label="$1" f="$2"
    if [ ! -f "$f" ]; then
        say_c "$RED" ">>> $label -- MISSING FILE: $f (SKIP)"
        ANS="missing-file"
        return 0
    fi
    say_c "$YELLOW" ">>> $label"
    aplay -q "$f" >/dev/null 2>&1 || say_c "$RED" "  (aplay exit $? on $f)"
    if read -t 8 -p "  malinis? (y/n): " ANS; then
        :
    else
        ANS="no-answer"
        echo "" >&2
    fi
    say "  verdict: $ANS"
    return 0
}
# XRUN proof helper (Section C): play with ALL output captured, count "verrun"
xrun_play() {
    local f="$1" label="$2"
    if [ ! -f "$f" ]; then
        say "$label | - | SKIP (file missing)"
        XRUN_LAST=""
        return 0
    fi
    aplay -q "$f" >"$XRUN_ERR_FILE" 2>&1 || true
    local n
    n="$(grep -o "verrun" "$XRUN_ERR_FILE" 2>/dev/null | wc -l)"
    n=$((n + 0))
    : > "$XRUN_ERR_FILE" 2>/dev/null || true
    XRUN_LAST="$n"
    XRUN_TOTAL=$((XRUN_TOTAL + n))
    if [ "$n" -eq 0 ]; then
        say "$label | 0 | PASS (clean DMA, no underruns)"
    else
        say "$label | $n | FAIL ($n over/underrun message/s)"
    fi
    return 0
}

say_c "$BLUE" "=================================================================="
say_c "$GREEN" " Brailce: SPEAKER + AMPLIFIER + VOICE-OUTPUT PROOF TEST"
say_c "$BLUE" "=================================================================="
say "Report file : $REPORT"
say "Started     : $(date)"
say "Host        : $(hostname 2>/dev/null || echo unknown)"

# --- [0] PREP ------------------------------------------------------------------
say ""
say_c "$BLUE" "---------- [0] PREP: calibrated proof signals ($PROOF_DIR/) ----------"
GEN_OUT="$(python3 tools/make_proof_signals.py "$PROOF_DIR" 2>&1)"
GEN_RC=$?
say "$GEN_OUT"
if [ "$GEN_RC" -ne 0 ]; then
    say_c "$RED" "WARN: proof signal generation FAILED (exit $GEN_RC) - sweep/noise sections will SKIP missing files"
fi

# --- SECTION A: system identity (objective) -----------------------------------
say ""
say_c "$BLUE" "---------- SECTION A: system identity (objective) ----------"
say "date       : $(date)"
say "hostname   : $(hostname 2>/dev/null || echo unknown)"
say "uname      : $(uname -a 2>/dev/null || echo unknown)"
say "git HEAD   : $(git rev-parse HEAD 2>/dev/null || echo "(git unavailable / not a repo)")"
say "git branch : $(git branch --show-current 2>/dev/null || echo "(detached / not a repo)")"
GIT_ST="$(git status --short 2>/dev/null | head -5)"
say "git status (first 5): ${GIT_ST:-(clean)}"
say ""
say "--- aplay -l ---"
say "$(aplay -l 2>/dev/null || echo "(aplay not available)")"
say "--- /proc/asound/cards ---"
say "$(cat /proc/asound/cards 2>/dev/null || echo "(no /proc/asound/cards)")"
say ""
say "--- custom ALSA config check (reference state = system default, NO custom files) ---"
if [ -f "$HOME/.asoundrc" ] || [ -f /etc/asound.conf ]; then
    if [ -f "$HOME/.asoundrc" ]; then
        say "  custom file present: $HOME/.asoundrc"
    fi
    if [ -f /etc/asound.conf ]; then
        say "  custom file present: /etc/asound.conf"
    fi
    say_c "$RED" "CUSTOM ALSA CHAIN STILL PRESENT — run: bash tools/fix_buzzing_audio.sh  (reference state = system default, no custom files)"
    ALSA_STATE="CUSTOM chain present (NOT the reference route)"
else
    say_c "$GREEN" "System default ALSA route (reference state) OK"
    ALSA_STATE="system default (reference state) - clean"
fi
say ""
say "--- amixer scontrols (expected on sndrpihifiberry: NO Master control) ---"
say "$(amixer scontrols 2>/dev/null || echo "(amixer not available)")"
say ""
say "--- bank level summary (python3 tools/verify_playback_chain.py) ---"
BANK_OUT="$(python3 tools/verify_playback_chain.py 2>&1)"
BANK_RC=$?
say "$BANK_OUT"
BANK_VERDICT="$(printf "%s\n" "$BANK_OUT" | grep -E "^Playback chain OK|playback-chain violations" | head -n1)"
say "bank verdict line: ${BANK_VERDICT:-(not found in verify output)}"
if [ "$BANK_RC" -eq 0 ]; then
    BANK_VERIFIED="YES (exit 0)"
    say_c "$GREEN" "bank verify: PASS"
else
    BANK_VERIFIED="NO (exit $BANK_RC)"
    say_c "$RED" "bank verify: FAIL (exit $BANK_RC)"
fi
WAV_COUNT="$(find brailce/audio -name "*.wav" 2>/dev/null | wc -l)"
WAV_COUNT=$((WAV_COUNT + 0))
say "bank WAV count: $WAV_COUNT"
say ""
say "--- kernel messages (dmesg | asound|i2s|snd|hifiberry, last 10) ---"
DMESG_OUT="$(dmesg 2>/dev/null | grep -iE "asound|i2s|snd|hifiberry" | tail -10)"
say "${DMESG_OUT:-(no matching kernel messages / dmesg unavailable)}"

# --- SECTION B: hardware proof - hw_params WHILE PLAYING (objective) ---------
say ""
say_c "$BLUE" "---------- SECTION B: hardware proof — hw_params WHILE PLAYING (objective) ----------"
HW_FILE="brailce/audio/filipino/mode_free_input.wav"
HW_RATE=""; HW_FORMAT=""; HW_CHANNELS=""; HW_STATE=""
HW_CAPTURED=0
if [ -f "$HW_FILE" ]; then
    say_c "$YELLOW" ">>> playing $HW_FILE via the DEFAULT route in the background; capturing hw_params/status 1 s into playback..."
    aplay -q "$HW_FILE" >/dev/null 2>&1 &
    APLAY_PID=$!
    sleep 1
    for HP in /proc/asound/card*/pcm0p/sub0/hw_params; do
        [ -f "$HP" ] || continue
        ST_FILE="${HP%hw_params}status"
        say "--- $HP (captured mid-playback) ---"
        say "$(cat "$HP" 2>/dev/null)"
        say "--- $ST_FILE (captured mid-playback) ---"
        say "$(cat "$ST_FILE" 2>/dev/null)"
        HW_RATE="$(grep -E "^rate:" "$HP" 2>/dev/null | head -n1)"
        HW_FORMAT="$(grep -E "^format:" "$HP" 2>/dev/null | head -n1)"
        HW_CHANNELS="$(grep -E "^channels:" "$HP" 2>/dev/null | head -n1)"
        HW_STATE="$(grep -E "^state:" "$ST_FILE" 2>/dev/null | head -n1)"
        HW_CAPTURED=1
    done
    wait "$APLAY_PID" 2>/dev/null || true
    say "waited for the background aplay - playback of $HW_FILE finished"
    say ""
    say "LABELED — the stream the MAX98357A amp actually received (hw_params/status):"
    say "  rate     : ${HW_RATE:-(not captured)}"
    say "  format   : ${HW_FORMAT:-(not captured)}"
    say "  channels : ${HW_CHANNELS:-(not captured)}"
    say "  state    : ${HW_STATE:-(not captured)}"
else
    say_c "$RED" "SKIP SECTION B: $HW_FILE missing"
fi

# --- SECTION C: underrun (XRUN) proof (objective) ------------------------------
say ""
say_c "$BLUE" "---------- SECTION C: underrun (XRUN) proof — aplay output capture (objective) ----------"
say "method: aplay -q <file> >err 2>&1; count lines matching \"verrun\" (= overruns + underruns); 0 = PASS (clean DMA, no underruns)"
say ""
say "file | xrun count | verdict"
xrun_play "brailce/audio/characters/char_A.wav" "bank/char_A"
xrun_play "brailce/audio/filipino/word_ano.wav" "bank/word_ano"
xrun_play "brailce/audio/filipino/mode_free_input.wav" "bank/mode_free_input"
for FREQ in 250 500 1000 2000 4000 6000 8000; do
    F4="$(printf "%04d" "$FREQ")"
    xrun_play "$PROOF_DIR/sine_${F4}_p4000.wav" "sine/${FREQ}Hz_p4000"
done
say ""
say "SECTION C running XRUN total: $XRUN_TOTAL"

# --- SECTION D: frequency sweep (interactive, labeled) -------------------------
say ""
say_c "$BLUE" "---------- SECTION D: frequency sweep 250..8000 Hz @ SAFE PEAK 4000 (INTERACTIVE) ----------"
say "answer y (malinis/clean) or n (may rattle/linga); 8 s without answer = no-answer"
say ""
for FREQ in 250 500 1000 2000 4000 6000 8000; do
    F4="$(printf "%04d" "$FREQ")"
    play_and_ask "SINE $FREQ Hz — malinis o may rattle/linga?" "$PROOF_DIR/sine_${F4}_p4000.wav"
    add_verdict "sine/freq    $FREQ Hz : $ANS"
done
say ""
say "NOTE: rattles at certain frequencies = speaker mechanical issue; clean everywhere = speaker OK at this level (PEAK 4000)."

# --- SECTION E: level sweep of speech (interactive) ----------------------------
say ""
say_c "$BLUE" "---------- SECTION E: speech level sweep PEAK 4000..25000 (INTERACTIVE) ----------"
CHARA_CLEAN_MAX=0
WORD_CLEAN_MAX=0
BOTH_CLEAN_MAX=0
for PEAK in 4000 8000 12000 16000 20000 25000; do
    P="$(printf "%05d" "$PEAK")"
    BOTH_CLEAN=1
    for STEM in charA word_ano; do
        play_and_ask "LEVEL $STEM @ PEAK $PEAK — malinis o basag?" "$PROOF_DIR/${STEM}_p${P}.wav"
        add_verdict "sweep/peak   $STEM p$PEAK : $ANS"
        case "$ANS" in
            y|Y|yes|Yes|YES)
                if [ "$STEM" = "charA" ]; then
                    CHARA_CLEAN_MAX="$PEAK"
                else
                    WORD_CLEAN_MAX="$PEAK"
                fi
                ;;
            *) BOTH_CLEAN=0 ;;
        esac
    done
    if [ "$BOTH_CLEAN" -eq 1 ]; then
        BOTH_CLEAN_MAX="$PEAK"
    fi
done
say ""
say "highest CLEAN peak: charA=$CHARA_CLEAN_MAX  word_ano=$WORD_CLEAN_MAX  both-clean=$BOTH_CLEAN_MAX  (0 = wala)"

# --- SECTION F: voice output proof (interactive) -------------------------------
say ""
say_c "$BLUE" "---------- SECTION F: voice output proof — 4 real bank sounds (INTERACTIVE) ----------"
SENT_FILE="$(ls brailce/audio/filipino/sentence_*.wav 2>/dev/null | head -n1)"
SENT_NAME="(no sentence file found)"
if [ -n "$SENT_FILE" ] && [ -f "$SENT_FILE" ]; then
    SENT_NAME="$(basename "$SENT_FILE")"
fi
say "sentence file picked: ${SENT_FILE:-none}"
say "each sound: XRUN-checked play (Section C helper) + your malinis? verdict"
say ""
for SPEC in "char_A.wav|letra|brailce/audio/characters/char_A.wav" \
            "word_ano.wav|salita|brailce/audio/filipino/word_ano.wav" \
            "mode_free_input.wav|mode intro|brailce/audio/filipino/mode_free_input.wav" \
            "$SENT_NAME|mahabang salita|$SENT_FILE"; do
    NAME="${SPEC%%|*}"
    REST="${SPEC#*|}"
    LABEL="${REST%%|*}"
    FILE="${REST#*|}"
    say_c "$YELLOW" ">>> VOICE [$LABEL]: $NAME"
    xrun_play "$FILE" "$LABEL ($NAME)"
    read -t 8 -p "  malinis? (y/n): " ANS || { ANS="no-answer"; echo "" >&2; }
    say "  verdict: $ANS (xrun=${XRUN_LAST:-n/a})"
    add_verdict "voice/sound   $LABEL ($NAME) : $ANS (xrun=${XRUN_LAST:-n/a})"
    say ""
done

# --- SECTION G: amplifier observations (objective + notes) ----------------------
say ""
say_c "$BLUE" "---------- SECTION G: amplifier observations (objective + notes) ----------"
NOISE_FILE="$PROOF_DIR/noise_burst_p3000.wav"
if [ -f "$NOISE_FILE" ]; then
    say_c "$YELLOW" ">>> AMP NOISE/RATTLE CHECK — noise burst 3 s @ PEAK 3000 (listen for hiss/buzz/rattle)"
    aplay -q "$NOISE_FILE" >/dev/null 2>&1 || true
    read -t 8 -p "  malinis? (y/n): " ANS || { ANS="no-answer"; echo "" >&2; }
    say "  verdict: $ANS"
    add_verdict "amp/noise    noise_burst_p3000 : $ANS"
else
    say_c "$RED" "SKIP: $NOISE_FILE missing (signal generation failed?)"
fi
say ""
say "--- hw_params interpretation (from the SECTION B capture) ---"
HW_VERDICT="UNKNOWN (hw_params not captured)"
if [ "$HW_CAPTURED" -eq 1 ]; then
    if printf "%s\n" "$HW_RATE" | grep -q "22050" && printf "%s\n" "$HW_CHANNELS" | grep -q "^channels: 1"; then
        say_c "$GREEN" "amp received 22050 Hz mono — matches reference pipeline"
        HW_VERDICT="22050 Hz mono — matches reference pipeline"
    else
        say_c "$RED" "amp received a NON-reference stream: [${HW_RATE}] [${HW_CHANNELS}] — check the ALSA route first"
        HW_VERDICT="NON-reference stream: [${HW_RATE:-?}] [${HW_CHANNELS:-?}]"
    fi
else
    say_c "$RED" "hw_params NOT captured — UNKNOWN what the amp received (playback too short or route silent)"
fi
say "interpretation guide: see the FINAL SUMMARY at the bottom of this report"

# --- FINAL SUMMARY --------------------------------------------------------------
say ""
say_c "$BLUE" "=================================================================="
say_c "$GREEN" " FINAL SUMMARY — Speaker + Amplifier + Voice Output PROOF"
say_c "$BLUE" "=================================================================="
say "(a) SYSTEM STATE"
say "    git HEAD      : $(git rev-parse HEAD 2>/dev/null || echo unknown)"
say "    ALSA route     : $ALSA_STATE"
say "    bank verified  : $BANK_VERIFIED"
say "    bank WAV count : $WAV_COUNT"
say "(b) HW STREAM THE AMP RECEIVED (SECTION B hw_params capture)"
say "    verdict   : $HW_VERDICT"
say "    captured  : rate=[${HW_RATE:-n/a}] format=[${HW_FORMAT:-n/a}] channels=[${HW_CHANNELS:-n/a}] state=[${HW_STATE:-n/a}]"
say "(c) XRUN TOTALS (all XRUN-checked plays, Sections C + F)"
say "    total over/underruns: $XRUN_TOTAL  (0 = PASS clean DMA; >0 = FAIL DMA/underrun issue, hindi speaker)"
say "(d) USER VERDICTS (y = malinis, n = may rattle/basag, no-answer = timeout, missing-file = hindi napatugtog)"
say "    highest clean sweep peak: charA=$CHARA_CLEAN_MAX  word_ano=$WORD_CLEAN_MAX  both-clean=$BOTH_CLEAN_MAX"
say "$SUMMARY_VERDICTS"
say "(e) INTERPRETATION GUIDE"
say "    - Kung lahat ng voice tests ay malinis → TAPOS, malinis na ang device"
say "    - Kung voice ay basag pero sines ay malinis → bank/synthesis issue (iulat kung aling tunog)"
say "    - Kung sines mismo ay may rattle sa mga frequency → speaker mechanical"
say "    - Kung XRUN > 0 → DMA/underrun issue, hindi speaker"
say "    - Kung lahat malinis pero mahina → tanging paraan: mas malakas na speaker o amp gain strap, HINDI software"
say ""
say "I-send ang buong report file pabalik para sa susunod na analisis."
say "END OF REPORT"

echo ""
echo -e "${GREEN}PROOF TEST DONE — evidence report:${NC} $REPORT_ABS"
echo -e "${GREEN}I-send ang buong report file pabalik para sa susunod na analisis.${NC}"
echo ""
cat "$REPORT"