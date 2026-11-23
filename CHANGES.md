# Brailce System Fixes & Architectural Changes (`CHANGES.md`)

## 1. Confirmed Root Causes & Fixes by Area

### Global Cleanup, Configuration & Bilingual i18n
- **Root cause:** Hardcoded Windows paths, Raspberry Pi IP addresses, SSH credentials, and host keys were scattered across `brailce/config/config.json`, root `deploy_to_pi.py`, `tools/deploy_to_pi.py`, `tools/generate_character_cache.py`, `piper_engine.py`, `filipino_engine.py`, and ~12 stale one-off helper scripts under `tools/`.
- **Fix:**
  - Removed hardcoded credentials/IPs/paths from tracked source and config (`vision.test_image_path` cleared to `""`).
  - Deleted stale one-off `tools/` scripts and stale root `deploy_to_pi.py`; updated `tools/deploy_to_pi.py` and `tools/generate_character_cache.py` to read `PI_IP`, `PI_USER`, `PI_PASS`, `PI_REMOTE_DIR`, and `PI_HOSTKEY` from `.env` / environment variables (documented in `.env.example`, ignored via `.gitignore`).
  - Added unified `brailce/i18n/strings.py` (`STRINGS`, `get_string()`, `get_mode_intro()`, `get_mode_name()`) so LCD display text and spoken TTS prompts use the exact same localized `"en"` and `"fil"` strings.

---

### A — Consistent English Word Pronunciation (`Free Input`, `Math`, `AI`, `Scanner`)
- **Root causes:**
  1. Isolated function words (`is`, `his`, `this`, `the`, `was`, `what`, `when`, `where`, `why`, `who`) were routed through inconsistent synthesis calls and hit Piper VITS boundary swallowing at `t = 0` plus `MagicMock` / `hasattr` branching in `free_input.py`.
  2. Multi-parameter mismatches existed across engines instead of one consistent `en_US-ryan-medium` voice configuration.
- **Fix & Rationale (Option 1 vs Option 2):**
  - Locked `PiperEngine` to a single VITS parameter set (`length_scale=1.05`, `noise_scale=0.5`, `noise_w=0.6`) across both isolated words and sentences so timbre stays uniform.
  - Selected **Option 1 (Carrier-Phrase Slicing + Pre-rendered Cache)** over purely raw single-word synthesis with ad-hoc glottal hacks: `SpeechService.speak_word_clean()` resolves lowercase words via `brailce/audio/words_en/<word>.wav` first, and on a miss synthesizes `"Please say: <word>."`, slices cleanly after the colon silence gap via `brailce/speech/audio_normalize.py` (`slice_carrier_audio()`), normalizes active speech RMS (`TARGET_ACTIVE_RMS = 4200.0`, peak cap `30000.0`), and pads with `120 ms` pre / `180 ms` post zero silence.
  - Removed `MagicMock` / `Mock` hacks in `brailce/modes/free_input.py`, `math_mode.py`, and `ai_assistant.py`, unifying all playback on `SpeechService` and `AudioDeviceCoordinator.play_wav_file()` (`aplay -q`).

---

### B — Filipino Math Mode & Arithmetic Visualization
- **Root causes:**
  1. `SafeArithmetic.solve()` generated hardcoded English step/counting explanations even in `"fil"` mode, and Filipino keypad/mode/math card LCD headers remained in English or clipped on `480x320` ILI9488 displays.
  2. `brailce/audio/filipino/word_oo.wav` was silent/near-empty (low RMS).
- **Fix:**
  - Implemented `SafeArithmetic.solve(expr, language=...)` with Tagalog `0–999` number-to-word conversion (including `-ng` / `na` linkers), Filipino Grade 1 counting steps/explanations, and localized error keys (`MATH_ERR_*`) sourced from `brailce/i18n/strings.py`.
  - Updated `ConsoleDisplay` and `ILI9488Display.show_math_solution()` with localized titles (`MATH_CARD_TITLE`, `MATH_LABEL_*`) and 48-character multi-line word wrapping to prevent LCD clipping on `480x320`.
  - Regenerated the Filipino audio cache and normalized `word_oo.wav` and all sibling WAVs uniformly.

---

### C — Accurate Offline Filipino AI Assistant (`lfm2.5-1.2b-instruct`)
- **Root causes:**
  1. Weak Filipino system prompting allowed the `1.2B` model to slip into English paragraphs or emit markdown/emojis/loanword clutter.
  2. Spoken responses read hyphens inconsistently or choked on unexpanded numbers in Filipino TTS.
