#!/usr/bin/env python3
"""
induce_breach.py - cause real, controlled RAM and CPU-temperature breaches
so the Sentinel's alert logic can be tested against genuine conditions.

Start the Sentinel FIRST (it writes sentinel_telemetry.csv), then in a
second terminal:

    python induce_breach.py ram --dry-run          # show the plan, change nothing
    python induce_breach.py ram                    # push RAM above the limit, hold, release
    python induce_breach.py cpu --dry-run
    python induce_breach.py cpu                    # load all cores until a sustained breach

Every phase start/stop is written, with millisecond timestamps, to
induce_breach_log.csv so you can line it up with the telemetry rows and
state in the report exactly how each breach was induced.

Safety (all enforced in code):
  RAM  - never above --max-percent (default 96%) or below 600 MB free;
         memory is released on any exit, including Ctrl+C.
  CPU  - the temperature is read from the Sentinel's own telemetry file;
         the load stops at --abort-temp (default 95 C), when the telemetry
         goes stale, or when the sustained-breach goal is reached.
         Use a hard flat surface, keep the laptop plugged in, and do NOT
         block the vents.
"""
import argparse
import ast
import csv
import gc
import glob
import multiprocessing
import os
import sys
import time
from datetime import datetime

LOG_FIELDS = ["timestamp", "phase", "event", "value", "detail"]
DEFAULTS = {"RAM_PERCENT_LIMIT": 90, "CPU_TEMP_WARN": 80, "CPU_TEMP_CRITICAL": 90}
MIN_FREE_BYTES = 600 * 1024 * 1024


def now_iso():
    return datetime.now().isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------
# Small, testable helpers
# --------------------------------------------------------------------------
class RunLog:
    """Phase log: printed, and appended to a CSV."""

    def __init__(self, path):
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._f = open(path, "a", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        if new:
            self._w.writerow(LOG_FIELDS)
            self._f.flush()

    def event(self, phase, event, value="", detail=""):
        ts = now_iso()
        self._w.writerow([ts, phase, event, value, detail])
        self._f.flush()
        print(f"[{ts[11:23]}] {phase:<8} {event:<18} {value} {detail}".rstrip())

    def close(self):
        self._f.close()


def load_thresholds(candidates):
    th = dict(DEFAULTS)
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id in th
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, (int, float))):
                    th[node.targets[0].id] = node.value.value
            return th, path
    return th, "built-in defaults"


def find_telemetry(pattern):
    """Newest telemetry CSV matching the pattern, ignoring renamed .old_ files."""
    files = [p for p in glob.glob(pattern) if ".old_" not in os.path.basename(p)]
    return max(files, key=os.path.getmtime) if files else None


def latest_row(path, tail_bytes=8192):
    """Last COMPLETE row of the telemetry CSV as a dict, or None."""
    try:
        with open(path, "rb") as f:
            header = f.readline().decode("utf-8-sig").strip()
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    lines = [l for l in tail.splitlines() if l.strip()]
    if not header or not lines or lines[-1].strip() == header:
        return None
    fields = header.split(",")
    try:
        row = next(csv.reader([lines[-1]]))
    except (csv.Error, StopIteration):
        return None
    return dict(zip(fields, row)) if len(row) == len(fields) else None


def read_cpu_temp(path):
    """-> (cpu_temp or None, age_in_seconds or None) from the newest telemetry row."""
    row = latest_row(path) if path else None
    if not row:
        return None, None
    try:
        age = (datetime.now() - datetime.fromisoformat(row["timestamp"])).total_seconds()
        temp = float(row["cpu_temp"]) if row.get("cpu_temp") else None
    except (KeyError, ValueError):
        return None, None
    return temp, age


def bytes_to_reach(total, current_percent, target_percent):
    return max(0, int(total * (target_percent - current_percent) / 100))


