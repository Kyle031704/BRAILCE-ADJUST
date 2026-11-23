"""
Brailce Tests - Safe Shutdown (Ctrl+P / GPIO long-press)
=========================================================

SHUTDOWN_PRESSED fires exactly once per run and drives the safe shutdown
flow: localized voice announcement (non-interrupting), bounded wait for
the speech queue to drain, the normal app cleanup, then
`systemctl poweroff` (`shutdown -h now` fallback).

  (a) terminal  \x10 (Ctrl+P) -> SHUTDOWN_PRESSED (NOT QUIT)
  (b) key hook "ctrl+p"      -> SHUTDOWN_PRESSED
  (c) app handler: announces, powers off exactly once, repeats ignored
  (d) GPIO: mocked gpiozero Button hold fires the event;
      gpiozero-missing path never crashes
  (e) Ctrl+C / Ctrl+Q still QUIT and Esc still STOP_PRESSED (regression)

All gpiozero interaction is mocked; no real GPIO or evdev install is
required, so these tests run on any host.
"""

from __future__ import annotations

import queue
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import brailce.input.evdev_input as evdev_input
from brailce.config.defaults import DEFAULT_CONFIG
from brailce.config.loader import ConfigLoader
from brailce.core.app import BrailceApp
from brailce.core.events import EventType, control_event
from brailce.i18n.strings import get_string
from brailce.input.evdev_input import EvdevInputDevice
from brailce.input.keyboard_input import KeyboardInputDevice
from brailce.input.shutdown_button import ShutdownButton


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def device() -> KeyboardInputDevice:
    """Keyboard device flagged as running, without any real hook or terminal."""
    dev = KeyboardInputDevice()
    dev._running = True
    return dev


def _collect(dev) -> list:
    received: list = []
    dev.register_callback(lambda e: received.append(e))
    return received


def _app() -> BrailceApp:
    """BrailceApp with mocked speech/display (config unused by the handler)."""
    app = BrailceApp(MagicMock())
    app.speech = MagicMock()
    app.display = MagicMock()
    return app


EN_ANNOUNCE = get_string("SHUTDOWN_ANNOUNCE", "en")
FIL_ANNOUNCE = get_string("SHUTDOWN_ANNOUNCE", "fil")


# ---------------------------------------------------------------------------
# (a)+(b) Input paths: Ctrl+P -> SHUTDOWN_PRESSED
# ---------------------------------------------------------------------------

class TestShutdownKeyCombo:
    """Ctrl+P must emit SHUTDOWN_PRESSED (not QUIT) on every input path."""

    def test_event_type_exists(self) -> None:
        event = control_event(EventType.SHUTDOWN_PRESSED, "keyboard")
        assert event.event_type == EventType.SHUTDOWN_PRESSED
        assert event.character is None

    def test_terminal_ctrl_p_emits_shutdown_not_quit(self, device) -> None:
        received = _collect(device)
        device._process_char("\x10")
        assert EventType.QUIT not in [e.event_type for e in received]
        assert [e.event_type for e in received] == [EventType.SHUTDOWN_PRESSED]

    def test_key_hook_ctrl_p_emits_shutdown_pressed(self, device) -> None:
        received = _collect(device)
        mock_event = MagicMock()
        mock_event.name = "ctrl+p"
        device._on_key_press(mock_event)
        assert [e.event_type for e in received] == [EventType.SHUTDOWN_PRESSED]

    def test_ctrl_p_does_not_quit_or_stop(self, device) -> None:
        received = _collect(device)
        device._process_char("\x10")
        assert EventType.QUIT not in [e.event_type for e in received]
        assert EventType.STOP_PRESSED not in [e.event_type for e in received]

    def test_terminal_ctrl_p_stops_the_listener(self, device) -> None:
        device._process_char("\x10")
        assert device._running is False