- **Fix:**
  - Upgraded `FILIPINO_SYSTEM_PROMPT` in `brailce/ai/ollama_client.py` with strict 3-example Tagalog few-shot prompting, plus `is_mostly_english()` detection with one automatic retry and localized `AI_EMPTY_FALLBACK` safety net.
  - Enhanced `clean_ai_response()` to strip markdown/emojis/special tokens and convert hyphens inside words into spaces for clean TTS articulation while preserving natural Braille terms (`Braille`, `Brailce`).
  - Pre-cleaned offline factual responses (`FACTUAL_QA_RESPONSES`, `FILIPINO_AI_OFFLINE_RESPONSES`) in pure Grade 1 Filipino.

---

### D — Single Consistent Voice per Language (Zero Online `edge-tts` / Zero Mixed Ryan-Filipino)
- **Root causes:**
  1. Previously, `FilipinoEngine` relied on `edge-tts` (`ffmpeg` conversion + internet access) or fell back to `filipino_to_phonetic()` spoken through English `PiperEngine` (switching speakers mid-sentence and mispronouncing Tagalog vowels/consonants).
  2. Word files had uneven volume and inconsistent sample rates across WAVs.
- **Fix & Rationale:**
  - Standardized strictly on **one offline voice per language**:
    - English (`"en"`): Piper `en_US-ryan-medium.onnx` via `PiperEngine` (single worker queue).
    - Filipino (`"fil"`): `sherpa-onnx` MMS-TTS Tagalog (`tgl`) VITS offline model (`brailce/models/filipino/model.onnx` + `tokens.txt`) via `FilipinoEngine` (single `queue.Queue` worker), plus `brailce/audio/filipino/` WAVs pre-rendered and RMS-normalized from that same voice via `tools/build_audio_bank.py` and `brailce/speech/audio_normalize.py`.
  - Deleted `filipino_to_phonetic()` runtime fallback and removed `edge-tts` / `ffmpeg` runtime imports from production engines. Added `speech.filipino_letter_voice` switch (`"filipino"` default vs `"english"`).
  - Created `tools/audit_audio.py` to enforce sample rate (`22050 Hz`), 16-bit mono PCM, active RMS consistency (`±3 dB` of cohort median, no silent/empty WAVs), and peak headroom across all cached audio banks.

---

### E — Offline Classical Grade 1 Braille Scanner & Camera Fixes
- **Root causes:**
  1. `PiCameraDevice` configured Picamera2 with `format="BGR888"`, which swaps R/B channels in OpenCV memory (cyan tint), captured single un-settled preview frames (`640x480`), and fell back to generating a fake synthetic Braille image when hardware was missing.
  2. `OBREngine` tried downloading YOLO weights from GitHub at runtime (failing offline) and stripped trailing `'s'` characters (`rstrip("sS")`).
  3. `ScannerMode` ran heavy detection continuously and spoke negative prompts on every cycle without hysteresis/cooldown.
- **Fix:**
  - `brailce/vision/camera.py`: Fixed Picamera2 `format="RGB888"` (matching OpenCV BGR order), added AE/AWB settle & colour-gain lock, `capture_still()`, `capture_sharpest(n=5)` via `compute_sharpness()`, and explicit `CameraNotConnectedError` reporting (`SCANNER_CAMERA_NOT_CONNECTED`) instead of synthetic Braille fabrication.
  - `brailce/vision/obr_engine.py`: Implemented an offline classical dot-level Grade 1 recognizer (quality gates for brightness/Laplacian sharpness/dot resolution, illumination flattening + Difference-of-Gaussians dot detector, border artifact filtering, nearest-neighbor skew estimation & deskew, 2x3 cell lattice fitter, exact `BINARY_DICT` translation returning `?` / `"unknown"` on invalid cells without guessing), removed runtime GitHub downloads and `rstrip("sS")`, and gated optional local YOLO weights behind `use_yolo_secondary=False`.
  - `brailce/modes/scanner_mode.py`: Added ~3.5 Hz lightweight classical presence polling with hysteresis (`3` positive frames / `4` negative frames) and `6 s` cooldown, sharpest still capture on Enter, actionable distance/blur feedback (`SCANNER_BLURRY`, `SCANNER_MOVE_CLOSER`, `SCANNER_MOVE_FARTHER`), systematic interruptible letter-by-letter -> whole-word readout (`CHAR_PACE_S`, `WORD_PAUSE_S`, `LINE_PAUSE_S`), Enter-to-repeat, Clear-to-reset, and any-key speech cancellation.
  - Added `tools/scan_debug.py` visual dot/cell annotation CLI and `brailce/tests/test_obr_classical.py`.