class CpuRunState:
    """Decides when the CPU load must stop. Pure logic, no I/O."""

    def __init__(self, warn, crit, abort_temp, sustain, max_seconds,
                 watching=True, max_age=20.0, stale_limit=3):
        self.warn, self.crit, self.abort_temp = warn, crit, abort_temp
        self.sustain, self.max_seconds = sustain, max_seconds
        self.watching, self.max_age, self.stale_limit = watching, max_age, stale_limit
        self.elapsed = 0.0
        self.peak = None
        self.above_warn = 0.0
        self.above_crit = 0.0
        self._stale = 0

    def update(self, temp, age, dt):
        """Feed one observation; returns a stop reason string, or None to continue."""
        self.elapsed += dt
        if self.watching:
            if temp is None or age is None or age > self.max_age:
                self._stale += 1
                if self._stale >= self.stale_limit:
                    return "telemetry went stale - stopped for safety"
            else:
                self._stale = 0
                self.peak = temp if self.peak is None else max(self.peak, temp)
                if temp >= self.warn:
                    self.above_warn += dt
                if temp >= self.crit:
                    self.above_crit += dt
                if temp >= self.abort_temp:
                    return f"abort temperature ({self.abort_temp:g} C) reached"
                if self.sustain and self.above_warn >= self.sustain:
                    return "sustained breach achieved"
        if self.elapsed >= self.max_seconds:
            return "time limit reached"
        return None


# --------------------------------------------------------------------------
# RAM
# --------------------------------------------------------------------------
def _allocate_until(blocks, psutil, target, max_percent, chunk):
    """Grow `blocks` until RAM% >= target. Returns None, or a reason it had to stop."""
    while True:
        vm = psutil.virtual_memory()
        if vm.percent >= target:
            return None
        if vm.percent >= max_percent or vm.available < MIN_FREE_BYTES:
            return f"safety limit hit at {vm.percent:.1f}% ({vm.available // 2**20} MB free)"
        try:
            blocks.append(bytearray(b"\xa5") * chunk)      # filled, so pages are really used
        except MemoryError:
            return "MemoryError"


def run_ram(args, th, log, psutil):
    limit = th["RAM_PERCENT_LIMIT"]
    target = args.target if args.target is not None else limit + 3
    chunk = args.chunk_mb * 2**20
    peak = 0.0
    for cycle in range(1, args.repeat + 1):
        phase = f"ram-{cycle}"
        blocks, above, reason = [], 0.0, None
        try:
            log.event(phase, "start", f"{psutil.virtual_memory().percent:.1f}%",
                      f"target {target:g}% (limit {limit:g}%), hold {args.hold:g}s")
            reason = _allocate_until(blocks, psutil, target, args.max_percent, chunk)
            vm = psutil.virtual_memory()
            peak = max(peak, vm.percent)
            if reason:
                log.event(phase, "ABORT", f"{vm.percent:.1f}%", reason)
            else:
                log.event(phase, "target_reached", f"{vm.percent:.1f}%",
                          f"holding {len(blocks) * args.chunk_mb} MB")
                end = time.time() + args.hold
                while time.time() < end:
                    time.sleep(1)
                    vm = psutil.virtual_memory()
                    peak = max(peak, vm.percent)
                    if vm.percent > limit:
                        above += 1
                    if vm.percent < target - 0.5:           # OS compressed/paged some: top up
                        reason = _allocate_until(blocks, psutil, target, args.max_percent, chunk)
                        if reason:
                            log.event(phase, "ABORT", f"{vm.percent:.1f}%", reason)
                            break
                log.event(phase, "hold_end", f"{psutil.virtual_memory().percent:.1f}%",
                          f"~{above:.0f}s above {limit:g}%")
        finally:
            blocks.clear()
            gc.collect()
            log.event(phase, "released", f"{psutil.virtual_memory().percent:.1f}%")
        if reason:
            break
        if cycle < args.repeat:
            _wait(log, phase, args.gap, lambda: f"{psutil.virtual_memory().percent:.1f}%")
    log.event("ram", "summary", f"peak {peak:.1f}%", f"limit {limit:g}%")
    print(f"\nExpected in sentinel_telemetry.csv: ram:WARNING:sent on the first cycle above {limit:g}%, "
          f"ram:WARNING:suppressed while it stays high, a reminder 'sent' about every 60 s, and "
          f"ram:recovered about 3 readings (~8 s) after release.")


