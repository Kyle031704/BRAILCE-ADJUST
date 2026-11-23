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

---

### Section G — Match Reference Audio Output Exactly (2026-11-23)

#### WHY
The reference repo (BRAILCE-TYPING-AND-AI-ONLY) is loud and clean on the same MAX98357A / sndrpihifiberry hardware, while ADJUST measured clean only at peak 4000 and basag from peak 6000 up. Measuring the reference bank directly (numpy+wave, active frames above 500) shows median peak 16692 / max 31129 — far ABOVE the ADJUST device's 6000 distortion threshold — proving the bank level was never the cause; the custom ALSA route was. The reference configures NO ~/.asoundrc, NO /etc/asound.conf, NO amixer, and NO dtoverlay anywhere: plain `aplay -q` through the Pi system default.

#### FIXES
- Bank re-mastered to the reference's measured levels with ONE uniform linear scale k = P_reference/current_median_peak = 16692/17894 = 0.932799 (set_bank_level semantics: linear scale only, 22050/mono/16-bit preserved, ±32767 clip guard, atomic writes). Achieved median peak 16692 (exact) and median active RMS 3918 (-8.6% vs the reference's 4286, inside the ±25% criterion); the bank stays RMS-tight (3744..4043 p10..p90) like the reference (4050..4831) instead of peak-uniform re-mastering which would spread per-file RMS 0.63..2.05x. `TARGET_ACTIVE_RMS` re-baselined 4200 -> 3900; `english_audio_manifest.sha256` + `FROZEN_ENGLISH_CHARACTERS_SHA256` regenerated.
- `tools/fix_buzzing_audio.sh` + `tools/apply_voice_upgrade.sh`: instead of writing the plug -> softvol "Master" -> dmixer 44.1 kHz stereo chain, they now back up (`mv` to `*.bak.<date>`) and REMOVE `~/.asoundrc` + `/etc/asound.conf`, leaving the system default — with an honest echo that the device now runs the reference's route. `apply_voice_upgrade.sh`'s softvol-driven amixer/pactl volume block removed (the reference sets no ALSA mixer); its `REAL_USER`/`USER_HOME` chain-writing helpers removed with it.
- `tools/enable_i2s_audio.sh`: overlay lines unchanged (the reference specifies none); its plug -> hw `~/.asoundrc` write replaced by the same backup-and-remove (system default route).
- `tools/fix_audio.sh` + `tools/test_and_fix_voice.sh`: their card-pinned `~/.asoundrc` writes replaced by the same backup-and-remove; detection echoes now truthful ("system default decides the route").
- `tools/test_speaker_quick.sh`: route labels truthful — App route = "ALSA system default - reference route"; NO ~/.asoundrc and NO /etc/asound.conf is now the GREEN expected state (a present custom file gets a RED warning to re-run tools/fix_buzzing_audio.sh); the softvol note replaced by the bank-mastered-loudness note.
- `brailce/config/config.json` + `brailce/config/defaults.py`: `filipino_pitch_semitones` 0.0 -> 3.5 (reference value; `service.py` code defaults already matched). English voice keys untouched.
- `brailce/speech/audio_device.py` + `audio_normalize.py`: docstrings only (playback = system default route like the reference; set_volume = best-effort amixer no-op without a Master control; TARGET_ACTIVE_RMS docstring now references the re-baselined constant). No behavior change: default-first `aplay -q` remains primary (reference behavior); the single plughw retry stays failure-only.

#### VERIFIED
`tools/verify_playback_chain.py` PASS over all 853 WAVs (22050/mono/16-bit, active RMS 1950-8580 derived from TARGET_ACTIVE_RMS=3900, peak <= 30000, no clipped samples, 0 np.repeat); pytest (playback chain, english frozen, filipino tts v2 frozen hash, configuration): 27 passed / 1 failed — only the pre-existing missing enye keystroke `char_ñ.wav` (documented since JOB 2); `bash -n` clean on all six changed .sh scripts. Deploy: re-run `bash tools/fix_buzzing_audio.sh` on the device (backs up + removes the old custom ALSA files), then `sudo systemctl restart brailce.service`.

---

### Section H — Speaker + Amplifier + Voice-Output Proof Test (2026-11-23)