---

## 2. Manual Raspberry Pi 5 Test Checklist

Run these checks directly on the Raspberry Pi 5 hardware after deployment:

### Checklist 1: Audio Bank & Offline Parity
- [ ] Run `python3 tools/audit_audio.py` on Pi and verify `0` failures (`en_characters` and `filipino` PASS).
- [ ] Disconnect Wi-Fi/Ethernet and launch `python3 main.py`; confirm zero network errors at startup and during speech.

### Checklist 2: Free Input & English Word Consistency
- [ ] Press single letter keys `A`–`Z` and confirm crisp, unclipped single-letter pronunciation.
- [ ] Type `is`, `his`, `this`, `the`, `was`, `what`, `when`, `where`, `why`, `who` followed by **Space** or **Enter**; verify natural pronunciation matches sentence playback (`This is when we read.`).
- [ ] Press Backspace/Clear during playback and verify speech stops immediately.

### Checklist 3: Filipino Mode (Language Toggle, Math & AI)
- [ ] Press Language toggle (`F3` / hardware switch) -> verify `"Wikang Filipino."` announcement and `[FIL]` LCD indicator.
- [ ] Switch to **Math Mode**: enter `5+3` and `12-7` -> verify Tagalog counting steps, LCD card wrapping (no text clipped on `480x320`), and clean `20-5` = `15` audio (`labinlima`) with uniform single Filipino voice.
- [ ] Switch to **AI Assistant Mode**: type `"Ano ang araw?"` and `"Ano ang Braille?"` -> verify short pure-Filipino voice replies without English filler, markdown characters, or speaker switching.

### Checklist 4: Scanner Mode (Pi Camera v2 + Embossed Sheet)
- [ ] Disconnect camera ribbon briefly -> enter Scanner Mode and verify LCD shows + speaks `"Camera not connected."` (and Filipino equivalent) without showing fake Braille.
- [ ] Connect Pi Camera v2 under side raking LED light (~15–30° angle, ~12–15 cm close focus). Hold blank paper -> no false `"Braille detected"` spam.
- [ ] Slide Grade 1 embossed sheet (`NEED` / `BRAILLE` / `#1234`) into view -> hear `"Braille detected. Press Enter to read."` once (no repeated nagging).
- [ ] Press **Enter** -> confirm LCD displays each character + Braille dots in sync while speaking letter-by-letter then the whole word (`N` `E` `E` `D` -> `need`).
- [ ] Press any key mid-readout -> playback stops immediately. Press **Enter** again to repeat or **Clear** to reset for a new page.
- [ ] Run `python3 tools/scan_debug.py --camera --out /tmp/scan_debug.png` to inspect dot alignment & Laplacian sharpness if focusing the IMX219 lens.

---

## 3. Phase 1–5 Comprehensive Architectural Upgrades (Round 3)

### Phase 1: Frozen English Baseline & Crash Isolation
- Verified and snapshot 100% frozen byte-for-byte behavior for all English audio files, `PiperEngine`, `EspeakEngine`, and `STRINGS["en"]` via `brailce/tests/test_english_frozen.py`.
- Fixed `UsbWebcamDevice` missing `import threading` and `from typing import Any` (`NameError: name 'threading' is not defined`).
- Resolved Filipino character synthesis regression when keypress events arrive with custom styles.

### Phase 2: Scale-Adaptive Classical OBR Braille Scanner Rewrite
- **Scale-Adaptive Multi-Scale Detection:** Difference-of-Gaussians ($\sigma=1.2, 1.8, 2.4, 3.2$) with shadow-highlight pairing and area filtering (`area >= 0.30 * med_a`) that eliminates edge artifacts while preserving embossed dot shadows.
- **Robust Skew & Dot Pitch Estimation:** Nearest-neighbor vector orientation clustering in $\pm 15^\circ$ range with distance histograms detecting true physical dot pitch ($p_{dot}$).
- **Global Lattice Fitter & Metric Alignment:** Symmetric cell boundary partition at $(p_{cell} + p_{dot}) / 2$ with intra-cell dot column tolerance $[0.82 \cdot p_{dot}, 1.18 \cdot p_{dot}]$ and inter-cell space tolerance $\ge 1.35 \cdot p_{dot}$.
- **Non-Braille Rejection:** Safe rejection of monitor terminal photos (`tools/test_real_cam.jpg`) and random noise pages via unknown-cell ratio limit ($>20\%$) and line density coherence.
- Added comprehensive ground truth suite `tools/synth_braille.py`, `tools/eval_real_scans.py`, and `brailce/tests/test_obr_synthetic.py` (accuracy $\ge 90\%$ across dot diameters 7–12px, rotations $\pm 5^\circ$, and both ink and embossed textures).

