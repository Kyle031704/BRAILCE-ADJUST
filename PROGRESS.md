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


---

## JOB 2: Restore Reference 22050 Mono Audio Pipeline (Anti-"Basag" Audio)
- **Status:** DONE
- **Files Changed:** `brailce/speech/audio_device.py`, `brailce/speech/service.py`, `brailce/speech/piper_engine.py`, `brailce/config/config.json`, `brailce/config/defaults.py`, `brailce/audio/**/*.wav` (853 files, 22050 Hz mono 16-bit), `brailce/tests/english_audio_manifest.sha256`, `brailce/tests/test_filipino_tts_v2.py` (frozen English hash), `tools/convert_bank_to_22050_mono.py` (new), `tools/verify_playback_chain.py` (new), `brailce/tests/test_playback_chain.py` (new), `tools/master_all_audio_44k_stereo.py` (deleted), `CHANGES.md`, `PROGRESS.md`.
- **Summary:** Matched the audio pipeline to the good-sounding reference repo (BRAILCE-TYPING-AND-AI-ONLY) except the English voice choice: playback is reference-simple `aplay -q <original.wav>` (default device first, one `plughw:<card>,0` retry) with zero file rewriting — the np.repeat upsampling, tanh limiter, temp WAVs, stereo conversion, and all software gain math (`set_gain_db`/`output_gain_db`/`_gain_cache`/`_volume_pct`) are gone. Volume exists in exactly one place again: `set_volume(pct)` -> `amixer sset Master <pct>% unmute`, wired from `SpeechService.set_volume` as before. The WAV bank was converted back to 22050 Hz / 1 ch / 16-bit (stereo->mono mean, `scipy.signal.resample_poly`, `normalize_loudness_pcm` active-RMS-4200/peak-30000 levels plus bank-median equalization to stay inside the audio audit's +/-3 dB band) — format and levels only, no re-synthesis or timing changes. Speech config restored to reference values (volume 100, filipino_char_gap_ms 200, added char_gap_ms 800 removed, filipino_pitch_semitones 3.5, filipino_word_gap_ms 30, comma 220 / sentence 380, Piper length_scale 1.05 / noise_scale 0.5 / noise_w 0.6, sentence_silence 0.35 normal / 0.55 carrier). A missing piper binary/model now warns loudly (named piece + one console print) before the espeak fallback. The 44.1 kHz stereo bank backup is kept at `C:\Users\MARK\Downloads\_BRAILCE-ADJUST_audio_backup_44k_stereo\` (853 WAVs).
- **Tests:** `brailce/tests/test_playback_chain.py` 5/5 (aplay receives the ORIGINAL path, no temp file created/read, single plughw retry on nonzero exit, zero "np.repeat" in brailce/speech, bank samples 22050/mono/16-bit). `tools/verify_playback_chain.py` clean across all 853 WAVs; `tools/audit_audio.py` PASS on both banks. Full suite: 537 passed / 7 failed / 1 skipped — all 7 failures pre-existed JOB 2 (AI query, Filipino LCD/speech parity x3, enye keystroke WAV, keypress crash x2, OBR synthetic) and the 7 previously failing audio format/hash/level audit tests now pass. The pre-existing audio-audit failures are resolved.

---
## Speaker Output Settings Debug (basag output + dead volume keys)
- **Status:** DONE
- **Files Changed:** `tools/fix_buzzing_audio.sh`, `tools/apply_voice_upgrade.sh`, `tools/test_speaker_quick.sh`, `tools/clean_voice_anti_bass.py`, `brailce/config/config.json`, `brailce/config/defaults.py`, `brailce/audio/**/*.wav` (853 files re-restored to 22050 Hz mono 16-bit), `brailce/tests/english_audio_manifest.sha256`, `brailce/tests/test_filipino_tts_v2.py` (frozen English hash), `CHANGES.md`, `PROGRESS.md`.
- **Audit Findings (verified on disk before fixing):**
  1. Both deploy scripts wrote a DIRECT `hw` chain (`pcm.!default -> type hw`) to `~/.asoundrc` + `/etc/asound.conf` with NO softvol and NO dmix, so `amixer sset Master` (brailce/speech/audio_device.py) always failed (no "Master" control in the chain) and the volume keys / config volume had zero effect. The direct route also defeated the repo's own anti-buzz theory: a 22050 Hz file opened `hw` at 22050 (about 705 kHz BCLK) instead of the intended 44.1 kHz stereo / 1.411 MHz lock.
  2. `tools/clean_voice_anti_bass.py` (untracked, had been run) had rewritten ALL 853 bank WAVs to 44.1 kHz STEREO with TARGET_PEAK = 5000 (about -16 dBFS; measured active RMS 891-1307 — extremely quiet), contradicting the verified JOB 2 bank (22050 Hz mono 16-bit, active RMS 4200, peak < 30000) enforced by tools/verify_playback_chain.py, brailce/tests/english_audio_manifest.sha256, and the frozen hash in test_filipino_tts_v2.py. Its docstring even claimed "Peak 14,000" while the code capped 5000.
  3. A dead `output_gain_db` key sat in config.json + defaults.py (nothing reads it since service.py dropped it), and speech `volume` was 70 instead of the verified reference 100.
  4. `tools/test_speaker_quick.sh` only tested the plughw route (not the default route the app uses), fell back to an arbitrary "card 2", and dumped no effective settings.
- **Fixes:**
  1. STEP 0 bank probe proved the anti-bass state (char_A.wav: 44100 Hz / 2 ch / active RMS 943 / peak 5000), so the bank was re-restored with `python tools/convert_bank_to_22050_mono.py` (22050 Hz mono 16-bit, active RMS 4200 median, peak <= 30000, durations preserved; the 250 Hz HPF stays baked in). Manifest + FROZEN_ENGLISH_CHARACTERS_SHA256 regenerated to match.
  2. `tools/clean_voice_anti_bass.py` REPAIRED, not deleted: 2nd-order Butterworth HPF @ 250 Hz kept, now running at each file's OWN sample rate (no resample); re-normalization via `brailce.speech.audio_normalize.normalize_loudness_pcm` (active RMS 4200 / peak < 30000); output written back at the INPUT rate, mono, 16-bit (22050 Hz files stay 22050 Hz mono); docstring fixed (44.1 kHz stereo / peak-5000 claims removed).
  3. `tools/fix_buzzing_audio.sh` + `tools/apply_voice_upgrade.sh` now write the SAME canonical chain to BOTH `$HOME/.asoundrc` AND `/etc/asound.conf`: `!default (plug) -> softvol "Master" -> dmixer (dmix 44.1 kHz stereo, period 1024, buffer 8192) -> hw:$DAC_CARD,0`, plus `ctl.!default hw $DAC_CARD`. Per-script DAC detection kept; the no-detection fallback is the `sndrpihifiberry` NAME, used consistently in `hw:` and `ctl:`. Added the one-line NOTE that the softvol "Master" control only exists while the default PCM is open; echo lines now truthfully describe the chain (44.1 kHz stereo lock = 1.411 MHz BCLK, volume via softvol Master). `apply_voice_upgrade.sh` amixer section: `amixer set Master 100% unmute` (best-effort `|| true` with an honest comment).
  4. Config cleanup: `output_gain_db` removed from config.json AND defaults.py; speech `volume` -> 100 in both; `filipino_pitch_semitones` left exactly as-is (0.0); nothing outside speech touched.
  5. `tools/test_speaker_quick.sh` upgraded into a real OUTPUT-SETTINGS diagnostic: aplay -l + /proc/asound/cards, effective ~/.asoundrc + /etc/asound.conf dump, `amixer scontrols` (+ softvol-Master-only-during-playback note), configured app volume from config.json, then char_A.wav AND word_ano.wav through BOTH routes (App route `aplay -q`, labeled "default -> softvol -> dmixer"; Direct `plughw:<card>,0`), ending with an English+Filipino decision summary (default basag + direct clean -> chain problem; both basag -> hardware: amp gain pin / 5V supply / speaker wiring / volume too high; both clean + app basag -> which sound: letra / salita / sagot ng AI). Arbitrary card-2 fallback removed.
- **Tests:** `python tools/verify_playback_chain.py` -> clean (853 WAVs 22050/mono/16-bit, active RMS 3500-5000, peak <= 30000, no clipped samples, 0 np.repeat). pytest test_playback_chain + test_english_frozen + test_filipino_tts_v2 + test_configuration + test_audio_audit: 32 passed / 1 failed — the single failure is the missing enye keystroke `char_?.wav`, which pre-existed (never committed in any bank version; documented in JOB 2). `bash -n` OK on all three scripts (Git Bash), plus stubbed dry-runs proving both deploy scripts write byte-identical chains to $HOME/.asoundrc and /etc/asound.conf with a detected card (hw:2,0) and with the name fallback (hw:sndrpihifiberry,0).

---
## Speaker Output Level Sweep (measure the clean ceiling, then re-master under it)
- **Status:** DONE (tooling ready; the clean ceiling itself must be MEASURED on the device first — set_bank_level was NOT run locally, the bank is untouched)
- **Diagnosis (settled):** the "basag" distortion is LEVEL-DRIVEN: the bank is mastered loud (active RMS 4200, peaks up to 30000) and the MAX98357A / sndrpihifiberry amp chain overloads somewhere above the clean-but-quiet zone (peak ~5000 was clean but too quiet). The clean ceiling is unknown until measured by ear, so: (1) sweep the SAME letter + word at rising peaks, (2) note the highest peak still MALINIS on both, (3) re-master the whole bank two steps under that ceiling, (4) move live-synthesis TARGET_ACTIVE_RMS to the re-mastered bank's median so Piper/Filipino speech matches the bank.
- **Files Changed:** `tools/make_speaker_sweep.py` (new), `tools/speaker_level_sweep.sh` (new), `tools/set_bank_level.py` (new), `tools/verify_playback_chain.py` (expected active-RMS band now derived from `brailce.speech.audio_normalize.TARGET_ACTIVE_RMS` instead of hard-coded 3500-5000), `brailce/speech/audio_normalize.py` (comment only — TARGET_ACTIVE_RMS value untouched at 4200.0), `.gitignore` (ignores `speaker_sweep_out/`), `CHANGES.md`, `PROGRESS.md`.
- **Tools & usage:**
  1. `python3 tools/make_speaker_sweep.py [outdir]` — linear-scaled copies of `char_A.wav` + `word_ano.wav` at peaks 4000/6000/8000/10000/12000/14000/16000/20000/24000/28000 plus 1000 Hz 0.5 s sines at 4000/12000/20000 (22050 Hz mono 16-bit, pure `samples * target_peak / current_peak` scaling with +/-32767 clip-guard; NO filtering/resampling/RMS re-normalization; aborts unless both sources are 22050/mono/16-bit).
  2. `bash tools/speaker_level_sweep.sh [direct]` — venv-activated, generates `speaker_sweep_out/`, then plays every charA + word_ano in ASCENDING peak order with `>>> PEAK N - pakiramdaman kung malinis o basag` labels, then the three sines, then the EN+FIL summary. App route = `aplay -q`; `direct` detects the DAC via `aplay -l` (grep dac|hifiberry|max98357|pcm5102a) and plays `aplay -D plughw:<card>,0 -q`; if no DAC is found it SAYS SO and uses the app route (no arbitrary numeric fallback). The summary reminds: (a) the highest MALINIS peak on BOTH letra at salita; (b) basag from PEAK 4000 up means level is NOT the problem — report it; (c) the follow-up `python3 tools/set_bank_level.py --peak <malinis na peak na 2 hakbang mas mababa>` with the concrete example (malinis hanggang 12000, basag sa 14000 -> --peak 10000); (d) the top levels are SUPPOSED to sound ugly; (e) restart the app/service after set_bank_level.
  3. `python3 tools/set_bank_level.py --peak N` (1000-30000, `--dry-run` supported) — refuses unless EVERY WAV under brailce/audio is 22050/mono/16-bit; scales each WAV's own peak to N (linear, +/-32767 guard, format preserved, progress every 150 files, atomic temp+rename writes); re-baselines `TARGET_ACTIVE_RMS` to the post-scale median active RMS (frames above amplitude 500, rounded to nearest 100) so live synthesis matches the bank; regenerates `brailce/tests/english_audio_manifest.sha256` + `FROZEN_ENGLISH_CHARACTERS_SHA256` in `test_filipino_tts_v2.py`; auto-runs `tools/verify_playback_chain.py` and prints its verdict + the `sudo systemctl restart brailce.service` line.
- **verify_playback_chain band change:** expected active RMS = `max(800, 0.5*T)..2.2*T` with `T = brailce.speech.audio_normalize.TARGET_ACTIVE_RMS` (imported at run time, no longer hard-coded 3500-5000; today T=4200 -> band 2100-9240 and the current bank passes at its measured 3510-4990). The 2.2x upper bound is MEASURED, not arbitrary: after per-file peak normalization the bank's active-RMS spread is 0.63-2.05x its median (853 files), so a tighter 1.5x cap would false-flag ~20 loud low-crest files at every mastered level.
- **Local verification:** py_compile OK (make_speaker_sweep.py, set_bank_level.py, verify_playback_chain.py, audio_normalize.py); `bash -n tools/speaker_level_sweep.sh` OK (Git Bash); smoke run of `make_speaker_sweep.py` into a TEMP DIR OUTSIDE the repo: 23 files, every speech/sine output peak EXACTLY equals its target (asserted programmatically), sines 0.5 s, all 22050/mono/16-bit — temp dir deleted, no WAVs committed. `python tools/verify_playback_chain.py` still PASSES the current bank (853 WAVs). `set_bank_level.py --peak 10000 --dry-run`: 853 files, projected median active RMS 2574 -> new TARGET_ACTIVE_RMS 2600, git status unchanged (writes nothing). pytest test_playback_chain + test_english_frozen + test_filipino_tts_v2 + test_configuration + test_audio_audit + test_filipino_keystrokes: 78 passed / 1 failed — the single failure is the pre-existing missing enye keystroke `char_ñ.wav` (documented since JOB 2, unrelated to this change).