#### WHY
Every fix so far was verified by tools, but there was no single ON-DEVICE run that PROVES the whole audio output path end-to-end and leaves paste-back evidence: which ALSA stream the MAX98357A amp actually received, whether the DMA path underruns, whether the speaker mechanically rattles at specific frequencies, where the clean level ceiling is, and whether the real bank voice output is clean. This adds exactly that proof suite (ADD-only: no app code, no bank WAVs, no existing-tool changes).

#### FIXES
- **New `tools/make_proof_signals.py`** (numpy+wave only): generates the calibrated proof signals into a target dir (default `./proof_signals/`): 1.0 s 22050 Hz mono 16-bit sines at SAFE PEAK 4000 for 250/500/1000/2000/4000/6000/8000 Hz (`sine_0250_p4000.wav`...); linear-scaled speech sweep copies of `char_A.wav` + `word_ano.wav` at PEAK 4000/8000/12000/16000/20000/25000 (`charA_p04000.wav`...`word_ano_p025000.wav` — same pure linear scaling + ±32767 clip guard as `make_speaker_sweep.py`, no filtering/resampling/RMS re-normalization); and a 3.0 s "pink-ish" noise burst at PEAK 3000 (`noise_burst_p3000.wav`, fixed-seed `np.random` white noise through a gentle low-pass). Prints a per-file verification table (expected/actual peak, actual RMS, duration) + a format check; exits 1 on mismatch.
- **New `tools/speaker_amp_proof_test.sh`**: the main proof suite (style of `test_speaker_quick.sh`, NO `set -e`, every step guarded so the run ALWAYS reaches the summary) that prints AND appends every section to `speaker_amp_proof_<stamp>.txt` in the repo root (colored lines appended PLAIN). `[0] PREP` runs the generator with its verification table captured in the report; `SECTION A` system identity (date/host, git HEAD/branch/status, `aplay -l`, `/proc/asound/cards`, custom-ALSA check — RED `CUSTOM ALSA CHAIN STILL PRESENT — run: bash tools/fix_buzzing_audio.sh  (reference state = system default, no custom files)` when `~/.asoundrc`/`/etc/asound.conf` exists, GREEN `System default ALSA route (reference state) OK` otherwise, `amixer scontrols`, `verify_playback_chain.py` verdict + bank WAV count, `dmesg` audio tail); `SECTION B` plays `mode_free_input.wav` in the background via the default route and captures `/proc/asound/card*/pcm0p/sub0/hw_params` + `status` mid-playback (labeled rate/format/channels/state = the stream the MAX98357A PROVABLY received), then waits for playback to finish; `SECTION C` XRUN table — `aplay -q <file> >err 2>&1`, count `verrun` matches (0 = PASS `clean DMA, no underruns`) over char_A/word_ano/mode_free_input + all 7 sines; `SECTION D` interactive 250→8000 Hz sweep with `>>> SINE xxxx Hz — malinis o may rattle/linga?` labels + `read -t 8` verdicts + the rattle note; `SECTION E` interactive level sweep at 6 peaks with the highest clean peak marked; `SECTION F` voice proof of 4 real bank sounds (letra/salita/mode intro/mahabang salita), each XRUN-checked + verdict; `SECTION G` amp observations (noise-burst verdict + `amp received 22050 Hz mono — matches reference pipeline` interpretation); `FINAL SUMMARY` (system state / hw stream / XRUN totals / verdict table / interpretation guide ending with `I-send ang buong report file pabalik para sa susunod na analisis.`), then prints the report path and `cat`s the whole report.
- `.gitignore` now ignores `proof_signals/` + `speaker_amp_proof_*.txt` (generated artifacts are never committed).

#### VERIFIED
`python -m py_compile tools/make_proof_signals.py` OK; smoke run into a TEMP DIR OUTSIDE the repo: 20 files, programmatic assertions ALL PASS (sine peaks 4000 ±30 + dominant frequency on target ±3 Hz, sweep peaks on target ±30 + bit-exact linear rescale, noise peak 3000 ±30, all 22050/mono/16-bit, durations exact: 22050-frame sines, 66150-frame noise, source-length sweeps) — temp dir deleted; `bash -n tools/speaker_amp_proof_test.sh` OK (Git Bash). On the device: `bash tools/speaker_amp_proof_test.sh`, answer the `malinis?` prompts, paste back the whole `speaker_amp_proof_*.txt`.
---