### Phase 3: Natural Filipino Prosody, Expanded Lexicon & Audition Suite
- **Single Lexicon Truth:** Mapped letter/number/symbol names via `brailce/i18n/fil_keys.py`.
- **Numbers to 999,999:** Full Tagalog number expansion in `brailce/speech/filipino_engine.py` (`tagalog_number`), supporting thousands (`libo`) and hundreds (`daan` / `raan`).
- **Natural Sentence Rhythm:** Configured 250ms silence pause for commas and semicolons, 400ms for terminal punctuation (`.`, `?`, `!`, `\n`).
- **Normalized Grammar Tokens:** Handled `"mga"` normalized to `"manga"`, and pre-synthesized all missing vocabulary WAVs (`word_libo.wav`, `word_manga.wav`, `word_pagpindot.wav`, `word_panuri.wav`, `word_posisyon.wav`, `word_tapos.wav`).
- Generated complete candidate parameter evaluation and human listening checklist in `AUDITION.md` (all 3 parameter sets achieve 0.000% clipping).

### Phase 4: High-Contrast ILI9488 LCD UI Typography & Layout
- **Bundled Font:** Packaged clean TrueType font `brailce/display/fonts/BrailceDefault.ttf` supporting both standard Latin ASCII and Unicode Braille patterns (U+2800–U+28FF).
- **Type Scale:** Active character scaled to 72px, mode header titles to 20px, labels to 18px, and body text to 15px.
- **Emoji Elimination:** Clean textual headers (`FREE INPUT`, `MATH MODE`, `AI ASSISTANT`, `SCANNER`) with clean ASCII battery meter (`pct%` / `pct%+`).
- Added screenshot verification tool `tools/render_screens.py` generating high-contrast renders across all operational modes.

### Phase 5: Production Deployment & Verification Scripts
- Added Windows PowerShell deployment script `tools/deploy_to_pi.ps1`.
- Added Raspberry Pi automated update script `tools/pi_update.sh`.

---

## 4. Camera, Scanner Mode & ILI9488 LCD Preview Hardening (Round 4)

### ILI9488 Camera Preview Crash & Rendering Pipeline
- **Root causes:**
  1. `Thread-7 (_preview_loop)` crashed with `UnboundLocalError: cannot access local variable 'exc' where it is not associated with a value` because `logger.debug(..., exc)` was placed in the `finally:` block where `exc` was unassigned when no exception occurred in `try:`.
  2. Frame ingestion lacked type-tolerance for 2D grayscale arrays, float32 arrays, and PIL Images, and float coordinates caused geometry calculation failures.
- **Fix:**
  - Removed dangling `exc` reference from the `finally:` block, strictly restricting it to `except Exception as exc:`.
  - Added robust conversion for PIL Images, 2D grayscale numpy arrays, 3-channel BGR/RGB, 4-channel BGRA, and float-normalized frames with automatic uint8 scaling.
  - Converted all canvas coordinates, letterbox offsets, guide boxes, and status badges to explicit integer tuples `(int(x), int(y))` and replaced deprecated Pillow font calls with modern `textbbox` safe rendering.
  - Added non-blocking lock acquisition (`self._display_lock.acquire(blocking=False)`) for hardware SPI flushes to drop intermediate frames when hardware is busy rather than starving audio/input threads.

### Scanner Mode & Detection Thread Bulletproofing
- **Root causes:**
  - `_preview_loop` and `_detection_worker` had uncaught exceptions inside their `while` loops that killed worker threads on frame glitches. The preview loop sleep was set to 0.18s (~5.5 FPS).
- **Fix:**
  - Wrapped `_preview_loop` and `_detection_worker` iterations in dedicated `try / except Exception as exc:` blocks to log warnings and continue streaming at >= 10 FPS (0.06s sleep target).
  - Ensured `on_enter` reliably restarts threads if dead, and `on_exit` cleanly joins daemon threads and closes preview state.
  - Throttled disk snapshots in `ConsoleDisplay.show_camera_preview` to 1 Hz to prevent I/O thrashing.

### USB Webcam Initialization, Warm-Up & High-Res Still Capture
- **Root causes:**
  - USB webcams output 5–20 initial black/empty frames before auto-exposure settles. A single-frame rejection caused false camera-not-connected errors.
