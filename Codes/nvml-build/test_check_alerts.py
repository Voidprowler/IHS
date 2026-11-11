"""Tests for main.check_alerts: SSD life alerts (by injected values, since a
healthy drive cannot breach), threshold boundaries, and missing or
unavailable readings.

main.py relaunches itself as administrator when it is imported by a normal
user, and it imports the Windows sensor stack. _import_main() switches off
both for the test, so no sensor, toast or administrator rights are needed.
"""
import ctypes
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

# alerts.py logs to ./sentinel_log.txt when imported. Move into a scratch
# folder first so test runs never write into the real evidence log.
os.chdir(tempfile.mkdtemp(prefix="ihs_tests_"))


def _import_main():
    fake_windll = mock.MagicMock()
    fake_windll.shell32.IsUserAnAdmin.return_value = 1       # already "admin"
    fake_sensors = types.ModuleType("sensors")
    fake_sensors.HardwareSensors = object                    # never instantiated here
    previous = sys.modules.get("sensors")
    sys.modules["sensors"] = fake_sensors
    try:
        with mock.patch.object(ctypes, "windll", fake_windll, create=True):
            import main as imported
    finally:
        if previous is None:
            sys.modules.pop("sensors", None)
        else:
            sys.modules["sensors"] = previous
    return imported


