"""
Brailce – Main Application Controller
========================================

Orchestrates the entire application lifecycle:
    1. Load configuration
    2. Initialize subsystems (input, display, speech, power)
    3. Run system checks
    4. Start the event loop
    5. Route events to the active mode
    6. Handle shutdown gracefully

This is the top-level coordinator — it does not contain
business logic itself, only wiring and lifecycle management.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from typing import Optional

from brailce.config.loader import ConfigLoader
from brailce.core.events import AppMode, EventType, InputEvent
from brailce.core.mode_manager import ModeManager
from brailce.core.state import ApplicationState
from brailce.display.base import DisplayDevice
from brailce.display.console_display import ConsoleDisplay
from brailce.input.base import InputDevice
from brailce.input.evdev_input import create_input_device
from brailce.input.shutdown_button import ShutdownButton
from brailce.braille.mapping import BrailleMappingService
from brailce.braille.encoder import BrailleEncoder
from brailce.modes.ai_assistant import AIAssistantMode
from brailce.modes.base_mode import BaseMode
from brailce.modes.free_input import FreeInputMode
from brailce.modes.math_mode import MathMode
from brailce.modes.scanner_mode import ScannerMode
from brailce.power.base import PowerMonitor
from brailce.power.poller import BatteryPoller
from brailce.power.simulated import SimulatedPowerMonitor
from brailce.speech.base import SpeechEngine
from brailce.speech.espeak_engine import EspeakEngine
from brailce.speech.piper_engine import PiperEngine
from brailce.speech.service import SpeechService
from brailce.utils.system_checks import run_all_checks
from brailce.vision.camera import CameraDevice, SimulatedCamera, PiCameraDevice, UsbWebcamDevice
from brailce.vision.obr_engine import OBREngine

logger = logging.getLogger(__name__)


class BrailceApp:
    """
    Main application controller for the Brailce system.

    Manages the lifecycle of all subsystems and routes input events
    to the currently active application mode.
    """

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config
        self.state = ApplicationState()
        self.mode_manager = ModeManager(self.state)

        # Subsystems (initialized in setup())
        self.input_device: Optional[InputDevice] = None
        self.display: Optional[DisplayDevice] = None
        self.speech: Optional[SpeechEngine] = None
        self.sound = None
        self.power: Optional[PowerMonitor] = None
        self.battery_poller: Optional[BatteryPoller] = None
        self.shutdown_button: Optional[ShutdownButton] = None
        self.camera: Optional[CameraDevice] = None
        self.obr: Optional[OBREngine] = None
        self.mapping_service: Optional[BrailleMappingService] = None
        self.encoder: Optional[BrailleEncoder] = None
        
        # We can't import OllamaClient at top level to avoid circular issues or keep it optional,
        # so we just use Any or type string for it if needed, but since we didn't import it at top,
        # we'll just declare it here.
        self.ai_client = None

        # Mode instances
        self._modes: dict[AppMode, BaseMode] = {}
        self._active_mode: Optional[BaseMode] = None

        # Safe shutdown (Ctrl+P / GPIO long-press / Backspace+Power combo) fires exactly once per run
        self._shutdown_requested: bool = False
        self._last_backspace_time: float = 0.0
        self._last_enter_time: float = 0.0
        self._keypad_shutdown_deadline: float = 0.0
        self._last_power_button_time: float = 0.0

    # ---- Lifecycle ----

    def setup(self) -> None:
        """Initialize all subsystems based on configuration."""
        logger.info("Brailce application setup starting...")

        # ---- Input Device ----
        # "keyboard" (default) AUTO-selects the direct USB /dev/input path
        # (evdev) for headless systemd operation (no SSH needed); "evdev"
        # forces it and "terminal" forces the legacy hook/stdin path.
        self.input_device = create_input_device(
            self.config, mode_getter=lambda: self.state.mode
        )

        self.input_device.register_callback(self._on_input_event)

        # ---- Display ----
        display_driver = self.config.display_driver
        if display_driver == "auto":
            # On Raspberry Pi with SPI TFT or framebuffer, automatically use ILI9488 TFT; otherwise Console
            if os.path.exists("/dev/spidev0.0") or os.path.exists("/dev/fb1") or (os.path.exists("/dev/fb0") and os.path.exists("/proc/device-tree/model")):
                from brailce.display.ili9488_display import ILI9488Display
                width = self.config.get("display", "width", default=480)
                height = self.config.get("display", "height", default=320)
                self.display = ILI9488Display(width=width, height=height,
                                              spi_speed_hz=self.config.get("display", "spi_speed_hz", default=None))
            else:
                self.display = ConsoleDisplay()
        elif display_driver == "ili9488":
            from brailce.display.ili9488_display import ILI9488Display
            width = self.config.get("display", "width", default=480)
            height = self.config.get("display", "height", default=320)
            self.display = ILI9488Display(width=width, height=height,
                                          spi_speed_hz=self.config.get("display", "spi_speed_hz", default=None))
        else:
            self.display = ConsoleDisplay()

        self.display.initialize()
        try:
            self.display.show_splash()
        except Exception:
            logger.debug("Splash screen failed.", exc_info=True)

        # ---- Speech ----
        if self.config.speech_enabled:
            self.speech = SpeechService.from_config(self.config)
            logger.info("Speech: initialized %s.", self.speech.engine_name)
        else:
            self.speech = None
            logger.info("Speech is disabled in configuration.")

        # ---- Sound Effects ----
        from brailce.audio.sound_effects import SoundEffectsEngine
        sound_enabled = self.config.get("sound", "enabled", default=False)
        sound_vol = self.config.get("sound", "volume", default=0)
        self.sound = SoundEffectsEngine(enabled=sound_enabled, volume=sound_vol)
            
        # ---- AI ----
        if self.config.ai_enabled:
            ai_cfg = self.config.section("ai")
            from brailce.ai.ollama_client import OllamaClient
            self.ai_client = OllamaClient(
                host=ai_cfg.get("host", "http://127.0.0.1:11434"),
                model=ai_cfg.get("model", "liquidai/lfm2.5-1.2b-instruct"),
                system_prompt=ai_cfg.get("system_prompt", None),
                num_predict=ai_cfg.get("max_response_tokens", 50),
            )
        else:
            self.ai_client = None

        # ---- Power ----
        # Driver is config-driven: "auto" (default) uses the real sysfs
        # battery driver when a capacity source exists, else the simulated
        # behavior. "sysfs" / "simulated" / "rpi" (I2C UPS HAT) stay
        # explicitly selectable.
        power_driver = str(self.config.get("power", "driver", default="auto")).lower()
        if power_driver == "sysfs":
            from brailce.power.sysfs import SysfsPowerMonitor
            self.power = SysfsPowerMonitor()
        elif power_driver == "simulated":
            self.power = SimulatedPowerMonitor()
        elif power_driver == "rpi":
            from brailce.power.rpi_power import PiPowerMonitor
            self.power = PiPowerMonitor()
        else:  # "auto": sysfs when a real capacity source exists, else simulated
            from brailce.power.auto import AutoPowerMonitor
            self.power = AutoPowerMonitor()
        logger.info("Power: initialized %s.", self.power.driver_name)

        if hasattr(self.display, "set_power_monitor"):
            self.display.set_power_monitor(self.power)
        
        # ---- Vision ----
        # BRAILCE_VISION_DRIVER overrides config (the test suite sets "simulated" so it
        # never opens the PC's real webcam).
        vision_driver = str(os.getenv("BRAILCE_VISION_DRIVER")
                            or self.config.get("vision", "driver", default="usb")).lower()
        test_image = os.getenv("BRAILCE_TEST_IMAGE") or self.config.get("vision", "test_image_path", default="")
        usb_device = self.config.get("vision", "usb_device", default=0)
        autofocus = bool(self.config.get("vision", "autofocus", default=True))
        focus_value = int(self.config.get("vision", "focus_value", default=-1))

        if vision_driver == "simulated":
            self.camera = SimulatedCamera(image_path=test_image if test_image else None)
        elif vision_driver == "picamera":
            self.camera = PiCameraDevice()
        else:  # "usb" or "auto"
            cam_res = self.config.get("vision", "resolution", default=(1280, 720))
            still_res = self.config.get("vision", "still_resolution", default=(1920, 1080))
            usb_cam = UsbWebcamDevice(
                device=usb_device,
                resolution=tuple(cam_res) if isinstance(cam_res, (list, tuple)) else (1280, 720),
                still_resolution=tuple(still_res) if isinstance(still_res, (list, tuple)) else (1920, 1080),
                autofocus=autofocus,
                focus_value=focus_value,
            )
            if usb_cam.is_available():
                self.camera = usb_cam
                logger.info("Using connected USB webcam.")
            else:
                pi_cam = PiCameraDevice()
                if pi_cam.is_available():
                    self.camera = pi_cam
                    logger.info("Using physical Raspberry Pi Camera Module.")
                elif test_image and os.path.isfile(test_image):
                    self.camera = SimulatedCamera(image_path=test_image)
                    logger.info("Physical camera not active — using test_image SimulatedCamera.")
                else:
                    self.camera = usb_cam
                    logger.info("Physical camera not detected — will report camera not connected cleanly.")
            
        model_path = self.config.get("vision", "model_path", default="brailce/vision/yolov8_braille.pt")
        conf = float(self.config.get("vision", "confidence_threshold", default=0.22))
        iou = float(self.config.get("vision", "iou_threshold", default=0.45))
        use_yolo_secondary = bool(self.config.get("vision", "use_yolo_secondary", default=False))
        self.obr = OBREngine(
            model_path=model_path,
            conf=conf,
            iou=iou,
            use_yolo_secondary=use_yolo_secondary,
        )

        # ---- Braille Mapping ----
        self.mapping_service = BrailleMappingService()
        self.mapping_service.load_from_config(self.config)
        self.encoder = BrailleEncoder(self.mapping_service)

        # ---- Modes ----
        self._modes[AppMode.FREE_INPUT] = FreeInputMode(self)
        self._modes[AppMode.MATH] = MathMode(self)
        self._modes[AppMode.AI_ASSISTANT] = AIAssistantMode(self)
        self._modes[AppMode.SCANNER] = ScannerMode(self)
        self._active_mode = self._modes[AppMode.FREE_INPUT]
        self._active_mode.on_enter()

        # Register mode-change listener
        self.mode_manager.register_listener(self._on_mode_change)

        # ---- System Checks ----
        ai_host = self.config.get("ai", "host", default="http://127.0.0.1:11434")
        checks = run_all_checks(ollama_host=ai_host)
        for check in checks:
            if not check.available:
                self.display.show_status(f"⚠ {check.name}: {check.message}")

        # ---- Display Initial State ----
        self.display.show_mode(self.state.mode)
        self.display.show_text_buffer(self.state.text_buffer)

        # Initial speech announcement from i18n table via unified queue
        if self.speech:
            self.speech.speak_mode_intro(self.state.mode, interrupt=False)

        # ---- Battery poller (after speech/display init) ----
        # Daemon thread: refreshes the LCD battery indicator and makes
        # non-interrupting voice announcements (75/50/25 crossings, charging
        # start, full) following the current app language.
        poll_seconds = float(self.config.get("power", "poll_seconds", default=30))
        self.battery_poller = BatteryPoller(
            monitor=self.power,
            display=self.display,
            speech=self.speech,
            state=self.state,
            poll_seconds=poll_seconds,
            critical_percent=float(self.config.get("power", "critical_battery_percent", default=5)),
            on_critical=self._handle_critical_battery,
        )
        self.battery_poller.start()

        # ---- Optional GPIO shutdown button (long-press power switch) ----
        # Emits SHUTDOWN_PRESSED through the normal app handler (the
        # same event as the Ctrl+P combo); disabled with one INFO log
        # when power.shutdown_gpio is null or gpiozero is unavailable.
        self._setup_shutdown_button()

        logger.info("Brailce application setup complete.")
        logger.info(
            "Input: %s | Display: %s | Speech: %s",
            self.input_device.device_name,
            self.display.driver_name,
            self.speech.engine_name if self.speech else "Disabled",
        )

    def run(self) -> None:
        """Start the main event loop."""
        if self.input_device is None:
            raise RuntimeError("Call setup() before run().")

        logger.info("Brailce application starting...")
        self.display.show_status("Ready — press any character key.")

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)

        try:
            self.input_device.start()

            # Keep main thread alive while input runs in background
            while self.state.is_running:
                try:
                    import time
                    time.sleep(0.1)
                except KeyboardInterrupt:
                    break

        except Exception as exc:
            logger.exception("Fatal error in main loop: %s", exc)
            if self.display:
                self.display.show_error(f"Fatal error: {exc}")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shut down all subsystems."""
        logger.info("Brailce shutting down...")
        self.state.is_running = False

        if self.battery_poller is not None:
            try:
                self.battery_poller.stop()
            except Exception:
                logger.exception("Error stopping battery poller.")

        if self.shutdown_button is not None:
            try:
                self.shutdown_button.stop()
            except Exception:
                logger.exception("Error stopping GPIO shutdown button.")

        if self.input_device:
            try:
                self.input_device.stop()
            except Exception:
                logger.exception("Error stopping input device.")

        if self.speech:
            try:
                if hasattr(self.speech, 'shutdown'):
                    self.speech.shutdown()
                else:
                    self.speech.stop()
            except Exception:
                logger.exception("Error stopping speech engine.")

        if self.display:
            try:
                self.display.show_status("Shutting down...")
                self.display.shutdown()
            except Exception:
                logger.exception("Error shutting down display.")

        if self.camera and hasattr(self.camera, "close"):
            try:
                self.camera.close()
            except Exception:
                logger.exception("Error stopping camera device.")

        logger.info("Brailce shutdown complete.")

    # ---- Event Handling ----

    def _on_input_event(self, event: InputEvent) -> None:
        """
        Central event router.

        Dispatches input events to the appropriate handler
        based on event type and current mode.
        """
        try:
            logger.debug("Event received: %s", event)

            # ---- System-level events ----
            if event.event_type == EventType.QUIT:
                logger.info("Quit event received.")
                self.state.is_running = False
                return

            if event.event_type == EventType.SHUTDOWN_PRESSED:
                # Direct safe shutdown event (announce, clean up, power off)
                self._handle_shutdown_pressed()
                return

            if event.event_type == EventType.POWER_BUTTON_PRESSED:
                # Power button pressed: check if Backspace was pressed at the same time (within 1.5s)
                now = time.time()
                self._last_power_button_time = now
                if (now - self._last_backspace_time) < 1.5:
                    logger.info("Power Button + Backspace combo detected -> initiating safe shutdown.")
                    self._handle_shutdown_pressed()
                return

            if event.event_type == EventType.BACKSPACE_PRESSED:
                # Check if Power Button is pressed at the same time (held or pressed within 1.5s)
                now = time.time()
                self._last_backspace_time = now
                power_held = (
                    self.shutdown_button is not None
                    and getattr(self.shutdown_button, "is_pressed", False)
                )
                power_recent = (now - self._last_power_button_time) < 1.5
                if power_held or power_recent:
                    logger.info("Backspace + Power Button combo detected -> initiating safe shutdown.")
                    self._handle_shutdown_pressed()
                    return
                if (now - getattr(self, "_last_enter_time", 0.0)) < self.KEYPAD_SHUTDOWN_COMBO_S:
                    # Enter came first (it already reached the mode); Backspace completes the combo
                    self._handle_keypad_shutdown_combo()
                    return

            if event.event_type == EventType.ENTER_PRESSED:
                now = time.time()
                self._last_enter_time = now
                if (now - self._last_backspace_time) < self.KEYPAD_SHUTDOWN_COMBO_S:
                    # Backspace + Enter together: shutdown request, NOT a scan / submit
                    self._handle_keypad_shutdown_combo()
                    return

            if event.event_type == EventType.STOP_PRESSED:
                # Esc: stop speech and the current mode activity without quitting
                logger.info("Stop pressed event received.")
                if self.speech:
                    try:
                        self.speech.stop()
                    except Exception:
                        logger.exception("Error stopping speech on stop pressed.")
                if self._active_mode:
                    self._active_mode.handle_event(event)
                # A pending AI request survives mode switches; cancel it too
                ai_mode = self._modes.get(AppMode.AI_ASSISTANT)
                if ai_mode and self.state.ai_processing:
                    ai_mode.handle_event(event)
                return

            if event.event_type == EventType.MODE_PRESSED:
                self.mode_manager.toggle()
                return

            if event.event_type == EventType.LANGUAGE_TOGGLE:
                self.toggle_language()
                return

            if event.event_type == EventType.VOLUME_UP:
                new_vol = self.state.increase_volume()
                from brailce.i18n.strings import get_string
                msg = get_string("VOLUME_ANNOUNCE", self.state.language, vol=new_vol)
                if self.display:
                    self.display.show_status(msg)
                if self.speech:
                    self.speech.set_volume(new_vol)
                    self.speech.speak_text(msg, interrupt=True)
                return

            if event.event_type == EventType.VOLUME_DOWN:
                new_vol = self.state.decrease_volume()
                from brailce.i18n.strings import get_string
                msg = get_string("VOLUME_ANNOUNCE", self.state.language, vol=new_vol)
                if self.display:
                    self.display.show_status(msg)
                if self.speech:
                    self.speech.set_volume(new_vol)
                    self.speech.speak_text(msg, interrupt=True)
                return

            # ---- Mode-specific events ----
            if self._active_mode:
                self._active_mode.handle_event(event)
        except Exception as exc:
            logger.error("Error handling input event %s: %s", event, exc, exc_info=True)

    def toggle_language(self) -> str:
        """Toggle between English ('en') and Filipino ('fil') globally via unified speech queue."""
        from brailce.i18n.strings import get_string

        new_lang = self.state.toggle_language()
        if self.speech and hasattr(self.speech, "language"):
            self.speech.language = new_lang
        if self.display and hasattr(self.display, "set_language"):
            self.display.set_language(new_lang)

        status_key = "LANG_STATUS_FIL" if new_lang == "fil" else "LANG_STATUS_EN"
        status_text = get_string(status_key, new_lang)
        if self.display:
            self.display.show_status(status_text)

        if self.speech:
            self.speech.speak_language_switch(new_lang, interrupt=True)

        logger.info("Language toggled to: %s", new_lang)
        return new_lang

    def _on_mode_change(self, old_mode: AppMode, new_mode: AppMode) -> None:
        """Handle mode transitions using unified i18n strings and the single speech queue."""
        from brailce.i18n.strings import get_mode_intro

        # 1. Immediately stop any in-flight speech from the previous mode
        if self.speech:
            try:
                self.speech.stop()
            except Exception:
                pass

        # 2. Reset display state to prevent cross-mode pollution
        if self.display:
            if hasattr(self.display, "_current_query"):
                self.display._current_query = ""

        # Exit old mode, enter the new one and draw its screen as ONE LCD update.
        # Without the batch, on_exit/on_enter/show_mode each sent a full frame over
        # the slow SPI bus (~1 s each), so a mode switch visibly wiped 5-6 screens,
        # including the OLD mode's screen again.
        import contextlib
        batch = getattr(self.display, "batch", None) if self.display else None
        try:
            ctx = batch() if callable(batch) else contextlib.nullcontext()
        except Exception:
            ctx = contextlib.nullcontext()
        intro_text = get_mode_intro(new_mode, language=self.state.language)
        with ctx:
            old_mode_instance = self._modes.get(old_mode)
            if old_mode_instance:
                old_mode_instance.on_exit()

            new_mode_instance = self._modes.get(new_mode)
            if new_mode_instance:
                new_mode_instance.on_enter()
                self._active_mode = new_mode_instance

            if self.display:
                self.display.show_mode(new_mode)
                self.display.show_status(intro_text)

        if self.speech:
            self.speech.speak_mode_intro(new_mode, interrupt=False)

        logger.info("Mode changed to: %s (%s)", new_mode.display_name(), self.state.language)

    # ---- Safe Shutdown (Ctrl+P / GPIO long-press) ----

    def _setup_shutdown_button(self) -> None:
        """
        Wire the optional GPIO long-press power switch
        (power.shutdown_gpio, BCM pin number).

        Disabled by default (null); with no pin configured or no
        gpiozero the app logs one INFO and continues without the
        button, so the service boots fine on dev machines too.
        """
        shutdown_gpio = self.config.get("power", "shutdown_gpio", default=None)
        if shutdown_gpio is None:
            logger.info(
                "No power.shutdown_gpio configured - GPIO shutdown button disabled."
            )
            return
        try:
            pin = int(shutdown_gpio)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid power.shutdown_gpio value %r - GPIO shutdown button disabled.",
                shutdown_gpio,
            )
            return
        hold_seconds = float(
            self.config.get("power", "shutdown_hold_seconds", default=2.0)
        )
        pull_up = bool(self.config.get("power", "shutdown_pull_up", default=True))
        require_backspace = bool(
            self.config.get("power", "shutdown_requires_backspace", default=False)
        )
        trigger_mode = str(
            self.config.get("power", "shutdown_trigger", default="hold")
        ).lower()
        self.shutdown_button = ShutdownButton(
            pin=pin,
            hold_seconds=hold_seconds,
            pull_up=pull_up,
            require_backspace=require_backspace,
            trigger_mode=trigger_mode,
        )
        self.shutdown_button.register_callback(self._on_input_event)
        self.shutdown_button.start()

    # Backspace + Enter pressed together (within this window) = keypad-only shutdown
    # request; it must be repeated within KEYPAD_SHUTDOWN_CONFIRM_S to power off.
    KEYPAD_SHUTDOWN_COMBO_S = 0.35
    KEYPAD_SHUTDOWN_CONFIRM_S = 6.0

    def _handle_keypad_shutdown_combo(self) -> None:
        """Safe shutdown from the keypad alone (no power button or switch needed):
        first Backspace + Enter asks for confirmation, a second one within
        KEYPAD_SHUTDOWN_CONFIRM_S shuts down. Prevents accidental power-offs."""
        now = time.time()
        self._last_backspace_time = 0.0
        self._last_enter_time = 0.0
        deadline = getattr(self, "_keypad_shutdown_deadline", 0.0)
        if deadline and now <= deadline:
            self._keypad_shutdown_deadline = 0.0
            logger.info("Backspace + Enter confirmed -> initiating safe shutdown.")
            self._handle_shutdown_pressed()
            return
        self._keypad_shutdown_deadline = now + self.KEYPAD_SHUTDOWN_CONFIRM_S
        logger.info("Backspace + Enter pressed -> asking for confirmation.")
        from brailce.i18n.strings import get_string
        msg = get_string("SHUTDOWN_CONFIRM_PROMPT", self.state.language)
        try:
            if self.display:
                self.display.show_status(msg)
            if self.speech:
                self.speech.speak_text(msg, interrupt=True)
        except Exception:
            logger.debug("Shutdown confirmation prompt failed.", exc_info=True)

    def _handle_critical_battery(self) -> None:
        """Battery nearly empty and not charging: shut down cleanly before the UPS
        cuts power (an abrupt cut can leave files on the SD card empty)."""
        logger.warning("Battery critically low - starting safe shutdown.")
        from brailce.i18n.strings import get_string
        msg = get_string("BATTERY_CRITICAL_SHUTDOWN", self.state.language)
        try:
            if self.display:
                self.display.show_status(msg)
            if self.speech:
                self.speech.speak_text(msg, interrupt=True)
                time.sleep(4.0)
        except Exception:
            logger.debug("Critical battery announcement failed.", exc_info=True)
        self._handle_shutdown_pressed()

    def _handle_shutdown_pressed(self) -> None:
        """
        Safe shutdown flow, fired EXACTLY ONCE per run:
            1. Announce over speech (non-interrupting) + show on display.
            2. Wait (bounded, up to 5 s) for the speech queue to drain
               so the announcement is actually heard.
            3. Run the normal app shutdown/cleanup path (same as QUIT).
            4. systemctl poweroff (fallback: shutdown -h now).

        Repeated SHUTDOWN_PRESSED events (extra Ctrl+P presses or a
        second GPIO hold while shutting down) are ignored.
        """
        if self._shutdown_requested:
            logger.info(
                "Shutdown already in progress - ignoring repeated SHUTDOWN_PRESSED."
            )
            return
        self._shutdown_requested = True
        logger.info("Shutdown pressed - starting safe shutdown.")

        from brailce.i18n.strings import get_string
        message = get_string("SHUTDOWN_ANNOUNCE", self.state.language)
        if self.display:
            try:
                self.display.show_status(message)
            except Exception:
                logger.exception("Error showing shutdown status.")

        if self.speech:
            try:
                self.speech.speak_text(message, interrupt=False)
            except Exception:
                logger.exception("Error announcing shutdown.")
            self._wait_for_speech_to_drain(timeout=5.0)

        self.shutdown()
        self._power_off()

    def _wait_for_speech_to_drain(self, timeout: float = 5.0) -> None:
        """
        Bounded wait for the shutdown announcement to finish playing.

        The speech stack has no explicit drain API, so poll its existing
        mechanisms: every engine queue (Piper/eSpeak/Filipino) must be
        empty AND AudioDeviceCoordinator must have no active playback
        process. Returns as soon as idle; never waits past *timeout*.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if self._speech_is_idle():
                return
            time.sleep(0.1)
        logger.info(
            "Speech drain wait timed out after %.1f s - proceeding.", timeout
        )

    def _speech_is_idle(self) -> bool:
        """True when no speech queue holds pending items and nothing plays."""
        speech = self.speech
        if speech is None:
            return True
        engines = []
        for attr in ("_engine", "_character_engine", "_text_engine", "_filipino_engine"):
            engine = getattr(speech, attr, None)
            if engine is not None and not any(engine is seen for seen in engines):
                engines.append(engine)
        for engine in engines:
            engine_queue = getattr(engine, "_queue", None)
            if engine_queue is None:
                continue
            try:
                if not engine_queue.empty():
                    return False
            except Exception:
                pass
        try:
            from brailce.speech.audio_device import AudioDeviceCoordinator
            if AudioDeviceCoordinator._active_process is not None:
                return False
        except Exception:
            pass
        return True

    def _power_off(self) -> None:
        """
        Power the machine off via systemd; fall back to shutdown(8)
        when systemctl is missing. List-form subprocess, all output
        suppressed, errors logged - the app is already cleaned up.
        """
        commands = (
            ["systemctl", "poweroff"],
            ["shutdown", "-h", "now"],
        )
        for command in commands:
            try:
                subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                logger.info("Power off requested via %s.", " ".join(command))
                return
            except FileNotFoundError:
                logger.error("%s not available - trying fallback.", command[0])
            except Exception:
                logger.exception("Power off command failed: %s", command)
        logger.error("Power off failed: no shutdown command available.")

    def _signal_handler(self, signum: int, frame: object) -> None:
        """Handle interrupt signals gracefully."""
        logger.info("Signal %d received — shutting down.", signum)
        self.state.is_running = False


if __name__ == "__main__":
    from main import main
    sys.exit(main())

