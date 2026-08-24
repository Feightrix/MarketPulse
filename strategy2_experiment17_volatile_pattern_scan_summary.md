# Experiment 17 — 2026 Volatile Pattern Scan

Research-only chart-pattern discovery on SIP 1-minute data. Candidate names are dynamically ranked each day by prior 20-day ATR%, subject to liquidity.

**Discovery-selected pattern: opening_range_breakout**

| Pattern | Discovery PF | Discovery return | Validation PF | Validation return | Holdout PF | Holdout return |
|---|---:|---:|---:|---:|---:|---:|
| opening_range_breakout | 1.04 | +0.80% | 0.21 | -10.08% | 0.57 | -3.89% |
| first_pullback_rebreak | 1.03 | +0.41% | 1.58 | +2.66% | 0.43 | -4.86% |
| vwap_reclaim | 0.47 | -14.92% | 0.57 | -4.80% | 0.71 | -3.44% |
| midday_compression_breakout | 0.61 | -16.44% | 0.79 | -4.35% | 0.56 | -8.60% |
| power_hour_breakout | 0.67 | -4.25% | 1.03 | +0.13% | 0.41 | -5.10% |

## Selected pattern detail
- discovery_jan_apr: 49 trades, win 38.8%, PF 1.04, return +0.80%, DD 6.07%, avg R +0.04
- validation_may_jun: 23 trades, win 13.0%, PF 0.21, return -10.08%, DD 10.60%, avg R -0.63
- holdout_jul_aug21: 22 trades, win 27.3%, PF 0.57, return -3.89%, DD 3.89%, avg R -0.25

Activation remains OFF. Aggressive sizing is not tested unless the pattern survives validation and holdout.
