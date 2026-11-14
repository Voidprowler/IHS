import argparse
import contextlib
import csv
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import analyze_telemetry as at

T0 = datetime(2026, 11, 1, 12, 0, 0)
TH = dict(at.DEFAULT_THRESHOLDS)
HEADER = ["timestamp", "loop", "cpu_temp", "gpu_temp", "ssd_temp",
          "ssd_health", "ram_percent", "alert_events", "alert_ms"]


def write_csv(path, specs, delimiter=","):
    """specs: (seconds, loop, cpu, ram, ssd_health, events)"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(HEADER)
        for sec, loop, cpu, ram, ssd, ev in specs:
            ts = (T0 + timedelta(seconds=sec)).isoformat(timespec="milliseconds")
            w.writerow([ts, loop, "" if cpu is None else cpu, 58.7, 52,
                        "" if ssd is None else ssd, "" if ram is None else ram, ev, 0.5])


class AnalyzerTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.csv")

    def tearDown(self):
        self.dir.cleanup()

    def run_specs(self, specs, **kw):
        write_csv(self.path, specs)
        rows, bad = at.read_rows(self.path)
        sessions = at.split_sessions(rows)
        chans = at.analyse(sessions, TH, kw.get("cooldown", 60.0),
                           kw.get("clear_after", 3), kw.get("tol", 0.1))
        return chans, sessions, rows, bad

    @staticmethod
    def sustained(first_events=None):
        """cpu 83C for 62 s at 2 s steps, then 3 normal readings."""
        specs = []
        for i, sec in enumerate(range(0, 64, 2), 1):
            ev = "cpu_temp:WARNING:sent" if sec in (0, 60) else "cpu_temp:WARNING:suppressed"
            if sec == 0 and first_events is not None:
                ev = first_events
            specs.append((sec, i, 83, 50, 99, ev))
        n = len(specs)
        for k, sec in enumerate((64, 66, 68), 1):
            specs.append((sec, n + k, 60, 50, 99, "cpu_temp:recovered" if k == 3 else ""))
        return specs

    def test_correct_log_has_no_disagreement(self):
        chans, *_ = self.run_specs(self.sustained())
        c = chans["cpu_temp"]
        self.assertEqual((c["expected_alerts"], c["detected"], c["missed"], c["false_alerts"]),
                         (2, 2, 0, 0))
        self.assertEqual(c["mismatches"], [])
        self.assertEqual(c["coverage"]["alert sent: first"], 1)
        self.assertEqual(c["coverage"]["alert sent: reminder"], 1)
        self.assertEqual(c["coverage"]["recovered"], 1)
        self.assertEqual(chans["ram"]["expected_alerts"], 0)
        self.assertEqual(chans["ssd_health"]["breach"], 0)

    def test_missed_alert_is_detected(self):
        chans, *_ = self.run_specs(self.sustained(first_events=""))
        c = chans["cpu_temp"]
        self.assertEqual((c["expected_alerts"], c["detected"], c["missed"]), (2, 1, 1))
        first = c["mismatches"][0]
        self.assertEqual((first["expected"], first["actual"]), ("sent", "none"))

    def test_false_alert_is_detected(self):
        specs = [(0, 1, 60, 50, 99, "cpu_temp:WARNING:sent")]
        chans, *_ = self.run_specs(specs)
        self.assertEqual(chans["cpu_temp"]["false_alerts"], 1)
        self.assertEqual(len(chans["cpu_temp"]["mismatches"]), 1)

    def test_cooldown_boundary_tie_is_not_an_error(self):
        specs = [(0, 1, 83, 50, 99, "cpu_temp:WARNING:sent"),
                 (59.95, 2, 83, 50, 99, "cpu_temp:WARNING:sent"),      # 0.05 s early
                 (61.95, 3, 83, 50, 99, "cpu_temp:WARNING:suppressed")]
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(c["boundary"], 1)
        self.assertEqual(c["mismatches"], [])

    def test_clearly_early_resend_is_an_error(self):
        specs = [(0, 1, 83, 50, 99, "cpu_temp:WARNING:sent"),
                 (30, 2, 83, 50, 99, "cpu_temp:WARNING:sent")]
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(len(c["mismatches"]), 1)

    def test_restart_resets_reference_state(self):
        specs = [(0, 1, 83, 50, 99, "cpu_temp:WARNING:sent"),
                 (10, 1, 83, 50, 99, "cpu_temp:WARNING:sent")]         # loop reset = new run
        chans, sessions, *_ = self.run_specs(specs)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(chans["cpu_temp"]["mismatches"], [])

    def test_escalation_deescalation_and_suppressed_reescalation(self):
        specs = [(0, 1, 83, 50, 99, "cpu_temp:WARNING:sent"),
                 (2, 2, 92, 50, 99, "cpu_temp:CRITICAL:sent"),
                 (4, 3, 85, 50, 99, "cpu_temp:WARNING:deescalated"),
                 (6, 4, 93, 50, 99, "cpu_temp:CRITICAL:suppressed")]   # inside the cooldown
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(c["mismatches"], [])
        self.assertEqual(c["coverage"]["alert sent: escalation"], 1)
        self.assertEqual(c["coverage"]["deescalated"], 1)
        self.assertEqual(c["coverage"]["suppressed (re-escalation)"], 1)

    def test_reescalation_after_the_cooldown_is_expected_to_send(self):
        specs = [(0, 1, 92, 50, 99, "cpu_temp:CRITICAL:sent"),
                 (2, 2, 85, 50, 99, "cpu_temp:WARNING:deescalated"),
                 (62, 3, 93, 50, 99, "cpu_temp:CRITICAL:sent")]
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(c["mismatches"], [])
        self.assertEqual(c["coverage"]["alert sent: re-escalation"], 1)

    def test_old_behaviour_of_toasting_every_reescalation_is_flagged(self):
        specs = [(0, 1, 92, 50, 99, "cpu_temp:CRITICAL:sent"),
                 (2, 2, 85, 50, 99, "cpu_temp:WARNING:deescalated"),
                 (4, 3, 93, 50, 99, "cpu_temp:CRITICAL:sent")]         # what the old code did
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(len(c["mismatches"]), 1)
        self.assertEqual((c["mismatches"][0]["expected"], c["mismatches"][0]["actual"]),
                         ("suppressed", "sent"))
        self.assertEqual(c["false_alerts"], 1)

    def test_wrong_severity_is_flagged(self):
        specs = [(0, 1, 92, 50, 99, "cpu_temp:WARNING:sent")]          # 92C is CRITICAL
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(len(c["mismatches"]), 1)

    def test_missing_reading_does_not_reset_cooldown(self):
        specs = [(0, 1, 83, 50, 99, "cpu_temp:WARNING:sent")]
        specs += [(2 * i, i + 1, None, 50, 99, "") for i in range(1, 6)]
        specs += [(12, 7, 83, 50, 99, "cpu_temp:WARNING:suppressed")]
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual(c["mismatches"], [])
        self.assertEqual(c["missing_in_alert"], 5)

    def test_ssd_zero_percent_is_a_valid_critical_reading(self):
        specs = [(0, 1, 60, 50, 0, "ssd_health:CRITICAL:sent")]
        c = self.run_specs(specs)[0]["ssd_health"]
        self.assertEqual((c["expected_alerts"], c["detected"], c["mismatches"]), (1, 1, []))

    def test_failed_delivery_counts_as_detected_but_is_reported(self):
        specs = [(0, 1, 83, 50, 99, "cpu_temp:WARNING:failed")]
        c = self.run_specs(specs)[0]["cpu_temp"]
        self.assertEqual((c["detected"], c["failed"], c["mismatches"]), (1, 1, []))

    def test_tab_delimited_input_is_read(self):
        write_csv(self.path, self.sustained(), delimiter="\t")
        rows, bad = at.read_rows(self.path)
        self.assertEqual((len(rows), bad), (35, 0))

    def test_report_marks_unexercised_channels(self):
        chans, sessions, rows, bad = self.run_specs(self.sustained())
        args = argparse.Namespace(cooldown=60.0, clear_after=3, tolerance=0.1)
        text = at.render(self.path, rows, bad, sessions, TH, "test", chans, args)
        self.assertIn("NOT EXERCISED", text)
        self.assertIn("100.0%", text)


    def test_per_drive_detail_is_parsed_and_reported(self):
        parsed = at.parse_drives("WDC-512G (C:)=85/53|WDC-1T00=99/")
        self.assertEqual(parsed["WDC-512G (C:)"], (85.0, 53.0))
        self.assertEqual(parsed["WDC-1T00"], (99.0, None))
        write_csv(self.path, self.sustained())
        with open(self.path, newline="", encoding="utf-8") as f:
            lines = f.read().splitlines()
        lines[0] += ",ssd_detail"
        lines[1:] = [l + ",WDC-512G (C:)=85/53|WDC-1T00=99/49" for l in lines[1:]]
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        rows, bad = at.read_rows(self.path)
        sessions = at.split_sessions(rows)
        chans = at.analyse(sessions, TH)
        args = argparse.Namespace(cooldown=60.0, clear_after=3, tolerance=0.1)
        text = at.render(self.path, rows, bad, sessions, TH, "test", chans, args)
        self.assertIn("SSD drives", text)
        self.assertIn("WDC-512G (C:)", text)


    def test_several_files_are_combined_as_separate_sessions(self):
        p1 = os.path.join(self.dir.name, "a.csv")
        p2 = os.path.join(self.dir.name, "b.csv")
        write_csv(p1, [(0, 1, 83, 50, 99, "cpu_temp:WARNING:sent")])
        write_csv(p2, [(10, 1, 83, 50, 99, "cpu_temp:WARNING:sent")])    # a new run, new file
        out = os.path.join(self.dir.name, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            at.main([p1, p2, "--out", out])
        with open(os.path.join(out, "analysis_report.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("2 session(s)", text)
        self.assertIn("Every logged alert event matches", text)

    def test_wildcards_are_expanded(self):
        for name in ("run1.csv", "run2.csv"):
            write_csv(os.path.join(self.dir.name, name), [(0, 1, 60, 50, 99, "")])
        out = os.path.join(self.dir.name, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            at.main([os.path.join(self.dir.name, "run*.csv"), "--out", out])
        with open(os.path.join(out, "analysis_report.md"), encoding="utf-8") as f:
            self.assertIn("2 session(s)", f.read())

    def test_wildcards_skip_retired_old_files_unless_named(self):
        good = os.path.join(self.dir.name, "sentinel_telemetry.csv")
        old = os.path.join(self.dir.name, "sentinel_telemetry.old_20261101_160050.csv")
        write_csv(good, [(0, 1, 60, 50, 99, "")])
        write_csv(old, [(0, 1, 60, 50, 99, "")])
        out = os.path.join(self.dir.name, "out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            at.main([os.path.join(self.dir.name, "sentinel_telemetry*.csv"), "--out", out])
        with open(os.path.join(out, "analysis_report.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("1 session(s)", text)
        self.assertIn("skipped retired", buf.getvalue())
        out2 = os.path.join(self.dir.name, "out2")
        with contextlib.redirect_stdout(io.StringIO()):
            at.main([old, "--out", out2])                       # named explicitly: analysed
        with open(os.path.join(out2, "analysis_report.md"), encoding="utf-8") as f:
            self.assertIn("1 session(s)", f.read())


    def test_a_critical_toast_covers_a_following_warning(self):
        specs = [(0, 1, 92, 50, 99, "cpu_temp:CRITICAL:sent"),
                 (2, 2, 85, 50, 99, "cpu_temp:WARNING:deescalated"),
                 (4, 3, 85, 50, 99, "cpu_temp:WARNING:suppressed")]
        self.assertEqual(self.run_specs(specs)[0]["cpu_temp"]["mismatches"], [])
        specs[2] = (4, 3, 85, 50, 99, "cpu_temp:WARNING:sent")                  # old behaviour
        self.assertEqual(len(self.run_specs(specs)[0]["cpu_temp"]["mismatches"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
