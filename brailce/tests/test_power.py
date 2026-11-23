"""
Brailce Tests - Power Drivers (sysfs + auto)
================================================

Tests the real sysfs battery driver and the auto driver using fake
sysfs trees in temp directories (monkeypatched base path).
"""

from __future__ import annotations

import os
import builtins

import pytest

from brailce.power.auto import AutoPowerMonitor
from brailce.power.base import PowerMonitor
from brailce.power.simulated import SimulatedPowerMonitor
from brailce.power.sysfs import SysfsPowerMonitor


def make_supply(base: str, name: str, capacity: str = None, status: str = None) -> None:
    """Create a fake /sys/class/power_supply/<name> entry."""
    supply_dir = os.path.join(base, name)
    os.makedirs(supply_dir, exist_ok=True)
    if capacity is not None:
        with open(os.path.join(supply_dir, "capacity"), "w") as f:
            f.write(capacity)
    if status is not None:
        with open(os.path.join(supply_dir, "status"), "w") as f:
            f.write(status)


@pytest.fixture
def sysfs_root(tmp_path) -> str:
    root = tmp_path / "power_supply"
    root.mkdir()
    return str(root)


class TestSysfsDriver:
    def test_is_power_monitor(self, sysfs_root: str) -> None:
        assert isinstance(SysfsPowerMonitor(sysfs_root), PowerMonitor)

    def test_reads_capacity_and_status_discharging(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="75", status="Discharging")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 75
        assert status.is_charging is False
        assert status.is_plugged_in is False
        assert "75" in status.status_text

    def test_reads_charging_status(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="60", status="Charging")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 60
        assert status.is_charging is True
        assert status.is_plugged_in is True

    def test_reads_full_status(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="100", status="Full")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 100
        assert status.is_charging is False
        assert status.is_plugged_in is True

    def test_reads_not_charging_status(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="98", status="Not charging")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 98
        assert status.is_charging is False
        assert status.is_plugged_in is True

    def test_missing_status_file_defaults_unknown(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="42")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 42
        assert status.is_charging is False
        assert status.is_plugged_in is None
        assert "Unknown" in status.status_text

    def test_no_supplies_reports_no_data(self, sysfs_root: str) -> None:
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage is None
        assert status.is_charging is None
        assert status.is_plugged_in is None

    def test_missing_base_path_reports_no_data(self, tmp_path) -> None:
        status = SysfsPowerMonitor(str(tmp_path / "does_not_exist")).get_status()
        assert status.percentage is None
        assert status.is_charging is None

    def test_supply_without_capacity_is_skipped(self, sysfs_root: str) -> None:
        # AC adapter exposes no capacity file; BAT0 does.
        make_supply(sysfs_root, "AC", status="Online")
        make_supply(sysfs_root, "BAT0", capacity="40", status="Charging")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 40

    def test_only_non_capacity_supplies_reports_no_data(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "AC", status="Online")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage is None

    def test_first_supply_with_capacity_wins(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "AC", status="Online")
        make_supply(sysfs_root, "BAT0", capacity="40", status="Charging")
        make_supply(sysfs_root, "BAT1", capacity="90", status="Discharging")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage == 40  # BAT0 is the first (sorted) with capacity

    def test_invalid_capacity_reports_no_data(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="not-a-number", status="Charging")
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage is None

    def test_capacity_clamped_to_0_100(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="150", status="Charging")
        assert SysfsPowerMonitor(sysfs_root).get_status().percentage == 100
        make_supply(sysfs_root, "BAT0", capacity="-5", status="Discharging")
        assert SysfsPowerMonitor(sysfs_root).get_status().percentage == 0

    def test_unreadable_capacity_reports_no_data(self, sysfs_root: str, monkeypatch) -> None:
        make_supply(sysfs_root, "BAT0", capacity="55", status="Discharging")
        real_open = builtins.open

        def denied_open(path, *args, **kwargs):
            if str(path).endswith("capacity"):
                raise OSError("permission denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", denied_open)
        status = SysfsPowerMonitor(sysfs_root).get_status()
        assert status.percentage is None
        assert status.is_charging is None

    def test_has_capacity_source(self, sysfs_root: str) -> None:
        monitor = SysfsPowerMonitor(sysfs_root)
        assert monitor.has_capacity_source() is False
        make_supply(sysfs_root, "BAT0", capacity="55", status="Discharging")
        assert monitor.has_capacity_source() is True


class TestAutoDriver:
    def test_auto_picks_sysfs_when_present(self, sysfs_root: str) -> None:
        make_supply(sysfs_root, "BAT0", capacity="55", status="Discharging")
        monitor = AutoPowerMonitor(sysfs_root)
        status = monitor.get_status()
        assert status.percentage == 55
        assert "sysfs" in monitor.driver_name

    def test_auto_falls_back_to_simulated_when_absent(self, sysfs_root: str) -> None:
        monitor = AutoPowerMonitor(sysfs_root)
        status = monitor.get_status()
        assert status.percentage is None
        assert "simulated" in monitor.driver_name
        # Same no-data behavior as the simulated monitor itself.
        assert status.status_text == SimulatedPowerMonitor().get_status().status_text

    def test_auto_falls_back_when_base_path_missing(self, tmp_path) -> None:
        monitor = AutoPowerMonitor(str(tmp_path / "does_not_exist"))
        assert monitor.get_status().percentage is None
        assert "simulated" in monitor.driver_name

    def test_auto_picks_up_supply_appearing_at_runtime(self, sysfs_root: str) -> None:
        monitor = AutoPowerMonitor(sysfs_root)
        assert monitor.get_status().percentage is None  # simulated fallback
        make_supply(sysfs_root, "BAT0", capacity="80", status="Charging")
        status = monitor.get_status()  # re-probes on every poll
        assert status.percentage == 80
        assert status.is_charging is True

    def test_auto_is_power_monitor(self, sysfs_root: str) -> None:
        assert isinstance(AutoPowerMonitor(sysfs_root), PowerMonitor)
