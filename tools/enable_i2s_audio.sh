#!/bin/bash
# ==============================================================================
# Brailce – Enable I2S DAC (MAX98357A) Audio Output on Raspberry Pi 5
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}     Brailce: Enabling I2S DAC Audio (MAX98357A / HiFiBerry)      ${NC}"
echo -e "${BLUE}==================================================================${NC}"

CONFIG_FILE=""
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f "/boot/config.txt" ]; then
    CONFIG_FILE="/boot/config.txt"
fi

if [ -z "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Raspberry Pi config.txt not found!${NC}"
    exit 1
fi

echo -e "Target config: ${CONFIG_FILE}"

# Check if overlay is already present
if grep -qE "dtoverlay=hifiberry-dac|dtoverlay=max98357a" "$CONFIG_FILE"; then
    echo -e "${GREEN}✓ I2S DAC overlay is already present in ${CONFIG_FILE}${NC}"
else
    echo -e "${YELLOW}Adding I2S DAC configuration to ${CONFIG_FILE}...${NC}"
    sudo bash -c "echo '' >> ${CONFIG_FILE}"
    sudo bash -c "echo '# Brailce I2S Audio DAC (MAX98357A)' >> ${CONFIG_FILE}"
    sudo bash -c "echo 'dtparam=i2s=on' >> ${CONFIG_FILE}"
    sudo bash -c "echo 'dtoverlay=hifiberry-dac' >> ${CONFIG_FILE}"
    sudo bash -c "echo 'gpio=18,19,21=a0' >> ${CONFIG_FILE} 2>/dev/null || true"
    echo -e "${GREEN}✓ Added dtoverlay=hifiberry-dac and dtparam=i2s=on successfully!${NC}"
fi

# ALSA route: match the reference repo - BRAILCE-TYPING-AND-AI-ONLY writes NO
# ~/.asoundrc at all (the Pi system default already routes to the I2S DAC once
# the overlay is active). Back up and REMOVE any custom ALSA config left by
# earlier Brailce scripts so the device runs the system-default route too.
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
echo -e "${GREEN}✓ No ~/.asoundrc written - system default ALSA route (reference behavior).${NC}"

echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}   Tapos na! Mag-reboot na ngayon para mabasa ng Pi ang DAC:     ${NC}"
echo -e "${GREEN}   sudo reboot                                                    ${NC}"
echo -e "${GREEN}==================================================================${NC}"
