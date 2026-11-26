"""
Brailce Direct USB Keyboard Input (evdev)
==========================================

Reads a USB keyboard plugged directly into the Raspberry Pi through
/dev/input/event* (python-evdev), so the app can be driven headlessly
under the brailce.service systemd unit WITHOUT any SSH/PuTTY session.

Design:
    - Enumerates /dev/input/event* devices exposing EV_KEY codes in the
      standard keyboard window (KEY_ESC..KEY_RIGHTMETA, codes 1..126);
      mice (BTN_* codes >= 256) are excluded.
    - GRABs each keyboard (EVIOCGRAB) so keystrokes do not leak into the
      Linux console.
    - Translates evdev keycodes to the SAME normalized key names the
      keyboard-library hook and terminal stdin paths produce ("a",
      "f1", "space", "esc", "ctrl+q", ...) and dispatches them through
      KeyboardInputDevice._dispatch_key_name - there is no second event
      vocabulary, so ALL existing behavior (Esc -> STOP_PRESSED,
      Ctrl+C/Ctrl+Q -> QUIT, Ctrl+P -> SHUTDOWN_PRESSED (safe
      shutdown), F1/F2/F3, volume keys, character events) works
      identically.
    - Hotplug tolerant: when no keyboard is found the loop re-enumerates
      every RESCAN_SECONDS (5 s), so the keyboard may be plugged in
      before or after boot without restarting the service. Read errors
      (unplug) trigger re-enumeration as well. Never crashes when no
      device is present - the service still starts.
    - The terminal/PuTTY listener still engages when a real TTY is
      attached, so SSH/PuTTY remains usable for debugging.

Configuration (config.json "input" section):
    - "device":
        * "keyboard" (default) AUTO-selects: this direct USB device when
          the evdev module is importable (and, on an interactive TTY,
          when a keyboard device is present); headless (no TTY, e.g. the
          systemd service) always prefers the direct device path so the
          app never blocks on stdin; otherwise the legacy device.
        * "evdev" forces the direct /dev/input USB path.
        * "terminal" forces the legacy keyboard hook + terminal stdin.
    - "usb_name_match": optional substring filter on the device name
      (e.g. "Logitech K120"); empty/unset means any keyboard.
"""

from __future__ import annotations

import logging
import select
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

from brailce.input.base import InputDevice
from brailce.input.keyboard_input import KeyboardInputDevice

logger = logging.getLogger(__name__)

# Standard keyboard keycode window (KEY_ESC..KEY_RIGHTMETA).
# BTN_MOUSE/BTN_LEFT etc. live at code >= 256, so mice are excluded.
KEYBOARD_CODE_MIN = 1
KEYBOARD_CODE_MAX = 126


def try_import_evdev():
    """Return the evdev module when importable, else None (never raises)."""
    try:
        import evdev

        return evdev
    except Exception as exc:  # pragma: no cover - depends on host
        logger.debug("evdev module not available: %s", exc)
        return None


def _matches_name_filter(device_name: str, name_match: Optional[str]) -> bool:
    """Case-insensitive substring filter on the device name."""
    if not name_match:
        return True
    return name_match.strip().lower() in (device_name or "").lower()


def open_usb_keyboards(evdev_module: Any, name_match: Optional[str] = None) -> List[Any]:
    """
    Enumerate /dev/input/event* keyboards, GRAB them, and return them.

    A device qualifies when it exposes EV_KEY codes in the standard
    keyboard window (mice excluded) and its name matches the optional
    filter. Devices that cannot be opened are skipped silently.
    """
    devices: List[Any] = []
    if evdev_module is None:
        return devices
    try:
        paths = list(evdev_module.list_devices())
    except Exception as exc:
        logger.debug("evdev list_devices failed: %s", exc)
        return devices
    for path in paths:
        dev = None
        try:
            dev = evdev_module.InputDevice(path)
        except Exception as exc:
            logger.debug("evdev cannot open %s: %s", path, exc)
            continue
        try:
            capabilities = dev.capabilities()
            key_codes = capabilities.get(evdev_module.ecodes.EV_KEY) or ()
            is_keyboard = any(
                KEYBOARD_CODE_MIN <= code <= KEYBOARD_CODE_MAX for code in key_codes
            )
            if not is_keyboard or not _matches_name_filter(dev.name, name_match):
                dev.close()
                continue
            try:
                dev.grab()  # exclusive: keystrokes never leak to the console
            except Exception as exc:
                logger.debug("evdev grab failed on %s (%s): %s", path, dev.name, exc)
            devices.append(dev)
        except Exception as exc:
            logger.debug("evdev device probe failed on %s: %s", path, exc)
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
    return devices


