# MarketPulse — Phase 3C Regime-Aware Validation

**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)
**Candidates tested:** 192
**Development-valid candidates:** 0
**Candidates passing both 2024 and 2025 validation:** 0

## Selected setup

- Regime filter: **strict_trend**
- ETF selection: strongest prior **5 trading-day** return
- Entry: **breakout6**
- Dynamic target: **1.50 × ATR**, bounded to 0.40%–0.80%
- Dynamic stop: **0.80 × ATR**, bounded to 0.25%–0.50%
- Max hold: **30 minutes**
- Max trades/day: **1**

## Results

| Period | Trades | Return | Expectancy | PF | Max DD | Positive months | Best month | Doubled months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development 2021-2023 | 0 | 0.00% | 0.00 bps | 0.00 | 0.00% | 0.00% | 0.00% | 0/36 |
| Validation 2024 | 0 | 0.00% | 0.00 bps | 0.00 | 0.00% | 0.00% | 0.00% | 0/12 |
| Validation 2025 | 0 | 0.00% | 0.00 bps | 0.00 | 0.00% | 0.00% | 0.00% | 0/12 |
| Validation 2024-2025 | 0 | 0.00% | 0.00 bps | 0.00 | 0.00% | 0.00% | 0.00% | 0/24 |
| 2026 check through Jul | 0 | 0.00% | 0.00 bps | 0.00 | 0.00% | 0.00% | 0.00% | 0/7 |

## Validation friction stress

| One-way friction | Expectancy | Return | PF |
|---:|---:|---:|---:|
| 2 bps | 0.00 bps | 0.00% | 0.00 |
| 4 bps | 0.00 bps | 0.00% | 0.00 |
| 6 bps | 0.00 bps | 0.00% | 0.00 |
| 10 bps | 0.00 bps | 0.00% | 0.00 |

**Phase 3C gate: FAIL**

## Important

The monthly doubling target is reported as an objective only. It never increases position size, leverage, trade frequency, or loss tolerance. A strategy that does not meet the target can still be a valid strategy; a strategy that reaches the target in a backtest is not guaranteed to repeat it.