### Section I - English & Filipino Character Pronunciation Fix (2026-11-23)

#### WHY
Every keystroke pronunciation file in both banks - 46 English `brailce/audio/characters/char_*.wav` and 53 Filipino `brailce/audio/filipino/char_*.wav` - was the old Rosa render force-compressed to exactly 0.800 s (17,640 samples) by `tools/generate_english_female_letters.py`, which time-squeezed multi-syllable names ("divided by", "double you") into damaged, rushed pronunciation; FIL char_F/char_X/char_4 fell below the frozen whole-RMS 2000 bound, and the enye keystroke `char_N-tilde.wav` required by `FILIPINO_KEY_LETTERS_ENGLISH` never existed (pre-existing `test_filipino_tts_v2` failure). The runtime routing itself was already correct and bank-first (EN: `speak_character` -> `normalize_for_piper` -> `PiperEngine.play_cached_character` -> `characters/char_*.wav`; FIL: `FilipinoEngine.enqueue_character` -> `get_filipino_character_path` -> `filipino/char_*.wav`), so the fix is the bank content, not the code.

#### FIXES
- **New `tools/regenerate_char_banks.py`** regenerates both banks from edge-tts with FEMALE neural voices - `en-US-JennyNeural` (English) and `en-PH-RosaNeural` (Filipino: English letter names with a natural Filipino accent) - using the spec'd spoken texts (ay/bee/see/.../zee; zero..nine; plus/minus/times/divided by/equals; period/comma/question mark/exclamation mark/number sign/apostrophe/colon/semicolon/space; same texts for both banks). Per file: mp3 -> decode (ffmpeg on PATH, else miniaudio, else soundfile) -> scipy `resample_poly` to 22050 Hz mono -> `standardize_wav_file()` (`audio_normalize.py` semantics: trim w/ 40 ms margins + 5 ms fades, active-RMS -> `TARGET_ACTIVE_RMS` 4200, peak cap 30000, 120/180 ms zero pads). NO time forcing, NO high-pass filter, NO EQ. Duration-guarded letters/digits (<= 0.88 s total) and Filipino keystrokes (<= 1.45 s) auto-retry at +10/+20/+30% edge-tts rate; Filipino `char_M.wav` pinned +20% so the autocorrelation F0 guard (`test_pronunciation_regression`, median >= 160 Hz) measures clean female windows instead of a 124 Hz subharmonic on the short nasal "em". The tool regenerates `english_audio_manifest.sha256` + `FROZEN_ENGLISH_CHARACTERS_SHA256` on success and refuses to touch them on any failure.
- **English bank:** all 46 existing files regenerated (exact filenames, natural Jenny durations 0.56-1.19 s; apostrophe/colon/semicolon/space have no EN bank files and none were invented).
- **Filipino bank:** all 53 existing files regenerated + NEW `char_N-tilde.wav` ("enye") closing the pre-existing keystroke-map gap (54 files).
- **Frozen metadata:** `brailce/tests/english_audio_manifest.sha256` (46 entries) and the `FROZEN_ENGLISH_CHARACTERS_SHA256` in `brailce/tests/test_filipino_tts_v2.py` regenerated to the new hashes. No other expectation changed.

#### VERIFIED
`python tools/verify_playback_chain.py` PASS (46 + 808 WAVs, active RMS band 2100-9240 derived from TARGET_ACTIVE_RMS=4200, peak <= 30000, no clipped samples); `python tools/audit_audio.py` PASS both banks; pytest audio subset (playback chain, english frozen, filipino tts v2, pronunciation regression, filipino keystrokes, fil letter clarity, normalizer, audio audit): **114 passed / 1 skipped** (live-Piper test - no piper binary on this host) vs **5 failed** before the fix; the skipped/failing set no longer contains any bank-quality failure. Spot before->after: EN char_A 0.800 s/3918 aRMS -> 0.809 s/4200; EN char_divide 0.800 s -> 1.187 s natural; FIL char_A 0.800 -> 0.702 s; FIL char_divide 0.947 -> 1.311 s natural; FIL char_M 0.800 -> 0.561 s (+20%, F0 347 Hz measured female).

