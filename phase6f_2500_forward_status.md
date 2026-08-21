# MarketPulse Phase 6F — $2,500 Forward Test

**Status: FORWARD_TEST_ACCUMULATING**

- Timestamp UTC: 2026-08-21T21:35:53.723999+00:00
- Official close equity: **$2,496.53**
- Daily P/L: **$+4.77 (+0.1914%)**
- Cumulative P/L: **$-3.47 (-0.1388%)**
- Max drawdown: **0.33%**
- Trading days: **5 / 126**
- Rebalances: **1 / 6**
- Close mark source: **Phase 6F 4pm ET close-window read**
- Operating-floor buffer: **$296.53** above $2,200

## Execution
- Filled orders: **10 / 10**
- Fill rate: **100.0%**
- Failed orders: **0**
- Slippage reference: **IEX bid/ask midpoint nearest order submission**
- Median adverse slippage: **0.43 bps**
- 95th percentile adverse slippage: **1.39 bps**
- Executed-portfolio tracking L1 error: **1.04%**
- Current capital-fit gate: **PASS**

## Holdings
- BIL: +11.6069 shares | value $+1,063.20 | unrealized $+0.58
- IWM: +0.858513 shares | value $+257.48 | unrealized $-3.18
- QQQ: +0.20436 shares | value $+145.71 | unrealized $-4.36
- SPY: +0.371074 shares | value $+284.03 | unrealized $-3.83
- XLE: +3.46665 shares | value $+220.55 | unrealized $+5.23
- XLK: +0.328904 shares | value $+60.30 | unrealized $-2.74
- XLP: +1.72498 shares | value $+148.33 | unrealized $+2.05
- XLU: -1 shares | value $-42.82 | unrealized $+1.06
- XLV: +0.373424 shares | value $+65.14 | unrealized $+2.51
- XLY: -1 shares | value $-118.02 | unrealized $-0.76

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
