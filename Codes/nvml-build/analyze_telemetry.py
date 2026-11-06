#!/usr/bin/env python3
"""
analyze_telemetry.py - audit sentinel_telemetry.csv.

Replays the raw readings in the CSV through an INDEPENDENT reference
implementation of the alert rules (written from the specification, not
copied from alerts.py) and compares what the rules say should have
happened, cycle by cycle, with what the tool actually logged.

Produces:
  * a Markdown report (printed, and saved as analysis_report.md)
  * analysis_mismatches.csv - every cycle where tool and reference disagree

Usage:
    python analyze_telemetry.py [file.csv ...] [--out DIR]
                                [--cooldown 60] [--clear-after 3]
                                [--tolerance 0.1] [--config PATH]

Thresholds are read from config.py (CPU_TEMP_WARN, CPU_TEMP_CRITICAL,
RAM_PERCENT_LIMIT, SSD_HEALTH_CRITICAL). --cooldown and --clear-after must
match AlertSystem's settings (60 and 3 by default).

What this does and does not show: it checks that the alert LOGIC responded
correctly to the readings the tool itself recorded. It does not validate
the accuracy of the sensors, and a channel that never breached during the
session cannot have its detection evaluated.
"""
import argparse
import ast
import csv
import glob
import os
import statistics
import sys
from collections import Counter
from datetime import datetime

SEV = {"GOOD": 0, "WARNING": 1, "CRITICAL": 2}
# alert channel name (as used in alert_events) -> CSV column
ALERT_CHANNELS = {"cpu_temp": "cpu_temp", "ram": "ram_percent", "ssd_health": "ssd_health"}
DISPLAY_ONLY = ["gpu_temp", "ssd_temp"]
ALL_COLUMNS = ["cpu_temp", "gpu_temp", "ssd_temp", "ssd_health", "ram_percent"]
DEFAULT_THRESHOLDS = {
    "CPU_TEMP_WARN": 80, "CPU_TEMP_CRITICAL": 90,
    "RAM_PERCENT_LIMIT": 90, "SSD_HEALTH_CRITICAL": 20,
}


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------
def load_thresholds(candidates):
    """Read numeric assignments from config.py without executing it."""
    th = dict(DEFAULT_THRESHOLDS)
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            found = []
            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id in th
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, (int, float))):
                    th[node.targets[0].id] = node.value.value
                    found.append(node.targets[0].id)
            missing = [k for k in th if k not in found]
            note = f"{path}"
            if missing:
                note += f" (not found there, defaults used: {', '.join(missing)})"
            return th, note
    return th, "built-in defaults (config.py not found)"


def _num(text):
    text = (text or "").strip()
    return None if text == "" else float(text)


def parse_events(text):
    """'cpu_temp:WARNING:sent;ram:recovered' -> {channel: (severity, outcome)}"""
    events = {}
    for part in (text or "").split(";"):
        bits = part.strip().split(":")
        if len(bits) == 3:
            events[bits[0]] = (bits[1], bits[2])
        elif len(bits) == 2:
            events[bits[0]] = (None, bits[1])
    return events


def parse_drives(text):
    """'name=health/temp|name=health/temp' -> {name: (health, temp)}"""
    out = {}
    for part in (text or "").split("|"):
        if "=" not in part:
            continue
        name, vals = part.rsplit("=", 1)
        h, _, t = vals.partition("/")
        out[name] = (_num(h), _num(t))
    return out


def read_rows(path):
    rows, bad = [], 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = f.readline()
        delim = "\t" if "\t" in head else ","
        f.seek(0)
        for rec in csv.DictReader(f, delimiter=delim):
            try:
                row = {"t": datetime.fromisoformat(rec["timestamp"].strip()),
                       "loop": int(rec["loop"]),
                       "events": parse_events(rec.get("alert_events")),
                       "alert_ms": _num(rec.get("alert_ms")) or 0.0,
                       "drives": parse_drives(rec.get("ssd_detail"))}
                for col in ALL_COLUMNS:
                    row[col] = _num(rec.get(col))
                rows.append(row)
            except (KeyError, ValueError, AttributeError):
                bad += 1
    return rows, bad


def split_sessions(rows):
    """The CSV is appended to on every run; a loop counter reset = new run."""
    sessions, cur = [], []
    for r in rows:
        if cur and (r["loop"] <= cur[-1]["loop"] or r["t"] < cur[-1]["t"]):
            sessions.append(cur)
            cur = []
        cur.append(r)
    if cur:
        sessions.append(cur)
    return sessions