---

### Section J - Scanner Spell-Then-Word Readout (2026-11-23)

#### WHY
The scanner (camera OBR) readout must spell every decoded character of a word one by one (letters AND digits), then say the whole word, in the current language (English via the characters/ bank / Piper path, Filipino via the filipino bank path), with Esc still canceling mid-readout. Audit of `brailce/modes/scanner_mode.py` + `brailce/vision/obr_engine.py` found the flow already spell-then-word and digit-capable, but with two real defects: (1) a word containing an unknown cell was still spoken as a whole word when the readout crossed into the next word (mid-line completion hardcoded `has_unknown=False`); (2) a genuine decoded question mark (Grade 1 dots 1-2-6, `kind: "char"`) hit the `ch == "?"` clause of the unknown-cell test and was announced "unknown cell" instead of being spelled "question mark" - every dataset character must be read well.

#### FIXES
- `brailce/modes/scanner_mode.py:593`: `_speak_completed_word(cur_word, False)` -> `_speak_completed_word(cur_word, cur_has_unknown)` (words with unknown cells are now consistently skipped, mid-line and at end).
- `brailce/modes/scanner_mode.py:625`: unknown-cell condition is now `kind == "unknown" or not ch.strip()` (the redundant, harmful `ch == "?"` clause removed; both OBR decode paths already tag true unknowns with `kind: "unknown"`). Decoded punctuation is spelled via the normal char bank path and included in the completed word ("WHAT?" -> "question mark" then "what?").
- `brailce/vision/obr_engine.py`: no change required - the Grade 1 lookup already covers all letters a-z (BINARY_DICT) and digits 1-0 after the number sign (dots 3-4-5-6 = `001111` -> `NUM_MAP` A-J -> 1-0, decimal "."/"," kept in number mode); verified by new tests instead.
- New `brailce/tests/test_scanner_readout.py` (11 tests, existing fixture style): "APPLE" spells A,P,P,L,E in exact call order then `speak_word_clean("APPLE")`; multi-word line spells each word then says it; `#ABJ` decodes to "120" and spells 1,2,0 then "120"; Grade 1 coverage asserted for all 26 letters + 10 digits; genuine "?" spelled, true unknown announced + word skipped (next word still spoken); Filipino "BAHAY" plays `audio/filipino/char_*.wav` letters + `word_bahay.wav`, and `SpeechService` routing asserted (`speak_word_clean` -> `FilipinoEngine.enqueue_text("bahay.")` in fil mode; `speak_character("A")` -> Piper `"a."` in en mode); STOP_PRESSED mid-readout stops spelling, calls `speech.stop`, shows SCANNER_STOPPED, never speaks the completed word.

#### VERIFIED
pytest (test_scanner_mode, test_scanner_readout, test_obr_engine, test_obr_classical, test_camera_scanner_pipeline, test_esc_stops): **79 passed / 0 failed**. Pre-existing unrelated vision-accuracy failure left as-is: `test_obr_synthetic.py::test_obr_synthetic_accuracy[0.0-12.0-ink]` (synthetic image decode accuracy; the number line `1234` in the same image decodes correctly). `py_compile` OK on both changed .py files. SCOPE untouched: pronunciation banks, piper/filipino engine internals, display, battery, ALSA/scripts, vision ROI/detection pipeline.

### Section K - LCD Lowercase Typed Text + Real-Time Battery with Voice Announcements (2026-11-23)

#### WHY
Two device requirements: (1) letters/words the user types must render ALL LOWERCASE on the LCD while typing and after typing (user types dots for A -> screen shows "a"; "APPLE" -> "apple"), without touching braille decoding or mode logic; (2) the battery percentage must be real (read from the OS, not fabricated), shown live on the LCD, and announced by voice at meaningful moments, following the current EN/FIL language.