- **Fix:**
  - Implemented a 30-frame / 2.0-second warm-up loop in `UsbWebcamDevice._open_capture` requiring both brightness and variance thresholds (`mean > 1.0` and `std > 0.5`) before accepting the stream.
  - Added multi-backend fallback (`V4L2`, `CAP_ANY` on Linux; `DSHOW`, `MSMF`, `CAP_ANY` on Windows) and automatic reconnection retry on transient frame drops.
  - Implemented high-resolution still capture with Laplacian variance (`cv2.Laplacian(gray, cv2.CV_64F).var()`) selecting the sharpest frame among burst captures.
  - Added CLAHE contrast enhancement fallback in `OBREngine.process_image` for low-contrast embossed paper.

### Complete Replacement of Filipino Speech Engine & Vocabulary Bank with ASR Verification
- **Root causes:**
  - Every Filipino WAV was previously generated via the offline sherpa-onnx MMS-TTS `tgl` VITS model. MMS-TTS reads text strictly as Tagalog orthography, causing English-style letter respellings ("ey", "bi", "dobol-yu") to sound like trailing glides/garbage ("eya", "teya", "heya").
  - On short words, MMS-TTS drops entire syllables; automated ASR round-trip verification proved that `word_kumusta.wav` generated via MMS-TTS transcribed as `"kus"` (dropping "mu-sta") with an active duration under 0.49s.
  - The runtime dispatch in `FilipinoEngine.speak_filipino` attempted live MMS-TTS synthesis before attempting concatenative synthesis, polluting the sentence hash cache with corrupted audio.
- **Fix:**
  - Replaced the fixed Filipino vocabulary generation engine on the PC build step:
    - `fil-PH-BlessicaNeural` is used for all Grade 1 Filipino sight words, arithmetic step tokens, mode introductions, and offline AI responses.
    - `en-PH-RosaNeural` with `"Please say: <char>."` carrier-phrase slicing is used for all character keystrokes (A-Z, 0-9, operators, punctuation), producing crisp English character names with a natural Filipino accent and zero trailing glides.
    - Code-switched terms (*Braille*, *Brailce*, *AI*, *Grade*) are spoken with natural Filipino-accented pronunciation and phonetically mapped in `fil_lexicon.py` and `filipino_engine.py`.
  - Built automated verification harness (`tools/verify_filipino_audio.py`) utilizing Google ASR round-trip transcription (`fil-PH` and `en-US`), syllable count duration sanity (>= 0.09s per vowel/syllable), peak clipping tests, and loudness bounds.
  - Regenerated all 602 Filipino WAV files in `brailce/audio/filipino/` and generated 30 audition samples in `tools/voice_audition/fil_v2/`.
  - Upgraded runtime `FilipinoEngine.speak_filipino` to strictly prioritize:
    1. Pre-cached single word and offline AI response lookup.
    2. Exact-phrase hash cache (`sentence_<md5>.wav`).
    3. Concatenative bank synthesis with natural 80ms word pauses, 250ms comma pauses, and 400ms sentence pauses (tested at 100% ASR accuracy on unseen sentences).
    4. Offline fallback synthesizer strictly for unseen out-of-vocabulary words.
  - English audio assets verified bit-exact and unchanged (`FROZEN_ENGLISH_CHARACTERS_SHA256 = "a3c783ddf53f47c20a025480064080bfd51a24ff6d23b46b273e3da32901d12e"`).
  - Added `brailce/tests/test_filipino_tts_v2.py` covering keystroke bounds, syllable retention, Free Input integration, and mode intros (59/59 pytest suite passing).






---

### Section C — Lineage Merge, Regression Fixes, Camera Pipeline & Letter Clarity (2026-11-19)

#### WHY IT REGRESSED
Two separate folder lineages (`BRAILCE ADJUST` and `BRAILCE TYPING AND AI ONLY`) diverged:
1. `BRAILCE ADJUST` had the updated scanner threshold (0.42) and camera warm-up logic, but was missing the re-rendered Filipino keystrokes, Tagalog punctuation names (`tuldok`, `kuwit`, `tandang pananong`, `tandang padamdam`), and test suites that existed only in `TYPING AND AI ONLY`.
2. Neither repo had the `allow_ai_cache=False` guard in `speak_filipino()` — so typing phrases like "Kumusta ka" in Free Input caused the system to mistakenly match and play AI answer clips (`ai_kumusta_ka.wav`).
3. Picamera2 locked AWB prematurely (after only 0.35 s) and turned off AWB (`AwbEnable=False`), causing a strong cyan/blue cast on camera frames.
4. USB webcam probe on Raspberry Pi 5 tested every `/dev/video*` node (including kernel ISP queues `/dev/video10..31`), hanging or causing false "camera not connected" errors on single dropped frames.

