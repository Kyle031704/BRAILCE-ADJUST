"""
Brailce Optional GPIO Safe-Shutdown Button
==========================================

Optional long-press power switch for users who wire their power button
to a Raspberry Pi GPIO pin (BCM numbering).

Configuration (config.json "power" section):
    - "shutdown_gpio": BCM pin number when wired (e.g. 17);
      null (default) = disabled, no GPIO wiring at all.
    - "shutdown_hold_seconds": how long the button must be held
      before the shutdown fires (default 2.0 s).
    - "shutdown_pull_up": internal pull-up (default true) - wire the
      button between the configured pin and GND.

Holding the button for the configured time emits ONE SHUTDOWN_PRESSED
event - the SAME event the Ctrl+P keyboard combo emits - so the app
handler (voice announcement -> clean app shutdown -> systemctl poweroff)
does all the work. No shutdown logic is duplicated here.

gpiozero is imported lazily inside start(); when it is unavailable
(development machines, or the dependency is not installed yet) one INFO
line is logged and the app continues without the button, so the service
still boots fine everywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from brailce.core.events import EventType, control_event
from brailce.input.base import InputDevice

logger = logging.getLogger(__name__)


class ShutdownButton(InputDevice):
    """
    Optional GPIO long-press power switch producing SHUTDOWN_PRESSED.

    Attributes:
        _pin:           BCM pin number the power switch is wired to.
        _hold_seconds:  Press duration required to trigger the shutdown.
        _pull_up:       gpiozero internal pull-up flag (button to GND).
        _button:        The gpiozero Button once wired (None when disabled).
    """

    SOURCE_NAME = "gpio_shutdown"

    def __init__(
        self,
        pin: int,
        hold_seconds: float = 2.0,
        pull_up: bool = True,
    ) -> None:
        super().__init__()
        self._pin = int(pin)
        self._hold_seconds = max(0.1, float(hold_seconds))
        self._pull_up = bool(pull_up)
        self._button: Optional[Any] = None
        self._running = False

    @property
    def device_name(self) -> str:
        return "GPIO Shutdown Button (BCM {}, hold {:.1f}s)".format(
            self._pin, self._hold_seconds
        )

    # ---- Lifecycle ----

    def start(self) -> None:
        """Wire the gpiozero Button; degrade gracefully when unavailable."""
        self._running = True
        try:
            from gpiozero import Button  # lazy: optional Pi-only dependency
        except Exception as exc:
            logger.info(
                "gpiozero unavailable - GPIO shutdown button disabled (%s).", exc
            )
            return
        try:
            self._button = Button(
                self._pin,
                pull_up=self._pull_up,
                bounce_time=0.05,
                hold_time=self._hold_seconds,
            )
            self._button.when_held = self._on_held
            logger.info(
                "GPIO shutdown button armed on BCM pin %d (hold %.1f s, pull_up=%s).",
                self._pin,
                self._hold_seconds,
                self._pull_up,
            )
        except Exception as exc:
            # e.g. no pin factory / not a Pi: keep the service running
            logger.info(
                "GPIO shutdown button not wired on BCM pin %d (%s).", self._pin, exc
            )
            self._button = None

    def stop(self) -> None:
        """Release the gpiozero Button (safe to call more than once)."""
        self._running = False
        if self._button is not None:
            try:
                self._button.close()
            except Exception:
                pass
            self._button = None
        logger.info("GPIO shutdown button stopped.")

    # ---- Callback ----

    def _on_held(self) -> None:
        """gpiozero hold callback: route SHUTDOWN_PRESSED to the app handler."""
        if not self._running:
            return
        logger.info(
            "GPIO shutdown button held %.1f s - emitting SHUTDOWN_PRESSED.",
            self._hold_seconds,
        )
        self._emit_event(control_event(EventType.SHUTDOWN_PRESSED, self.SOURCE_NAME))