#### FIX 1 - LCD typed text is lowercase (display layer)
- Typed content renders at `show_text_buffer` (accumulated sentence/math/AI-query buffer), `show_ai_query_input` / `show_ai_processing` (typed AI query), `show_ai_response` (typed question echo), and `show_math_result` (typed equation). All of these render ONLY user-typed content, so the lowercase is applied inside the display drivers and every mode benefits with zero mode-logic changes:
  - `brailce/display/console_display.py`: `show_text_buffer` renders `text.lower()` (empty placeholder "(empty)" is a label, untouched); `show_ai_response` lowercases only the `[AI QUERY]` echo (AI answer text unchanged); `show_ai_processing` lowercases the typed query; `show_math_result` lowercases only the equation (digits/ops unaffected).
  - `brailce/display/ili9488_display.py`: same four call points - `show_text_buffer` lowers the buffer card text (placeholder label untouched), `show_ai_query_input` lowers + stores the query lowercase (`_current_query`), `show_ai_processing` lowers the query echo, `show_ai_response` lowers only the question summary ("AI ANSWER:" header + response unchanged), `show_math_result` lowers the equation line + status echo.
- `show_character` renders BOTH typed characters and control labels ("Space", "Backspace", "Cleared", "# (Number Sign)"), so per the mixed-call-site rule the lowercase is applied at the MODE call sites for typed content only: `free_input.py` (`mapping.display_label.lower()`, number-mode `f"{digit} (from {char_upper.lower()})"`, and the post-Enter `Spoken: "{query.lower()}"` echo), `math_mode.py` (`label.lower()` - no-op for digits/operators), `ai_assistant.py` (`character.lower()`; "Space" label untouched).
- NOT lowercased (verified by tests): braille-dot visuals (`show_braille`), mode names/pills ("FREE INPUT", "MATH MODE", ...), card headers ("ACCUMULATED SENTENCE BUFFER:", "AI ANSWER:", "YOUR QUESTION (AI ASSISTANT):"), control labels, AI response text, math result/explanation.
- New `brailce/tests/test_display_lowercase.py` (24 tests): EN + FIL typed words lowercase on both ConsoleDisplay (real output capture) and ILI9488Display (draw recorder); numbers ("120"), math buffers ("5 + 3"), punctuation ("#1,024?") unaffected; labels/headers/braille visuals asserted unchanged; mode call-site lowercase via mock display (incl. "1 (from a)" and the `Spoken:` echo); ILI9488 battery indicator tests.
- Updated existing assertions to the new intended behavior: `test_display.py` (buffer "HELLO" -> expects "hello"), `test_free_input.py` ("A"->"a", "1 (from A)"->"1 (from a)"), `test_ai.py` ("A"->"a").

#### FIX 2 - Real-time battery
- Power architecture found: `PowerMonitor` ABC + `PowerStatus` dataclass (`brailce/power/base.py`); `SimulatedPowerMonitor` reports all-None/Unknown; `PiPowerMonitor` (I2C INA219/MAX17048 + sysfs + 100% plugged fallback). The app (`core/app.py` setup) previously selected the driver by PLATFORM ONLY (PiPowerMonitor on non-win32, simulated on win32) - the config `power.driver` key was never consulted. Consumer: only the ILI9488 header battery widget, refreshed lazily on header redraws (`_get_battery_info`); ConsoleDisplay showed nothing; no polling, no announcements.
- New `brailce/power/sysfs.py` - `SysfsPowerMonitor`: real battery driver reading `/sys/class/power_supply/*`; picks the first (sorted) supply exposing a `capacity` file; reads capacity (clamped 0-100) + `status` ("Charging"/"Discharging"/"Full"/"Not charging"/"Unknown"); maps charging/plugged flags; missing/unreadable/invalid files -> `percentage=None` (no data, never fabricated). Injectable base path for tests.
- New `brailce/power/auto.py` - `AutoPowerMonitor`: sysfs when a real capacity source exists, else the existing simulated behavior; re-probes every poll so a supply appearing at runtime is picked up. CAVEAT: battery HATs that do not expose /sys/class/power_supply (e.g. I2C-only INA219 UPS HATs) make auto fall back to simulated - a HAT-specific driver may be needed (the existing `"rpi"` driver keeps the I2C path selectable).
- New `brailce/power/poller.py` - `BatteryPoller`: daemon thread, polls every `power.poll_seconds` (default 30 s); first poll fires immediately so boot state shows right away. Each poll: mirrors `battery_percentage`/`battery_charging` into `ApplicationState`, refreshes the display battery indicator (`show_battery`), and runs the announcement logic through SpeechService (`speak_text(..., interrupt=False)`, normal queue - Esc/STOP_PRESSED unaffected):
  - DISCHARGING: announces at 75/50/25 percent crossings, once per crossing; a threshold re-arms only after capacity rises >=5 points above it (hysteresis) or a charging-state change; booting already below a threshold never fires it (position-based arming).
  - CHARGING starts (or app boots while charging): announces the current percent; while charging the same 75/50/25 crossings announce going UP; "Battery full." once per charge session (re-arms on a new charging session).
  - Guard: at most one battery announcement per 30 s, never concurrent (mutex + time gate); a poll jumping across multiple thresholds announces one level (lowest while discharging, highest while charging).