#### ROOT CAUSES, FIXES & VERIFICATION TABLE
- **Camera Probe Hang & Disconnects:**
  - *Cause:* Probed kernel ISP queues video10-video31; single failed read immediately triggered "not connected" speech.
  - *Fix:* Restricted candidates to symlinks, low-index `/dev/video0..9` (skipping ISP names in sysfs), and indices 0, 1, 2. Added per-device 1.5s cap, whole probe 6s cap. Tolerates 10 consecutive failed reads with 3s background re-probing. Added `tools/camera_check.py`.
  - *Files:* `brailce/vision/camera.py`, `brailce/modes/scanner_mode.py`, `brailce/tests/test_camera_probe.py`, `tools/camera_check.py`.
  - *Verified:* `test_camera_probe.py` 5/5 passed. Needs Pi check: Y. Needs PC re-render: N.

- **Filipino Letter Quality & English Letter Names:**
  - *Cause:* Letter respellings used awkward phonetic glides; letter WAVs lacked standardized level and female pitch consistency.
  - *Fix:* Re-rendered 26 letters (A-Z) and 10 digits (0-9) using `fil-PH-BlessicaNeural` with exact phonetic respellings (ey, bi, si, di, ..., dobolyu, eks, wai, zi). Normalized to 22050 Hz mono s16, 15ms lead / 80ms tail, RMS ~3500-4500 (within 2000-6500 bounds).
  - *Files:* `brailce/i18n/fil_keys.py`, `brailce/audio/filipino/char_*.wav`, `tools/render_filipino_letters.py`, `brailce/tests/test_fil_letter_clarity.py`, `brailce/tests/test_filipino_keystrokes.py`.
  - *Verified:* `test_fil_letter_clarity.py` (F0 median 196-265 Hz female, centroid 850-4383 Hz) and `test_filipino_keystrokes.py` 46/46 passed. Needs Pi check: Y. Needs PC re-render: N.

- **Free Input Mode AI Answer Echo:**
  - *Cause:* `speak_filipino()` checked `ai_<slug>.wav` for all text indiscriminately.
  - *Fix:* Added `allow_ai_cache: bool = False` to `speak_filipino()`, `enqueue_text()`, and `SpeechService.speak_sentence()`. Only `AIAssistantMode` passes `allow_ai_cache=True`.
  - *Files:* `brailce/speech/filipino_engine.py`, `brailce/speech/service.py`, `brailce/modes/ai_assistant.py`, `brailce/tests/test_filipino_junctures.py`.
  - *Verified:* `test_filipino_junctures.py` 2/2 passed. Needs Pi check: Y. Needs PC re-render: N.

- **Filipino Junctures & Prosody:**
  - *Cause:* Fixed gap bytes and uneven word padding caused unnatural stutter and pauses between words.
  - *Fix:* Active speech trimming (threshold ~ -45 dBFS, 8ms padding), 12ms cosine crossfade, 15ms inter-word gap, 250ms comma pause, 450ms period/question pause, 300ms semicolon pause.
  - *Files:* `brailce/speech/filipino_engine.py`.
  - *Verified:* Measured inter-word gap 15ms (<= 60ms bound), punctuation pause 450ms (350-550ms bound). Needs Pi check: Y. Needs PC re-render: N.

- **Scanner Presence Gate & Sparse Line Rejection:**
  - *Cause:* Confidence threshold 0.60 was too strict (real Braille page scored 0.478); sparse-lines gate rejected real pages with 1-2 words.
  - *Fix:* Lowered confidence threshold to 0.42; relaxed sparse-lines gate to only trigger on >=5 lines with <1.0 cell/line.
  - *Files:* `brailce/vision/obr_engine.py`, `brailce/modes/scanner_mode.py`, `brailce/config/defaults.py`, `config.json`.
  - *Verified:* Real Braille test images pass with confidence 0.507-0.638, decoded "HAD" successfully. Needs Pi check: Y. Needs PC re-render: N.


---

### Section D — Restored Reference 22050 Mono Audio Pipeline & Speech Settings (JOB 2, 2026-11-23)

