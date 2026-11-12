# Telemetry analysis report

- Input: `game_run.csv`
- Rows analysed: 1722 in 1 session(s); unreadable rows skipped: 0
- Span: 2026-11-08 17:20:07 to 2026-11-08 18:22:46 (1.04 h wall-clock, includes gaps between sessions)
- Thresholds: config.py
  - CPU warn >= 80, CPU critical >= 90, RAM warn > 90, SSD life critical < 20
- Reference rules: cooldown 60 s, recovery after 3 consecutive normal readings, timing tolerance 0.1 s

## Sampling period
| Intervals | Mean (s) | Median (s) | Min (s) | p95 (s) | Max (s) |
|---|---|---|---|---|---|
| 1721 | 2.18 | 2.18 | 2.15 | 2.20 | 2.75 |

Loop-number discontinuities within sessions: 0; intervals longer than 3x the median: 0.

## Alert channels: summary
| Channel | Valid cycles | No reading | Breach cycles | Expected alerts | Detected | Missed | False alerts | Detection rate | Event agreement |
|---|---|---|---|---|---|---|---|---|---|
| cpu_temp | 1722 | 0 | 200 | 20 | 20 | 0 | 0 | 20/20 = 100.0% | 240/240 = 100.0% |
| ram | 1722 | 0 | 305 | 13 | 13 | 0 | 0 | 13/13 = 100.0% | 311/311 = 100.0% |
| ssd_health | 1722 | 0 | 0 | 0 | 0 | 0 | 0 | NOT EXERCISED | n/a |

*Expected alerts* are the toasts the reference rules say should have been shown (first breach, escalation, or reminder/re-escalation/re-breach after the cooldown). *Event agreement* counts cycles where either side logged an event and both agree. A channel marked NOT EXERCISED never breached its threshold in this data, so its detection is untested here.

### cpu_temp
Observed range 54 to 87 (mean 72.7).
Detection 20/20 = 100.0%; false alerts 0 in 1702 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: first | 1 |
| alert sent: re-breach | 15 |
| alert sent: reminder | 4 |
| recovered | 40 |
| suppressed | 156 |
| suppressed (re-breach) | 24 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| recovered | recovered | 40 |
| sent | sent | 20 |
| suppressed | suppressed | 180 |

### ram
Observed range 47.2 to 100 (mean 83.6).
Detection 13/13 = 100.0%; false alerts 0 in 1709 cycles with no alert expected (0.00%).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

| Rule path exercised (per reference) | Cycles |
|---|---|
| alert sent: first | 1 |
| alert sent: re-breach | 1 |
| alert sent: reminder | 11 |
| recovered | 6 |
| suppressed | 288 |
| suppressed (re-breach) | 4 |

| Reference expected | Tool logged | Cycles |
|---|---|---|
| recovered | recovered | 6 |
| sent | sent | 13 |
| suppressed | suppressed | 292 |

### ssd_health
Observed range 85 to 85 (mean 85.0).
Delivery failures logged: 0; timing-boundary cases accepted: 0; no-reading cycles during an active alert: 0.

## Alert processing time (alert_ms, cycles that showed a toast)
| Cycles | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |
|---|---|---|---|---|
| 33 | 14.7 | 15.4 | 30.9 | 50.6 |

Worst-case threshold-crossing-to-toast bound in this data: one sampling interval (2.75 s) plus processing (51 ms) = 2.80 s.
alert_ms covers the whole alert check for the cycle, including toast display.

## SSD drives (per-drive readings; the ssd_health channel uses the lowest)
| Drive | Health min / mean / max (%) | Temp min / mean / max (C) |
|---|---|---|
| WDC PC SN730 SDBPNTY-1T00-1032 | 99 / 99.0 / 99 | 49 / 55.2 / 57 |
| WDC PC SN730 SDBPNTY-512G-1032 | 85 / 85.0 / 85 | 52 / 59.1 / 63 |

## Monitored but not alerting (display only)
| Channel | Readings | No reading | Min | Mean | Max | Resolution |
|---|---|---|---|---|---|---|
| gpu_temp | 1722 | 0 | 54.3 | 72.4 | 87.8 | decimals |
| ssd_temp | 1722 | 0 | 52 | 59.1 | 63 | whole numbers |

## Disagreements between tool and reference
None. Every logged alert event matches the reference rules.