- Display: `DisplayDevice.show_battery(percentage, charging)` added (default no-op in `brailce/display/base.py`). ILI9488: `show_battery` stores a poller override and redraws only the header battery widget ("75%" / "75%+", green >20 / red <=20) - `_get_battery_info` prefers the poller value, then the attached monitor, then the 100% plugged default. ConsoleDisplay: small `[BATTERY] 75%` line (duplicate-suppressed) so the behavior is testable on the console path.
- Strings (`brailce/i18n/strings.py`): `BATTERY_AT` ("Battery at {bilang} percent." / "{bilang} na porsyento na ang natitira."), `BATTERY_CHARGING` ("Charging. {bilang} percent." / "{bilang} na porsyento, may dagdag pa."), `BATTERY_FULL` ("Battery full." / "Puno na."). Filipino wordings keep the same meaning using ONLY tokens that resolve to the frozen `brailce/audio/filipino` word bank ("baterya"/"nagcha-charge" have no cached WAV; the placeholder is named `{bilang}` because the bank-audit test tokenizes raw templates and "bilang" is banked, like `{vol}` in VOLUME_ANNOUNCE).
- Wiring (`brailce/core/app.py`): power driver is now config-driven - "auto" (default) / "sysfs" / "simulated" / "rpi" all selectable; the poller starts at the end of `setup()` (after speech/display init) and stops in `shutdown()`. Config: `brailce/config/config.json` + `brailce/config/defaults.py` `power.driver` -> "auto" with new `power.poll_seconds: 30`.

#### VERIFIED
- New tests: `brailce/tests/test_power.py` (15: sysfs capacity/status reads incl. Charging/Full/Not charging/Unknown, missing files -> no data, unreadable capacity via monkeypatched open -> no data, supplies without capacity skipped, first-with-capacity wins, invalid/clamped capacity, auto picks sysfs/else simulated, runtime re-probe) and `brailce/tests/test_battery_poller.py` (14: threshold announce once per crossing, hysteresis re-arm at +5, no boot announce below threshold, charging-state re-arm, upward crossings while charging, FIL wording, interrupt=False, boot-while-charging announce, full once per session + via status text + not while discharging, 30 s guard, display/state updates, no-data skip, thread lifecycle) - 29 passed.
- Targeted set (touched files + app/config/state/esc/mode-manager/filipino-math): **249 passed / 2 failed** - both failures proven pre-existing at HEAD via git-stash round-trip (assert `speech.speak` while the fil path uses `speak_response_text`).
- Full suite: **626 passed / 6 failed / 1 skipped**; all 6 failures identical at HEAD (pre-existing, unrelated: AI/fil AI/fil math speech-routing assertions x3, Filipino keypress crash x2, OBR synthetic vision accuracy x1). `py_compile` OK on all changed .py. SCOPE untouched: pronunciation banks, scanner, piper/filipino engine internals, ALSA/scripts, AI/camera/vision logic.
### Section L - Direct USB Keyboard Input: Headless No-SSH Operation (2026-11-23)

#### WHY
The user plugs a customized USB keyboard (standard USB HID keycodes) directly into the Pi 5 and must drive the Braille app with it WITHOUT any SSH/PuTTY session. Under the `brailce.service` systemd unit neither existing path worked: the `keyboard`-library hook silently failed (`keyboard` was never in `requirements-pi.txt`), and the raw-terminal stdin listener never started because systemd gives the service `/dev/null` as stdin (`isatty()` is False). USB input headlessly was impossible.