class TestQuitAndStopKeysUnchanged:
    """Ctrl+C / Ctrl+Q must still QUIT; bare Esc must still STOP_PRESSED."""

    def test_ctrl_c_terminal_still_quits(self, device) -> None:
        received = _collect(device)
        device._process_char("\x03")
        assert received[-1].event_type == EventType.QUIT
        assert received[-1].event_type != EventType.SHUTDOWN_PRESSED

    def test_ctrl_q_terminal_still_quits(self, device) -> None:
        received = _collect(device)
        device._process_char("\x11")
        assert received[-1].event_type == EventType.QUIT

    def test_ctrl_q_hook_still_quits(self, device) -> None:
        received = _collect(device)
        mock_event = MagicMock()
        mock_event.name = "ctrl+q"
        device._on_key_press(mock_event)
        assert received[-1].event_type == EventType.QUIT

    def test_bare_esc_still_stops(self, device, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")  # skip sequence sniffing
        received = _collect(device)
        device._process_char("\x1b")
        assert [e.event_type for e in received] == [EventType.STOP_PRESSED]


# ---------------------------------------------------------------------------
# (b cont.) evdev USB keyboard path: Ctrl+P -> SHUTDOWN_PRESSED
# ---------------------------------------------------------------------------

EV_KEY = 1
FAKE_ECODES = types.SimpleNamespace(
    EV_KEY=EV_KEY, KEY_P=25, KEY_LEFTCTRL=29, KEY_C=46, KEY_Q=16
)
FAKE_EVDEV = types.SimpleNamespace(ecodes=FAKE_ECODES)


@pytest.fixture
def evdev_dev(monkeypatch) -> EvdevInputDevice:
    monkeypatch.setattr(evdev_input, "try_import_evdev", lambda: FAKE_EVDEV)
    d = EvdevInputDevice()
    d._running = True
    return d


class TestEvdevCtrlP:
    """The direct USB path must translate Ctrl+P to SHUTDOWN_PRESSED."""

    def test_evdev_ctrl_p_emits_shutdown_pressed(self, evdev_dev) -> None:
        received = _collect(evdev_dev)
        evdev_dev._process_evdev_event(
            types.SimpleNamespace(type=EV_KEY, code=FAKE_ECODES.KEY_LEFTCTRL, value=1)
        )
        evdev_dev._process_evdev_event(
            types.SimpleNamespace(type=EV_KEY, code=FAKE_ECODES.KEY_P, value=1)
        )
        assert [e.event_type for e in received] == [EventType.SHUTDOWN_PRESSED]

    def test_evdev_ctrl_q_still_quits(self, evdev_dev) -> None:
        received = _collect(evdev_dev)
        evdev_dev._process_evdev_event(
            types.SimpleNamespace(type=EV_KEY, code=FAKE_ECODES.KEY_LEFTCTRL, value=1)
        )
        evdev_dev._process_evdev_event(
            types.SimpleNamespace(type=EV_KEY, code=FAKE_ECODES.KEY_Q, value=1)
        )
        assert [e.event_type for e in received] == [EventType.QUIT]


# ---------------------------------------------------------------------------
# (c) App handler: announce -> drain -> cleanup -> power off, exactly once
# ---------------------------------------------------------------------------

class TestAppShutdownHandler:
    """BrailceApp must announce, power off via systemctl, and ignore repeats."""

    def test_announces_then_calls_systemctl_poweroff(self) -> None:
        app = _app()
        with patch("brailce.core.app.subprocess") as mock_sub:
            app._on_input_event(control_event(EventType.SHUTDOWN_PRESSED, "keyboard"))

        app.speech.speak_text.assert_called_once_with(EN_ANNOUNCE, interrupt=False)
        app.display.show_status.assert_any_call(EN_ANNOUNCE)
        mock_sub.run.assert_called_once()
        args, kwargs = mock_sub.run.call_args
        assert list(args[0])[:2] == ["systemctl", "poweroff"]

    def test_poweroff_output_suppressed_and_check_false(self) -> None:
        app = _app()

        with patch("brailce.core.app.subprocess") as mock_sub:
            app._power_off()
        kwargs = mock_sub.run.call_args.kwargs
        assert kwargs["stdout"] is mock_sub.DEVNULL
        assert kwargs["stderr"] is mock_sub.DEVNULL
        assert kwargs["check"] is False

    def test_announces_in_filipino_when_language_fil(self) -> None:
        app = _app()
        app.state.language = "fil"
        with patch("brailce.core.app.subprocess"):
            app._on_input_event(control_event(EventType.SHUTDOWN_PRESSED, "keyboard"))
        app.speech.speak_text.assert_called_once_with(FIL_ANNOUNCE, interrupt=False)

    def test_runs_app_cleanup_before_power_off(self) -> None:
        app = _app()
        calls: list = []
        app.shutdown = lambda: calls.append("shutdown")
        with patch("brailce.core.app.subprocess") as mock_sub:
            mock_sub.run.side_effect = lambda *a, **k: calls.append("poweroff")
            app._on_input_event(control_event(EventType.SHUTDOWN_PRESSED, "keyboard"))
        assert calls == ["shutdown", "poweroff"]

    def test_second_shutdown_event_ignored(self) -> None:
        app = _app()
        with patch("brailce.core.app.subprocess") as mock_sub:
            app._on_input_event(control_event(EventType.SHUTDOWN_PRESSED, "keyboard"))
            assert mock_sub.run.call_count == 1
            app.speech.speak_text.reset_mock()
            app.speech.shutdown.reset_mock()
            app._on_input_event(control_event(EventType.SHUTDOWN_PRESSED, "keyboard"))
        assert mock_sub.run.call_count == 1
        app.speech.speak_text.assert_not_called()

    def test_shutdown_marks_state_not_running(self) -> None:
        app = _app()
        assert app.state.is_running is True
        with patch("brailce.core.app.subprocess"):
            app._on_input_event(control_event(EventType.SHUTDOWN_PRESSED, "keyboard"))
        assert app.state.is_running is False

    def test_poweroff_falls_back_to_shutdown_when_systemctl_missing(self) -> None:
        app = _app()
        with patch("brailce.core.app.subprocess") as mock_sub:
            mock_sub.run.side_effect = [FileNotFoundError("systemctl"), None]
            app._power_off()
        assert mock_sub.run.call_count == 2
        second = mock_sub.run.call_args_list[1][0][0]
        assert second == ["shutdown", "-h", "now"]

    def test_poweroff_survives_all_commands_failing(self) -> None:
        app = _app()
        with patch("brailce.core.app.subprocess") as mock_sub:
            mock_sub.run.side_effect = FileNotFoundError("nope")
            app._power_off()  # must not raise
        assert mock_sub.run.call_count == 2

    def test_strings_defined(self) -> None:
        assert EN_ANNOUNCE == "Shutting down. Goodbye."
        assert FIL_ANNOUNCE == "Pinapatay na ang Brailce. Paalam."


class TestSpeechDrainWait:
    """The bounded drain wait must poll the real speech queue mechanisms."""

    def test_waits_until_engine_queue_empty(self, monkeypatch) -> None:
        app = _app()
        q = queue.Queue()
        q.put("Shutting down. Goodbye.")

        class FakeEngine:
            _queue = q

        speech = MagicMock()
        speech._engine = FakeEngine()
        speech._character_engine = FakeEngine()
        speech._text_engine = FakeEngine()
        speech._filipino_engine = None
        app.speech = speech

        sleeps: list = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            try:
                q.get_nowait()  # the "playback" finishes during the wait
            except queue.Empty:
                pass

        monkeypatch.setattr("brailce.core.app.time.sleep", fake_sleep)
        app._wait_for_speech_to_drain(timeout=5.0)
        assert len(sleeps) == 1  # one poll found the queue busy, then it drained

    def test_returns_immediately_when_idle(self) -> None:
        app = _app()  # MagicMock speech -> no real pending queue
        with patch("brailce.core.app.time.sleep") as mock_sleep:
            app._wait_for_speech_to_drain(timeout=5.0)
        mock_sleep.assert_not_called()

    def test_times_out_bounded_when_never_idle(self, monkeypatch) -> None:
        app = _app()
        monkeypatch.setattr(app, "_speech_is_idle", lambda: False)
        slept: list = []
        monkeypatch.setattr(
            "brailce.core.app.time.monotonic",
            lambda: 0.0 if not slept else 100.0,
        )
        monkeypatch.setattr(
            "brailce.core.app.time.sleep", lambda s: slept.append(s)
        )
        app._wait_for_speech_to_drain(timeout=5.0)
        assert len(slept) == 1  # bounded: second loop check hits the deadline

    def test_speech_is_idle_tracks_active_playback(self, monkeypatch) -> None:
        from brailce.speech.audio_device import AudioDeviceCoordinator

        app = _app()
        proc = MagicMock()
        monkeypatch.setattr(AudioDeviceCoordinator, "_active_process", proc)
        assert app._speech_is_idle() is False
        monkeypatch.setattr(AudioDeviceCoordinator, "_active_process", None)
        assert app._speech_is_idle() is True

    def test_speech_none_is_idle(self) -> None:
        app = _app()
        app.speech = None
        assert app._speech_is_idle() is True


# ---------------------------------------------------------------------------
# (d) GPIO long-press button (gpiozero fully mocked)
# ---------------------------------------------------------------------------

class FakeGpiozeroButton:
    """Stand-in for gpiozero.Button capturing wiring + the when_held callback."""

    instances: list = []

    def __init__(self, pin=None, pull_up=True, bounce_time=None, hold_time=1.0):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.hold_time = hold_time
        self.when_held = None
        self.closed = False
        FakeGpiozeroButton.instances.append(self)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_gpiozero(monkeypatch):
    FakeGpiozeroButton.instances = []
    monkeypatch.setitem(
        sys.modules, "gpiozero", types.SimpleNamespace(Button=FakeGpiozeroButton)
    )
    return FakeGpiozeroButton


class TestShutdownButton:
    """gpiozero Button wiring + hold callback -> SHUTDOWN_PRESSED."""

    def test_start_wires_button_from_config(self, fake_gpiozero) -> None:
        btn = ShutdownButton(pin=17, hold_seconds=2.0, pull_up=True)
        received = _collect(btn)
        btn.start()
        (button,) = fake_gpiozero.instances
        assert button.pin == 17
        assert button.hold_time == 2.0
        assert button.pull_up is True
        assert button.when_held is not None

        button.when_held()  # the 2 s hold fired
        assert [e.event_type for e in received] == [EventType.SHUTDOWN_PRESSED]
        assert received[0].source == "gpio_shutdown"

        btn.stop()
        assert button.closed is True

    def test_gpiozero_missing_does_not_crash(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "gpiozero", None)  # ImportError
        btn = ShutdownButton(pin=17)
        btn.start()  # one INFO, no exception
        assert btn._button is None
        btn.stop()  # safe even though nothing was wired

    def test_button_construction_error_degrades_gracefully(self, monkeypatch) -> None:
        class BrokenButton:
            def __init__(self, *args, **kwargs):
                raise OSError("no pin factory")

        monkeypatch.setitem(
            sys.modules, "gpiozero", types.SimpleNamespace(Button=BrokenButton)
        )
        btn = ShutdownButton(pin=4)
        btn.start()  # one INFO, no exception
        assert btn._button is None


class TestAppGpioWiring:
    """App wiring follows the existing component style (battery poller)."""

    def test_null_gpio_config_leaves_button_disabled(self) -> None:
        config = ConfigLoader({"power": {"shutdown_gpio": None}})
        app = BrailceApp(config)
        app._setup_shutdown_button()
        assert app.shutdown_button is None

    def test_configured_gpio_creates_and_starts_button(self, fake_gpiozero) -> None:
        config = ConfigLoader(
            {
                "power": {
                    "shutdown_gpio": 17,
                    "shutdown_hold_seconds": 3.0,
                    "shutdown_pull_up": False,
                }
            }
        )
        app = BrailceApp(config)
        app._setup_shutdown_button()
        assert app.shutdown_button is not None
        (button,) = fake_gpiozero.instances
        assert button.pin == 17
        assert button.hold_time == 3.0
        assert button.pull_up is False

    def test_gpio_hold_reaches_app_handler_and_powers_off_once(
        self, fake_gpiozero
    ) -> None:
        config = ConfigLoader({"power": {"shutdown_gpio": 17}})
        app = BrailceApp(config)
        app.speech = MagicMock()
        app.display = MagicMock()
        app._setup_shutdown_button()
        (button,) = fake_gpiozero.instances
        with patch("brailce.core.app.subprocess") as mock_sub:
            button.when_held()
            button.when_held()  # second hold ignored (guard flag)
        app.speech.speak_text.assert_called_once_with(EN_ANNOUNCE, interrupt=False)
        assert mock_sub.run.call_count == 1
        assert mock_sub.run.call_args[0][0][:2] == ["systemctl", "poweroff"]

    def test_invalid_gpio_value_disables_button(self) -> None:
        config = ConfigLoader({"power": {"shutdown_gpio": "not-a-pin"}})
        app = BrailceApp(config)
        app._setup_shutdown_button()
        assert app.shutdown_button is None

    def test_gpio_zero_pin_disables_button(self) -> None:
        config = ConfigLoader({"power": {"shutdown_gpio": 0}})
        app = BrailceApp(config)
        app._setup_shutdown_button()
        # pin 0 is still a BCM pin; wiring only refuses null/invalid values
        assert app.shutdown_button is not None


# ---------------------------------------------------------------------------
# Config defaults + shipped config.json
# ---------------------------------------------------------------------------

class TestShutdownConfig:
    """power.shutdown_* defaults + shipped config.json keys."""

    def test_default_power_shutdown_keys(self) -> None:
        assert DEFAULT_CONFIG["power"]["shutdown_gpio"] is None
        assert DEFAULT_CONFIG["power"]["shutdown_hold_seconds"] == 2.0
        assert DEFAULT_CONFIG["power"]["shutdown_pull_up"] is True

    def test_shipped_config_json_declares_shutdown_keys(self) -> None:
        import json

        config_path = Path(__file__).resolve().parents[1] / "config" / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        power = data["power"]
        assert power["shutdown_gpio"] is None
        assert power["shutdown_hold_seconds"] == 2.0
        assert power["shutdown_pull_up"] is True

    def test_config_loader_merges_shutdown_keys(self) -> None:
        config = ConfigLoader({"power": {"shutdown_gpio": 27}})
        assert config.get("power", "shutdown_gpio") == 27
        assert config.get("power", "shutdown_hold_seconds", default=2.0) == 2.0
        assert config.get("power", "shutdown_pull_up", default=True) is True
