"""Indicator correspondence test (Objective d).

Checks that the colour the dashboard shows for a reading (green, amber, red,
grey) corresponds to the state the alert logic would assign to the same
reading (Good, Warning, Critical, or no reading). The alert state is not
re-implemented here: it is obtained by running main.check_alerts on the
reading, so the test compares the dashboard with the real alert code.

main.py relaunches itself as administrator when imported, and both main.py
and dashboard.py import the Windows sensor stack; _import() switches those
off for the test. The dashboard window itself is never created.
"""
import ctypes
import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

os.chdir(tempfile.mkdtemp(prefix="ihs_tests_"))    # alerts.py logs to ./sentinel_log.txt


def _import(name):
    fake_windll = mock.MagicMock()
    fake_windll.shell32.IsUserAnAdmin.return_value = 1
    fake_sensors = types.ModuleType("sensors")
    fake_sensors.HardwareSensors = object
    previous = sys.modules.get("sensors")
    sys.modules["sensors"] = fake_sensors
    try:
        with mock.patch.object(ctypes, "windll", fake_windll, create=True):
            return importlib.import_module(name)
    finally:
        if previous is None:
            sys.modules.pop("sensors", None)
        else:
            sys.modules["sensors"] = previous


main = _import("main")
try:
    dashboard = _import("dashboard")
except ImportError as exc:                          # tkinter, Pillow or pystray missing
    raise unittest.SkipTest(f"dashboard could not be imported: {exc}")

from alerts import AlertSystem

GOOD, WARNING, CRITICAL = "GOOD", "WARNING", "CRITICAL"
COLOUR_STATE = {"#00ff00": GOOD, "#ffaa00": WARNING, "#ff0000": CRITICAL, "#666666": None}
ALERTING = {"cpu_temp": "cpu_temp", "ram_percent": "ram_percent", "ssd_health": "ssd_health"}


class FakeClock:
    def __call__(self):
        return 0.0


class RecordingAlerts(AlertSystem):
    def __init__(self):
        super().__init__(clock=FakeClock())

    def _show_toast(self, title, message):
        pass


def stats(**changes):
    base = {"cpu_temp": 60.0, "gpu_temp": 55.0, "ssd_temp": 50.0, "ssd_health": 85.0,
            "ssd_health_drive": "WDC PC SN730 SDBPNTY-512G-1032", "ssd_drives": [],
            "ram_percent": 50.0}
    base.update(changes)
    return base


def alert_state(key, value):
    """The state main.check_alerts assigns to one reading of one channel."""
    if value is None:
        return None
    events = main.check_alerts(RecordingAlerts(), stats(**{key: value}))
    if any(e.endswith(":CRITICAL:sent") for e in events):
        return CRITICAL
    if any(e.endswith(":WARNING:sent") for e in events):
        return WARNING
    return GOOD


def dashboard_state(key, value):
    """The state the dashboard's colour for one reading stands for."""
    dash = dashboard.SentinelDashboard.__new__(dashboard.SentinelDashboard)
    dash.labels = {                                  # as built in setup_ui
        "cpu_temp": {"threshold": main.CPU_TEMP_CRITICAL},
        "gpu_temp": {"threshold": 80},
        "ssd_temp": {"threshold": 70},
        "ssd_health": {"threshold": main.SSD_HEALTH_CRITICAL},
        "ram_percent": {"threshold": main.RAM_PERCENT_LIMIT},
    }
    return COLOUR_STATE[dash.get_status_color(value, key)]


def frange(start, stop, step):
    n = int(round((stop - start) / step))
    return [round(start + i * step, 3) for i in range(n + 1)]


def summarise(pairs):
    """Compact ranges: consecutive grid points with the same (dashboard, alert) pair."""
    out, run = [], None
    for value, dash, alert in pairs:
        if run and run[2:] == (dash, alert):
            run = (run[0], value, dash, alert)
        else:
            if run:
                out.append(run)
            run = (value, value, dash, alert)
    if run:
        out.append(run)
    return "; ".join(f"{a} to {b}: dashboard {d}, alert {t}" for a, b, d, t in out)


class CorrespondenceOverAGridTests(unittest.TestCase):
    """Every grid reading must give the same state on the dashboard and in the alert logic."""

    def check(self, key, values):
        mismatches = [(v, dashboard_state(key, v), alert_state(key, v)) for v in values]
        mismatches = [m for m in mismatches if m[1] != m[2]]
        self.assertEqual(mismatches, [], f"{key}: {len(mismatches)} of {len(values)} readings "
                         f"disagree. " + summarise(mismatches))

    def test_cpu_temperature(self):
        self.check("cpu_temp", frange(30, 105, 0.5))

    def test_ram_utilisation(self):
        self.check("ram_percent", frange(20, 100, 0.5))

    def test_ssd_life(self):
        self.check("ssd_health", frange(0, 100, 1) + [19.9, 20.0, 20.1])


class BoundaryTests(unittest.TestCase):

    def test_cpu_boundaries(self):
        for value, expected in [(79.9, GOOD), (80, WARNING), (89.9, WARNING), (90, CRITICAL)]:
            with self.subTest(value=value):
                self.assertEqual(dashboard_state("cpu_temp", value), expected)

    def test_ram_boundaries(self):
        for value, expected in [(90.0, GOOD), (90.1, WARNING)]:
            with self.subTest(value=value):
                self.assertEqual(dashboard_state("ram_percent", value), expected)

    def test_ssd_life_boundaries(self):
        for value, expected in [(20, GOOD), (19.9, CRITICAL), (0, CRITICAL)]:
            with self.subTest(value=value):
                self.assertEqual(dashboard_state("ssd_health", value), expected)

    def test_display_only_channels_show_amber_at_their_limits_and_never_red(self):
        for key, limit in [("gpu_temp", 80), ("ssd_temp", 70)]:
            with self.subTest(key=key):
                self.assertEqual(dashboard_state(key, limit - 0.1), GOOD)
                self.assertEqual(dashboard_state(key, limit), WARNING)
                self.assertEqual(dashboard_state(key, 120), WARNING)

    def test_no_reading_is_grey_on_every_channel(self):
        for key in ["cpu_temp", "gpu_temp", "ssd_temp", "ssd_health", "ram_percent"]:
            with self.subTest(key=key):
                self.assertIsNone(dashboard_state(key, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