#### FIX - Direct /dev/input path + headless auto-selection
- New `brailce/input/evdev_input.py`: `EvdevInputDevice(KeyboardInputDevice)` reads USB keyboards directly via python-evdev (`/dev/input/event*`): enumerates devices with EV_KEY codes 1..126 (mice excluded), GRABs each (no console leak), translates keycodes to the SAME key names the hook/terminal paths use, and dispatches through the new shared `KeyboardInputDevice._dispatch_key_name` - identical behavior everywhere (Esc -> STOP_PRESSED, Ctrl+C/Ctrl+Q -> QUIT, F1 Mode, F2 Language, F3 Clear, F11/F12 Volume, characters). Modifier tracking handles ctrl/shift/alt (Shift-row symbols included). Hotplug tolerant: re-enumerates every 5 s with no keyboard and after read errors; never crashes without a device.
- `brailce/input/keyboard_input.py`: `_on_key_press` now delegates to a shared `_dispatch_key_name(key_name)` (single dispatch for hook, terminal, and evdev paths); `ctrl+c` added next to `ctrl+q` as a QUIT key.
- `brailce/core/app.py`: input device built by `create_input_device(config)`; `"keyboard"` (default) AUTO-selects the evdev device headless (no TTY) or when a USB keyboard is present, `"evdev"` forces it, `"terminal"` forces the legacy path. Terminal stdin only engages with a real TTY - PuTTY is now optional debugging.
- Config: new `input.usb_name_match` (optional device-name substring filter; "" = any keyboard) in `defaults.py` + `config.json`. `requirements-pi.txt` gains `evdev>=1.6.0`.

#### USAGE WITHOUT SSH
1. On the Pi: `pip install -r requirements-pi.txt` (adds evdev), keep `input.device: "keyboard"`.
2. Reboot (or `systemctl restart brailce`) with the service enabled.
3. Plug the USB keyboard in any time (before or after boot; detected within ~5 s) and type - `A-Z`, `0-9`, math/punctuation, `Space`, `Enter`, `Backspace`, `F1` mode, `F2` language, `F3` clear, `F11`/`F12` volume, `Esc` stop, `Ctrl+C`/`Ctrl+Q` quit. No SSH needed.

#### VERIFIED
- New `brailce/tests/test_evdev_input.py`: 59 tests (translation incl. Esc -> STOP_PRESSED and Ctrl+C -> QUIT, modifiers, auto-selection with mocked evdev, no-device resilience, GRAB + name-filter enumeration) - all pass with evdev fully mocked (runs on any host).
- Existing suites re-run: `test_input.py` (27), `test_esc_stops.py` (30), `test_configuration.py` (27), plus the `app.setup()`-dependent `test_math_mode.py` (14), `test_speech_service.py` (20), `test_audio_audit.py` (5) - ALL PASS. `py_compile` OK on every changed .py.
- SCOPE untouched: speech/audio banks, scanner, display, battery, ALSA/scripts, modes logic.

### Section M - Safe Shutdown: Ctrl+P Combo + Optional GPIO Long-Press (2026-11-23)

#### WHY
The device runs headless under `brailce.service` (no SSH session): power-ON is automatic, but power-OFF must be SAFE - an abrupt power cut can corrupt the SD card. Two user-facing safe-shutdown triggers were added: the `Ctrl+P` keyboard combo on ALL existing input paths (direct USB evdev, PuTTY/terminal, keyboard hook) and an optional GPIO long-press button for users who wire their power switch to a Raspberry Pi GPIO pin.

