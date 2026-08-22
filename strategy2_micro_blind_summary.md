# Strategy 2 Micro Profit-Lock — Blind Backtest

**Gate: FAIL**

## Locked protocol
- Holdout: **2026-01-02 through 2026-07-31**
- Parameters were frozen before results were viewed.
- Historical data: Alpaca IEX **1-minute** bars.
- Live monitor is 15-second sampling; this test uses one close-price observation per eligible minute.
- Modeled execution cost: **10 bps per fill**.
- No parameter changes are permitted after viewing this result.

## Control
- Final equity: **$2553.59**
- Total return: **+2.14%**
- Max drawdown: **2.09%**
- Average daily P&L: **$+0.37**

## Micro profit-lock
- Final equity: **$2386.21**
- Total return: **-4.55%**
- Max drawdown: **5.63%**
- Average daily P&L: **$-0.78**
- Positive-day rate: **51.4%**
- Total round trips: **632**
- Average round trips/day: **4.4**
- Max round trips in one day: **31**
- Kill-switch days: **0**
- Total modeled execution costs: **$178.34**

## Blind gate checks
- FAIL — micro_net_positive_after_costs
- FAIL — micro_beats_control_total_return
- FAIL — micro_drawdown_within_control_plus_1pp

## Stretch checks
- FAIL — avg_daily_pnl_at_least_7_50
- FAIL — avg_round_trips_per_day_at_least_25

## Interpretation rule
If the gate fails, the current micro-profit concept is rejected as configured. We do not loosen the gate or retune these parameters using this holdout.

Research only. Paper/simulated performance does not guarantee live execution or returns.
