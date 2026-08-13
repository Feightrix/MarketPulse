# MarketPulse Phase 2 — Strategy Validation

**Data through:** 2026-04-15
**Parameter combinations tested:** 96
**Trading friction assumed:** 5 bps one-way per unit of turnover

## Selected strategy

- Short momentum lookback: 63 trading days
- Long momentum lookback: 252 trading days
- Trend filter: 200-day moving average
- Hold top: 1 qualifying asset(s)
- Rebalance every: 10 trading days
- Volatility target: 15.00%
- Volatility lookback: 60 trading days
- Universe: SPY, QQQ, GLD, HYG, USO
- Goes to cash when no asset has both positive long momentum and a positive trend filter

## Results

| Period | CAGR | Sharpe | Max drawdown | Positive months | $100 became | SPY CAGR | SPY drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Train 2010-2018 | 9.72% | 0.74 | -15.79% | 61.05% | $209.55 | 11.42% | -19.35% |
| Validation 2019-2022 | 14.37% | 0.92 | -21.12% | 65.96% | $170.90 | 13.09% | -33.72% |
| Untouched holdout 2023-2026-04-15 | 25.66% | 1.43 | -15.95% | 71.79% | $211.52 | 21.95% | -18.76% |

## Robustness check

- Block-bootstrap estimated probability of a positive 3-month result: **70.08%**
- Block-bootstrap estimated probability of a positive 1-year result: **84.00%**
- Phase 2 validation gate: **PASS**

## Important

This backtest does not guarantee future profit. Historical data, parameter selection, execution assumptions, slippage, taxes, market structure changes, and future regimes can all cause live performance to differ materially. MarketPulse will not move to real-money automation solely because a backtest passes.
