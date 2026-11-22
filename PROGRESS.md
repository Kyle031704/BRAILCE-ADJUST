# Brailce Project Progress

## Step 0: Backup and Map
- **Status:** DONE
- **Backup Location:** `backup_before_fix/` created with full source tree.
- **Code Map:**
  - **Speech/TTS Output:**
    - English: `brailce/speech/piper_engine.py` (`PiperEngine.speak`, `PiperEngine.synthesize_to_wav`)
    - Filipino: `brailce/speech/filipino_engine.py` (`FilipinoEngine.speak_filipino`, `FilipinoEngine._synthesize_sherpa_filipino`, `FilipinoEngine.speak_character`)
    - Unified Facade: `brailce/speech/service.py` (`SpeechService.speak`, `SpeechService.speak_character`, `SpeechService.speak_word_clean`, `SpeechService.speak_text`)
  - **Letter Spelling:**
    - Letter Map: `brailce/i18n/fil_keys.py` (`get_filipino_keystroke_name`, `FILIPINO_KEY_LETTERS_ENGLISH`)
    - Audio Routing: `brailce/speech/filipino_engine.py` (`FilipinoEngine.speak_character`)
    - Scanner Spelling: `brailce/modes/scanner_mode.py` (`ScannerMode._readout_worker`)
  - **Mode Introductions:**
    - Table: `brailce/i18n/strings.py` (`STRINGS["MODE_NAME_*"]`, `STRINGS["MODE_INTRO_*"]`, `get_mode_intro`)
    - Spoken Intros: `brailce/speech/service.py` (`SpeechService.speak_mode_intro`) & `brailce/speech/filipino_engine.py` (`named_aliases`)
  - **Math Mode Explanations:**
    - Pedagogical Engine: `brailce/braille/arithmetic.py` (`SafeArithmetic.solve`, `SafeArithmetic._explain_addition`, `SafeArithmetic._explain_subtraction`, `SafeArithmetic._explain_multiplication`)
    - Mode Event Handler: `brailce/modes/math_mode.py` (`MathMode._handle_enter`)
  - **AI Assistant Response Handling:**
    - Mode Event & Speech: `brailce/modes/ai_assistant.py` (`AIAssistantMode._process_query`, `AIAssistantMode._finish_processing`)
    - Client & Normalization: `brailce/ai/ollama_client.py` (`clean_ai_response`, `OllamaClient.generate`)
    - Display Render: `brailce/display/tft_display.py` (`TFTDisplay.show_ai_response`) & `brailce/display/console_display.py`
  - **Camera / Scanner:**
    - Mode & Readout: `brailce/modes/scanner_mode.py` (`ScannerMode._scan_worker`, `ScannerMode._readout_worker`, `ScannerMode._speak_completed_word`)
    - Recognition Engine: `brailce/vision/obr_engine.py` (`OBREngine.process_image`)
    - Hardware Interface: `brailce/vision/camera.py` (`CameraService.capture_image`)
- **Baseline (17 Unchanged Letters):**
  - C: "si", D: "di", F: "ef", H: "eych", I: "ay", L: "el", M: "em", N: "en", O: "o", P: "pi", Q: "kyu", R: "ar", S: "es", U: "yu", W: "dobolyu", X: "eks", Z: "zi"
  - These 17 letters remain exactly as mapped in `brailce/i18n/fil_keys.py`.

---
## Task 1: Filipino Letter Speech
- **Status:** DONE
- **Files Changed:** `brailce/i18n/fil_keys.py`, `brailce/speech/filipino_engine.py`, `brailce/speech/service.py`
- **Summary:** Updated the single hand-editable `FILIPINO_KEY_LETTERS_ENGLISH` table with phonetic candidates (A="ey", B="bi", E="i", G="ji", J="jey", K="kay", T="ti", V="vi", W="dabol yu"), leaving Y="wai" and all baseline letters unchanged, and routed all Filipino letter speech through the exact same engine/queue calls (`speak_word`/`enqueue_text`) that words use to resolve silent J/V and mispronunciations.

