# MarketPulse — Phase 3D Cross-Sectional Swing Momentum

**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)
**Universe:** 20 liquid ETFs / large-cap stocks
**Candidates tested:** 288
**Development-valid candidates:** 240
**Candidates positive in both 2024 and 2025:** 20

## Selected setup

- Rank by prior **20-day** return
- Require price within **1.0%** of a prior **20-day high**
- Trend gate: **fast**
- Hold up to **10 trading days**
- Stop: **5.0%**
- Target: **6.0%**
- Capital deployed: **95%**, long-only, no leverage

## Results

| Period | Trades | Return | Win rate | Expectancy | PF | Max DD | Positive months | Best month | Median month | Doubled months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Development 2021-2023 | 95 | 136.24% | 60.00% | 100.4 bps | 1.66 | 21.96% | 55.56% | 15.10% | 1.05% | 0/36 |
| Validation 2024 | 37 | 49.06% | 59.46% | 116.6 bps | 1.87 | 11.63% | 58.33% | 14.46% | 3.84% | 0/12 |
| Validation 2025 | 34 | 40.64% | 64.71% | 110.1 bps | 1.69 | 20.62% | 50.00% | 27.34% | 0.78% | 0/12 |
| Validation 2024-2025 | 71 | 102.70% | 61.97% | 108.8 bps | 1.70 | 20.62% | 54.17% | 27.34% | 2.21% | 0/24 |
| 2026 check through Jul | 27 | 27.19% | 55.56% | 100.1 bps | 1.51 | 14.84% | 85.71% | 15.03% | 3.75% | 0/7 |

## Validation friction stress

| One-way friction | Expectancy | Return | PF |
|---:|---:|---:|---:|
| 5 bps | 108.8 bps | 102.70% | 1.70 |
| 10 bps | 78.1 bps | 65.15% | 1.44 |
| 20 bps | 77.4 bps | 63.33% | 1.46 |
| 30 bps | 8.8 bps | -0.11% | 1.00 |

**Phase 3D gate: PASS**

## Important

This is a genuinely different strategy class from the earlier intraday micro tests. It reduces trading frequency and seeks larger multi-day moves. The fixed present-day universe may create survivorship bias, so even a PASS would require a separate universe-robustness test and paper execution before any real-money use. The 2× monthly objective is a scorecard, not an expected or guaranteed return.
