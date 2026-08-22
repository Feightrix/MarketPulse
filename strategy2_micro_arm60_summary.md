# Strategy 2 — 0.60% Arm Follow-Up Backtest

This is a **follow-up, non-blind** test. The 0.60% arm threshold was selected after reviewing the prior 0.35% test.

## Frozen setup
- Test: **2026-01-02 through 2026-07-31**
- Only changed variable: **profit-lock arm 0.35% → 0.60%**
- All other execution/risk settings: **unchanged**
- Historical data: **Alpaca IEX 1-minute bars**
- Cost model: **10 bps per fill**

## Results
- Control ending equity: **$2,553.59**
- Control total return: **+2.144%**
- Control max drawdown: **2.088%**
- 0.60% micro ending equity: **$2,443.31**
- 0.60% micro total return: **-2.268%**
- 0.60% micro max drawdown: **3.930%**
- Avg daily P&L: **$-0.39**
- Round trips: **401**
- Avg round trips/day: **2.77**
- Modeled execution costs: **$118.40**

## Gate: **FAIL**
- micro_beats_control_total_return: **FAIL**
- micro_drawdown_within_control_plus_1pp: **FAIL**
- micro_net_positive_after_costs: **FAIL**
