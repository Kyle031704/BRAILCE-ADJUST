"""
Brailce Tests - Esc Stops Current Activity
===========================================

Bare Esc must emit STOP_PRESSED (stop speech, scanner readout, pending AI
request) instead of QUIT so the app stays usable for typing. Ctrl+C and
Ctrl+Q still quit, and PuTTY F1/F2/F3 escape sequences keep resolving to
their original events.
"""

import select
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from brailce.core.app import BrailceApp
from brailce.core.events import AppMode, EventType, InputEvent, control_event
from brailce.core.state import ApplicationState
from brailce.input.keyboard_input import KeyboardInputDevice
from brailce.modes.ai_assistant import AIAssistantMode
from brailce.modes.scanner_mode import ScannerMode


@pytest.fixture
def device() -> KeyboardInputDevice:
    """Keyboard device flagged as running, without any real hook or terminal."""
    dev = KeyboardInputDevice()
    dev._running = True
    return dev


def _collect(dev: KeyboardInputDevice) -> list:
    received: list = []
    dev.register_callback(lambda e: received.append(e))
    return received


def _feed_escape_sequence(monkeypatch: pytest.MonkeyPatch, dev: KeyboardInputDevice, seq: str) -> None:
    """Feed Esc followed by a PuTTY escape sequence through a mocked POSIX stdin."""
    pending = list(seq)
    fake_stdin = MagicMock()
    fake_stdin.read.side_effect = lambda n=1: pending.pop(0) if pending else ""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(
        select,
        "select",
        lambda r, w, e, t: ([fake_stdin], [], []) if pending else ([], [], []),
    )
    dev._process_char("\x1b")