#### WHY IT REGRESSED
The audio sounded harsh ("basag"): `tools/master_all_audio_44k_stereo.py` (commit 474570b) converted the whole WAV bank to 44.1 kHz stereo peak-20000, then `audio_device.py` grew a runtime gain stage that rewrote every WAV to a temp file — 22050->44100 via `np.repeat` (buzzy duplication), tanh limiter, software volume/gain math (`set_gain_db`/`_volume_pct`/`_gain_cache`). Live synthesis was also re-gained at playback and Piper `sentence_silence` had drifted to 0.20.

#### FIXES
- **`brailce/speech/audio_device.py`:** restored reference-simple playback `aplay -q <file.wav>` — default device first, ONE `plughw:<card>,0` retry, process registration/interrupt machinery kept. Removed ALL gain staging: `np.repeat` upsampling, tanh limiter, temp WAV rewriting, stereo conversion, `set_gain_db`/`_apply_output_gain`/`_gain_cache`/`_volume_pct`. The ORIGINAL file path goes to aplay untouched; `set_volume(pct)` -> `amixer sset Master <pct>% unmute` is the single volume control.
- **`brailce/speech/service.py`:** removed `output_gain_db` read and `set_gain_db()` call; restored reference fallback defaults (filipino_pitch_semitones 3.5, word/comma/sentence/char gaps 30/220/380/200).
- **`brailce/speech/piper_engine.py`:** `sentence_silence` 0.20 -> 0.35 (carrier stays 0.55); missing piper binary/model now emits a loud named WARNING (console print once per process) before any espeak fallback.
- **WAV bank -> 22050 Hz mono 16-bit:** new `tools/convert_bank_to_22050_mono.py` (stereo->mono mean, `scipy.signal.resample_poly` only, `normalize_loudness_pcm` levels, bank-median RMS equalization) converted all 853 files. Old 44.1 kHz stereo bank backed up to `C:\Users\MARK\Downloads\_BRAILCE-ADJUST_audio_backup_44k_stereo\`.
- **`tools/master_all_audio_44k_stereo.py` deleted** (no callers existed in scripts/docs).
- **Config/defaults speech -> reference:** volume 100, `filipino_char_gap_ms` 200 (added `char_gap_ms` 800 key removed), `filipino_pitch_semitones` 3.5, `filipino_word_gap_ms` 30, comma/sentence pauses 220/380, `filipino_rate` "-5%"; `output_gain_db` removed from config.json AND defaults.py. English voice keys (Piper lessac/amy model order) untouched.
- **New guards:** `tools/verify_playback_chain.py` (numpy+wave bank audit: 22050/mono/16-bit, active RMS 3500-5000, peak <= 30000, no clipped samples, zero `np.repeat` in brailce/speech) and `brailce/tests/test_playback_chain.py` (aplay gets the ORIGINAL path, no temp file created/read, one plughw retry, source scan, sample format checks). `brailce/tests/english_audio_manifest.sha256` and the frozen English-characters hash in `test_filipino_tts_v2.py` regenerated for the restored format.

#### VERIFIED
`tools/verify_playback_chain.py` passes clean (853 WAVs); `tools/audit_audio.py` PASS both banks; pytest 537 passed / 7 failed — all 7 remaining failures pre-existed the job (AI/Ollama query, Filipino LCD/speech parity, enye keystroke WAV never present in any bank version, OBR synthetic accuracy) and are outside JOB 2 scope. The 7 previously failing audio-bank format/hash/level audits now pass.

---

### Section E — Speaker Output Settings Debug (2026-11-23)

#### WHY
The deploy scripts wrote a direct `hw` `!default` chain (no softvol, no dmix) to `~/.asoundrc` + `/etc/asound.conf`, so `amixer sset Master` could never succeed (volume keys dead) and 22050 Hz bank files drove the I2S clock at ~705 kHz instead of the 44.1 kHz stereo / 1.411 MHz lock. Meanwhile `tools/clean_voice_anti_bass.py` had re-mastered the entire bank to 44.1 kHz stereo peak-5000 (inaudibly quiet, active RMS ~891-1307), and speech config re-grew a dead `output_gain_db` key with volume 70.

#### FIXES
- `tools/fix_buzzing_audio.sh` + `tools/apply_voice_upgrade.sh`: canonical chain written identically to `~/.asoundrc` AND `/etc/asound.conf` — `!default plug -> softvol "Master" -> dmixer (dmix 44100 Hz stereo, period 1024, buffer 8192) -> hw:$DAC_CARD,0` + `ctl.!default hw $DAC_CARD`; truthful echo lines; NOTE that the softvol "Master" control only exists while the default PCM is open; `amixer set Master 100% unmute` best-effort.
- Bank re-restored to 22050 Hz mono 16-bit active-RMS-4200 via `tools/convert_bank_to_22050_mono.py`; `tools/clean_voice_anti_bass.py` repaired (250 Hz HPF at each file's own rate + `normalize_loudness_pcm` re-normalization, INPUT rate / mono / 16-bit preserved, docstring fixed); manifest + frozen English hash regenerated.
- `brailce/config/config.json` + `brailce/config/defaults.py`: dead `output_gain_db` removed, speech `volume` -> 100 (reference); `filipino_pitch_semitones` untouched.
- `tools/test_speaker_quick.sh`: full output-settings diagnostic (cards, effective asoundrc/asound.conf, scontrols, app volume, char_A.wav + word_ano.wav via BOTH routes, English+Filipino decision guide); arbitrary card-2 fallback removed.

#### VERIFIED
`tools/verify_playback_chain.py` clean over 853 WAVs; audio/config/manifest pytest suites 32/33 (only the pre-existing enye `char_?.wav` failure); `bash -n` + stubbed dry-runs OK for all three scripts (identical chains to $HOME and /etc with both card-number and name fallback).

---

### Section F — Speaker Output Level Sweep & Bank Re-Master Tooling (2026-11-23)

#### WHY
The remaining "basag" is level-driven: the active-RMS-4200 / peak-30000 bank overloads the MAX98357A / sndrpihifiberry amp chain somewhere above the clean-but-quiet peak-5000 zone, and the true clean ceiling is unknown until it is MEASURED by ear on the device.

#### FIXES
- **New `tools/make_speaker_sweep.py`** (numpy+wave only): linear-scaled `char_A.wav`/`word_ano.wav` copies at peaks 4000-28000 plus 1000 Hz sines at 4000/12000/20000, 22050 Hz mono 16-bit, no filtering / resampling / RMS re-normalization; aborts unless both sources are 22050/mono/16-bit.
- **New `tools/speaker_level_sweep.sh [direct]`**: generates `speaker_sweep_out/`, plays every charA + word_ano in ascending peak order with `>>> PEAK N - pakiramdaman kung malinis o basag` labels (app route `aplay -q`; direct route `aplay -D plughw:<card>,0 -q` with the DAC detected via `aplay -l` grep dac|hifiberry|max98357|pcm5102a — no numeric fallback, falls back to the app route with a notice), then the three sines, ending with the EN+FIL summary (highest clean peak on BOTH letra at salita; basag-from-4000 means level is NOT the problem; the `python3 tools/set_bank_level.py --peak <malinis na peak na 2 hakbang mas mababa>` follow-up with the 12000-clean/14000-basag -> `--peak 10000` example; the ugly top levels are the point; restart the service after).
- **New `tools/set_bank_level.py --peak N` (+ `--dry-run`)**: re-masters every bank WAV to its own peak N (linear, +/-32767 clip-guard, 22050/mono/16-bit preserved, format-gated with a clear abort, atomic temp+rename writes, progress every 150 files), re-baselines `TARGET_ACTIVE_RMS` in `audio_normalize.py` to the post-scale median active RMS (frames above amplitude 500, rounded to the nearest 100), regenerates `english_audio_manifest.sha256` + `FROZEN_ENGLISH_CHARACTERS_SHA256`, and auto-runs `tools/verify_playback_chain.py`, printing its verdict + the restart line.
- **`tools/verify_playback_chain.py`**: expected active-RMS band now derives from `brailce.speech.audio_normalize.TARGET_ACTIVE_RMS` (`max(800, 0.5x)..2.2x`) instead of hard-coded 3500-5000, so the checker stays truthful at any mastered level. `brailce/speech/audio_normalize.py` received a comment only (value still 4200.0); `.gitignore` now ignores `speaker_sweep_out/`.

#### VERIFIED
py_compile OK for every new/changed .py; `bash -n tools/speaker_level_sweep.sh` OK; smoke run into a temp dir OUTSIDE the repo produced 23 WAVs with every peak EXACTLY on target (temp dir deleted, no WAVs committed); `python tools/verify_playback_chain.py` still passes the current bank (853 WAVs, band 2100-9240 derived from T=4200); `set_bank_level.py --peak 10000 --dry-run` projects 853 files / median active RMS 2574 / new TARGET 2600 with zero writes; pytest (playback chain, english frozen, filipino tts v2, configuration, audio audit, filipino keystrokes): 78 passed / 1 pre-existing failure (missing enye keystroke WAV, documented since JOB 2).
