"""
Brailce Tests - Direct USB Keyboard Input (evdev)
====================================================

Verifies the headless /dev/input input path:
  (a) evdev keycode -> app event translation (same events as the
      hook/terminal paths: letters, F1-F3, Esc -> STOP_PRESSED,
      Ctrl+C/Ctrl+Q -> QUIT, F11/F12 volume),
  (b) modifier tracking (ctrl/shift/alt),
  (c) input.device auto-selection logic with mocked evdev availability,
  (d) no-device-present does not crash (service still starts),
  (e) GRAB is called on opened keyboards and name filtering works.

All evdev interaction is mocked; no real /dev/input or evdev install
is required, so these tests run on any host.
"""

from __future__ import annotations

import sys
import time
import types
from unittest.mock import MagicMock

import pytest

import brailce.input.evdev_input as evdev_input
from brailce.config.defaults import DEFAULT_CONFIG
from brailce.config.loader import ConfigLoader
from brailce.core.events import EventType
from brailce.input.evdev_input import EvdevInputDevice, create_input_device
from brailce.input.keyboard_input import KeyboardInputDevice


# ---------------------------------------------------------------------------
# Fake evdev module (real Linux keycode numbers)
# ---------------------------------------------------------------------------

EV_KEY = 1
EV_REL = 2

_CODE_VALUES = {
    "ESC": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11,
    "MINUS": 12, "EQUAL": 13, "BACKSPACE": 14, "TAB": 15,
    "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22,
    "I": 23, "O": 24, "P": 25, "ENTER": 28, "LEFTCTRL": 29,
    "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35, "J": 36,
    "K": 37, "L": 38, "SEMICOLON": 39, "APOSTROPHE": 40, "GRAVE": 41,
    "LEFTSHIFT": 42, "Z": 44, "X": 45, "C": 46, "V": 47, "B": 48,
    "N": 49, "M": 50, "COMMA": 51, "DOT": 52, "SLASH": 53,
    "RIGHTSHIFT": 54, "KPASTERISK": 55, "LEFTALT": 56, "SPACE": 57,
    "F1": 59, "F2": 60, "F3": 61,
    "KPMINUS": 74, "KPPLUS": 78,
    "KP7": 71, "KP8": 72, "KP9": 73, "KP4": 75, "KP5": 76, "KP6": 77,
    "KP1": 79, "KP2": 80, "KP3": 81, "KP0": 82, "KPDOT": 83,
    "F11": 87, "F12": 88, "KPENTER": 96, "RIGHTCTRL": 97, "KPSLASH": 98,
    "RIGHTALT": 100, "DELETE": 111,
}
BTN_MOUSE_CODES = [272, 273]  # BTN_LEFT / BTN_RIGHT (>= 256 window -> a mouse)

FAKE_ECODES = types.SimpleNamespace(EV_KEY=EV_KEY, **{
    ("KEY_" + name if not name.startswith("BTN") else name): value
    for name, value in _CODE_VALUES.items()
})


class FakeInputDevice:
    """Minimal stand-in for evdev.InputDevice."""

    def __init__(self, name, path, key_codes, fail_grab=False, read_error=None):
        self.name = name
        self.path = path
        self.fd = object()
        self.grabbed = False
        self.grab_failed = fail_grab
        self.closed = False
        self.read_error = read_error
        self._capabilities = {EV_KEY: list(key_codes)} if key_codes is not None else {}

    def capabilities(self):
        return dict(self._capabilities)

    def grab(self):
        if self.grab_failed:
            raise OSError("EVIOCGRAB busy")
        self.grabbed = True

    def close(self):
        self.closed = True

    def read(self):
        if self.read_error is not None:
            raise self.read_error
        return []


class FakeEvdev:
    """Minimal stand-in for the evdev module."""

    def __init__(self, devices=None, list_error=False):
        self.devices = list(devices or [])
        self.list_error = list_error
        self.ecodes = FAKE_ECODES

    def list_devices(self):
        if self.list_error:
            raise OSError("permission denied")
        return [d.path for d in self.devices]

    def InputDevice(self, path):
        for d in self.devices:
            if d.path == path:
                return d
        raise OSError("no such device")


def _press(code, value=1):
    return types.SimpleNamespace(type=EV_KEY, code=code, value=value)


def _non_stdin(monkeypatch):
    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = False
    monkeypatch.setattr(sys, "stdin", fake_stdin)


@pytest.fixture
def dev() -> EvdevInputDevice:
    d = EvdevInputDevice()
    d._running = True
    return d


@pytest.fixture
def collector(dev):
    received = []
    dev.register_callback(lambda e: received.append(e))
    return received