class TestBareEscEmitsStopPressed:
    """A bare Esc with no following character must stop, not quit."""

    def test_terminal_bare_esc_does_not_emit_quit(self, device, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")  # skip escape-sequence sniffing
        received = _collect(device)
        device._process_char("\x1b")
        assert EventType.QUIT not in [e.event_type for e in received]
        assert [e.event_type for e in received] == [EventType.STOP_PRESSED]

    def test_key_hook_esc_does_not_emit_quit(self, device) -> None:
        received = _collect(device)
        mock_event = MagicMock()
        mock_event.name = "esc"
        device._on_key_press(mock_event)
        assert EventType.QUIT not in [e.event_type for e in received]
        assert [e.event_type for e in received] == [EventType.STOP_PRESSED]

    def test_terminal_bare_esc_with_empty_posix_stdin(self, device, monkeypatch) -> None:
        received = _collect(device)
        _feed_escape_sequence(monkeypatch, device, "")
        assert [e.event_type for e in received] == [EventType.STOP_PRESSED]

    def test_stop_pressed_event_type_exists(self) -> None:
        event = control_event(EventType.STOP_PRESSED, "keyboard")
        assert event.event_type == EventType.STOP_PRESSED
        assert event.character is None


class TestQuitKeysStillQuit:
    """Ctrl+C (and Ctrl+Q) must still emit QUIT."""

    def test_ctrl_c_terminal_still_quits(self, device) -> None:
        received = _collect(device)
        device._process_char("\x03")
        assert received[-1].event_type == EventType.QUIT

    def test_ctrl_q_terminal_quits(self, device) -> None:
        received = _collect(device)
        device._process_char("\x11")
        assert received[-1].event_type == EventType.QUIT

    def test_ctrl_q_key_hook_quits(self, device) -> None:
        received = _collect(device)
        mock_event = MagicMock()
        mock_event.name = "ctrl+q"
        device._on_key_press(mock_event)
        assert received[-1].event_type == EventType.QUIT


class TestPuttyEscapeSequencesPreserved:
    """ESC-prefixed PuTTY sequences must keep their existing events."""

    @pytest.mark.parametrize(
        "seq,expected",
        [
            ("OP", EventType.MODE_PRESSED),
            ("[11~", EventType.MODE_PRESSED),
            ("[[A", EventType.MODE_PRESSED),
            ("O1;2P", EventType.MODE_PRESSED),
            ("OQ", EventType.LANGUAGE_TOGGLE),
            ("[12~", EventType.LANGUAGE_TOGGLE),
            ("[[B", EventType.LANGUAGE_TOGGLE),
            ("O1;2Q", EventType.LANGUAGE_TOGGLE),
            ("OR", EventType.CLEAR_PRESSED),
            ("[13~", EventType.CLEAR_PRESSED),
            ("[[C", EventType.CLEAR_PRESSED),
            ("O1;2R", EventType.CLEAR_PRESSED),
        ],
    )
    def test_function_key_sequences_resolve(self, device, monkeypatch, seq, expected) -> None:
        received = _collect(device)
        _feed_escape_sequence(monkeypatch, device, seq)
        assert [e.event_type for e in received] == [expected]
        assert EventType.QUIT not in [e.event_type for e in received]


def _scanner_app() -> MagicMock:
    app = MagicMock()
    app.state = ApplicationState()
    app.display = MagicMock()
    app.speech = MagicMock()
    app.camera = MagicMock()
    app.camera.is_available.return_value = True
    app.config = MagicMock()
    app.config.get.return_value = False
    return app


class TestScannerStopsOnEsc:
    """STOP_PRESSED must wind down scanner readout/spelling via the stop flag."""

    def test_stop_pressed_stops_active_readout(self) -> None:
        app = _scanner_app()
        mode = ScannerMode(app)
        mode._is_reading = True

        mode.handle_event(control_event(EventType.STOP_PRESSED))

        assert mode._stop_readout_event.is_set()
        assert mode._is_reading is False
        app.speech.stop.assert_called()

    def test_stop_pressed_during_scan_discards_result_and_readout(self) -> None:
        app = _scanner_app()
        frame = np.full((240, 320, 3), 200, dtype=np.uint8)
        app.camera.capture_sharpest.return_value = frame
        app.obr = MagicMock()
        app.obr.process_image.return_value = {"readable": True, "text": "NEED", "cells": []}
        mode = ScannerMode(app)
        mode._is_scanning = True
        mode._stop_readout_event.set()

        mode._scan_worker()

        assert mode._is_scanning is False
        assert mode._is_reading is False
        app.speech.speak_text.assert_not_called()
        app.display.show_text_buffer.assert_not_called()

    def test_new_scan_clears_stop_flag(self) -> None:
        app = _scanner_app()
        mode = ScannerMode(app)
        mode._stop_readout_event.set()

        with patch("threading.Thread.start"):
            mode._start_scan()

        assert not mode._stop_readout_event.is_set()


def _ai_app() -> MagicMock:
    app = MagicMock()
    app.state = MagicMock()
    app.state.ai_processing = False
    app.state.ai_query_buffer = "What is Braille?"
    app.state.language = "en"
    app.display = MagicMock()
    app.speech = MagicMock()
    return app


class TestAIAssistantCancelsOnEsc:
    """STOP_PRESSED must cancel a pending AI request via the cancel event."""

    def test_stop_pressed_cancels_pending_request(self) -> None:
        app = _ai_app()
        app.state.ai_processing = True
        mode = AIAssistantMode(app)

        mode.handle_event(control_event(EventType.STOP_PRESSED))

        assert mode._cancel_event.is_set()
        assert app.state.ai_processing is False
        app.display.show_text_buffer.assert_called_once_with("What is Braille?")

    def test_cancelled_worker_discards_response(self) -> None:
        app = _ai_app()
        app.ai_client = MagicMock()
        app.ai_client.generate.return_value = "Answer"
        mode = AIAssistantMode(app)
        mode._cancel_event.set()

        mode._process_query("What?")

        app.display.show_ai_response.assert_not_called()
        app.speech.speak_text.assert_not_called()

    def test_new_request_clears_cancel_flag(self) -> None:
        app = _ai_app()
        app.state.get_ai_query.return_value = "What is Braille?"
        app.config = MagicMock()
        app.config.get.return_value = False
        app.encoder = MagicMock()
        mode = AIAssistantMode(app)
        mode._cancel_event.set()

        with patch("threading.Thread.start"):
            mode.handle_event(InputEvent(EventType.ENTER_PRESSED))

        assert not mode._cancel_event.is_set()


class TestAppStopPressedDispatch:
    """BrailceApp must stop speech, forward to modes, and keep running."""

    def test_stop_pressed_stops_speech_and_forwards_to_active_mode(self) -> None:
        app = BrailceApp(MagicMock())
        app.speech = MagicMock()
        app._active_mode = MagicMock()

        event = control_event(EventType.STOP_PRESSED, "keyboard")
        app._on_input_event(event)

        app.speech.stop.assert_called_once()
        app._active_mode.handle_event.assert_called_once_with(event)
        assert app.state.is_running is True

    def test_stop_pressed_without_speech_still_forwards(self) -> None:
        app = BrailceApp(MagicMock())
        app.speech = None
        app._active_mode = MagicMock()

        app._on_input_event(control_event(EventType.STOP_PRESSED))

        app._active_mode.handle_event.assert_called_once()
        assert app.state.is_running is True

    def test_stop_pressed_cancels_pending_ai_even_when_other_mode_active(self) -> None:
        app = BrailceApp(MagicMock())
        app.speech = MagicMock()
        app.state.ai_processing = True
        app._modes = {AppMode.AI_ASSISTANT: MagicMock()}
        app._active_mode = MagicMock()

        event = control_event(EventType.STOP_PRESSED, "keyboard")
        app._on_input_event(event)

        app._modes[AppMode.AI_ASSISTANT].handle_event.assert_called_once_with(event)