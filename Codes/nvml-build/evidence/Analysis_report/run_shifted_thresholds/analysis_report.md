# Telemetry analysis report

- Input: `run2_shifted_cpu.csv`
- Rows analysed: 155 in 1 session(s); unreadable rows skipped: 0
- Span: 2026-11-02 21:22:45 to 2026-11-02 21:30:00 (0.12 h wall-clock, includes gaps between sessions)
- Thresholds: config_shifted_test.py
  - CPU warn >= 72, CPU critical >= 82, RAM warn > 90, SSD life critical < 20
- Reference rules: cooldown 60 s, recovery after 3 consecutive normal readings, timing tolerance 0.1 s

## Sampling period
| Intervals | Mean (s) | Median (s) | Min (s) | p95 (s) | Max (s) |
|---|---|---|---|---|---|
| 154 | 2.82 | 2.85 | 2.31 | 3.23 | 3.62 |

Loop-number discontinuities within sessions: 0; intervals longer than 3x the median: 0.

## Alert channels: summary
| Channel | Valid cycles | No reading | Breach cycles | Expected alerts | Detected | Missed | False alerts | Detection rate | Event agreement |
|---|---|---|---|---|---|---|---|---|---|
| cpu_temp | 155 | 0 | 102 | 14 | 14 | 0 | 0 | 14/14 = 100.0% | 103/103 = 100.0% |
| ram | 155 | 0 | 0 | 0 | 0 | 0 | 0 | NOT EXERCISED | n/a |
| ssd_health | 155 | 0 | 0 | 0 | 0 | 0 | 0 | NOT EXERCISED | n/a |

*Expected alerts* are the toasts the reference rules say should have been shown (first breach, escalation, or reminder after cooldown). *Event agreement* counts cycles where either side logged an event and both agree. A channel marked NOT EXERCISED never breached its threshold in this data, so its detection is untested here.

### cpu_temp
Observed range 50 to 86 (mean 73.5).
Detection 14/14 = 100.0%; false alerts 0 in 141 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: escalation | 12 |
| alert sent: first | 1 |
| alert sent: reminder | 1 |
| deescalated | 12 |
| recovered | 1 |
| suppressed | 76 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| deescalated | deescalated | 12 |
| recovered | recovered | 1 |
| sent | sent | 14 |
| suppressed | suppressed | 76 |

### ram
Observed range 58.5 to 60.8 (mean 60.0).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

### ssd_health
Observed range 85 to 85 (mean 85.0).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

## Alert processing time (alert_ms, cycles that showed a toast)
| Cycles | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |
|---|---|---|---|---|
| 14 | 16.4 | 10.4 | 31.8 | 59.2 |

Worst-case threshold-crossing-to-toast bound in this data: one sampling interval (3.62 s) plus processing (59 ms) = 3.68 s.
alert_ms covers the whole alert check for the cycle, including toast display.

## SSD drives (per-drive readings; the ssd_health channel uses the lowest)
| Drive | Health min / mean / max (%) | Temp min / mean / max (C) |
|---|---|---|
| WDC PC SN730 SDBPNTY-1T00-1032 | 99 / 99.0 / 99 | 46 / 47.5 / 48 |
| WDC PC SN730 SDBPNTY-512G-1032 | 85 / 85.0 / 85 | 48 / 49.4 / 52 |

## Monitored but not alerting (display only)
| Channel | Readings | No reading | Min | Mean | Max | Resolution |
|---|---|---|---|---|---|---|
| gpu_temp | 155 | 0 | 51.2 | 56.6 | 59.1 | decimals |
| ssd_temp | 155 | 0 | 48 | 49.4 | 52 | whole numbers |

## Disagreements between tool and reference
None. Every logged alert event matches the reference rules.