# --------------------------------------------------------------------------
# Reference implementation of the alert rules
# --------------------------------------------------------------------------
def classify(channel, value, th):
    """Severity for one reading, or None if there is no valid reading."""
    if channel == "cpu_temp":
        if not value:                      # None or 0 = sensor locked
            return None
        if value >= th["CPU_TEMP_CRITICAL"]:
            return "CRITICAL"
        return "WARNING" if value >= th["CPU_TEMP_WARN"] else "GOOD"
    if value is None:
        return None
    if channel == "ram":
        return "WARNING" if value > th["RAM_PERCENT_LIMIT"] else "GOOD"
    if channel == "ssd_health":
        return "CRITICAL" if value < th["SSD_HEALTH_CRITICAL"] else "GOOD"
    raise ValueError(channel)


class Reference:
    """Per-channel alert state machine, from the written rules.

    The cooldown is tracked per severity: a level that alerted within the
    cooldown is suppressed even when the channel dipped to a lower level and
    came back (re-escalation). A toast at one severity also counts as a
    recent notice for every lower severity."""

    def __init__(self, cooldown, clear_after):
        self.cooldown, self.clear_after = cooldown, clear_after
        self.state = None            # {"sev", "sent_at": {sev: time}, "ok"}
        self._undo = None            # copy of sent_at before the last send, for adopt()

    def _mark_sent(self, severity, t):
        self._undo = dict(self.state["sent_at"])
        for name, rank in SEV.items():
            if 0 < rank <= SEV[severity]:
                self.state["sent_at"][name] = t

    def step(self, severity, t):
        """-> (outcome, reason, elapsed_seconds_or_None)"""
        if severity is None:
            return "none", "no valid reading", None
        if severity == "GOOD":
            if self.state is None:
                return "none", "good", None
            self.state["ok"] += 1
            if self.state["ok"] >= self.clear_after:
                self.state = None
                return "recovered", "", None
            return "none", "recovering", None
        if self.state is None:
            self.state = {"sev": severity, "sent_at": {}, "ok": 0}
            self._mark_sent(severity, t)
            return "sent", "first", None
        self.state["ok"] = 0
        old, new = SEV[self.state["sev"]], SEV[severity]
        if new < old:
            self.state["sev"] = severity
            return "deescalated", "", None
        self.state["sev"] = severity
        last = self.state["sent_at"].get(severity)
        if last is None:
            self._mark_sent(severity, t)
            return "sent", "escalation", None
        elapsed = (t - last).total_seconds()
        reason = "re-escalation" if new > old else "reminder"
        if elapsed < self.cooldown:
            return "suppressed", reason, elapsed
        self._mark_sent(severity, t)
        return "sent", reason, elapsed

    def adopt(self, actual, t):
        """Timing-boundary disagreement: follow what the tool did."""
        if self.state is None:
            return
        if actual == "sent":
            self._mark_sent(self.state["sev"], t)
        elif actual == "suppressed" and self._undo is not None:
            self.state["sent_at"] = dict(self._undo)

    @property
    def active(self):
        return self.state is not None


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def _new_channel():
    return {"cycles": 0, "valid": 0, "missing": 0, "breach": 0,
            "values": [], "confusion": Counter(), "coverage": Counter(),
            "failed": 0, "boundary": 0, "mismatches": [], "missing_in_alert": 0}


