# MarketPulse — Phase 3C Regime-Aware Validation

**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)
**Candidates tested:** 192
**Candidates with at least one development trade:** 192
**Positive-expectancy development candidates:** 49
**Development-valid candidates:** 12
**Candidates passing development + both 2024 and 2025 validation:** 0

## Selected strongest candidate (not necessarily a PASS)

- Development gate passed: **YES**
- Regime filter: **momentum_breadth**
- ETF selection: strongest prior **5 trading-day** return
- Entry: **pullback9**
- Dynamic target: **2.00 × ATR**, bounded to 0.40%–0.80%
- Dynamic stop: **0.80 × ATR**, bounded to 0.25%–0.50%
- Max hold: **30 minutes**
- Max trades/day: **1**

## Results

| Period | Trades | Return | Expectancy | PF | Max DD | Positive months | Best month | Doubled months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development 2021-2023 | 77 | 0.78% | 1.04 bps | 1.11 | 1.54% | 22.22% | 1.31% | 0/36 |
| Validation 2024 | 32 | -0.76% | -2.36 bps | 0.72 | 0.94% | 33.33% | 0.54% | 0/12 |
| Validation 2025 | 31 | -0.18% | -0.57 bps | 0.93 | 0.92% | 33.33% | 0.50% | 0/12 |
| Validation 2024-2025 | 63 | -0.94% | -1.48 bps | 0.82 | 1.60% | 33.33% | 0.54% | 0/24 |
| 2026 check through Jul | 18 | 0.26% | 1.43 bps | 1.29 | 0.40% | 42.86% | 0.28% | 0/7 |

## Development diagnostics

- Maximum trades among any candidate: **148**
- Highest candidate expectancy: **1.61 bps/trade**
- Highest candidate total return: **1.10%**

## Validation friction stress

| One-way friction | Expectancy | Return | PF |
|---:|---:|---:|---:|
| 2 bps | -1.48 bps | -0.94% | 0.82 |
| 4 bps | -5.26 bps | -3.27% | 0.48 |
| 6 bps | -10.45 bps | -1.36% | 0.42 |
| 10 bps | 0.00 bps | 0.00% | 0.00 |

**Phase 3C gate: FAIL**

## Important

The monthly doubling target is an objective only. It never raises leverage, position size, trade frequency, or loss tolerance. This corrected report ranks failed candidates honestly when none clears the development gate; it does not lower the gate to create a PASS.