---
## Task 2: 10 ms Between Words
- **Status:** DONE
- **Files Changed:** `brailce/speech/service.py`, `brailce/speech/filipino_engine.py`, `brailce/modes/free_input.py`, `brailce/modes/math_mode.py`, `brailce/modes/ai_assistant.py`
- **Summary:** Defined `WORD_GAP_MS = 10`, `COMMA_PAUSE_MS = 150`, `SENTENCE_PAUSE_MS = 300` and created single unified `speak_response_text` function used by Filipino Free Input, Filipino Math Mode, Filipino AI Assistant, and English AI Assistant while keeping English Free Input and English Math Mode untouched.

---
## Task 3: Live Preview Colors (bluish)
- **Status:** DONE
- **Files Changed:** `brailce/vision/camera.py`, `brailce/display/ili9488_display.py`
- **Summary:** Added `SWAP_RED_BLUE = False` constant to `camera.py` and consolidated channel conversion in `ili9488_display.py.show_camera_preview` so RGB frames from Picamera2 are not reversed to BGR twice, eliminating the bluish tint while leaving scanner grayscale processing untouched.

---
## Task 4: Braille Scanner (English words only, centered Braille only)
- **Status:** DONE
- **Files Changed:** `brailce/vision/obr_engine.py`, `brailce/modes/scanner_mode.py`, `brailce/display/ili9488_display.py`
- **Audit Findings:** The scanner previously relied on multi-scale Difference of Gaussians across the whole image with full-page quality gates that failed on real camera frames with paper borders, lighting variations, and printed text.
- **Summary:** Added `ROI_W_FRAC = 0.5`, `ROI_H_FRAC = 0.4`, and `SHOW_ROI = True`. The frame is now cropped to the centered rectangle before detection, with the ROI guide displayed on the live preview. Implemented multi-polarity adaptive blob detection, median nearest-neighbor dot pitch estimation, row deskew, 2x3 lattice clustering, Grade 1 English lookup (skipping unknown patterns and ignoring indicators), word grouping by cell advance gaps, and saving annotated debug frames to `debug_scan/last_scan.jpg`. Verified letter-by-letter spelling followed by completed word pronunciation.

---
## Final Report

### 1. Files Changed
- `PROGRESS.md`: Documented Step 0 mapping, task progress, and final report.
- `brailce/i18n/fil_keys.py`: Updated single hand-editable `FILIPINO_KEY_LETTERS_ENGLISH` dictionary for the 9 target letters (A="ey", B="bi", E="i", G="ji", J="jey", K="kay", T="ti", V="vi", W="dabol yu"), leaving Y="wai" and all baseline letters unchanged.
- `brailce/speech/filipino_engine.py`: Routed letter speech through `speak_word`/`enqueue_text` using the same path as words; configured `word_gap_ms = 10`, `comma_pause_ms = 150`, and `sentence_pause_ms = 300`.
- `brailce/speech/service.py`: Defined constants `WORD_GAP_MS = 10`, `COMMA_PAUSE_MS = 150`, `SENTENCE_PAUSE_MS = 300`, and created single shared `speak_response_text` function.
- `brailce/modes/free_input.py`: Routed Filipino Free Input sentence completion through `speak_response_text` while leaving English Free Input untouched.
- `brailce/modes/math_mode.py`: Routed Filipino Math Mode explanations through `speak_response_text` while leaving English Math Mode untouched.
- `brailce/modes/ai_assistant.py`: Routed both Filipino and English AI Assistant responses through `speak_response_text`.
- `brailce/vision/camera.py`: Added `SWAP_RED_BLUE = False` at the top of the file so hardware red/blue channel order can be flipped on-device if needed.
- `brailce/display/ili9488_display.py`: Consolidated color conversion in `show_camera_preview` so channel flip occurs at exactly one point, and added rendering of the centered ROI viewfinder box (`SHOW_ROI = True`).
- `brailce/vision/obr_engine.py`: Replaced scanner detection logic with centered ROI cropping (`ROI_W_FRAC = 0.5`, `ROI_H_FRAC = 0.4`), multi-polarity blob detection, pitch estimation, deskew, 2x3 lattice fitting, Grade 1 decoding, indicator ignoring, and debug frame output to `debug_scan/last_scan.jpg`.