def _wait(log, phase, seconds, probe):
    log.event(phase, "wait", "", f"{seconds:g}s for recovery")
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(5, max(0.0, end - time.time())))
        log.event(phase, "idle", probe())


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------
def _burn(stop):
    x = 1.0001
    while not stop.is_set():
        for _ in range(200_000):
            x = (x * 1.0000001 + 1e-7) % 1000.0


def run_cpu(args, th, log, telemetry_path):
    workers = args.workers or os.cpu_count() or 2
    state = CpuRunState(th["CPU_TEMP_WARN"], th["CPU_TEMP_CRITICAL"], args.abort_temp,
                        args.sustain, args.seconds, watching=not args.force)
    stop = multiprocessing.Event()
    procs = [multiprocessing.Process(target=_burn, args=(stop,), daemon=True) for _ in range(workers)]
    reason = "interrupted"
    log.event("cpu", "start", f"{workers} workers",
              f"warn {state.warn:g} / critical {state.crit:g} / abort {args.abort_temp:g} C; "
              f"stop after {args.sustain:g}s above warn or {args.seconds:g}s total")
    try:
        for p in procs:
            p.start()
        last = time.time()
        while True:
            time.sleep(2)
            now = time.time()
            temp, age = read_cpu_temp(telemetry_path)
            reason = state.update(temp, age, now - last)
            last = now
            log.event("cpu", "tick", f"{temp if temp is not None else 'n/a'} C",
                      f"above_warn {state.above_warn:.0f}s, peak {state.peak}")
            if reason:
                break
    except KeyboardInterrupt:
        reason = "interrupted by user"
    finally:
        stop.set()
        for p in procs:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
        log.event("cpu", "load_stopped", "", reason)
    if not args.force and args.settle > 0:
        _wait(log, "cpu", args.settle, lambda: f"{read_cpu_temp(telemetry_path)[0]} C")
    log.event("cpu", "summary", f"peak {state.peak} C",
              f"{state.above_warn:.0f}s >= {state.warn:g} C, {state.above_crit:.0f}s >= {state.crit:g} C")
    if reason.startswith("telemetry went stale"):
        print("\nThe Sentinel stopped writing telemetry, so the load was stopped for safety. "
              "Check that the Sentinel is still running, then try again.")
    elif state.peak is not None and state.peak < state.warn and reason == "time limit reached":
        print(f"\nThe CPU peaked at {state.peak:g} C and never reached the warning threshold "
              f"({state.warn:g} C), so no CPU alert could fire. Do not block the vents. To test the "
              f"alert LOGIC, run with a temporarily lowered CPU_TEMP_WARN in a copy of config.py, "
              f"and analyse with 'analyze_telemetry.py --config <that copy>'. Report it as a "
              f"threshold-shifted test, not as proof that {state.warn:g} C is reached in practice.")
    else:
        print("\nExpected in sentinel_telemetry.csv: cpu_temp:WARNING:sent on the first cycle at or above "
              "the warning threshold, then 'suppressed' with a reminder about every 60 s, "
              "cpu_temp:CRITICAL:sent if it reaches critical, and cpu_temp:recovered about 3 "
              "readings after it cools below the warning threshold.")