def usb_keyboard_present(evdev_module: Any, name_match: Optional[str] = None) -> bool:
    """Probe whether at least one matching USB keyboard exists right now."""
    devices = open_usb_keyboards(evdev_module, name_match)
    for dev in devices:
        try:
            dev.close()
        except Exception:
            pass
    return bool(devices)


def _stdin_is_tty() -> bool:
    """True only when a real interactive terminal is attached."""
    try:
        stdin = sys.stdin
        return bool(stdin is not None and hasattr(stdin, "isatty") and stdin.isatty())
    except Exception:
        return False


class EvdevInputDevice(KeyboardInputDevice):
    """
    Direct USB keyboard input device (python-evdev /dev/input reader).

    Subclasses KeyboardInputDevice so every key press is dispatched
    through the SAME _dispatch_key_name pipeline the hook/terminal
    paths use. Adds: device enumeration + GRAB, keycode translation,
    modifier tracking, and a hotplug-tolerant read loop.
    """

    SOURCE_NAME = "evdev"
    RESCAN_SECONDS = 5.0  # hotplug re-enumeration when no keyboard found
    COMBO_WINDOW_S = 0.30  # Space + Enter pressed within this window -> Change Mode
    SELECT_TIMEOUT = 1.0  # keep the loop responsive to stop()

    # evdev KEY_* suffix -> normalized app key name (shared vocabulary)
    KEY_NAME_BY_SUFFIX: Dict[str, str] = {
        "A": "a", "B": "b", "C": "c", "D": "d", "E": "e", "F": "f",
        "G": "g", "H": "h", "I": "i", "J": "j", "K": "k", "L": "l",
        "M": "m", "N": "n", "O": "o", "P": "p", "Q": "q", "R": "r",
        "S": "s", "T": "t", "U": "u", "V": "v", "W": "w", "X": "x",
        "Y": "y", "Z": "z",
        "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
        "6": "6", "7": "7", "8": "8", "9": "9", "0": "0",
        "SPACE": "space",
        "ENTER": "enter", "KPENTER": "enter",
        "BACKSPACE": "backspace",
        "ESC": "esc",
        "TAB": "tab",
        "F1": "f1", "F2": "f2", "F3": "f3", "F11": "f11", "F12": "f12",
        "GRAVE": "`",
        "DELETE": "delete",
        "POWER": "power",
        "MINUS": "-", "EQUAL": "=",
        "COMMA": ",", "DOT": ".", "SLASH": "/",
        "SEMICOLON": ";", "APOSTROPHE": "'",
        "KPASTERISK": "*", "KPPLUS": "+", "KPMINUS": "-",
        "KPSLASH": "/", "KPDOT": ".",
        "KP1": "1", "KP2": "2", "KP3": "3", "KP4": "4", "KP5": "5",
        "KP6": "6", "KP7": "7", "KP8": "8", "KP9": "9", "KP0": "0",
    }

    # evdev KEY_* suffix -> logical modifier tracked for combos
    MODIFIER_BY_SUFFIX: Dict[str, str] = {
        "LEFTCTRL": "ctrl", "RIGHTCTRL": "ctrl",
        "LEFTSHIFT": "shift", "RIGHTSHIFT": "shift",
        "LEFTALT": "alt", "RIGHTALT": "alt",
    }

    # Shift + key -> shifted symbol (unsupported ones are ignored by dispatch)
    SHIFT_SYMBOLS: Dict[str, str] = {
        "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
        "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
        "`": "~", "-": "_", "=": "+", ";": ":", "'": '"',
        ",": "<", ".": ">", "/": "?",
    }

    def __init__(
        self,
        mode_getter: Optional[callable] = None,
        name_match: Optional[str] = None,
    ) -> None:
        super().__init__(mode_getter=mode_getter)
        self._name_match = name_match or None
        self._modifiers: Set[str] = set()
        self._held_keys: Set[str] = set()
        self._last_space_time: float = 0.0
        self._last_enter_time: float = 0.0
        self._devices: List[Any] = []
        self._thread: Optional[threading.Thread] = None
        self._tty_thread: Optional[threading.Thread] = None
        self._key_table: Optional[Dict[int, str]] = None
        self._modifier_table: Optional[Dict[int, str]] = None

    @property
    def device_name(self) -> str:
        return "USB Keyboard (direct /dev/input)"

    # ---- Lifecycle ----

    def start(self) -> None:
        """Start the evdev read loop; attach the TTY listener only if interactive."""
        self._running = True
        self._thread = threading.Thread(
            target=self._evdev_loop, name="brailce-evdev", daemon=True
        )
        self._thread.start()
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            self._tty_thread = threading.Thread(
                target=self._terminal_listener, name="brailce-evdev-tty", daemon=True
            )
            self._tty_thread.start()
        logger.info(
            "EvdevInputDevice started - direct USB keyboard via /dev/input (hotplug tolerant)."
        )

    def stop(self) -> None:
        """Stop the read loop and release every grabbed device."""
        self._running = False
        self._close_devices()
        logger.info("EvdevInputDevice stopped.")

    # ---- Device management ----

    def _close_devices(self) -> None:
        for dev in self._devices:
            try:
                dev.close()
            except Exception:
                pass
        self._devices = []

    def _evdev_loop(self) -> None:
        """
        Watch keyboards; re-enumerate every RESCAN_SECONDS while none is
        present and after any read error (hotplug before/after boot).
        """
        while self._running:
            evdev_module = try_import_evdev()
            devices = (
                open_usb_keyboards(evdev_module, self._name_match)
                if evdev_module is not None
                else []
            )
            if not devices:
                time.sleep(self.RESCAN_SECONDS)
                continue
            self._devices = devices
            logger.info(
                "evdev: reading %d keyboard device(s): %s",
                len(devices),
                ", ".join(str(d.name) for d in devices),
            )
            try:
                self._read_devices(devices)
            except Exception as exc:
                logger.debug("evdev read error (%s); re-enumerating devices.", exc)
            finally:
                self._close_devices()
            time.sleep(0.2)  # brief pause before re-enumerating after an error

    def _read_devices(self, devices: List[Any]) -> None:
        """Blocking read of the grabbed devices until stopped or error."""
        while self._running:
            by_fd = {}
            for dev in devices:
                by_fd[dev.fd] = dev
            readable, _, _ = select.select(list(by_fd), [], [], self.SELECT_TIMEOUT)
            for fd in readable:
                for event in by_fd[fd].read():
                    self._process_evdev_event(event)

    # ---- Translation ----

    def _ensure_tables(self) -> None:
        """Build the keycode->key-name and keycode->modifier maps (cached)."""
        if self._key_table is not None:
            return
        key_table: Dict[int, str] = {}
        modifier_table: Dict[int, str] = {}
        evdev_module = try_import_evdev()
        if evdev_module is not None:
            ecodes = evdev_module.ecodes
            for suffix, name in self.KEY_NAME_BY_SUFFIX.items():
                code = getattr(ecodes, "KEY_" + suffix, None)
                if code is not None:
                    key_table[code] = name
            for suffix, modifier in self.MODIFIER_BY_SUFFIX.items():
                code = getattr(ecodes, "KEY_" + suffix, None)
                if code is not None:
                    modifier_table[code] = modifier
        self._key_table = key_table
        self._modifier_table = modifier_table

    def _process_evdev_event(self, event: Any) -> None:
        """Translate one evdev event and dispatch via the shared pipeline."""
        try:
            evdev_module = try_import_evdev()
            if evdev_module is None or event.type != evdev_module.ecodes.EV_KEY:
                return
            self._ensure_tables()
            assert self._key_table is not None and self._modifier_table is not None

            modifier = self._modifier_table.get(event.code)
            if modifier is not None:
                if event.value:  # press or auto-repeat keeps the modifier held
                    self._modifiers.add(modifier)
                else:
                    self._modifiers.discard(modifier)
                return

            name = self._key_table.get(event.code)
            if event.value == 0:
                if name is not None:
                    self._held_keys.discard(name)
                return
            if event.value != 1:  # ignore auto-repeat (2)
                return

            if "ctrl" in self._modifiers:
                if name in ("c", "q", "p"):  # c/q quit; p = safe shutdown
                    self._dispatch_key_name("ctrl+" + name)
                return  # other ctrl combos intentionally ignored
            if "alt" in self._modifiers:
                return  # alt combos intentionally ignored

            if name is None:
                return
            # Space + Enter combo on standalone USB keypad -> Change Mode.
            # Decided by press TIMING only (both pressed within 0.30s). The old
            # "still held" check misfired on keypads without 2-key rollover (Custom
            # braille38): a lost Space key-up left Space "held" forever, so the next
            # Enter (e.g. Scan in Scanner Mode) jumped to the next mode (Free Input).
            now = time.time()
            if name == "enter" and (now - self._last_space_time) < self.COMBO_WINDOW_S:
                self._held_keys.add(name)
                self._last_space_time = 0.0
                self._last_enter_time = 0.0
                self._dispatch_key_name("tab")
                return
            if name == "space" and (now - self._last_enter_time) < self.COMBO_WINDOW_S:
                self._held_keys.add(name)
                self._last_space_time = 0.0
                self._last_enter_time = 0.0
                self._dispatch_key_name("tab")
                return
            if name == "space":
                self._last_space_time = now
            elif name == "enter":
                self._last_enter_time = now
            self._held_keys.add(name)
            if "shift" in self._modifiers and len(name) == 1 and not name.isalpha():
                name = self.SHIFT_SYMBOLS.get(name, name)
            self._dispatch_key_name(name)
        except Exception as exc:
            logger.error("evdev event handling error: %s", exc, exc_info=True)


