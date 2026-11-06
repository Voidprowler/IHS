import contextlib
import io
import os
import tempfile
import unittest

# alerts.py logs to ./sentinel_log.txt when imported. Move into a scratch
# folder first so test runs never write into the real evidence log.
os.chdir(tempfile.mkdtemp(prefix="ihs_tests_"))

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
        self.fail_toasts = False

    def _show_toast(self, title, message):
        if self.fail_toasts:
            raise RuntimeError("toast backend unavailable")
        self.toasts.append((title, message))


def make():
    clock = FakeClock()
    return RecordingAlerts(clock=clock), clock


class AlertKeyTests(unittest.TestCase):

    def test_sustained_breach_changing_values_one_toast_per_cooldown(self):
        # The original defect: value embedded in the message changed every
        # reading, so every reading looked like a new alert.
        a, clock = make()
        fired = 0
        for i in range(30):                       # 30 readings x 2 s = 58 s
            temp = 83 + (i % 5)                   # 83..87, always WARNING
            fired += a.notify("CPU HOT", f"CPU at {temp}C",
                              channel="cpu_temp", severity="WARNING")
            clock.advance(2)
        self.assertEqual(fired, 1)
        self.assertEqual(a.stats["cpu_temp"]["suppressed"], 29)

    def test_reminder_after_cooldown(self):
        a, clock = make()
        fired = 0
        for i in range(61):                       # 0..120 s
            fired += a.notify("CPU HOT", f"CPU at {83 + i % 3}C",
                              channel="cpu_temp", severity="WARNING")
            clock.advance(2)
        self.assertEqual(fired, 3)                # t=0, 60, 120

    def test_escalation_is_never_suppressed(self):
        a, clock = make()
        self.assertTrue(a.notify("CPU HOT", "86C", channel="cpu_temp", severity="WARNING"))
        clock.advance(2)
        self.assertTrue(a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL"))
        self.assertEqual(len(a.toasts), 2)

    def test_deescalation_is_logged_not_toasted(self):
        a, clock = make()
        a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL")
        clock.advance(2)
        self.assertFalse(a.notify("CPU HOT", "85C", channel="cpu_temp", severity="WARNING"))
        self.assertEqual(a.stats["cpu_temp"]["deescalated"], 1)
        self.assertEqual(len(a.toasts), 1)

    def test_reescalation_inside_the_cooldown_is_suppressed(self):
        # The defect found by the shifted-threshold run: a dip to WARNING and
        # back to CRITICAL used to count as a fresh escalation and toast again.
        a, clock = make()
        self.assertTrue(a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL"))
        clock.advance(2)
        a.notify("CPU HOT", "85C", channel="cpu_temp", severity="WARNING")
        clock.advance(2)
        self.assertFalse(a.notify("CPU CRITICAL", "93C", channel="cpu_temp", severity="CRITICAL"))
        self.assertEqual(a.last_outcome, "suppressed")
        self.assertEqual(len(a.toasts), 1)

    def test_reescalation_after_the_cooldown_toasts_again(self):
        a, clock = make()
        a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL")
        clock.advance(2)
        a.notify("CPU HOT", "85C", channel="cpu_temp", severity="WARNING")
        clock.advance(60)
        self.assertTrue(a.notify("CPU CRITICAL", "93C", channel="cpu_temp", severity="CRITICAL"))
        self.assertEqual(len(a.toasts), 2)

    def test_hovering_around_the_critical_threshold_gives_one_toast_per_cooldown(self):
        # Pattern from the real run: 82/81/82/84/81/... every ~3 s for 5 minutes.
        a, clock = make()
        critical_toasts = 0
        for i in range(100):                                   # 100 x 3 s = 300 s
            hot = (i % 4 != 3)                                 # dips to WARNING every 4th reading
            sev, temp = ("CRITICAL", 83) if hot else ("WARNING", 81)
            if a.notify(f"CPU {sev}", f"{temp}C", channel="cpu_temp", severity=sev) and sev == "CRITICAL":
                critical_toasts += 1
            clock.advance(3)
        self.assertEqual(critical_toasts, 5)                   # t = 0, 60, 120, 180, 240
        self.assertEqual(len(a.toasts), 5)                     # and no WARNING toasts in between

    def test_first_escalation_is_never_suppressed_even_right_after_a_warning(self):
        a, clock = make()
        a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING")
        clock.advance(1)
        self.assertTrue(a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL"))

    def test_recovery_then_renewed_breach_toasts_immediately(self):
        a, clock = make()
        a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING")
        for _ in range(3):                        # 3 consecutive normal readings
            clock.advance(2)
            a.report_ok("cpu_temp")
        self.assertEqual(a.stats["cpu_temp"]["recovered"], 1)
        clock.advance(2)                          # well inside the old cooldown
        self.assertTrue(a.notify("CPU HOT", "84C", channel="cpu_temp", severity="WARNING"))

    def test_flapping_does_not_clear_state(self):
        a, clock = make()
        a.notify("CPU HOT", "81C", channel="cpu_temp", severity="WARNING")
        for _ in range(2):
            clock.advance(2)
            a.report_ok("cpu_temp")               # streak = 2 (not yet 3)
        clock.advance(2)
        self.assertFalse(a.notify("CPU HOT", "81C", channel="cpu_temp", severity="WARNING"))
        for _ in range(2):
            clock.advance(2)
            a.report_ok("cpu_temp")               # streak restarted: 2 again
        self.assertEqual(a.stats["cpu_temp"]["recovered"], 0)
        clock.advance(2)
        a.report_ok("cpu_temp")                   # third consecutive
        self.assertEqual(a.stats["cpu_temp"]["recovered"], 1)

    def test_missing_readings_leave_state_untouched(self):
        # Caller skips report_ok() for None/stale readings.
        a, clock = make()
        a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING")
        clock.advance(30)                         # sensor returned None throughout
        self.assertFalse(a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING"))

    def test_channels_are_independent(self):
        a, _ = make()
        self.assertTrue(a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING"))
        self.assertTrue(a.notify("GPU HOT", "83C", channel="gpu_temp", severity="WARNING"))

    def test_toast_failure_still_applies_cooldown_and_is_counted(self):
        a, clock = make()
        a.fail_toasts = True
        self.assertFalse(a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING"))
        clock.advance(2)
        self.assertFalse(a.notify("CPU HOT", "84C", channel="cpu_temp", severity="WARNING"))
        self.assertEqual(a.stats["cpu_temp"]["failed"], 1)
        self.assertEqual(a.stats["cpu_temp"]["suppressed"], 1)

    def test_invalid_severity_rejected(self):
        a, _ = make()
        with self.assertRaises(ValueError):
            a.notify("X", "y", channel="cpu_temp", severity="GOOD")
        with self.assertRaises(ValueError):
            a.notify("X", "y", channel="cpu_temp", severity="banana")


    def test_last_outcome_and_report_ok_return_value(self):
        a, clock = make()
        a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING")
        self.assertEqual(a.last_outcome, "sent")
        clock.advance(2)
        a.notify("CPU HOT", "84C", channel="cpu_temp", severity="WARNING")
        self.assertEqual(a.last_outcome, "suppressed")
        a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL")
        self.assertEqual(a.last_outcome, "sent")
        a.notify("CPU HOT", "85C", channel="cpu_temp", severity="WARNING")
        self.assertEqual(a.last_outcome, "deescalated")
        self.assertFalse(a.report_ok("cpu_temp"))
        self.assertFalse(a.report_ok("cpu_temp"))
        self.assertTrue(a.report_ok("cpu_temp"))      # third consecutive
        self.assertFalse(a.report_ok("cpu_temp"))     # nothing left to clear
        a.fail_toasts = True
        a.notify("CPU HOT", "83C", channel="cpu_temp", severity="WARNING")
        self.assertEqual(a.last_outcome, "failed")


    def test_startup_line_lists_only_the_monitored_channels(self):
        a, _ = make()
        data = {"cpu_temp": 60, "gpu_temp": None, "ssd_temp": 50, "ssd_health": 85,
                "ram_percent": 55.0, "ssd_health_drive": "WDC-512G", "ssd_drives": []}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            a.log_system_profile(data)
        out = buf.getvalue()
        self.assertIn("CPU_TEMP", out)
        self.assertNotIn("DRIVE", out)
        self.assertNotIn("GPU_TEMP", out)


    def test_a_critical_toast_also_covers_warning_for_the_cooldown(self):
        a, clock = make()
        a.notify("CPU CRITICAL", "92C", channel="cpu_temp", severity="CRITICAL")
        clock.advance(2)
        a.notify("CPU HOT", "81C", channel="cpu_temp", severity="WARNING")      # de-escalation
        clock.advance(2)
        self.assertFalse(a.notify("CPU HOT", "81C", channel="cpu_temp", severity="WARNING"))
        self.assertEqual(len(a.toasts), 1)
        clock.advance(60)                                                     # now 64 s after CRITICAL
        self.assertTrue(a.notify("CPU HOT", "81C", channel="cpu_temp", severity="WARNING"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
