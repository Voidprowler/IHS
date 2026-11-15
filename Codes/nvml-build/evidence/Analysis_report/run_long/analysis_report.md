# Telemetry analysis report

- Input: `..\nvml-build\evidence\Analysis_report\run_long_final_analyzer\sentinel_telemetry_before_fix.csv`
- Rows analysed: 24370 in 5 session(s); unreadable rows skipped: 0
- Span: 2026-11-03 18:18:16 to 2026-11-06 19:47:27 (73.49 h wall-clock, includes gaps between sessions)
- Thresholds: config.py (not found there, defaults used: SSD_HEALTH_CRITICAL)
  - CPU warn >= 80, CPU critical >= 90, RAM warn > 90, SSD life critical < 20
- Reference rules: cooldown 60 s, recovery after 3 consecutive normal readings, timing tolerance 0.1 s

## Sampling period
| Intervals | Mean (s) | Median (s) | Min (s) | p95 (s) | Max (s) |
|---|---|---|---|---|---|
| 24365 | 2.46 | 2.35 | 2.30 | 2.88 | 8.77 |

Loop-number discontinuities within sessions: 0; intervals longer than 3x the median: 3.

## Alert channels: summary
| Channel | Valid cycles | No reading | Breach cycles | Expected alerts | Detected | Missed | False alerts | Detection rate | Event agreement |
|---|---|---|---|---|---|---|---|---|---|
| cpu_temp | 24370 | 0 | 1513 | 197 | 197 | 0 | 0 | 197/197 = 100.0% | 1648/1648 = 100.0% |
| ram | 24370 | 0 | 966 | 43 | 43 | 0 | 0 | 43/43 = 100.0% | 975/975 = 100.0% |
| ssd_health | 24370 | 0 | 0 | 0 | 0 | 0 | 0 | NOT EXERCISED | n/a |

*Expected alerts* are the toasts the reference rules say should have been shown (first breach, escalation, or reminder/re-escalation after the cooldown). *Event agreement* counts cycles where either side logged an event and both agree. A channel marked NOT EXERCISED never breached its threshold in this data, so its detection is untested here.

### cpu_temp
Observed range 48 to 94 (mean 66.3).
Detection 197/197 = 100.0%; false alerts 0 in 24173 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: escalation | 1 |
| alert sent: first | 135 |
| alert sent: re-escalation | 23 |
| alert sent: reminder | 38 |
| deescalated | 142 |
| recovered | 135 |
| suppressed | 1056 |
| suppressed (re-escalation) | 118 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| deescalated | deescalated | 142 |
| recovered | recovered | 135 |
| sent | sent | 197 |
| suppressed | suppressed | 1174 |

### ram
Observed range 37.6 to 99 (mean 57.1).
Detection 43/43 = 100.0%; false alerts 0 in 24327 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: first | 9 |
| alert sent: reminder | 34 |
| recovered | 9 |
| suppressed | 923 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| recovered | recovered | 9 |
| sent | sent | 43 |
| suppressed | suppressed | 923 |

### ssd_health
Observed range 85 to 99 (mean 87.8).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

## Alert processing time (alert_ms, cycles that showed a toast)
| Cycles | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |
|---|---|---|---|---|
| 238 | 10.8 | 5.0 | 30.7 | 81.5 |

Worst-case threshold-crossing-to-toast bound in this data: one sampling interval (8.77 s) plus processing (82 ms) = 8.86 s.
alert_ms covers the whole alert check for the cycle, including toast display.

## SSD drives (per-drive readings; the ssd_health channel uses the lowest)
| Drive | Health min / mean / max (%) | Temp min / mean / max (C) |
|---|---|---|
| WDC PC SN730 SDBPNTY-1T00-1032 | 99 / 99.0 / 99 | 42 / 51.3 / 60 |
| WDC PC SN730 SDBPNTY-512G-1032 | 85 / 85.0 / 85 | 46 / 54.2 / 73 |

## Monitored but not alerting (display only)
| Channel | Readings | No reading | Min | Mean | Max | Resolution |
|---|---|---|---|---|---|---|
| gpu_temp | 24370 | 0 | 51.2 | 66.1 | 92 | decimals |
| ssd_temp | 24370 | 0 | 46 | 54.1 | 73 | whole numbers |

## Disagreements between tool and reference
None. Every logged alert event matches the reference rules.

