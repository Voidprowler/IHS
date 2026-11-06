import csv
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta

import induce_breach as ib

HEADER = ("timestamp,loop,cpu_temp,gpu_temp,ssd_temp,ssd_health,ram_percent,"
          "alert_events,alert_ms,ssd_detail")


def write_telemetry(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        for ts, cpu in rows:
            f.write(f"{ts.isoformat(timespec='milliseconds')},1,{cpu},58.7,52,85,57.3,,0.0,\n")


class HelperTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.dir.name, "sentinel_telemetry.csv")

    def tearDown(self):
        self.dir.cleanup()

    def test_latest_row_returns_the_last_complete_row(self):
        now = datetime.now()
        write_telemetry(self.p, [(now - timedelta(seconds=5), 61), (now, 83)])
        self.assertEqual(ib.latest_row(self.p)["cpu_temp"], "83")

    def test_partial_last_row_is_ignored_not_misread(self):
        write_telemetry(self.p, [(datetime.now(), 70)])
        with open(self.p, "a", encoding="utf-8") as f:
            f.write("2026-11-01T18:00:00.000,2,9")            # row still being written
        self.assertIsNone(ib.latest_row(self.p))

    def test_header_only_file_gives_none(self):
        write_telemetry(self.p, [])
        self.assertIsNone(ib.latest_row(self.p))
        self.assertEqual(ib.read_cpu_temp(self.p), (None, None))

    def test_read_cpu_temp_reports_age(self):
        write_telemetry(self.p, [(datetime.now() - timedelta(seconds=30), 75)])
        temp, age = ib.read_cpu_temp(self.p)
        self.assertEqual(temp, 75.0)
        self.assertGreaterEqual(age, 29)

    def test_find_telemetry_prefers_newest_and_skips_renamed_old_files(self):
        old = os.path.join(self.dir.name, "sentinel_telemetry.old_20261101_155718.csv")
        fb = os.path.join(self.dir.name, "sentinel_telemetry_20261101_164004.csv")
        for p in (old, self.p, fb):
            write_telemetry(p, [(datetime.now(), 60)])
        os.utime(old, (time.time() + 100, time.time() + 100))   # newest, but retired
        os.utime(fb, (time.time() + 50, time.time() + 50))
        found = ib.find_telemetry(os.path.join(self.dir.name, "sentinel_telemetry*.csv"))
        self.assertEqual(found, fb)

    def test_bytes_to_reach(self):
        self.assertEqual(ib.bytes_to_reach(16 * 2**30, 57.0, 93.0), int(16 * 2**30 * 0.36))
        self.assertEqual(ib.bytes_to_reach(16 * 2**30, 95.0, 93.0), 0)

    def test_thresholds_read_from_config_without_executing_it(self):
        cfg = os.path.join(self.dir.name, "config.py")
        with open(cfg, "w") as f:
            f.write("RAM_PERCENT_LIMIT = 85\nCPU_TEMP_WARN = 70\nimport os\nos.remove('x')\n")
        th, src = ib.load_thresholds([cfg])
        self.assertEqual((th["RAM_PERCENT_LIMIT"], th["CPU_TEMP_WARN"], th["CPU_TEMP_CRITICAL"]),
                         (85, 70, 90))

    def test_run_log_appends_with_one_header(self):
        path = os.path.join(self.dir.name, "log.csv")
        for _ in range(2):
            log = ib.RunLog(path)
            log.event("ram-1", "start", "57%", "x")
            log.close()
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ib.LOG_FIELDS)
        self.assertEqual(len(rows), 3)


class CpuRunStateTests(unittest.TestCase):

    def make(self, **kw):
        base = dict(warn=80, crit=90, abort_temp=95, sustain=10, max_seconds=100)
        base.update(kw)
        return ib.CpuRunState(**base)

    def test_stops_once_the_breach_has_been_sustained(self):
        s = self.make()
        self.assertIsNone(s.update(70, 1, 2))
        for _ in range(4):
            self.assertIsNone(s.update(82, 1, 2))              # 8 s above warn
        self.assertEqual(s.update(82, 1, 2), "sustained breach achieved")
        self.assertEqual(s.peak, 82)

    def test_aborts_immediately_at_the_abort_temperature(self):
        s = self.make()
        self.assertIn("abort temperature", s.update(95, 1, 2))

    def test_stale_telemetry_stops_the_load_after_three_ticks(self):
        s = self.make()
        self.assertIsNone(s.update(70, 1, 2))
        self.assertIsNone(s.update(70, 60, 2))                 # row 60 s old
        self.assertIsNone(s.update(None, None, 2))
        self.assertIn("stale", s.update(70, 60, 2))

    def test_a_fresh_reading_resets_the_stale_count(self):
        s = self.make()
        s.update(70, 60, 2)
        s.update(70, 60, 2)
        self.assertIsNone(s.update(70, 1, 2))
        self.assertIsNone(s.update(70, 60, 2))                 # count restarted

    def test_time_limit(self):
        s = self.make(max_seconds=6)
        self.assertIsNone(s.update(60, 1, 2))
        self.assertIsNone(s.update(60, 1, 2))
        self.assertEqual(s.update(60, 1, 2), "time limit reached")

    def test_never_reaching_warn_is_reported_through_peak(self):
        s = self.make(max_seconds=4)
        s.update(66, 1, 2)
        self.assertEqual(s.update(68, 1, 2), "time limit reached")
        self.assertEqual((s.peak, s.above_warn), (68, 0.0))

    def test_force_mode_ignores_telemetry_and_uses_only_the_time_limit(self):
        s = self.make(watching=False, max_seconds=4)
        self.assertIsNone(s.update(None, None, 2))
        self.assertEqual(s.update(None, None, 2), "time limit reached")

    def test_critical_time_is_counted_separately(self):
        s = self.make(sustain=0)
        s.update(91, 1, 2)
        s.update(85, 1, 2)
        self.assertEqual((s.above_warn, s.above_crit), (4.0, 2.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
