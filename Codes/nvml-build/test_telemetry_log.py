import builtins
import csv
import os
import tempfile
import unittest
from unittest import mock

from telemetry_log import TelemetryLog


class TelemetryLogTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.csv")

    def tearDown(self):
        self.dir.cleanup()

    def rows(self):
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_header_once_and_none_written_as_empty(self):
        t = TelemetryLog(self.path)
        t.write("2026-10-31T12:00:00.000", 1,
                {"cpu_temp": 83, "gpu_temp": None, "ssd_temp": 41,
                 "ssd_health": 85, "ram_percent": 55.2,
                 "ssd_drives": [{"name": "WDC-512G (C:)", "health": 85.0, "temp": 53.0},
                                {"name": "WDC-1T00", "health": 99.0, "temp": None}]},
                ["cpu_temp:WARNING:sent"], 12.34)
        t.close()
        rows = self.rows()
        self.assertEqual(rows[0], TelemetryLog.FIELDS)
        self.assertEqual(rows[1], ["2026-10-31T12:00:00.000", "1", "83", "",
                                   "41", "85", "55.2", "cpu_temp:WARNING:sent", "12.3",
                                   "WDC-512G (C:)=85/53|WDC-1T00=99/"])

    def test_reopen_appends_without_second_header(self):
        for n in (1, 2):
            t = TelemetryLog(self.path)
            t.write("ts", n, {}, [], 0.0)
            t.close()
        rows = self.rows()
        self.assertEqual(len(rows), 3)                 # header + 2 data rows
        self.assertEqual(rows.count(TelemetryLog.FIELDS), 1)

    def test_multiple_events_joined(self):
        t = TelemetryLog(self.path)
        t.write("ts", 1, {}, ["cpu_temp:WARNING:sent", "ram:WARNING:suppressed"], 1.0)
        t.close()
        self.assertEqual(self.rows()[1][7], "cpu_temp:WARNING:sent;ram:WARNING:suppressed")

    def test_old_column_layout_is_retired_not_mixed_into(self):
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            f.write("timestamp,loop,cpu_temp,gpu_temp,ssd_temp,ssd_health,ram_percent,alert_events,alert_ms\n")
            f.write("2026-11-01T15:57:18.272,1,57,58.7,52,99,57.3,,0\n")
        t = TelemetryLog(self.path)
        t.write("ts", 1, {}, [], 0.0)
        t.close()
        self.assertEqual(self.rows()[0], TelemetryLog.FIELDS)       # fresh file, new header
        self.assertEqual(len(self.rows()), 2)
        kept = [n for n in os.listdir(self.dir.name) if ".old_" in n]
        self.assertEqual(len(kept), 1)                              # the old data survives
        with open(os.path.join(self.dir.name, kept[0]), encoding="utf-8") as f:
            self.assertIn("57.3", f.read())

    def test_unwritable_path_never_raises(self):
        with self.assertLogs(level="ERROR"):           # failure is logged, not raised
            t = TelemetryLog(os.path.join(self.dir.name, "no_such_dir", "t.csv"))
        t.write("ts", 1, {}, [], 0.0)                  # must not raise
        t.close()

    def test_write_after_close_never_raises(self):
        t = TelemetryLog(self.path)
        t.close()
        t.write("ts", 1, {}, [], 0.0)


    def test_locked_old_file_falls_back_to_a_new_file_and_keeps_logging(self):
        # Reproduces the real failure: an old-layout CSV that Windows will not rename
        # because another program (Excel) has it open.
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            f.write("timestamp,loop,cpu_temp,gpu_temp,ssd_temp,ssd_health,ram_percent,alert_events,alert_ms\n")
            f.write("2026-11-01T15:57:18.272,1,57,58.7,52,99,57.3,,0\n")
        with mock.patch("telemetry_log.os.replace", side_effect=PermissionError(13, "in use")):
            t = TelemetryLog(self.path)
        self.assertTrue(t.active)
        self.assertNotEqual(t.path, self.path)
        t.write("ts", 1, {"cpu_temp": 60}, [], 0.0)
        t.close()
        with open(t.path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], TelemetryLog.FIELDS)
        self.assertEqual(len(rows), 2)
        with open(self.path, encoding="utf-8") as f:                 # original untouched
            self.assertIn("57.3", f.read())

    def test_locked_current_file_falls_back_to_a_new_file(self):
        t = TelemetryLog(self.path)
        t.close()
        real_open = builtins.open

        def locked_for_append(path, *a, **k):
            if path == self.path and a and a[0] == "a":
                raise PermissionError(13, "in use")
            return real_open(path, *a, **k)

        with mock.patch("telemetry_log.open", side_effect=locked_for_append, create=True):
            t2 = TelemetryLog(self.path)
        self.assertTrue(t2.active)
        self.assertNotEqual(t2.path, self.path)
        self.assertEqual(t2.requested_path, self.path)
        t2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
