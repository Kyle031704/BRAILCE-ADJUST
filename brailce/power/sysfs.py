"""
Brailce Sysfs Power Monitor - Real Battery Driver
===================================================

Reads real battery state from the Linux standard power_supply sysfs
interface (/sys/class/power_supply/*).

Selection rule: pick the first supply entry (sorted for determinism)
that exposes a "capacity" file. Read:
    - capacity : 0-100 percent (clamped)
    - status   : "Charging" / "Discharging" / "Full" / "Not charging" /
                 "Unknown"

Missing or unreadable files are handled gracefully: the driver reports
percentage=None (no data) instead of fabricating values.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from brailce.power.base import PowerMonitor, PowerStatus

logger = logging.getLogger(__name__)

DEFAULT_SYSFS_BASE = "/sys/class/power_supply"

# sysfs "status" values that mean external power is connected
_PLUGGED_STATUSES = {"Charging", "Full", "Not charging"}


class SysfsPowerMonitor(PowerMonitor):
    """
    Real battery monitor backed by /sys/class/power_supply/*.

    The base path is injectable for unit tests (temp-dir fake sysfs trees).
    """

    def __init__(self, base_path: Optional[str] = None) -> None:
        self._base_path = base_path or DEFAULT_SYSFS_BASE

    @property
    def driver_name(self) -> str:
        return f"Sysfs Power Monitor ({self._base_path})"

    # ---- probing ----

    def _find_supply(self) -> Optional[str]:
        """Return the first supply directory (sorted) that has a capacity file."""
        try:
            if not os.path.isdir(self._base_path):
                return None
            entries = sorted(os.listdir(self._base_path))
        except Exception as exc:
            logger.debug("Sysfs power supply scan failed: %s", exc)
            return None
        for name in entries:
            try:
                if os.path.isfile(os.path.join(self._base_path, name, "capacity")):
                    return name
            except Exception:
                continue
        return None

    def has_capacity_source(self) -> bool:
        """True when at least one sysfs supply exposes a real capacity file."""
        return self._find_supply() is not None

    # ---- reading ----

    def get_status(self) -> PowerStatus:
        """Read capacity + status from the first supply with a capacity file."""
        supply = self._find_supply()
        if supply is None:
            return self._no_data()

        capacity = self._read_capacity(supply)
        if capacity is None:
            return self._no_data()

        status = self._read_status(supply)
        is_charging = status == "Charging"
        if status in _PLUGGED_STATUSES:
            is_plugged: Optional[bool] = True
        elif status == "Discharging":
            is_plugged = False
        else:  # "Unknown" or anything unexpected
            is_plugged = None

        return PowerStatus(
            percentage=capacity,
            is_charging=is_charging,
            is_plugged_in=is_plugged,
            status_text=f"Battery: {capacity}% ({status})",
        )

    def _read_capacity(self, supply: str) -> Optional[int]:
        """Read and clamp the 0-100 capacity value; None when unreadable."""
        path = os.path.join(self._base_path, supply, "capacity")
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            return max(0, min(100, int(raw)))
        except Exception as exc:
            logger.debug("Could not read capacity from %s: %s", path, exc)
            return None

    def _read_status(self, supply: str) -> str:
        """Read the status string; 'Unknown' when missing or unreadable."""
        path = os.path.join(self._base_path, supply, "status")
        try:
            with open(path, "r") as f:
                return f.read().strip() or "Unknown"
        except Exception as exc:
            logger.debug("Could not read status from %s: %s", path, exc)
            return "Unknown"

    @staticmethod
    def _no_data() -> PowerStatus:
        return PowerStatus(
            percentage=None,
            is_charging=None,
            is_plugged_in=None,
            status_text="No battery data (sysfs)",
        )
