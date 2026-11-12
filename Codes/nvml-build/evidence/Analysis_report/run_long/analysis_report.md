# Telemetry analysis report

- Input: `sentinel_telemetry_before_fix.csv`
- Rows analysed: 24370 in 5 session(s); unreadable rows skipped: 0
- Span: 2026-11-03 18:18:16 to 2026-11-06 19:47:27 (73.49 h wall-clock, includes gaps between sessions)
- Thresholds: config.py
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
| cpu_temp | 24370 | 0 | 1513 | 149 | 146 | 3 | 51 | 146/149 = 98.0% | 1594/1648 = 96.7% |
| ram | 24370 | 0 | 966 | 42 | 41 | 1 | 2 | 41/42 = 97.6% | 972/975 = 99.7% |
| ssd_health | 24370 | 0 | 0 | 0 | 0 | 0 | 0 | NOT EXERCISED | n/a |

*Expected alerts* are the toasts the reference rules say should have been shown (first breach, escalation, or reminder/re-escalation/re-breach after the cooldown). *Event agreement* counts cycles where either side logged an event and both agree. A channel marked NOT EXERCISED never breached its threshold in this data, so its detection is untested here.

### cpu_temp
Observed range 48 to 94 (mean 66.3).
Detection 146/149 = 98.0%; false alerts 51 in 24221 cycles with no alert expected (0.21%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: escalation | 1 |
| alert sent: first | 4 |
| alert sent: re-breach | 82 |
| alert sent: re-escalation | 23 |
| alert sent: reminder | 39 |
| deescalated | 142 |
| recovered | 135 |
| suppressed | 1055 |
| suppressed (re-breach) | 49 |
| suppressed (re-escalation) | 118 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| deescalated | deescalated | 142 |
| recovered | recovered | 135 |
| sent | sent | 146 |
| sent | suppressed | 3 |
| suppressed | sent | 51 |
| suppressed | suppressed | 1171 |

### ram
Observed range 37.6 to 99 (mean 57.1).
Detection 41/42 = 97.6%; false alerts 2 in 24328 cycles with no alert expected (0.01%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: first | 3 |
| alert sent: re-breach | 4 |
| alert sent: reminder | 35 |
| recovered | 9 |
| suppressed | 922 |
| suppressed (re-breach) | 2 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| recovered | recovered | 9 |
| sent | sent | 41 |
| sent | suppressed | 1 |
| suppressed | sent | 2 |
| suppressed | suppressed | 922 |

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
57 cycle(s) disagree (full list in analysis_mismatches.csv). One logic defect can cascade into later cycles, so start with the earliest:

| Session | Loop | Time | Channel | Value | Expected | Logged |
|---|---|---|---|---|---|---|
| 1 | 75 | 2026-11-03T18:21:10.803 | cpu_temp | 80.0 | suppressed | sent |
| 1 | 85 | 2026-11-03T18:21:34.505 | cpu_temp | 80.0 | suppressed | sent |
| 1 | 91 | 2026-11-03T18:21:48.702 | cpu_temp | 80.0 | suppressed | sent |
| 1 | 173 | 2026-11-03T18:25:02.036 | ram | 90.1 | suppressed | sent |
| 1 | 183 | 2026-11-03T18:25:25.486 | ram | 91.5 | sent | suppressed |
| 2 | 2310 | 2026-11-04T21:47:40.588 | cpu_temp | 80.0 | suppressed | sent |
| 2 | 3167 | 2026-11-04T22:21:04.846 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 2881 | 2026-11-05T17:46:19.964 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 2892 | 2026-11-05T17:46:45.910 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 2944 | 2026-11-05T17:48:48.562 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 2994 | 2026-11-05T17:50:45.555 | ram | 90.3 | suppressed | sent |
| 3 | 6393 | 2026-11-05T20:12:26.522 | cpu_temp | 81.0 | suppressed | sent |
| 3 | 7196 | 2026-11-05T20:43:38.123 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 7490 | 2026-11-05T20:55:03.524 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 7520 | 2026-11-05T20:56:13.549 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 7524 | 2026-11-05T20:56:22.881 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 7528 | 2026-11-05T20:56:32.212 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 7534 | 2026-11-05T20:56:46.214 | cpu_temp | 80.0 | suppressed | sent |
| 3 | 7589 | 2026-11-05T20:58:54.735 | cpu_temp | 82.0 | suppressed | sent |
| 3 | 7626 | 2026-11-05T21:00:21.189 | cpu_temp | 81.0 | suppressed | sent |

