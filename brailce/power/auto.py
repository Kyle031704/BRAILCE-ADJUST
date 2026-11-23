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
from brailce.power.simulated import SimulatedPowerMonitor
from brailce.power.sysfs import SysfsPowerMonitor


class AutoPowerMonitor(PowerMonitor):
    """
    Chooses sysfs when a real capacity source exists, otherwise the
    existing simulated monitor. Re-probes on every get_status() so a
    supply that appears/disappears at runtime is picked up.
    """

    def __init__(self, base_path: Optional[str] = None) -> None:
        self._sysfs = SysfsPowerMonitor(base_path)
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
        self._active = "simulated"
        return self._simulated

    def get_status(self) -> PowerStatus:
        return self._resolve().get_status()
