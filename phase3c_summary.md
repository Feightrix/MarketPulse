# MarketPulse — Phase 3C Regime-Aware Validation

**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)
**Candidates tested:** 192
**Candidates with at least one development trade:** 0
**Positive-expectancy development candidates:** 0
**Development-valid candidates:** 0
**Candidates passing development + both 2024 and 2025 validation:** 0

## Selected strongest candidate (not necessarily a PASS)

- Development gate passed: **NO**
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

## Development diagnostics

- Maximum trades among any candidate: **0**
- Highest candidate expectancy: **0.00 bps/trade**
- Highest candidate total return: **0.00%**

## Validation friction stress

| One-way friction | Expectancy | Return | PF |
|---:|---:|---:|---:|
| 2 bps | 0.00 bps | 0.00% | 0.00 |
| 4 bps | 0.00 bps | 0.00% | 0.00 |
| 6 bps | 0.00 bps | 0.00% | 0.00 |
| 10 bps | 0.00 bps | 0.00% | 0.00 |

**Phase 3C gate: FAIL**

## Important

The monthly doubling target is an objective only. It never raises leverage, position size, trade frequency, or loss tolerance. This corrected report ranks failed candidates honestly when none clears the development gate; it does not lower the gate to create a PASS.