@pytest.fixture
def fake_evdev(monkeypatch):
    fake = FakeEvdev()
    monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: fake)
    return fake


def _cfg(device="keyboard", usb_name_match=""):
    return ConfigLoader(
        {"input": {"device": device, "usb_name_match": usb_name_match}}
    )


# ---------------------------------------------------------------------------
# (a) evdev code -> app event translation
# ---------------------------------------------------------------------------

class TestEvdevTranslation:
    """USB keycodes must produce the SAME events as the existing paths."""

    def test_letter_press(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_A))
        assert [e.event_type for e in collector] == [EventType.CHARACTER_PRESSED]
        assert collector[0].character == "A"

    def test_digit_press(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_5))
        assert [e.event_type for e in collector] == [EventType.CHARACTER_PRESSED]
        assert collector[0].character == "5"

    def test_numpad_digit_press(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_KP1))
        assert [e.event_type for e in collector] == [EventType.CHARACTER_PRESSED]
        assert collector[0].character == "1"

    @pytest.mark.parametrize(
        "code,expected_char",
        [
            (FAKE_ECODES.KEY_DOT, "."),
            (FAKE_ECODES.KEY_COMMA, ","),
            (FAKE_ECODES.KEY_SLASH, "/"),
            (FAKE_ECODES.KEY_SEMICOLON, ";"),
            (FAKE_ECODES.KEY_APOSTROPHE, "'"),
            (FAKE_ECODES.KEY_MINUS, "-"),
            (FAKE_ECODES.KEY_EQUAL, "="),
            (FAKE_ECODES.KEY_KPASTERISK, "*"),
            (FAKE_ECODES.KEY_KPPLUS, "+"),
            (FAKE_ECODES.KEY_KPSLASH, "/"),
        ],
    )
    def test_punctuation_and_math(self, dev, collector, fake_evdev, code, expected_char) -> None:
        dev._process_evdev_event(_press(code))
        assert [e.event_type for e in collector] == [EventType.CHARACTER_PRESSED]
        assert collector[0].character == expected_char

    def test_space_press(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_SPACE))
        assert [e.event_type for e in collector] == [EventType.SPACE_PRESSED]

    @pytest.mark.parametrize("code", [FAKE_ECODES.KEY_ENTER, FAKE_ECODES.KEY_KPENTER])
    def test_enter_press(self, dev, collector, fake_evdev, code) -> None:
        dev._process_evdev_event(_press(code))
        assert [e.event_type for e in collector] == [EventType.ENTER_PRESSED]

    def test_backspace_press(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_BACKSPACE))
        assert [e.event_type for e in collector] == [EventType.BACKSPACE_PRESSED]

    def test_esc_emits_stop_not_quit(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_ESC))
        assert EventType.QUIT not in [e.event_type for e in collector]
        assert [e.event_type for e in collector] == [EventType.STOP_PRESSED]

    @pytest.mark.parametrize(
        "code,expected",
        [
            (FAKE_ECODES.KEY_F1, EventType.MODE_PRESSED),
            (FAKE_ECODES.KEY_TAB, EventType.MODE_PRESSED),
            (FAKE_ECODES.KEY_F2, EventType.LANGUAGE_TOGGLE),
            (FAKE_ECODES.KEY_GRAVE, EventType.LANGUAGE_TOGGLE),
            (FAKE_ECODES.KEY_F3, EventType.CLEAR_PRESSED),
            (FAKE_ECODES.KEY_DELETE, EventType.CLEAR_PRESSED),
            (FAKE_ECODES.KEY_F11, EventType.VOLUME_DOWN),
            (FAKE_ECODES.KEY_F12, EventType.VOLUME_UP),
        ],
    )
    def test_control_keys(self, dev, collector, fake_evdev, code, expected) -> None:
        dev._process_evdev_event(_press(code))
        assert [e.event_type for e in collector] == [expected]

    def test_release_and_autorepeat_ignored(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_A, value=0))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_A, value=2))
        assert collector == []

    def test_unknown_keycode_ignored(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(9999))
        assert collector == []

    def test_non_key_event_ignored(self, dev, collector, fake_evdev) -> None:
        event = types.SimpleNamespace(type=EV_REL, code=FAKE_ECODES.KEY_A, value=1)
        dev._process_evdev_event(event)
        assert collector == []


# ---------------------------------------------------------------------------
# (b) Modifier tracking
# ---------------------------------------------------------------------------

