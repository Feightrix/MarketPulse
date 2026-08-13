# MarketPulse Micro — Phase 3 Intraday Validation

**Data:** Alpaca IEX 5-minute bars, 2021-01-01 through 2026-07-31  
**Universe:** SPY, QQQ, IWM  
**Starting capital model:** $100, 1x buying power, long-only  
**Base friction:** 2.0 bps one-way  

## Selected micro setup

- Take profit: **0.20%**
- Stop loss: **0.15%**
- Maximum hold: **20 minutes**
- RSI reclaim: **48**
- Max trades/day: **3**
- Cooldown: **15 minutes**

## Results

| Period | Trades | Return | Win rate | Expectancy | Profit factor | Daily Sharpe | Max DD | Positive days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Train 2021-2023 | 459 | -16.49% | 34.20% | -3.92 bps/trade | 0.49 | -5.75 | 16.89% | 32.95% |
| Validation 2024 | 161 | -4.17% | 39.75% | -2.64 bps/trade | 0.57 | -4.14 | 4.17% | 39.66% |
| Untouched holdout 2025-2026-07 | 285 | -6.58% | 38.60% | -2.38 bps/trade | 0.62 | -3.84 | 7.36% | 40.69% |

## Friction stress — untouched holdout

| One-way friction | Expectancy | Return | Profit factor |
|---:|---:|---:|---:|
| 2 bps | -2.38 bps/trade | -6.58% | 0.62 |
| 4 bps | -6.01 bps/trade | -15.81% | 0.31 |
| 6 bps | -9.60 bps/trade | -24.17% | 0.16 |
| 10 bps | -16.71 bps/trade | -38.74% | 0.05 |

**Nearby parameter combinations profitable on holdout:** 0/9  
**Phase 3 historical gate:** FAIL

## Important

This is a historical simulation, not a guarantee or promise of profit. Micro strategies are especially sensitive to spread, slippage, fills, data-feed differences, taxes, and market regime changes. The exact locked strategy must pass live paper trading before any real-money automation is considered.
