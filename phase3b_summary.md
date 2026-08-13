# MarketPulse Micro — Phase 3B Selective Intraday Validation

**Candidates tested:** 320
**Development-valid candidates:** 56
**Candidates passing both 2024 and 2025 validation:** 0

## Selected setup

- Variant: **pullback9_vr10_atr8_tr5**
- Dynamic target: **2.00 × ATR**, bounded to 0.40%–0.80%
- Dynamic stop: **0.80 × ATR**, bounded to 0.25%–0.50%
- Maximum hold: **30 minutes**
- Maximum trades/day: **1**
- Required target/cost buffer: **8× estimated round-trip friction**

## Results

| Period | Trades | Return | Win rate | Expectancy | Profit factor | Sharpe | Max DD | Target exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development 2021-2023 | 126 | 1.84% | 52.38% | 1.48 bps/trade | 1.14 | 0.90 | 2.09% | 9.52% |
| Validation 2024 | 38 | -1.89% | 42.11% | -5.01 bps/trade | 0.54 | -3.93 | 2.06% | 2.63% |
| Validation 2025 | 31 | -1.72% | 35.48% | -5.57 bps/trade | 0.56 | -3.52 | 2.61% | 3.23% |
| Validation 2024-2025 | 69 | -3.58% | 39.13% | -5.26 bps/trade | 0.55 | -3.70 | 4.50% | 2.90% |
| 2026 check through Jul | 17 | 0.49% | 52.94% | 2.91 bps/trade | 1.50 | 2.48 | 0.56% | 5.88% |

## Execution-friction stress — 2024-2025 validation

| One-way friction | Expectancy | Return | Profit factor |
|---:|---:|---:|---:|
| 2 bps | -5.26 bps/trade | -3.58% | 0.55 |
| 4 bps | -17.13 bps/trade | -1.37% | 0.43 |
| 6 bps | 0.00 bps/trade | 0.00% | 0.00 |
| 10 bps | 0.00 bps/trade | 0.00% | 0.00 |

## 2026 execution-friction check

| One-way friction | Expectancy | Return | Profit factor |
|---:|---:|---:|---:|
| 2 bps | 2.91 bps/trade | 0.49% | 1.50 |
| 4 bps | 4.47 bps/trade | 0.22% | 1.73 |
| 6 bps | 0.00 bps/trade | 0.00% | 0.00 |
| 10 bps | 0.00 bps/trade | 0.00% | 0.00 |

**Nearby exit settings profitable on 2024-2025:** 0/9
**Phase 3B gate: FAIL**

## Important

This phase deliberately trades less often. It requires trend, liquidity, volatility, and execution-cost headroom before an entry is permitted. Because 2026 was observed during earlier experiments, it is a forward-like check rather than a pristine holdout. A PASS still does not authorize real-money trading; it only permits the next paper-order phase.