class TestModifierTracking:

    @pytest.mark.parametrize("ctrl_code", [FAKE_ECODES.KEY_LEFTCTRL, FAKE_ECODES.KEY_RIGHTCTRL])
    def test_ctrl_c_quits(self, dev, collector, fake_evdev, ctrl_code) -> None:
        dev._process_evdev_event(_press(ctrl_code))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_C))
        assert [e.event_type for e in collector] == [EventType.QUIT]

    @pytest.mark.parametrize("ctrl_code", [FAKE_ECODES.KEY_LEFTCTRL, FAKE_ECODES.KEY_RIGHTCTRL])
    def test_ctrl_q_quits(self, dev, collector, fake_evdev, ctrl_code) -> None:
        dev._process_evdev_event(_press(ctrl_code))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_Q))
        assert [e.event_type for e in collector] == [EventType.QUIT]

    def test_ctrl_release_restores_normal_keys(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTCTRL))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTCTRL, value=0))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_C))
        assert [e.event_type for e in collector] == [EventType.CHARACTER_PRESSED]
        assert collector[0].character == "C"

    def test_ctrl_other_keys_ignored(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTCTRL))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_A))
        assert collector == []

    @pytest.mark.parametrize("shift_code", [FAKE_ECODES.KEY_LEFTSHIFT, FAKE_ECODES.KEY_RIGHTSHIFT])
    def test_shift_number_gives_symbol(self, dev, collector, fake_evdev, shift_code) -> None:
        dev._process_evdev_event(_press(shift_code))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_1))
        assert [e.event_type for e in collector] == [EventType.CHARACTER_PRESSED]
        assert collector[0].character == "!"

    def test_shift_release_restores_digit(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTSHIFT))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTSHIFT, value=0))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_1))
        assert collector[0].character == "1"

    def test_shift_slash_gives_question_mark(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTSHIFT))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_SLASH))
        assert collector[0].character == "?"

    def test_shift_three_gives_number_sign(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTSHIFT))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_3))
        assert collector[0].character == "#"

    def test_shift_grave_gives_tilde_language_toggle(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTSHIFT))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_GRAVE))
        assert [e.event_type for e in collector] == [EventType.LANGUAGE_TOGGLE]

    def test_alt_combo_ignored(self, dev, collector, fake_evdev) -> None:
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTALT))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_A))
        dev._process_evdev_event(_press(FAKE_ECODES.KEY_LEFTALT, value=0))
        assert collector == []


# ---------------------------------------------------------------------------
# (c) Auto-selection logic (input.device)
# ---------------------------------------------------------------------------

class TestAutoSelection:

    def test_evdev_unavailable_falls_back_to_legacy(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: None)
        device = create_input_device(_cfg(), mode_getter=None)
        assert isinstance(device, KeyboardInputDevice)
        assert not isinstance(device, EvdevInputDevice)

    def test_usb_present_selects_evdev(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: FakeEvdev())
        monkeypatch.setattr(evdev_input, "usb_keyboard_present", lambda *a, **k: True)
        device = create_input_device(_cfg(), mode_getter=None)
        assert isinstance(device, EvdevInputDevice)

    def test_interactive_tty_without_usb_keeps_legacy(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: FakeEvdev())
        monkeypatch.setattr(evdev_input, "usb_keyboard_present", lambda *a, **k: False)
        monkeypatch.setattr(evdev_input, "_stdin_is_tty", lambda: True)
        device = create_input_device(_cfg(), mode_getter=None)
        assert isinstance(device, KeyboardInputDevice)
        assert not isinstance(device, EvdevInputDevice)

    def test_headless_no_tty_selects_evdev_even_without_device(self, monkeypatch) -> None:
        # The brailce.service systemd case: no TTY, keyboard maybe unplugged.
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: FakeEvdev())
        monkeypatch.setattr(evdev_input, "usb_keyboard_present", lambda *a, **k: False)
        monkeypatch.setattr(evdev_input, "_stdin_is_tty", lambda: False)
        device = create_input_device(_cfg(), mode_getter=None)
        assert isinstance(device, EvdevInputDevice)

    def test_forced_evdev_selects_evdev(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: None)
        device = create_input_device(_cfg(device="evdev"), mode_getter=None)
        assert isinstance(device, EvdevInputDevice)

    def test_forced_terminal_selects_legacy(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: FakeEvdev())
        device = create_input_device(_cfg(device="terminal"), mode_getter=None)
        assert isinstance(device, KeyboardInputDevice)
        assert not isinstance(device, EvdevInputDevice)

    def test_unknown_device_falls_back_to_legacy(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: None)
        device = create_input_device(_cfg(device="bogus"), mode_getter=None)
        assert isinstance(device, KeyboardInputDevice)

    def test_usb_name_match_passes_through(self, monkeypatch) -> None:
        monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: FakeEvdev())
        monkeypatch.setattr(evdev_input, "usb_keyboard_present", lambda *a, **k: True)
        device = create_input_device(_cfg(usb_name_match="Logitech"), mode_getter=None)
        assert isinstance(device, EvdevInputDevice)
        assert device._name_match == "Logitech"

    def test_default_config_declares_usb_name_match(self) -> None:
        assert DEFAULT_CONFIG["input"]["device"] == "keyboard"
        assert DEFAULT_CONFIG["input"]["usb_name_match"] == ""