def analyse(sessions, th, cooldown=60.0, clear_after=3, tol=0.1):
    chans = {ch: _new_channel() for ch in ALERT_CHANNELS}
    for si, rows in enumerate(sessions, 1):
        refs = {ch: Reference(cooldown, clear_after) for ch in ALERT_CHANNELS}
        for r in rows:
            for ch, col in ALERT_CHANNELS.items():
                c, ref = chans[ch], refs[ch]
                value = r[col]
                sev = classify(ch, value, th)
                c["cycles"] += 1
                if sev is None:
                    c["missing"] += 1
                    if ref.active:
                        c["missing_in_alert"] += 1
                else:
                    c["valid"] += 1
                    c["values"].append(value)
                    if sev != "GOOD":
                        c["breach"] += 1
                expected, reason, elapsed = ref.step(sev, r["t"])
                actual = r["events"].get(ch)
                a_out = actual[1] if actual else "none"
                a_sev = actual[0] if actual else None
                if a_out == "failed":
                    c["failed"] += 1
                a_cmp = "sent" if a_out == "failed" else a_out

                if (a_cmp != expected and expected in ("sent", "suppressed")
                        and a_cmp in ("sent", "suppressed") and elapsed is not None
                        and abs(elapsed - cooldown) <= tol):
                    c["boundary"] += 1          # within timing tolerance
                    ref.adopt(a_cmp, r["t"])
                    expected = a_cmp
                elif a_cmp != expected:
                    c["mismatches"].append({
                        "session": si, "loop": r["loop"],
                        "timestamp": r["t"].isoformat(timespec="milliseconds"),
                        "channel": ch, "value": value, "severity": sev or "",
                        "expected": expected, "actual": a_out})
                elif (a_sev and sev and a_cmp in ("sent", "suppressed", "deescalated")
                        and a_sev != sev):
                    c["mismatches"].append({
                        "session": si, "loop": r["loop"],
                        "timestamp": r["t"].isoformat(timespec="milliseconds"),
                        "channel": ch, "value": value, "severity": sev,
                        "expected": f"{expected} ({sev})",
                        "actual": f"{a_out} ({a_sev})"})
                c["confusion"][(expected, a_cmp)] += 1
                if expected == "sent":
                    c["coverage"][f"alert sent: {reason}"] += 1
                elif expected == "suppressed":
                    c["coverage"]["suppressed (re-escalation)" if reason == "re-escalation"
                                  else "suppressed"] += 1
                elif expected in ("deescalated", "recovered"):
                    c["coverage"][expected] += 1
    for c in chans.values():
        conf = c["confusion"]
        c["expected_alerts"] = sum(n for (e, _), n in conf.items() if e == "sent")
        c["detected"] = conf[("sent", "sent")]
        c["missed"] = c["expected_alerts"] - c["detected"]
        c["false_alerts"] = sum(n for (e, a), n in conf.items() if a == "sent" and e != "sent")
        c["negatives"] = c["valid"] - c["expected_alerts"]
        event_cycles = sum(n for (e, a), n in conf.items() if e != "none" or a != "none")
        c["event_cycles"] = event_cycles
        c["event_matches"] = sum(n for (e, a), n in conf.items() if e == a and e != "none")
    return chans


def sampling_stats(sessions):
    gaps, loop_jumps = [], 0
    for rows in sessions:
        for a, b in zip(rows, rows[1:]):
            gaps.append((b["t"] - a["t"]).total_seconds())
            if b["loop"] != a["loop"] + 1:
                loop_jumps += 1
    return gaps, loop_jumps


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def ratio(n, d):
    return f"{n}/{d} = {n / d:.1%}" if d else "n/a"


