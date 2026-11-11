"""Overhead measurement for Sentinel (checklist item [15]).

Alternates BASELINE blocks (Sentinel not running) with MONITORED blocks
(Sentinel running) under the SAME workload, several pairs of each, and
records once per second:
  * system-wide CPU percent and memory in use,
  * Sentinel's own process CPU (percent of one logical core) and memory.

Run it as administrator (so the elevated Sentinel process can be read), on
mains power, from a SCRATCH copy of the Sentinel folder: Sentinel's own
telemetry and log files from these runs then never touch the evidence files.

  python measure_overhead.py --sentinel-dir .
  python measure_overhead.py --sentinel-dir . --block-sec 180 --pairs 3 --workload both

Workloads: "idle" (nothing else started by this script) and "load"
(--load-workers busy processes running throughout both conditions).
"""
import argparse
import csv
import multiprocessing as mp
import os
import platform
import re
import statistics as st
import subprocess
import sys
import time
from datetime import datetime

import psutil


def burn(stop_evt):
    x = 0
    while not stop_evt.is_set():
        for _ in range(20000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF


class Workload:
    def __init__(self, name, workers):
        self.name, self.workers, self.procs, self.evt = name, workers, [], None

    def start(self):
        if self.name != "load":
            return
        self.evt = mp.Event()
        self.procs = [mp.Process(target=burn, args=(self.evt,)) for _ in range(self.workers)]
        for p in self.procs:
            p.start()

    def stop(self):
        if self.evt is not None:
            self.evt.set()
            for p in self.procs:
                p.join(timeout=10)
        self.procs, self.evt = [], None


def start_sentinel(args):
    cmd = [sys.executable, "main.py"] if not args.exe else [args.exe]
    p = subprocess.Popen(cmd, cwd=args.sentinel_dir, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    time.sleep(args.startup_wait)           # LibreHardwareMonitor takes a moment to open
    if p.poll() is not None:
        raise SystemExit("Sentinel exited at start-up (exit code %s). Run it by hand "
                         "from this folder as administrator and check it works." % p.returncode)
    return p


def stop_sentinel(p):
    try:
        p.terminate()
        p.wait(timeout=15)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def read_tree(root, tracked):
    """CPU (percent of one core), RSS and USS summed over Sentinel and any child
    processes (a virtual-environment python.exe starts the real interpreter as a child)."""
    cpu, rss, uss, have_uss = 0.0, 0, 0, False
    for m in [root] + root.children(recursive=True):
        p = tracked.get(m.pid)
        if p is None:                      # first sight: prime the CPU counter, read next time
            tracked[m.pid] = m
            m.cpu_percent(None)
            continue
        try:
            cpu += p.cpu_percent(None)
            rss += p.memory_info().rss
            try:
                uss += p.memory_full_info().uss
                have_uss = True
            except Exception:
                pass
        except psutil.NoSuchProcess:
            tracked.pop(m.pid, None)
    return cpu, rss, (uss if have_uss else None)


def run_block(args, writer, workload, condition, pair):
    wl = Workload(workload, args.load_workers)
    wl.start()
    sent = start_sentinel(args) if condition == "monitored" else None
    proc = psutil.Process(sent.pid) if sent else None
    ncpu = psutil.cpu_count(logical=True)
    time.sleep(2)                             # let the workload and Sentinel settle
    psutil.cpu_percent(None)
    tracked = {}
    if proc:
        read_tree(proc, tracked)           # prime
    t_start = time.time()
    print(f"  {workload:5} {condition:9} pair {pair}: ", end="", flush=True)
    try:
        while time.time() - t_start < args.block_sec:
            time.sleep(args.step)
            now = time.time() - t_start
            row = {"timestamp": datetime.now().isoformat(timespec="milliseconds"),
                   "workload": workload, "condition": condition, "pair": pair,
                   "t_sec": round(now, 1), "settling": int(now < args.settle_sec),
                   "sys_cpu_pct": psutil.cpu_percent(None),
                   "sys_mem_used_mb": round(psutil.virtual_memory().used / 2**20, 1),
                   "proc_cpu_pct_one_core": "", "proc_rss_mb": "", "proc_uss_mb": ""}
            if proc:
                try:
                    cpu, rss, uss = read_tree(proc, tracked)
                    row["proc_cpu_pct_one_core"] = round(cpu, 2)
                    row["proc_rss_mb"] = round(rss / 2**20, 1)
                    if uss is not None:
                        row["proc_uss_mb"] = round(uss / 2**20, 1)
                except psutil.NoSuchProcess:
                    raise SystemExit("Sentinel stopped during the block; results are invalid.")
            writer.writerow(row)
    finally:
        if sent:
            stop_sentinel(sent)
        wl.stop()
    print("done", flush=True)


def mean(xs):
    xs = [x for x in xs if x != ""]
    return st.mean(xs) if xs else float("nan")


def pctl(xs, q):
    xs = sorted(float(x) for x in xs if x != "")
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def summarise(path, args, header):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows = [r for r in rows if r["settling"] == "0"]
    ncpu = psutil.cpu_count(logical=True)
    out = ["# Overhead measurement", "", *header, ""]
    for wl in sorted({r["workload"] for r in rows}):
        out += [f"## Workload: {wl}", ""]
        blocks = {}
        for r in rows:
            if r["workload"] == wl:
                blocks.setdefault((r["condition"], int(r["pair"])), []).append(r)
        pairs = sorted({p for (_, p) in blocks})
        base = [mean([float(x["sys_cpu_pct"]) for x in blocks[("baseline", p)]]) for p in pairs if ("baseline", p) in blocks]
        mon = [mean([float(x["sys_cpu_pct"]) for x in blocks[("monitored", p)]]) for p in pairs if ("monitored", p) in blocks]
        bmem = [mean([float(x["sys_mem_used_mb"]) for x in blocks[("baseline", p)]]) for p in pairs if ("baseline", p) in blocks]
        mmem = [mean([float(x["sys_mem_used_mb"]) for x in blocks[("monitored", p)]]) for p in pairs if ("monitored", p) in blocks]
        diffs = [m - b for b, m in zip(base, mon)]
        mdiffs = [m - b for b, m in zip(bmem, mmem)]
        out += ["| Measure | Baseline | Monitored | Paired difference (monitored - baseline) |",
                "|---|---|---|---|",
                f"| System CPU, % of all logical CPUs (mean of block means) | {mean(base):.2f} | {mean(mon):.2f} | "
                f"{mean(diffs):+.2f} (pairs: {', '.join(f'{d:+.2f}' for d in diffs)}) |",
                f"| System memory in use, MB (mean of block means) | {mean(bmem):.0f} | {mean(mmem):.0f} | "
                f"{mean(mdiffs):+.0f} (pairs: {', '.join(f'{d:+.0f}' for d in mdiffs)}) |", ""]
        sent = [r for (c, _), v in blocks.items() if c == "monitored" for r in v]
        cpu1 = [float(r["proc_cpu_pct_one_core"]) for r in sent if r["proc_cpu_pct_one_core"] != ""]
        rss = [float(r["proc_rss_mb"]) for r in sent if r["proc_rss_mb"] != ""]
        uss = [float(r["proc_uss_mb"]) for r in sent if r["proc_uss_mb"] != ""]
        if cpu1:
            out += ["Sentinel's own process:", "",
                    "| Measure | Mean | Median | 95th percentile | Maximum |", "|---|---|---|---|---|",
                    f"| CPU, % of one logical core | {mean(cpu1):.2f} | {st.median(cpu1):.2f} | {pctl(cpu1, .95):.2f} | {max(cpu1):.2f} |",
                    f"| CPU, % of all {ncpu} logical CPUs | {mean(cpu1)/ncpu:.3f} | {st.median(cpu1)/ncpu:.3f} | {pctl(cpu1, .95)/ncpu:.3f} | {max(cpu1)/ncpu:.3f} |",
                    f"| Working set (RSS), MB | {mean(rss):.1f} | {st.median(rss):.1f} | {pctl(rss, .95):.1f} | {max(rss):.1f} |"]
            if uss:
                out += [f"| Unique memory (USS), MB | {mean(uss):.1f} | {st.median(uss):.1f} | {pctl(uss, .95):.1f} | {max(uss):.1f} |"]
            out += [f"", f"Samples per condition: {len(cpu1)} monitored, "
                    f"{sum(len(v) for (c, _), v in blocks.items() if c == 'baseline')} baseline (one per {args.step:g} s, "
                    f"first {args.settle_sec:g} s of each block excluded).", ""]
    open(path.replace("_samples.csv", "_summary.md"), "w", encoding="utf-8").write("\n".join(out))
    print("\n" + "\n".join(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sentinel-dir", default=".", help="scratch copy of the Sentinel folder")
    ap.add_argument("--exe", default=None, help="run this executable instead of python main.py")
    ap.add_argument("--workload", choices=["idle", "load", "both"], default="both")
    ap.add_argument("--load-workers", type=int, default=4, help="busy processes in the load workload")
    ap.add_argument("--block-sec", type=float, default=300)
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--settle-sec", type=float, default=30)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--startup-wait", type=float, default=8)
    ap.add_argument("--allow-battery", action="store_true")
    ap.add_argument("--out", default="overhead_samples.csv")
    args = ap.parse_args()

    bat = psutil.sensors_battery()
    if bat is not None and not bat.power_plugged and not args.allow_battery:
        raise SystemExit("Plug the laptop in first (or pass --allow-battery and say so in the report).")
    cfg = os.path.join(args.sentinel_dir, "config.py")
    debug = "unknown"
    if os.path.exists(cfg):
        m = re.search(r"^\s*DEBUG_SENSORS\s*=\s*(\w+)", open(cfg, encoding="utf-8").read(), re.M)
        debug = m.group(1) if m else "not set"
    header = [f"- Date: {datetime.now():%Y-%m-%d %H:%M}",
              f"- Machine: {platform.platform()}, {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count(logical=True)} logical CPUs, "
              f"{psutil.virtual_memory().total / 2**30:.1f} GB RAM",
              f"- Python {platform.python_version()}, psutil {psutil.__version__}",
              f"- DEBUG_SENSORS = {debug}; power plugged in: {None if bat is None else bat.power_plugged}",
              f"- Design: {args.pairs} pairs of baseline and monitored blocks of {args.block_sec:g} s, alternating, "
              f"workload(s): {args.workload} (load = {args.load_workers} busy processes)"]
    workloads = ["idle", "load"] if args.workload == "both" else [args.workload]
    total = len(workloads) * args.pairs * 2 * (args.block_sec + 12) / 60
    print(f"About {total:.0f} minutes. Do not use the laptop meanwhile.\n")
    fields = ["timestamp", "workload", "condition", "pair", "t_sec", "settling", "sys_cpu_pct",
              "sys_mem_used_mb", "proc_cpu_pct_one_core", "proc_rss_mb", "proc_uss_mb"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for wl in workloads:
            for pair in range(1, args.pairs + 1):
                run_block(args, w, wl, "baseline", pair)
                f.flush()
                run_block(args, w, wl, "monitored", pair)
                f.flush()
    summarise(args.out, args, header)


if __name__ == "__main__":
    mp.freeze_support()
    main()