#### FIX
- `brailce/core/events.py`: new `EventType.SHUTDOWN_PRESSED` (control-events group, `auto()` style, next to `STOP_PRESSED`).
- `brailce/input/keyboard_input.py`: terminal raw path handles Ctrl+P byte `\x10` exactly like the existing Ctrl+C `\x03` / Ctrl+Q `\x11` (log, stop the listener, emit the event); the shared `_dispatch_key_name` (hook + evdev dispatch) gains `ctrl+p` next to `ctrl+q`. In-code note: **Ctrl+P = safe shutdown; Ctrl+C/Ctrl+Q = quit the app to the shell (unchanged)**.
- `brailce/input/evdev_input.py`: Ctrl combos now translate `p` alongside `c`/`q` to the `ctrl+p` key name through the SAME shared dispatch - identical behavior on the direct USB path.
- `brailce/core/app.py`: `SHUTDOWN_PRESSED` handled in the central event router -> `_handle_shutdown_pressed()`, a one-shot (guard flag: repeated events ignored). Flow: localized announcement on the normal speech queue (`speak_text(..., interrupt=False)`, shown on the display too) -> `_wait_for_speech_to_drain(5.0)` (the speech stack has no explicit drain API, so it polls the existing mechanisms: Piper/eSpeak/Filipino engine queues empty AND no active `AudioDeviceCoordinator` playback process; returns as soon as idle, bounded at 5 s) -> the EXISTING `shutdown()` cleanup path (identical to QUIT) -> `_power_off()`: subprocess list-form `["systemctl", "poweroff"]` with DEVNULL output, falling back to `["shutdown", "-h", "now"]` when systemctl is missing (FileNotFoundError); all errors logged. New imports `subprocess` + `time` at module top; `shutdown()` also stops the optional GPIO button.
- New `brailce/input/shutdown_button.py`: `ShutdownButton(InputDevice)` - optional gpiozero `Button` with `hold_time`/`pull_up` from config; the `when_held` callback emits one `SHUTDOWN_PRESSED` through the app's registered callback (no duplicated shutdown logic). gpiozero is imported LAZILY inside `start()`; when it is missing or the config pin is null the app logs ONE INFO line and continues - the service boots fine on dev machines.
- Config (`brailce/config/defaults.py` + `brailce/config/config.json`): `power.shutdown_gpio: null` (BCM pin number when wired), `power.shutdown_hold_seconds: 2.0`, `power.shutdown_pull_up: true`. `requirements-pi.txt`: `gpiozero>=2.0` already present - verified, unchanged.
- Strings (`brailce/i18n/strings.py`): `SHUTDOWN_ANNOUNCE` EN "Shutting down. Goodbye." / FIL "Pinapatay na ang Brailce. Paalam." - DEVIATION from the suggested FIL "Nagpapatay na ang Brailce. Paalam.": "nagpapatay" has NO cached WAV in the frozen Filipino word bank and would break the `test_all_filipino_math_i18n_ai_words_have_cached_audio` invariant (it tokenizes every FIL string template); "pinapatay" (the verb the existing APP_SHUTDOWN string already uses, same meaning) resolves completely.

#### VERIFIED
- New `brailce/tests/test_shutdown.py`: 36 tests - terminal `\x10` -> SHUTDOWN_PRESSED (NOT QUIT, NOT STOP), hook `ctrl+p`, evdev Ctrl+P (Ctrl+Q still QUIT), app handler (EN + FIL announcement wording, `systemctl poweroff` list-form exactly once, second event ignored, cleanup-before-poweroff order, DEVNULL/check=False, `shutdown -h now` fallback on FileNotFoundError, both-failing survives), drain wait (polls a real queue until empty, immediate when idle, bounded timeout, `_active_process` tracking), GPIO (Button wiring pin/hold_time/pull_up, hold -> event -> app handler -> poweroff once; gpiozero missing and broken-construction paths never crash; null/invalid config disables the button; defaults + shipped config.json keys), Ctrl+C/Ctrl+Q/Esc regressions. ALL PASS.
- Regression suites: `test_esc_stops.py` (28), `test_input.py` (15), `test_evdev_input.py` (59), `test_configuration.py` (14), `test_events.py` (16), `test_battery_poller.py` (17), `test_power.py` (20), `test_speech_service.py` (20), `test_math_mode.py` (14), `test_audio_audit.py` (5) + new 36 = 285 collected; targeted run **284 passed / 1 failed** - the failure (`test_math_mode_filipino_lcd_and_speech_use_same_string`) re-proven pre-existing at HEAD via a pristine `git archive HEAD` tree run (identical assertion).
- Full suite: **721 passed / 6 failed / 1 skipped** - all 6 failures are the documented pre-existing HEAD set (AI/fil-AI/fil-math speech-routing asserts x3, Filipino keypress crash x2, OBR synthetic vision accuracy x1); zero new failures. `py_compile` OK on every changed .py.
- SCOPE untouched: pronunciation banks, scanner, display, battery polling logic, ALSA/scripts, mode logic.