def render(path, rows, bad, sessions, th, th_source, chans, args):
    L = []
    L.append("# Telemetry analysis report")
    L.append("")
    span = (rows[-1]["t"] - rows[0]["t"]).total_seconds() if rows else 0
    L.append(f"- Input: `{path}`")
    L.append(f"- Rows analysed: {len(rows)} in {len(sessions)} session(s); "
             f"unreadable rows skipped: {bad}")
    L.append(f"- Span: {rows[0]['t']:%Y-%m-%d %H:%M:%S} to {rows[-1]['t']:%Y-%m-%d %H:%M:%S} "
             f"({span / 3600:.2f} h wall-clock, includes gaps between sessions)" if rows else "- Span: n/a")
    L.append(f"- Thresholds: {th_source}")
    L.append(f"  - CPU warn >= {th['CPU_TEMP_WARN']}, CPU critical >= {th['CPU_TEMP_CRITICAL']}, "
             f"RAM warn > {th['RAM_PERCENT_LIMIT']}, SSD life critical < {th['SSD_HEALTH_CRITICAL']}")
    L.append(f"- Reference rules: cooldown {args.cooldown:g} s, recovery after "
             f"{args.clear_after} consecutive normal readings, timing tolerance {args.tolerance:g} s")
    L.append("")

    gaps, jumps = sampling_stats(sessions)
    L.append("## Sampling period")
    if gaps:
        med = statistics.median(gaps)
        L += md_table(["Intervals", "Mean (s)", "Median (s)", "Min (s)", "p95 (s)", "Max (s)"],
                      [[len(gaps), f"{statistics.mean(gaps):.2f}", f"{med:.2f}",
                        f"{min(gaps):.2f}", f"{pct(gaps, 95):.2f}", f"{max(gaps):.2f}"]])
        big = sum(1 for g in gaps if g > 3 * med)
        L.append("")
        L.append(f"Loop-number discontinuities within sessions: {jumps}; "
                 f"intervals longer than 3x the median: {big}.")
    else:
        L.append("Not enough rows.")
    L.append("")

    L.append("## Alert channels: summary")
    summary = []
    for ch, c in chans.items():
        summary.append([ch, c["valid"], c["missing"], c["breach"], c["expected_alerts"],
                        c["detected"], c["missed"], c["false_alerts"],
                        ratio(c["detected"], c["expected_alerts"]) if c["expected_alerts"] else "NOT EXERCISED",
                        ratio(c["event_matches"], c["event_cycles"])])
    L += md_table(["Channel", "Valid cycles", "No reading", "Breach cycles", "Expected alerts",
                   "Detected", "Missed", "False alerts", "Detection rate", "Event agreement"], summary)
    L.append("")
    L.append("*Expected alerts* are the toasts the reference rules say should have been shown "
             "(first breach, escalation, or reminder/re-escalation after the cooldown). *Event agreement* counts cycles "
             "where either side logged an event and both agree. A channel marked NOT EXERCISED "
             "never breached its threshold in this data, so its detection is untested here.")
    L.append("")

    for ch, c in chans.items():
        L.append(f"### {ch}")
        if c["valid"]:
            v = c["values"]
            L.append(f"Observed range {min(v):g} to {max(v):g} (mean {statistics.mean(v):.1f}).")
        if c["expected_alerts"]:
            neg = c["negatives"]
            L.append(f"Detection {ratio(c['detected'], c['expected_alerts'])}; false alerts "
                     f"{c['false_alerts']} in {neg} cycles with no alert expected "
                     f"({c['false_alerts'] / neg:.2%})." if neg else
                     f"Detection {ratio(c['detected'], c['expected_alerts'])}.")
        L.append(f"Delivery failures logged: {c['failed']}; timing-boundary cases accepted: "
                 f"{c['boundary']}; no-reading cycles during an active alert: {c['missing_in_alert']}.")
        if c["coverage"]:
            L.append("")
            L += md_table(["Rule path exercised (per reference)", "Cycles"],
                          [[k, n] for k, n in sorted(c["coverage"].items())])
        interesting = [(k, n) for k, n in sorted(c["confusion"].items()) if k != ("none", "none")]
        if interesting:
            L.append("")
            L += md_table(["Reference expected", "Tool logged", "Cycles"],
                          [[e, a, n] for (e, a), n in interesting])
        L.append("")

    lat = [r["alert_ms"] for r in rows
           if any(o in ("sent", "failed") for _, o in r["events"].values())]
    L.append("## Alert processing time (alert_ms, cycles that showed a toast)")
    if lat:
        L += md_table(["Cycles", "Mean (ms)", "Median (ms)", "p95 (ms)", "Max (ms)"],
                      [[len(lat), f"{statistics.mean(lat):.1f}", f"{statistics.median(lat):.1f}",
                        f"{pct(lat, 95):.1f}", f"{max(lat):.1f}"]])
        bound = (max(gaps) if gaps else 0) + max(lat) / 1000
        L.append("")
        L.append(f"Worst-case threshold-crossing-to-toast bound in this data: one sampling interval "
                 f"({max(gaps):.2f} s) plus processing ({max(lat):.0f} ms) = {bound:.2f} s."
                 if gaps else "")
        L.append("alert_ms covers the whole alert check for the cycle, including toast display.")
    else:
        L.append("No cycle in this data showed a toast, so there is nothing to measure.")
    L.append("")

    drive_stats = {}
    for r in rows:
        for name, (h, t) in r["drives"].items():
            d = drive_stats.setdefault(name, {"h": [], "t": []})
            if h is not None:
                d["h"].append(h)
            if t is not None:
                d["t"].append(t)
    if drive_stats:
        L.append("## SSD drives (per-drive readings; the ssd_health channel uses the lowest)")
        def rng(v):
            return f"{min(v):g} / {statistics.mean(v):.1f} / {max(v):g}" if v else "no reading"
        L += md_table(["Drive", "Health min / mean / max (%)", "Temp min / mean / max (C)"],
                      [[n, rng(d["h"]), rng(d["t"])] for n, d in sorted(drive_stats.items())])
        L.append("")

    L.append("## Monitored but not alerting (display only)")
    disp = []
    for col in DISPLAY_ONLY:
        vals = [r[col] for r in rows if r[col] is not None]
        miss = len(rows) - len(vals)
        if vals:
            res = "whole numbers" if all(float(x).is_integer() for x in vals) else "decimals"
            disp.append([col, len(vals), miss, f"{min(vals):g}", f"{statistics.mean(vals):.1f}",
                         f"{max(vals):g}", res])
        else:
            disp.append([col, 0, miss, "", "", "", ""])
    L += md_table(["Channel", "Readings", "No reading", "Min", "Mean", "Max", "Resolution"], disp)
    L.append("")

    total_mis = sum(len(c["mismatches"]) for c in chans.values())
    L.append("## Disagreements between tool and reference")
    if total_mis == 0:
        L.append("None. Every logged alert event matches the reference rules.")
    else:
        first = sorted((m for c in chans.values() for m in c["mismatches"]),
                       key=lambda m: (m["session"], m["loop"]))[:20]
        L.append(f"{total_mis} cycle(s) disagree (full list in analysis_mismatches.csv). "
                 "One logic defect can cascade into later cycles, so start with the earliest:")
        L.append("")
        L += md_table(["Session", "Loop", "Time", "Channel", "Value", "Expected", "Logged"],
                      [[m["session"], m["loop"], m["timestamp"], m["channel"], m["value"],
                        m["expected"], m["actual"]] for m in first])
    L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit sentinel_telemetry.csv against the alert rules.")
    ap.add_argument("csv", nargs="*", help="one or more telemetry CSVs; wildcards allowed "
                    "(default: sentinel_telemetry.csv)")
    ap.add_argument("--out", default=None, help="folder for the report files (default: next to the CSV)")
    ap.add_argument("--cooldown", type=float, default=60.0)
    ap.add_argument("--clear-after", type=int, default=3)
    ap.add_argument("--tolerance", type=float, default=0.1,
                    help="seconds either side of the cooldown boundary treated as a timing tie")
    ap.add_argument("--config", default=None, help="path to config.py")
    args = ap.parse_args(argv)

    paths, skipped = [], []
    for pattern in (args.csv or ["sentinel_telemetry.csv"]):
        if any(c in pattern for c in "*?["):
            for hit in sorted(glob.glob(pattern)):
                # Files the logger renamed aside (old column layout / old code) are
                # not part of a wildcard match; name them explicitly to analyse them.
                (skipped if ".old_" in os.path.basename(hit) else paths).append(hit)
        else:
            paths.append(pattern)
    if skipped:
        print(f"Note: skipped retired file(s) matched by the wildcard: {', '.join(skipped)} "
              f"(name a file explicitly to analyse it)\n")
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing or not paths:
        sys.exit(f"CSV not found: {', '.join(missing) or '(no files matched)'}")
    rows, sessions, bad = [], [], 0
    for p in paths:
        file_rows, file_bad = read_rows(p)
        bad += file_bad
        rows += file_rows
        sessions += split_sessions(file_rows)
    if not rows:
        sys.exit("No readable rows in the CSV.")
    rows.sort(key=lambda r: r["t"])
    sessions.sort(key=lambda s: s[0]["t"])
    first_dir = os.path.dirname(os.path.abspath(paths[0]))
    here = os.path.dirname(os.path.abspath(__file__))
    th, th_source = load_thresholds([args.config,
                                     os.path.join(first_dir, "config.py"),
                                     os.path.join(here, "config.py"),
                                     os.path.join(os.getcwd(), "config.py")])
    chans = analyse(sessions, th, args.cooldown, args.clear_after, args.tolerance)
    report = render(", ".join(paths), rows, bad, sessions, th, th_source, chans, args)

    out_dir = args.out or first_dir
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "analysis_report.md"), "w", encoding="utf-8") as f:
        f.write(report + "\n")
    mism = [m for c in chans.values() for m in c["mismatches"]]
    with open(os.path.join(out_dir, "analysis_mismatches.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["session", "loop", "timestamp", "channel", "value",
                                          "severity", "expected", "actual"])
        w.writeheader()
        w.writerows(sorted(mism, key=lambda m: (m["session"], m["loop"])))
    print(report)
    print(f"\nSaved: {os.path.join(out_dir, 'analysis_report.md')} and analysis_mismatches.csv")


if __name__ == "__main__":
    main()
