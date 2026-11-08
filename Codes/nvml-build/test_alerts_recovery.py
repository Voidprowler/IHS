"""Tests for the recovery rule: recovery ends an episode but keeps the
per-severity cooldown timers (see AlertSystem docstring and Section 3.5.4)."""
import unittest

from alerts import AlertSystem


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class QuietAlerts(AlertSystem):
    """No real toasts; counts what would have been shown."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.shown = []

    def _show_toast(self, title, message):
        self.shown.append((title, message))


class RecoveryKeepsCooldownTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.a = QuietAlerts(clock=self.clock)

    def breach(self, severity="WARNING", channel="cpu_temp"):
        return self.a.notify("t", "m", channel=channel, severity=severity)

    def recover(self, channel="cpu_temp"):
        results = [self.a.report_ok(channel) for _ in range(self.a.clear_after)]
        return results

    def test_first_breach_ever_still_toasts(self):
        self.assertTrue(self.breach())
        self.assertEqual(self.a.last_outcome, "sent")

    def test_rebreach_inside_cooldown_after_recovery_is_suppressed(self):
        self.assertTrue(self.breach())
        self.clock.advance(5)
        self.assertEqual(self.recover(), [False, False, True])
        self.clock.advance(5)
        self.assertFalse(self.breach())
        self.assertEqual(self.a.last_outcome, "suppressed")
        self.assertEqual(len(self.a.shown), 1)

    def test_rebreach_after_cooldown_toasts_again(self):
        self.assertTrue(self.breach())
        self.recover()
        self.clock.advance(self.a.cooldown + 1)
        self.assertTrue(self.breach())
        self.assertEqual(len(self.a.shown), 2)

    def test_cooldown_boundary_is_not_extended_by_suppressed_rebreach(self):
        self.assertTrue(self.breach())            # t0
        self.recover()
        self.clock.advance(30)
        self.assertFalse(self.breach())           # suppressed at t0+30
        self.recover()
        self.clock.advance(31)                     # t0+61 since last TOAST
        self.assertTrue(self.breach())

    def test_escalation_after_recovery_toasts_immediately(self):
        self.assertTrue(self.breach("WARNING"))
        self.recover()
        self.clock.advance(3)
        self.assertTrue(self.breach("CRITICAL"))
        self.assertEqual(self.a.last_outcome, "sent")

    def test_critical_toast_covers_warning_after_recovery(self):
        self.assertTrue(self.breach("CRITICAL"))
        self.recover()
        self.clock.advance(3)
        self.assertFalse(self.breach("WARNING"))
        self.assertEqual(self.a.last_outcome, "suppressed")

    def test_critical_rebreach_inside_cooldown_is_suppressed(self):
        self.assertTrue(self.breach("CRITICAL"))
        self.recover()
        self.clock.advance(3)
        self.assertFalse(self.breach("CRITICAL"))

    def test_channels_are_independent(self):
        self.assertTrue(self.breach("WARNING", "cpu_temp"))
        self.recover("cpu_temp")
        self.assertTrue(self.breach("WARNING", "ram_percent"))
        self.assertFalse(self.breach("WARNING", "cpu_temp"))

    def test_recovered_counter_and_single_true(self):
        self.breach()
        self.assertEqual(self.recover(), [False, False, True])
        self.assertFalse(self.a.report_ok("cpu_temp"))   # already recovered
        self.assertEqual(self.a.stats["cpu_temp"]["recovered"], 1)

    def test_breach_interrupts_recovery_streak(self):
        self.breach()
        self.a.report_ok("cpu_temp")
        self.a.report_ok("cpu_temp")
        self.clock.advance(1)
        self.breach()                                    # streak reset, still suppressed
        self.assertFalse(self.a.report_ok("cpu_temp"))
        self.assertEqual(self.a.stats["cpu_temp"]["recovered"], 0)

    def test_suppressed_rebreach_is_counted(self):
        self.breach()
        self.recover()
        self.breach()
        self.assertEqual(self.a.stats["cpu_temp"]["sent"], 1)
        self.assertEqual(self.a.stats["cpu_temp"]["suppressed"], 1)

    def test_flapping_reading_gives_one_toast_per_cooldown(self):
        """A reading that dips below the threshold for 3 cycles and breaches
        again every ~7 s must not toast every time."""
        toasts = 0
        for _ in range(12):                              # 12 flaps over ~84 s
            toasts += bool(self.breach())
            self.clock.advance(2.4)
            self.recover()
            self.clock.advance(4.8)
        self.assertEqual(toasts, 2)                      # t=0 and the first flap after t>=60


if __name__ == "__main__":
    unittest.main()