# --------------------------------------------------------------------------
def confirm(message, assume_yes):
    print(message)
    if assume_yes:
        return True
    return input("Type 'yes' to continue: ").strip().lower() == "yes"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Induce controlled RAM / CPU breaches for Sentinel testing.")
    ap.add_argument("--log", default="induce_breach_log.csv")
    ap.add_argument("--telemetry", default="sentinel_telemetry*.csv",
                    help="telemetry CSV (wildcards ok; the newest match is used)")
    ap.add_argument("--config", default=None, help="config.py to read thresholds from")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    sub = ap.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("ram", help="push RAM above RAM_PERCENT_LIMIT")
    r.add_argument("--target", type=float, default=None, help="RAM%% to reach (default: limit + 3)")
    r.add_argument("--hold", type=float, default=150, help="seconds to hold (default 150)")
    r.add_argument("--repeat", type=int, default=1, help="number of breach cycles")
    r.add_argument("--gap", type=float, default=45, help="seconds of recovery between cycles")
    r.add_argument("--max-percent", type=float, default=96)
    r.add_argument("--chunk-mb", type=int, default=64)

    c = sub.add_parser("cpu", help="load all cores until a sustained CPU temperature breach")
    c.add_argument("--workers", type=int, default=None, help="default: all logical CPUs")
    c.add_argument("--seconds", type=float, default=600, help="hard time limit (default 600)")
    c.add_argument("--sustain", type=float, default=150,
                   help="stop after this many seconds at/above the warning threshold (default 150)")
    c.add_argument("--abort-temp", type=float, default=95)
    c.add_argument("--settle", type=float, default=45, help="seconds to record cooling afterwards")
    c.add_argument("--force", action="store_true",
                   help="run without the telemetry temperature watch (time limit only; not recommended)")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    th, th_src = load_thresholds([args.config, os.path.join(os.getcwd(), "config.py"),
                                  os.path.join(here, "config.py")])
    telemetry_path = find_telemetry(args.telemetry)
    temp, age = read_cpu_temp(telemetry_path)
    fresh = age is not None and age <= 20
    print(f"Thresholds from: {th_src}")
    print(f"Telemetry file : {telemetry_path or 'NOT FOUND'}"
          f"{'' if fresh else '  (not updating - is the Sentinel running?)'}")

    import psutil
    if args.mode == "ram":
        vm = psutil.virtual_memory()
        limit = th["RAM_PERCENT_LIMIT"]
        target = args.target if args.target is not None else limit + 3
        if target > args.max_percent:
            sys.exit(f"Target {target:g}% is above the safety cap of {args.max_percent:g}%.")
        need = bytes_to_reach(vm.total, vm.percent, target)
        msg = (f"RAM now {vm.percent:.1f}% of {vm.total / 2**30:.1f} GB; limit {limit:g}%. "
               f"Plan: allocate about {need / 2**30:.1f} GB to reach {target:g}%, hold {args.hold:g}s, "
               f"release; {args.repeat} cycle(s). Save your work and close heavy programs first.")
        if sys.maxsize <= 2**32:
            sys.exit("This is 32-bit Python and cannot allocate enough memory.")
        if args.dry_run:
            return print(msg + "\n(dry run: nothing changed)")
        if not fresh and not confirm("WARNING: no live Sentinel telemetry found, so this run will "
                                     "produce no evidence.", args.yes):
            return
        if not confirm(msg, args.yes):
            return
        log = RunLog(args.log)
        try:
            run_ram(args, th, log, psutil)
        finally:
            log.close()
    else:
        if not fresh and not args.force:
            sys.exit("No live Sentinel telemetry: the CPU load watches the temperature through it. "
                     "Start the Sentinel first (or use --force, not recommended).")
        msg = (f"CPU now {temp if temp is not None else 'n/a'} C; warn {th['CPU_TEMP_WARN']:g}, "
               f"critical {th['CPU_TEMP_CRITICAL']:g}, abort at {args.abort_temp:g} C. Plan: load "
               f"{args.workers or os.cpu_count()} workers until {args.sustain:g}s at/above warn or "
               f"{args.seconds:g}s total. Laptop plugged in, on a hard flat surface, vents clear.")
        if args.dry_run:
            return print(msg + "\n(dry run: nothing changed)")
        if not confirm(msg, args.yes):
            return
        log = RunLog(args.log)
        try:
            run_cpu(args, th, log, telemetry_path)
        finally:
            log.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
