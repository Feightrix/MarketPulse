# MarketPulse Phase 6F — $2,500 Forward Test

**Status: FORWARD_TEST_ACCUMULATING**

- Timestamp UTC: 2026-08-24T21:41:21.684753+00:00
- Official close equity: **$2,491.69**
- Daily P/L: **$-4.84 (-0.1939%)**
- Cumulative P/L: **$-8.31 (-0.3324%)**
- Max drawdown: **0.33%**
- Trading days: **6 / 126**
- Rebalances: **1 / 6**
- Close mark source: **Phase 6F 4pm ET close-window read**
- Operating-floor buffer: **$291.69** above $2,200

## Execution
- Filled orders: **10 / 10**
- Fill rate: **100.0%**
- Failed orders: **0**
- Slippage reference: **IEX bid/ask midpoint nearest order submission**
- Median adverse slippage: **0.43 bps**
- 95th percentile adverse slippage: **1.39 bps**
- Executed-portfolio tracking L1 error: **1.30%**
- Current capital-fit gate: **PASS**

## Holdings
- BIL: +11.6069 shares | value $+1,063.31 | unrealized $+0.70
- IWM: +0.858513 shares | value $+255.97 | unrealized $-4.69
- QQQ: +0.20436 shares | value $+144.35 | unrealized $-5.72
- SPY: +0.371074 shares | value $+283.40 | unrealized $-4.47
- XLE: +3.46665 shares | value $+218.68 | unrealized $+3.36
- XLK: +0.328904 shares | value $+59.21 | unrealized $-3.83
- XLP: +1.72498 shares | value $+150.68 | unrealized $+4.40
- XLU: -1 shares | value $-43.24 | unrealized $+0.64
- XLV: +0.373424 shares | value $+65.09 | unrealized $+2.46
- XLY: -1 shares | value $-118.37 | unrealized $-1.11

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
