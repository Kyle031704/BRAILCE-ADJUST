"""
Brailce Tests - Battery Poller (announcements + display indicator)
=====================================================================

Tests threshold announcements (once per crossing, hysteresis re-arm),
charging-start/full announcements, the 30 s guard, and the real-time
display/state updates. Uses fakes; poll_once() is driven directly.
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

from brailce.core.state import ApplicationState
from brailce.power.base import PowerMonitor, PowerStatus
from brailce.power.poller import BatteryPoller


class FakeMonitor(PowerMonitor):
    def __init__(self) -> None:
        self._pct: Optional[int] = None
        self._charging: bool = False
        self._status_text: str = "Test supply"

    def set(self, pct: Optional[int], charging: bool, status_text: str = None) -> None:
        self._pct = pct
        self._charging = charging
        if status_text is not None:
            self._status_text = status_text

    @property
    def driver_name(self) -> str:
        return "Fake Power Monitor"

    def get_status(self) -> PowerStatus:
        return PowerStatus(
            percentage=self._pct,
            is_charging=self._charging if self._pct is not None else None,
            is_plugged_in=self._charging if self._pct is not None else None,
            status_text=self._status_text,
        )


class RecordingDisplay:
    def __init__(self) -> None:
        self.battery_calls = []

    def show_battery(self, percentage, charging: bool = False) -> None:
        self.battery_calls.append((percentage, charging))


class RecordingSpeech:
    def __init__(self) -> None:
        self.calls = []

    def speak_text(self, text: str, interrupt: bool = False, **kwargs) -> None:
        self.calls.append((text, interrupt))


def make_poller(poll_seconds: float = 30.0, min_interval: float = 0.0):
    monitor = FakeMonitor()
    display = RecordingDisplay()
    speech = RecordingSpeech()
    state = ApplicationState()
    poller = BatteryPoller(
        monitor=monitor,
        display=display,
        speech=speech,
        state=state,
        poll_seconds=poll_seconds,
        min_announce_interval_s=min_interval,
    )
    return poller, monitor, display, speech, state


def announced(speech: RecordingSpeech) -> list:
    return [text for text, _interrupt in speech.calls]


class TestThresholdAnnouncements:
    def test_discharging_crossings_announced_once_each(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(80, False)
        poller.poll_once()  # boot baseline: no announcements

        monitor.set(74, False)  # crosses 75
        poller.poll_once()
        monitor.set(73, False)  # still below 75: no repeat
        poller.poll_once()
        monitor.set(50, False)  # crosses 50
        poller.poll_once()
        monitor.set(25, False)  # crosses 25
        poller.poll_once()

        assert announced(speech) == [
            "Battery at 75 percent.",
            "Battery at 50 percent.",
            "Battery at 25 percent.",
        ]

    def test_hysteresis_rearm_after_rising_five_points(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(80, False)
        poller.poll_once()
        monitor.set(49, False)  # crosses 75 and 50 in one poll: lowest wins
        poller.poll_once()
        assert announced(speech) == ["Battery at 50 percent."]

        monitor.set(54, False)  # +5 above 50? no (54 < 55): 50 stays disarmed
        poller.poll_once()
        monitor.set(49, False)
        poller.poll_once()
        assert announced(speech) == ["Battery at 50 percent."]  # no re-fire

        monitor.set(56, False)  # >= 50+5: threshold re-arms
        poller.poll_once()
        monitor.set(49, False)  # crosses again
        poller.poll_once()
        assert announced(speech) == [
            "Battery at 50 percent.",
            "Battery at 50 percent.",
        ]

    def test_no_boot_announcement_when_already_below_threshold(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(60, False)  # already below 75 at boot
        poller.poll_once()
        assert announced(speech) == []

        monitor.set(50, False)  # only the 50 crossing fires, not 75
        poller.poll_once()
        assert announced(speech) == ["Battery at 50 percent."]

    def test_charging_state_change_rearms_all_thresholds(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(80, False)
        poller.poll_once()
        monitor.set(74, False)  # fires 75, disarms it
        poller.poll_once()
        assert announced(speech) == ["Battery at 75 percent."]

        monitor.set(74, True)  # plug in: charging start + re-arms thresholds
        poller.poll_once()
        assert announced(speech) == [
            "Battery at 75 percent.",
            "Charging. 74 percent.",
        ]

        monitor.set(76, True)  # 75 re-armed by the state change: fires again
        poller.poll_once()
        assert announced(speech) == [
            "Battery at 75 percent.",
            "Charging. 74 percent.",
            "Battery at 75 percent.",
        ]

        # Unplug at 49: 50 is already passed in the discharging direction
        # (position-based re-arm), so it must NOT fire...
        count_before_unplug = len(announced(speech))
        monitor.set(49, False)
        poller.poll_once()
        assert len(announced(speech)) == count_before_unplug

        # ...but the not-yet-crossed 25 threshold fires when reached.
        monitor.set(20, False)
        poller.poll_once()
        assert announced(speech)[-1] == "Battery at 25 percent."

    def test_charging_upward_crossings_announced(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(40, True)
        poller.poll_once()  # boot while charging: charging announce only
        assert announced(speech) == ["Charging. 40 percent."]

        monitor.set(52, True)  # crosses 50 going up
        poller.poll_once()
        monitor.set(76, True)  # crosses 75 going up
        poller.poll_once()
        assert announced(speech) == [
            "Charging. 40 percent.",
            "Battery at 50 percent.",
            "Battery at 75 percent.",
        ]

        monitor.set(100, True, status_text="Full")
        poller.poll_once()  # full announcement
        assert announced(speech) == [
            "Charging. 40 percent.",
            "Battery at 50 percent.",
            "Battery at 75 percent.",
            "Battery full.",
        ]

    def test_fil_wording_follows_app_language(self) -> None:
        poller, monitor, _display, speech, state = make_poller()
        state.language = "fil"
        monitor.set(80, False)
        poller.poll_once()
        monitor.set(50, False)
        poller.poll_once()
        assert announced(speech) == ["50 na porsyento na ang natitira."]

    def test_announcements_are_non_interrupting(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(80, False)
        poller.poll_once()
        monitor.set(50, False)
        poller.poll_once()
        assert speech.calls[0][1] is False  # interrupt=False (normal queue)


class TestChargingAndFullAnnouncements:
    def test_boot_while_charging_announces_current_percent(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(60, True)
        poller.poll_once()
        assert announced(speech) == ["Charging. 60 percent."]

    def test_charging_start_transition_announces_percent(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(40, False)
        poller.poll_once()  # discharging baseline: silence
        assert announced(speech) == []

        monitor.set(40, True)
        poller.poll_once()
        assert announced(speech) == ["Charging. 40 percent."]

    def test_full_announced_once_per_charge_session(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(95, True)
        poller.poll_once()
        monitor.set(100, True, status_text="Full")
        poller.poll_once()
        monitor.set(100, True, status_text="Full")  # still full: no repeat
        poller.poll_once()
        assert announced(speech) == [
            "Charging. 95 percent.",
            "Battery full.",
        ]

        # New charge session after discharging: full may fire again.
        monitor.set(90, False)
        poller.poll_once()
        monitor.set(90, True)
        poller.poll_once()
        monitor.set(100, True, status_text="Full")
        poller.poll_once()
        assert announced(speech) == [
            "Charging. 95 percent.",
            "Battery full.",
            "Charging. 90 percent.",
            "Battery full.",
        ]

    def test_full_detected_via_status_text_below_100(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(97, True, status_text="Full")
        poller.poll_once()
        monitor.set(97, True, status_text="Full")
        poller.poll_once()
        assert "Battery full." in announced(speech)
        assert announced(speech).count("Battery full.") == 1

    def test_full_not_announced_while_discharging(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(100, False, status_text="Full")
        poller.poll_once()
        assert "Battery full." not in announced(speech)


class TestAnnouncementGuard:
    def test_rapid_announcements_suppressed_within_interval(self) -> None:
        poller, monitor, _display, speech, _state = make_poller(
            min_interval=30.0
        )
        monitor.set(80, False)
        poller.poll_once()
        monitor.set(74, False)
        poller.poll_once()  # fires 75
        monitor.set(50, False)
        poller.poll_once()  # within 30 s of the last announcement: suppressed

        assert announced(speech) == ["Battery at 75 percent."]

    def test_no_data_polls_announce_nothing(self) -> None:
        poller, monitor, _display, speech, _state = make_poller()
        monitor.set(None, False)
        poller.poll_once()
        poller.poll_once()
        assert announced(speech) == []


class TestDisplayAndStateUpdates:
    def test_poll_updates_display_indicator_and_state(self) -> None:
        poller, monitor, display, _speech, state = make_poller()
        monitor.set(77, False)
        poller.poll_once()
        assert display.battery_calls == [(77, False)]
        assert state.battery_percentage == 77
        assert state.battery_charging is False

        monitor.set(60, True)
        poller.poll_once()
        assert display.battery_calls == [(77, False), (60, True)]
        assert state.battery_percentage == 60
        assert state.battery_charging is True

    def test_no_data_poll_skips_display(self) -> None:
        poller, monitor, display, _speech, state = make_poller()
        monitor.set(None, False)
        poller.poll_once()
        assert display.battery_calls == []
        assert state.battery_percentage is None

    def test_poller_thread_runs_and_stops(self) -> None:
        poller, monitor, display, _speech, _state = make_poller(
            poll_seconds=0.05, min_interval=0.0
        )
        monitor.set(64, False)
        poller.start()
        try:
            deadline = time.monotonic() + 5.0
            while len(display.battery_calls) < 3 and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            poller.stop()
        assert len(display.battery_calls) >= 3
        assert poller._thread.is_alive() is False
