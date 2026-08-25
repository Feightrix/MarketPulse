# MarketPulse Phase 6F — $2,500 Forward Test

**Status: FORWARD_TEST_ACCUMULATING**

- Timestamp UTC: 2026-08-25T20:41:10.292431+00:00
- Official close equity: **$2,490.36**
- Daily P/L: **$-1.33 (-0.0534%)**
- Cumulative P/L: **$-9.64 (-0.3856%)**
- Max drawdown: **0.39%**
- Trading days: **7 / 126**
- Rebalances: **1 / 6**
- Close mark source: **Phase 6F 4pm ET close-window read**
- Operating-floor buffer: **$290.36** above $2,200

## Execution
- Filled orders: **10 / 10**
- Fill rate: **100.0%**
- Failed orders: **0**
- Slippage reference: **IEX bid/ask midpoint nearest order submission**
- Median adverse slippage: **0.43 bps**
- 95th percentile adverse slippage: **1.39 bps**
- Executed-portfolio tracking L1 error: **0.98%**
- Current capital-fit gate: **PASS**

## Holdings
- BIL: +11.6069 shares | value $+1,063.43 | unrealized $+0.81
- IWM: +0.858513 shares | value $+256.85 | unrealized $-3.81
- QQQ: +0.20436 shares | value $+145.31 | unrealized $-4.76
- SPY: +0.371074 shares | value $+284.23 | unrealized $-3.64
- XLE: +3.46665 shares | value $+214.93 | unrealized $-0.38
- XLK: +0.328904 shares | value $+59.83 | unrealized $-3.20
- XLP: +1.72498 shares | value $+149.09 | unrealized $+2.81
- XLU: -1 shares | value $-43.31 | unrealized $+0.57
- XLV: +0.373424 shares | value $+65.46 | unrealized $+2.83
- XLY: -1 shares | value $-118.09 | unrealized $-0.83

## Forward-test gate
- WAIT/FAIL — minimum_126_trading_days
- WAIT/FAIL — minimum_6_rebalances
- WAIT/FAIL — cumulative_return_positive
- PASS — max_drawdown_at_most_5pct
- WAIT/FAIL — positive_month_rate_at_least_66_7pct
- WAIT/FAIL — worst_completed_month_above_minus_2_5pct
- PASS — fill_rate_at_least_99pct
- PASS — zero_failed_orders
- PASS — median_adverse_slippage_at_most_15bps
- PASS — p95_adverse_slippage_at_most_30bps
- PASS — executed_portfolio_tracking_at_most_5pct
- PASS — current_capital_fit_gate_pass
- PASS — equity_at_or_above_2200_operating_floor

## Rule
Phase 6F is read-only. It cannot place orders, modify Phase 5H, or enable live-money trading.

Paper trading is simulated and does not guarantee live results.
