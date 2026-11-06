#!/usr/bin/env python3
"""
compare_gpu_sources.py - validate the NVML GPU-temperature fallback.

Reads the GPU temperature from LibreHardwareMonitor and from NVML at the same
moment, once per second, and reports how closely they agree. Run it on the
machine you can test on (the one where LibreHardwareMonitor reports the GPU).

    python compare_gpu_sources.py [--seconds 60]

Run it from an ADMINISTRATOR terminal with the Sentinel STOPPED (two programs
should not open the hardware bridge at the same time).
"""
import argparse
import statistics
import sys
import time


def summarise(pairs):
    """pairs: [(lhm_temp, nvml_temp), ...] -> dict of agreement statistics."""
    diffs = [abs(a - b) for a, b in pairs]
    return {"n": len(pairs),
            "mean_lhm": statistics.mean(a for a, _ in pairs),
            "mean_nvml": statistics.mean(b for _, b in pairs),
            "mean_abs_diff": statistics.mean(diffs),
            "max_abs_diff": max(diffs),
            "within_2c": sum(1 for d in diffs if d <= 2) / len(diffs)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compare LibreHardwareMonitor and NVML GPU temperatures.")
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args(argv)

    import sensors                                   # needs the Windows hardware bridge
    from gpu_nvml import NvmlReader
    sensors.GPU_FORCE_NVML = False                   # we want LHM's own value here
    hw = sensors.HardwareSensors()
    nv = NvmlReader()
    if not hw.ohm_enabled:
        sys.exit("The hardware bridge did not start (run as administrator, with the Sentinel stopped).")
    if nv.read() is None:
        sys.exit("NVML is not available on this machine (no NVIDIA GPU/driver found), so there is "
                 "nothing to compare.")

    pairs = []
    print(f"Sampling for {args.seconds} s ...")
    for _ in range(args.seconds):
        stats = hw.get_system_stats()
        a, b = stats.get("gpu_temp"), nv.read()
        if a is not None and b is not None:
            pairs.append((a, b))
            print(f"  LHM {a:5.1f} C   NVML {b:5.1f} C   diff {abs(a - b):4.1f}")
        time.sleep(1)
    if not pairs:
        sys.exit("No simultaneous readings: LibreHardwareMonitor reported no GPU temperature.")
    r = summarise(pairs)
    print(f"\n{r['n']} paired readings. LHM mean {r['mean_lhm']:.1f} C, NVML mean {r['mean_nvml']:.1f} C; "
          f"mean absolute difference {r['mean_abs_diff']:.2f} C, maximum {r['max_abs_diff']:.1f} C; "
          f"{r['within_2c']:.0%} of readings within 2 C.")
    print(f"Source LibreHardwareMonitor used: {stats.get('gpu_source')}")


if __name__ == "__main__":
    main()
