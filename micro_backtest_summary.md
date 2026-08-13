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
| Train 2021-2023 | 82 | -3.31% | 31.71% | -4.10 bps/trade | 0.49 | -5.16 | 3.52% | 31.71% |
| Validation 2024 | 26 | -0.59% | 34.62% | -2.26 bps/trade | 0.58 | -3.53 | 0.75% | 34.62% |
| Untouched holdout 2025-2026-07 | 46 | 0.40% | 50.00% | 0.88 bps/trade | 1.15 | 1.04 | 1.25% | 50.00% |

## Friction stress — untouched holdout

| One-way friction | Expectancy | Return | Profit factor |
|---:|---:|---:|---:|
| 2 bps | 0.88 bps/trade | 0.40% | 1.15 |
| 4 bps | -2.87 bps/trade | -1.32% | 0.64 |
| 6 bps | -7.20 bps/trade | -3.26% | 0.31 |
| 10 bps | -14.05 bps/trade | -6.27% | 0.08 |

**Nearby parameter combinations profitable on holdout:** 7/9  
**Phase 3 historical gate:** FAIL

## Important

This is a historical simulation, not a guarantee or promise of profit. Micro strategies are especially sensitive to spread, slippage, fills, data-feed differences, taxes, and market regime changes. The exact locked strategy must pass live paper trading before any real-money automation is considered.
