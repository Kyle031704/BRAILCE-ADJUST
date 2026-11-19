#!/bin/bash
# Brailce Post-Install Verification Script for Raspberry Pi 5
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "=== Brailce Installation Verification ==="
echo "Running from: $(pwd)"

CHAR_COUNT=$(ls -1 brailce/audio/filipino/char_*.wav 2>/dev/null | wc -l)
echo "Filipino character audio clips found: ${CHAR_COUNT}"

if [ -f brailce/audio/filipino/char_A.wav ]; then
    echo "char_A.wav SHA256: $(sha256sum brailce/audio/filipino/char_A.wav | awk '{print $1}')"
fi

echo ""
echo "=== Probing Camera Device ==="
python3 tools/camera_check.py || true

echo ""
echo "=== Running Filipino Character Clarity Tests ==="
python3 -m pytest -q brailce/tests/test_fil_letter_clarity.py

echo ""
echo "✓ Brailce install verified successfully!"
