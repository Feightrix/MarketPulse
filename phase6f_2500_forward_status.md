# MarketPulse Phase 6F — $2,500 Forward Test

**Status: FORWARD_TEST_ACCUMULATING**

- Timestamp UTC: 2026-08-20T20:40:55.854004+00:00
- Official close equity: **$2,491.76**
- Daily P/L: **$-7.54 (-0.3017%)**
- Cumulative P/L: **$-8.24 (-0.3296%)**
- Max drawdown: **0.33%**
- Trading days: **4 / 126**
- Rebalances: **1 / 6**
- Close mark source: **Phase 6F 4pm ET close-window read**
- Operating-floor buffer: **$291.76** above $2,200

## Execution
- Filled orders: **10 / 10**
- Fill rate: **100.0%**
- Failed orders: **0**
- Slippage reference: **IEX bid/ask midpoint nearest order submission**
- Median adverse slippage: **0.43 bps**
- 95th percentile adverse slippage: **1.39 bps**
- Executed-portfolio tracking L1 error: **1.23%**
- Current capital-fit gate: **PASS**

## Holdings
- BIL: +11.6069 shares | value $+1,062.87 | unrealized $+0.25
- IWM: +0.858513 shares | value $+255.42 | unrealized $-5.24
- QQQ: +0.20436 shares | value $+145.29 | unrealized $-4.78
- SPY: +0.371074 shares | value $+283.07 | unrealized $-4.79
- XLE: +3.46665 shares | value $+221.07 | unrealized $+5.75
- XLK: +0.328904 shares | value $+60.22 | unrealized $-2.82
- XLP: +1.72498 shares | value $+147.31 | unrealized $+1.03
- XLU: -1 shares | value $-43.89 | unrealized $-0.01
- XLV: +0.373424 shares | value $+64.45 | unrealized $+1.82
- XLY: -1 shares | value $-116.68 | unrealized $+0.58

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
