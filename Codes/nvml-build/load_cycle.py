"""Loaded-run driver for the Sentinel retest (T8).

Phases (all times in minutes, floats allowed):
  1. warm-up : sustained all-core load, so the CPU settles in the warning/critical band
  2. cycling : LOAD minutes on, REST minutes off, repeated, so the CPU crosses the
               warning threshold many times (the pattern that exposed the recovery defect)
  3. RAM     : one RAM breach (grow to --ram-target percent, hold, release)
  4. cooldown: idle tail so recoveries are recorded

Every phase change is written to load_phases.log with a timestamp.
Stop at any time with Ctrl+C; memory is released and workers are stopped.
"""
import argparse
import multiprocessing as mp
import time
from datetime import datetime


def burn(stop_at, flag):
    x = 0
    while time.time() < stop_at and not flag.is_set():
        for _ in range(20000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open("load_phases.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_load(minutes, workers, flag):
    stop_at = time.time() + minutes * 60
    procs = [mp.Process(target=burn, args=(stop_at, flag)) for _ in range(workers)]
    for p in procs:
        p.start()
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        flag.set()
        for p in procs:
            p.join()
        raise


def idle(minutes):
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(min(1.0, end - time.time()))


def ram_breach(target_pct, hold_min):
    try:
        import psutil
    except ImportError:
        log("psutil not installed; RAM phase skipped (pip install psutil)")
        return
    blocks = []
    chunk = 128 * 1024 * 1024
    try:
        while psutil.virtual_memory().percent < target_pct:
            b = bytearray(chunk)
            for i in range(0, chunk, 4096):      # touch pages so they are really used
                b[i] = 1
            blocks.append(b)
            if len(blocks) > 400:                 # safety stop at ~50 GB
                break
        log(f"RAM at {psutil.virtual_memory().percent:.1f}% - holding for {hold_min} min")
        idle(hold_min)
    finally:
        blocks.clear()
        log("RAM released")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--total-min", type=float, default=120, help="length of warm-up + cycling")
    ap.add_argument("--warm-min", type=float, default=35)
    ap.add_argument("--load-min", type=float, default=2)
    ap.add_argument("--rest-min", type=float, default=1)
    ap.add_argument("--ram-target", type=float, default=93, help="percent; Sentinel limit is 90")
    ap.add_argument("--ram-hold-min", type=float, default=3)
    ap.add_argument("--tail-min", type=float, default=5)
    ap.add_argument("--no-ram", action="store_true")
    ap.add_argument("--workers", type=int, default=mp.cpu_count())
    a = ap.parse_args()

    flag = mp.Event()
    start = time.time()
    try:
        log(f"START total={a.total_min} warm={a.warm_min} load={a.load_min} rest={a.rest_min} workers={a.workers}")
        log("PHASE warm-up (sustained load)")
        run_load(min(a.warm_min, a.total_min), a.workers, flag)
        n = 0
        while (time.time() - start) / 60 < a.total_min:
            n += 1
            left = a.total_min - (time.time() - start) / 60
            log(f"PHASE cycle {n}: load")
            run_load(min(a.load_min, left), a.workers, flag)
            left = a.total_min - (time.time() - start) / 60
            if left <= 0:
                break
            log(f"PHASE cycle {n}: rest")
            idle(min(a.rest_min, left))
        if not a.no_ram:
            log("PHASE RAM breach")
            ram_breach(a.ram_target, a.ram_hold_min)
        log("PHASE tail (idle)")
        idle(a.tail_min)
        log("DONE")
    except KeyboardInterrupt:
        flag.set()
        log("STOPPED by user")


if __name__ == "__main__":
    mp.freeze_support()
    main()
