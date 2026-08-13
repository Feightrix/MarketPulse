# MarketPulse Micro — Strategy Family Comparison

**Candidates tested:** 180
**Development-valid candidates:** 9
**Candidates positive in both 2024 and 2025 validation:** 0

## Selected candidate

- Family: **breakout_6bar**
- Take profit: **0.45%**
- Stop loss: **0.40%**
- Maximum hold: **60 minutes**

## Period results

| Period | Trades | Return | Win rate | Expectancy | Profit factor | Sharpe | Max DD | Positive days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development 2021-2023 | 89 | 0.03% | 53.93% | 0.07 bps/trade | 1.00 | 0.04 | 2.73% | 53.93% |
| Validation 2024 | 30 | -0.56% | 50.00% | -1.84 bps/trade | 0.80 | -1.37 | 1.22% | 50.00% |
| Validation 2025 | 30 | 0.02% | 40.00% | 0.10 bps/trade | 1.01 | 0.07 | 0.74% | 40.00% |
| 2026 check through Jul | 16 | 0.60% | 62.50% | 3.78 bps/trade | 1.51 | 2.57 | 0.58% | 62.50% |

## 2026 friction stress

| One-way friction | Expectancy | Return | Profit factor |
|---:|---:|---:|---:|
| 2 bps | 3.78 bps/trade | 0.60% | 1.51 |
| 4 bps | 0.33 bps/trade | 0.05% | 1.03 |
| 6 bps | -8.41 bps/trade | -1.34% | 0.40 |
| 10 bps | -15.05 bps/trade | -2.39% | 0.21 |

**Micro family gate: FAIL**

## Important

This comparison deliberately tests multiple fixed micro-trading families rather than repeatedly tuning one failed setup. Because earlier experiments have already exposed later-year data, 2026 is described as a forward-like check rather than a pristine untouched holdout. A passing historical gate still requires live paper trading before any real-money use.