# ---------------------------------------------------------------------------
# (d)/(e) Enumeration, GRAB, filters, and no-device resilience
# ---------------------------------------------------------------------------

class TestDeviceEnumeration:

    def test_keyboard_grabbed_and_mouse_excluded(self, fake_evdev) -> None:
        keyboard = FakeInputDevice("USB Keyboard", "/dev/input/event3", [FAKE_ECODES.KEY_A, FAKE_ECODES.KEY_ENTER])
        mouse = FakeInputDevice("USB Mouse", "/dev/input/event4", BTN_MOUSE_CODES)
        fake_evdev.devices = [mouse, keyboard]
        opened = evdev_input.open_usb_keyboards(fake_evdev, None)
        assert opened == [keyboard]
        assert keyboard.grabbed is True
        assert mouse.closed is True  # opened only to be inspected, then rejected

    def test_name_filter_excludes_non_matching(self, fake_evdev) -> None:
        keyboard = FakeInputDevice("Logitech USB Keyboard", "/dev/input/event3", [FAKE_ECODES.KEY_A])
        other = FakeInputDevice("Generic HID", "/dev/input/event5", [FAKE_ECODES.KEY_A])
        fake_evdev.devices = [other, keyboard]
        opened = evdev_input.open_usb_keyboards(fake_evdev, "logitech")
        assert opened == [keyboard]
        assert other.closed is True

    def test_empty_filter_accepts_any_keyboard(self, fake_evdev) -> None:
        keyboard = FakeInputDevice("Anything", "/dev/input/event3", [FAKE_ECODES.KEY_A])
        fake_evdev.devices = [keyboard]
        opened = evdev_input.open_usb_keyboards(fake_evdev, "")
        assert opened == [keyboard]

    def test_grab_failure_still_usable(self, fake_evdev) -> None:
        keyboard = FakeInputDevice("USB Keyboard", "/dev/input/event3", [FAKE_ECODES.KEY_A], fail_grab=True)
        fake_evdev.devices = [keyboard]
        opened = evdev_input.open_usb_keyboards(fake_evdev, None)
        assert opened == [keyboard]
        assert keyboard.grabbed is False

    def test_list_devices_error_returns_empty(self, fake_evdev) -> None:
        fake_evdev.list_error = True
        assert evdev_input.open_usb_keyboards(fake_evdev, None) == []

    def test_none_module_returns_empty(self) -> None:
        assert evdev_input.open_usb_keyboards(None, None) == []


class TestNoDevicePresentDoesNotCrash:

    def test_start_and_stop_with_no_devices(self, monkeypatch, fake_evdev) -> None:
        _non_stdin(monkeypatch)
        dev = EvdevInputDevice()
        dev.RESCAN_SECONDS = 0.05
        dev.start()
        try:
            time.sleep(0.3)
            assert dev._thread is not None and dev._thread.is_alive()
        finally:
            dev.stop()
        dev._thread.join(timeout=2.0)
        assert not dev._thread.is_alive()

    def test_read_error_re_enumerates_without_crashing(self, monkeypatch, fake_evdev) -> None:
        _non_stdin(monkeypatch)
        keyboard = FakeInputDevice(
            "USB Keyboard", "/dev/input/event3", [FAKE_ECODES.KEY_A],
            read_error=OSError("device unplugged"),
        )
        fake_evdev.devices = [keyboard]
        calls = {"n": 0}

        def fake_select(rlist, wlist, elist, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                return ([list(rlist)[0]], [], [])
            return ([], [], [])

        monkeypatch.setattr(evdev_input.select, "select", fake_select)
        dev = EvdevInputDevice()
        dev.RESCAN_SECONDS = 0.05
        dev.start()
        try:
            time.sleep(0.5)
            assert dev._thread is not None and dev._thread.is_alive()
            assert calls["n"] >= 1
            assert keyboard.closed is True  # released after the read error
        finally:
            dev.stop()
        dev._thread.join(timeout=2.0)
        assert not dev._thread.is_alive()
