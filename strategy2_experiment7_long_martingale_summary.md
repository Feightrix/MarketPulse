# MarketPulse Strategy 2 — Experiment 7: Long-Only Capped Martingale

**Gate: FAIL**

Research only. No Alpaca orders. Long-only and cash-funded; no leverage.

Locked rule: 1x / 2x / 4x tranches, each next tranche after a 2% adverse close, max three tranches, full exit at +1% over weighted average cost or trend break, 10 bps per fill.

## development_2008_2014
- Baseline long trend: ending $3459.76 | return +38.390% | DD 11.289% | avg day $+0.545
- Martingale: ending $2676.51 | return +7.060% | DD 3.544% | avg day $+0.100
- Martingale cycles 919 | win rate 79.11% | add1 178 | add2 51 | fills 2073 | modeled costs $136.21
- Martingale best/worst day $+49.36 / $-57.12

## validation_2015_2019
- Baseline long trend: ending $2875.24 | return +15.010% | DD 8.821% | avg day $+0.299
- Martingale: ending $2548.89 | return +1.956% | DD 2.258% | avg day $+0.039
- Martingale cycles 663 | win rate 72.40% | add1 127 | add2 31 | fills 1491 | modeled costs $94.95
- Martingale best/worst day $+22.62 / $-29.87

## holdout1_2020_2023
- Baseline long trend: ending $2986.08 | return +19.443% | DD 8.621% | avg day $+0.484
- Martingale: ending $2618.72 | return +4.749% | DD 3.698% | avg day $+0.118
- Martingale cycles 492 | win rate 75.41% | add1 98 | add2 37 | fills 1124 | modeled costs $76.69
- Martingale best/worst day $+22.66 / $-40.39

## holdout2_2024_2026
- Baseline long trend: ending $3219.91 | return +28.796% | DD 7.057% | avg day $+1.114
- Martingale: ending $2641.97 | return +5.679% | DD 3.510% | avg day $+0.220
- Martingale cycles 523 | win rate 81.26% | add1 136 | add2 38 | fills 1225 | modeled costs $88.07
- Martingale best/worst day $+23.71 / $-23.20

## Predeclared checks
- PASS — recent_holdout_positive_after_costs
- FAIL — recent_holdout_beats_long_only_trend_baseline
- FAIL — recent_holdout_avg_daily_pnl_beats_baseline
- PASS — recent_holdout_drawdown_at_most_10pct
- PASS — prior_holdout_positive_after_costs
- PASS — prior_holdout_drawdown_at_most_10pct

**Activation remains OFF regardless of backtest result; paper/shadow validation would be a separate step.**
