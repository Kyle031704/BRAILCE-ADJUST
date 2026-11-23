"""
Brailce Battery Poller - Real-Time Battery Updates
=====================================================

Background daemon thread that polls the power monitor every
`power.poll_seconds` (default 30 s) and:

    1. Refreshes the LCD battery indicator in real time
       (DisplayDevice.show_battery -> "75%" style widget).
    2. Mirrors battery state into ApplicationState
       (battery_percentage / battery_charging).
    3. Makes non-interrupting voice announcements through SpeechService
       (normal speech queue, interrupt=False), localized via strings.py:
         - DISCHARGING: capacity crossings at 75 / 50 / 25 percent,
           once per crossing. A threshold re-arms only after capacity
           rises >= 5 points above it (hysteresis) or after a
           charging-state change.
         - CHARGING starts (or app boots while charging): announce the
           current percent. While charging, the same 75/50/25 crossings
           are announced going UP, plus "Battery full." once per charge
           session.
       Guard: never more than one battery announcement per 30 s and
       never concurrently with another battery announcement (all
       announcements funnel through a single mutex + time gate; the
       speech queue itself serializes playback).

Esc/STOP_PRESSED is unaffected: announcements use the normal speech
queue without interrupting, and the poller never touches input events.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from brailce.i18n.strings import get_string
from brailce.power.base import PowerMonitor, PowerStatus

logger = logging.getLogger(__name__)

# Percent levels announced on crossing (down while discharging, up while charging)
ANNOUNCE_THRESHOLDS = (75, 50, 25)
# Hysteresis: points a threshold must be cleared by before it re-arms
HYSTERESIS_POINTS = 5
# Default minimum spacing between battery announcements
MIN_ANNOUNCE_INTERVAL_S = 30.0


class BatteryPoller:
    """Polls a PowerMonitor and drives the display indicator + announcements."""

    def __init__(
        self,
        monitor: PowerMonitor,
        display: Any = None,
        speech: Any = None,
        state: Any = None,
        poll_seconds: float = 30.0,
        min_announce_interval_s: float = MIN_ANNOUNCE_INTERVAL_S,
    ) -> None:
        self._monitor = monitor
        self._display = display
        self._speech = speech
        self._state = state
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._min_announce_interval_s = max(0.0, float(min_announce_interval_s))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # announcement guard: single mutex + last-announce timestamp
        self._announce_mutex = threading.Lock()
        self._last_announce_time = float("-inf")
        # threshold state: armed -> will announce on next crossing
        self._armed = {t: True for t in ANNOUNCE_THRESHOLDS}
        self._prev_charging: Optional[bool] = None
        self._full_announced_this_session = False

    # ---- lifecycle ----

    def start(self) -> None:
        """Start the daemon poller thread (first poll fires immediately)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="brailce-battery-poller", daemon=True
        )
        self._thread.start()
        logger.info(
            "Battery poller started (every %.1fs, driver: %s).",
            self._poll_seconds,
            getattr(self._monitor, "driver_name", "unknown"),
        )

    def stop(self) -> None:
        """Signal the poller thread to stop and wait briefly for it."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        logger.info("Battery poller stopped.")

    def _run(self) -> None:
        # First poll immediately so boot state (charging / battery %) shows
        # on the display right away.
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("Battery poll cycle failed.")
            self._stop_event.wait(self._poll_seconds)

    # ---- polling ----

    def poll_once(self) -> Optional[PowerStatus]:
        """Run one poll cycle: display refresh + announcement logic."""
        status = self._monitor.get_status()
        pct = status.percentage
        charging = bool(status.is_charging)

        # Mirror battery state into the application state.
        if self._state is not None:
            try:
                self._state.battery_percentage = pct
                self._state.battery_charging = status.is_charging
            except Exception:
                logger.debug("Could not mirror battery state into app state.")

        if pct is None:
            # No real battery data: nothing to show or announce.
            return status

        # 1. Real-time display indicator refresh.
        if self._display is not None:
            try:
                self._display.show_battery(pct, charging)
            except Exception:
                logger.debug("Display battery indicator update failed.")

        # 2. Charging-state transitions (also the boot baseline on first poll).
        if self._prev_charging is None or self._prev_charging != charging:
            self._reset_thresholds(pct, charging)
            self._full_announced_this_session = False
            if charging:
                # Charging just started, or the app booted while charging.
                self._announce(get_string("BATTERY_CHARGING", self._language(), bilang=pct))
        self._prev_charging = charging

        # 3. Threshold crossings (down while discharging, up while charging).
        crossed = []
        for threshold in ANNOUNCE_THRESHOLDS:
            if self._armed[threshold]:
                hit = (pct >= threshold) if charging else (pct <= threshold)
                if hit:
                    crossed.append(threshold)
            else:
                # Hysteresis re-arm in the same direction only.
                if charging and pct <= threshold - HYSTERESIS_POINTS:
                    self._armed[threshold] = True
                elif not charging and pct >= threshold + HYSTERESIS_POINTS:
                    self._armed[threshold] = True
        if crossed:
            # One announcement per poll: the most urgent level reached while
            # discharging (lowest), the highest level reached while charging.
            level = max(crossed) if charging else min(crossed)
            for threshold in crossed:
                self._armed[threshold] = False
            self._announce(get_string("BATTERY_AT", self._language(), bilang=level))

        # 4. Full announcement, once per charge session.
        if (
            charging
            and not self._full_announced_this_session
            and self._is_full(status, pct)
        ):
            self._full_announced_this_session = True
            self._announce(get_string("BATTERY_FULL", self._language()))

        return status

    # ---- internals ----

    def _reset_thresholds(self, pct: int, charging: bool) -> None:
        """
        Re-arm thresholds after a charging-state change (or at boot).
        Position-based: only thresholds not yet crossed in the current
        direction stay armed, so 'already below 75 at boot' never fires.
        """
        for threshold in ANNOUNCE_THRESHOLDS:
            self._armed[threshold] = (pct < threshold) if charging else (pct > threshold)

    @staticmethod
    def _is_full(status: PowerStatus, pct: int) -> bool:
        text = (status.status_text or "").lower()
        return "full" in text or pct >= 100

    def _language(self) -> str:
        if self._state is not None:
            try:
                lang = getattr(self._state, "language", "en")
                if lang:
                    return "fil" if str(lang).lower() == "fil" else "en"
            except Exception:
                pass
        return "en"

    def _announce(self, message: str) -> bool:
        """
        Enqueue one non-interrupting announcement on the normal speech
        queue. Guarded: at most one battery announcement per
        min_announce_interval_s (default 30 s) and never issued while
        another battery announcement decision is in flight.
        """
        with self._announce_mutex:
            now = time.monotonic()
            if now - self._last_announce_time < self._min_announce_interval_s:
                return False
            self._last_announce_time = now

        if self._speech is None:
            return False
        try:
            self._speech.speak_text(message, interrupt=False)
            logger.info("Battery announcement: %s", message)
            return True
        except Exception:
            logger.exception("Battery announcement failed.")
            return False