main = _import_main()
from alerts import AlertSystem


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class RecordingAlerts(AlertSystem):
    """Replaces the Windows toast with a recorder so tests run anywhere."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.toasts = []

    def _show_toast(self, title, message):
        self.toasts.append((title, message))


def make():
    clock = FakeClock()
    return RecordingAlerts(clock=clock), clock


def stats(**changes):
    """One polling cycle with every channel healthy, then the given changes."""
    base = {"cpu_temp": 60.0, "gpu_temp": 55.0, "ssd_temp": 50.0,
            "ssd_health": 85.0, "ssd_health_drive": "WDC PC SN730 SDBPNTY-512G-1032",
            "ssd_drives": [], "ram_percent": 50.0}
    base.update(changes)
    return base


LOW_LIFE = main.SSD_HEALTH_CRITICAL - 5


class SsdLifeAlertTests(unittest.TestCase):
    # A healthy drive never breaches, so these inject values into check_alerts.

    def test_life_below_the_threshold_raises_one_critical_toast(self):
        a, _ = make()
        events = main.check_alerts(a, stats(ssd_health=LOW_LIFE))
        self.assertEqual(events, ["ssd_health:CRITICAL:sent"])
        self.assertEqual(len(a.toasts), 1)
        title, message = a.toasts[0]
        self.assertIn("SSD CRITICAL", title)
        self.assertIn(f"SSD Life Remaining: {LOW_LIFE}%", message)
        self.assertIn("SDBPNTY-512G-1032", message)          # names the failing drive

    def test_life_exactly_at_the_threshold_is_not_an_alert(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(ssd_health=main.SSD_HEALTH_CRITICAL)), [])
        self.assertEqual(a.toasts, [])

    def test_zero_percent_life_is_a_real_reading(self):
        a, _ = make()
        events = main.check_alerts(a, stats(ssd_health=0.0))
        self.assertEqual(events, ["ssd_health:CRITICAL:sent"])

    def test_healthy_life_gives_no_event(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(ssd_health=85.0)), [])
        self.assertEqual(a.toasts, [])

    def test_repeat_inside_the_cooldown_is_withheld_and_a_reminder_follows(self):
        a, clock = make()
        main.check_alerts(a, stats(ssd_health=LOW_LIFE))
        clock.advance(2)
        self.assertEqual(main.check_alerts(a, stats(ssd_health=LOW_LIFE)),
                         ["ssd_health:CRITICAL:suppressed"])
        clock.advance(61)
        self.assertEqual(main.check_alerts(a, stats(ssd_health=LOW_LIFE)),
                         ["ssd_health:CRITICAL:sent"])
        self.assertEqual(len(a.toasts), 2)

    def test_recovery_is_reported_after_three_normal_readings(self):
        a, clock = make()
        main.check_alerts(a, stats(ssd_health=LOW_LIFE))
        results = []
        for _ in range(3):
            clock.advance(2)
            results.append(main.check_alerts(a, stats(ssd_health=85.0)))
        self.assertEqual(results, [[], [], ["ssd_health:recovered"]])

    def test_a_renewed_breach_inside_the_cooldown_is_withheld(self):
        a, clock = make()
        main.check_alerts(a, stats(ssd_health=LOW_LIFE))
        for _ in range(3):
            clock.advance(2)
            main.check_alerts(a, stats(ssd_health=85.0))
        clock.advance(2)
        self.assertEqual(main.check_alerts(a, stats(ssd_health=LOW_LIFE)),
                         ["ssd_health:CRITICAL:suppressed"])
        self.assertEqual(len(a.toasts), 1)

    def test_the_message_omits_the_drive_name_when_it_is_unknown(self):
        a, _ = make()
        main.check_alerts(a, stats(ssd_health=LOW_LIFE, ssd_health_drive=None))
        message = a.toasts[0][1]
        self.assertTrue(message.startswith("SSD Life Remaining"))


class ThresholdBoundaryTests(unittest.TestCase):

    def test_cpu_exactly_at_the_warning_threshold_warns(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(cpu_temp=float(main.CPU_TEMP_WARN))),
                         ["cpu_temp:WARNING:sent"])

    def test_cpu_just_below_the_warning_threshold_does_not(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(cpu_temp=main.CPU_TEMP_WARN - 0.1)), [])

    def test_cpu_exactly_at_the_critical_threshold_is_critical(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(cpu_temp=float(main.CPU_TEMP_CRITICAL))),
                         ["cpu_temp:CRITICAL:sent"])

    def test_ram_exactly_at_the_limit_does_not_alert_but_above_it_does(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(ram_percent=float(main.RAM_PERCENT_LIMIT))), [])
        self.assertEqual(main.check_alerts(a, stats(ram_percent=main.RAM_PERCENT_LIMIT + 0.1)),
                         ["ram:WARNING:sent"])


class MissingValueTests(unittest.TestCase):
    # A missing reading must never raise an alert, never count as healthy and
    # never reset a cooldown.

    def test_no_reading_on_any_channel_gives_no_events_and_no_toasts(self):
        a, _ = make()
        events = main.check_alerts(a, stats(cpu_temp=None, ram_percent=None, ssd_health=None))
        self.assertEqual(events, [])
        self.assertEqual(a.toasts, [])

    def test_a_cpu_reading_of_zero_means_no_reading(self):
        a, _ = make()
        self.assertEqual(main.check_alerts(a, stats(cpu_temp=0)), [])
        self.assertEqual(a.toasts, [])

    def test_missing_readings_never_count_as_normal(self):
        a, clock = make()
        main.check_alerts(a, stats(cpu_temp=main.CPU_TEMP_WARN + 2))
        for _ in range(10):                        # sensor unavailable for 10 cycles
            clock.advance(2)
            self.assertEqual(main.check_alerts(a, stats(cpu_temp=None)), [])
        self.assertEqual(a.stats["cpu_temp"]["recovered"], 0)
        results = []
        for _ in range(3):                         # now three valid normal readings
            clock.advance(2)
            results.append(main.check_alerts(a, stats(cpu_temp=60.0)))
        self.assertEqual(results, [[], [], ["cpu_temp:recovered"]])

    def test_missing_readings_do_not_reset_the_cooldown(self):
        a, clock = make()
        main.check_alerts(a, stats(cpu_temp=main.CPU_TEMP_WARN + 2))
        for _ in range(3):
            clock.advance(2)
            main.check_alerts(a, stats(cpu_temp=None))
        clock.advance(2)
        self.assertEqual(main.check_alerts(a, stats(cpu_temp=main.CPU_TEMP_WARN + 2)),
                         ["cpu_temp:WARNING:suppressed"])
        self.assertEqual(len(a.toasts), 1)

    def test_a_missing_ssd_reading_does_not_hide_a_cpu_alert(self):
        a, _ = make()
        events = main.check_alerts(a, stats(ssd_health=None, cpu_temp=main.CPU_TEMP_CRITICAL + 1))
        self.assertEqual(events, ["cpu_temp:CRITICAL:sent"])

    def test_one_missing_channel_does_not_stop_the_others(self):
        a, _ = make()
        events = main.check_alerts(a, stats(cpu_temp=None,
                                            ram_percent=main.RAM_PERCENT_LIMIT + 5,
                                            ssd_health=LOW_LIFE))
        self.assertEqual(sorted(events), ["ram:WARNING:sent", "ssd_health:CRITICAL:sent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