def create_input_device(config: Any, mode_getter: Optional[callable] = None) -> InputDevice:
    """
    Build the configured input device (input.device selector).

    Selection rules:
      - "evdev":    force the direct /dev/input USB keyboard device.
      - "terminal": force the legacy keyboard hook + terminal stdin device.
      - "keyboard" (default): AUTO -
          * evdev importable + USB keyboard present -> direct device;
          * evdev importable + no TTY (headless systemd service) ->
            direct device (hotplug tolerant, never blocks on stdin);
          * otherwise -> legacy device (unchanged behavior).
      - anything else: legacy device + warning (existing behavior).
    """
    try:
        device_type = str(config.input_device).strip().lower()
    except Exception:
        device_type = "keyboard"
    try:
        name_match = str(config.get("input", "usb_name_match", default="") or "")
    except Exception:
        name_match = ""

    if device_type == "evdev":
        logger.info("Input: direct USB keyboard (evdev) selected by config.")
        return EvdevInputDevice(mode_getter=mode_getter, name_match=name_match)
    if device_type == "terminal":
        return KeyboardInputDevice(mode_getter=mode_getter)
    if device_type == "keyboard":
        evdev_module = try_import_evdev()
        if evdev_module is not None:
            if usb_keyboard_present(evdev_module, name_match):
                logger.info("Input auto: USB keyboard detected - direct evdev input.")
                return EvdevInputDevice(mode_getter=mode_getter, name_match=name_match)
            if not _stdin_is_tty():
                # Headless (brailce.service): never block on stdin; the
                # evdev loop re-enumerates so the keyboard can be plugged
                # in later without restarting the service.
                logger.info(
                    "Input auto: no TTY - direct evdev input (hotplug tolerant)."
                )
                return EvdevInputDevice(mode_getter=mode_getter, name_match=name_match)
        return KeyboardInputDevice(mode_getter=mode_getter)

    logger.warning(
        "Input device '%s' not available - falling back to keyboard simulation.",
        device_type,
    )
    return KeyboardInputDevice(mode_getter=mode_getter)