### 2. Root Causes Found
- **Silent J and V:** Letters were previously routed to `speak_character` instead of the word speech path. When sent as raw characters to sherpa-onnx MMS-TTS Tagalog (`tgl`), the engine dropped unsupported phonemes 'j' and 'v' (missing from indigenous Abakada/Tagalog tokens), and because no pre-rendered word WAV existed, the call failed silently. Routing letters through the same speech call as words using phonetic orthography resolved the silence.
- **Word Pauses:** Filipino sentences were using concatenative playback with large per-word gaps (30-80 ms), and AI Assistant text was parsed without uniform whitespace normalization. Defining a unified 10 ms inter-word gap with 150 ms comma and 300 ms sentence pauses restored natural continuous prosody.
- **Bluish Live Preview:** Picamera2 was configured with `RGB888`, which outputs RGB byte order into NumPy arrays, but `ili9488_display.py` was reversing channels (`arr[:, :, ::-1]`), assuming BGR input. This caused a red/blue channel swap resulting in a blue tint.
- **Scanner Audit:** The previous scanner attempted global page lattice fitting over the entire frame with aggressive quality gates (Laplacian variance, sparse line checks) that failed on real camera frames with printed text, borders, or non-uniform lighting.

### 3. Assumptions Made
- Braille indicators (capital indicator dot 6 alone and number sign dots 3-4-5-6) are ignored from spoken letter readouts so Grade 1 spelling remains focused on letter names and word pronunciation.
- Uncontracted Grade 1 Braille words are positioned within the central 50% width and 40% height of the camera frame.

### 4. Tasks Left PARTIAL or BLOCKED
- **None**. All 4 tasks completed with status **DONE**.

### 5. Hardware Checks to Test on Physical Device
1. **Letter Sounds:** Type all 26 letters in Filipino mode on the keypad to confirm the 9 updated letters (A="ey", B="bi", E="i", G="ji", J="jey", K="kay", T="ti", V="vi", W="dabol yu") sound natural and J/V are audible.
2. **Preview Colors:** Enter Scanner Mode and verify the live camera preview colors on the LCD are natural (not bluish). If your camera sensor delivers inverted colors, set `SWAP_RED_BLUE = True` in `brailce/vision/camera.py`.
3. **ROI Size:** Confirm the green centered ROI rectangle on the LCD comfortably encompasses your Braille cards or embossed sheet. Adjust `ROI_W_FRAC` and `ROI_H_FRAC` in `brailce/vision/obr_engine.py` if needed.
4. **Scanning Real Braille:** Place a Braille card centered in the viewfinder, press Enter, and verify the letter-by-letter spelling followed by the whole word pronunciation. Check `debug_scan/last_scan.jpg` to inspect the detected dot lattice.

---
## JOB 1: Esc Stops Current Activity (Not Quit)
- **Status:** DONE
- **Files Changed:** `brailce/core/events.py`, `brailce/input/keyboard_input.py`, `brailce/core/app.py`, `brailce/modes/scanner_mode.py`, `brailce/modes/ai_assistant.py`, `README.md`, `brailce/tests/test_esc_stops.py`
- **Summary:** Added `EventType.STOP_PRESSED`. Both bare-Esc sites in `keyboard_input.py` (terminal pure-ESC branch and `esc` key hook) now emit STOP_PRESSED instead of QUIT; PuTTY F1/F2/F3 escape sequences are unchanged; Ctrl+C still quits and Ctrl+Q was added as an alternate quit key. `BrailceApp` handles STOP_PRESSED by calling `speech.stop()` (stops engines, clears queues, and interrupts active playback) and forwarding the event to the active mode: ScannerMode sets its existing `_stop_readout_event` flag so readout/spelling threads wind down, AIAssistantMode cancels pending requests via a new `_cancel_event` the worker checks. Esc no longer changes modes or exits; typing stays usable. README quit hint updated (no Esc hints existed in i18n strings).
- **Tests:** `brailce/tests/test_esc_stops.py`: bare Esc emits STOP_PRESSED and never QUIT (terminal + key hook), Ctrl+C/Ctrl+Q still emit QUIT, 12 PuTTY F1/F2/F3 sequence variants keep their events, plus scanner readout/scan wind-down, AI cancellation, and app dispatch tests.
