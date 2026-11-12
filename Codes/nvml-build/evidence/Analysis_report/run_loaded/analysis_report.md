# Telemetry analysis report

- Input: `sentinel_telemetry_T8_loaded.csv`
- Rows analysed: 2632 in 2 session(s); unreadable rows skipped: 0
- Span: 2026-11-08 15:13:13 to 2026-11-08 17:03:45 (1.84 h wall-clock, includes gaps between sessions)
- Thresholds: config.py
  - CPU warn >= 80, CPU critical >= 90, RAM warn > 90, SSD life critical < 20
- Reference rules: cooldown 60 s, recovery after 3 consecutive normal readings, timing tolerance 0.1 s

## Sampling period
| Intervals | Mean (s) | Median (s) | Min (s) | p95 (s) | Max (s) |
|---|---|---|---|---|---|
| 2630 | 2.49 | 2.58 | 2.15 | 2.94 | 3.66 |

Loop-number discontinuities within sessions: 0; intervals longer than 3x the median: 0.

## Alert channels: summary
| Channel | Valid cycles | No reading | Breach cycles | Expected alerts | Detected | Missed | False alerts | Detection rate | Event agreement |
|---|---|---|---|---|---|---|---|---|---|
| cpu_temp | 2632 | 0 | 1550 | 70 | 70 | 0 | 0 | 70/70 = 100.0% | 1567/1567 = 100.0% |
| ram | 2632 | 0 | 152 | 6 | 6 | 0 | 0 | 6/6 = 100.0% | 154/154 = 100.0% |
| ssd_health | 2632 | 0 | 0 | 0 | 0 | 0 | 0 | NOT EXERCISED | n/a |

*Expected alerts* are the toasts the reference rules say should have been shown (first breach, escalation, or reminder/re-escalation/re-breach after the cooldown). *Event agreement* counts cycles where either side logged an event and both agree. A channel marked NOT EXERCISED never breached its threshold in this data, so its detection is untested here.

### cpu_temp
Observed range 52 to 91 (mean 75.1).
Detection 70/70 = 100.0%; false alerts 0 in 2562 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 1; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: escalation | 1 |
| alert sent: first | 1 |
| alert sent: re-breach | 16 |
| alert sent: re-escalation | 2 |
| alert sent: reminder | 50 |
| deescalated | 5 |
| recovered | 17 |
| suppressed | 1473 |
| suppressed (re-escalation) | 2 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| deescalated | deescalated | 5 |
| recovered | recovered | 17 |
| sent | sent | 70 |
| suppressed | suppressed | 1475 |

### ram
Observed range 42.5 to 94 (mean 50.5).
Detection 6/6 = 100.0%; false alerts 0 in 2626 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: first | 2 |
| alert sent: reminder | 4 |
| recovered | 2 |
| suppressed | 146 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| recovered | recovered | 2 |
| sent | sent | 6 |
| suppressed | suppressed | 146 |

### ssd_health
Observed range 85 to 85 (mean 85.0).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

## Alert processing time (alert_ms, cycles that showed a toast)
| Cycles | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |
|---|---|---|---|---|
| 76 | 9.3 | 6.0 | 22.3 | 58.3 |

Worst-case threshold-crossing-to-toast bound in this data: one sampling interval (3.66 s) plus processing (58 ms) = 3.72 s.
alert_ms covers the whole alert check for the cycle, including toast display.

## SSD drives (per-drive readings; the ssd_health channel uses the lowest)
| Drive | Health min / mean / max (%) | Temp min / mean / max (C) |
|---|---|---|
| WDC PC SN730 SDBPNTY-1T00-1032 | 99 / 99.0 / 99 | 48 / 49.7 / 51 |
| WDC PC SN730 SDBPNTY-512G-1032 | 85 / 85.0 / 85 | 50 / 51.3 / 54 |

## Monitored but not alerting (display only)
| Channel | Readings | No reading | Min | Mean | Max | Resolution |
|---|---|---|---|---|---|---|
| gpu_temp | 2632 | 0 | 53.5 | 59.8 | 66.4 | decimals |
| ssd_temp | 2632 | 0 | 50 | 51.3 | 54 | whole numbers |

## Disagreements between tool and reference
None. Every logged alert event matches the reference rules.

