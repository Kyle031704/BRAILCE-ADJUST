"""
Brailce Auto Power Monitor
=============================

Automatic power driver selection:
    - "sysfs" real battery driver when a real capacity source exists
      (any /sys/class/power_supply/*/capacity), else
    - the existing simulated behavior (SimulatedPowerMonitor).

NOTE: if the device's battery HAT does not expose /sys/class/power_supply
(many I2C-only UPS HATs, e.g. INA219-based boards, do not), auto falls
back to simulated and a HAT-specific driver may be needed.
"""

from __future__ import annotations

from typing import Optional

from brailce.power.base import PowerMonitor, PowerStatus
from brailce.power.rpi_power import PiPowerMonitor
from brailce.power.simulated import SimulatedPowerMonitor
from brailce.power.sysfs import SysfsPowerMonitor


class AutoPowerMonitor(PowerMonitor):
    """
    Chooses sysfs when a real capacity source exists, then an I2C UPS HAT
    (such as Geekworm X1203 MAX17040 at 0x36 or INA219), otherwise the
    existing simulated monitor.
    """

    def __init__(self, base_path: Optional[str] = None) -> None:
        self._sysfs = SysfsPowerMonitor(base_path)
        self._rpi = PiPowerMonitor()
        self._simulated = SimulatedPowerMonitor()
        self._active: Optional[str] = None

    @property
    def driver_name(self) -> str:
        active = self._active or "probing"
        return f"Auto Power Monitor ({active})"

    def _resolve(self) -> PowerMonitor:
        if self._sysfs.has_capacity_source():
            self._active = "sysfs"
            return self._sysfs
        if getattr(self._rpi, "_ups_type", None) is not None:
            self._active = self._rpi._ups_type
            return self._rpi
        self._active = "simulated"
        return self._simulated

    def get_status(self) -> PowerStatus:
        return self._resolve().get_status()
