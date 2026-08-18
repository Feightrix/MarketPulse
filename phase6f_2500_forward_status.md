# MarketPulse Phase 6F — $2,500 Forward Test

**Status: FORWARD_TEST_ACCUMULATING**

- Timestamp UTC: 2026-08-18T01:13:15.565250+00:00
- Dedicated paper equity: **$2,499.99**
- Daily P/L: **$-0.01 (-0.0004%)**
- Cumulative P/L: **$-0.01 (-0.0004%)**
- Max drawdown: **0.00%**
- Trading days: **1 / 126**
- Rebalances: **1 / 6**
- Operating-floor buffer: **$299.99** above $2,200

## Execution
- Filled orders: **10 / 10**
- Fill rate: **100.0%**
- Failed orders: **0**
- Median adverse slippage: **26.08 bps**
- 95th percentile adverse slippage: **90.77 bps**
- Executed-portfolio tracking L1 error: **0.39%**
- Current capital-fit gate: **PASS**

## Holdings
- BIL: +11.6069 shares | value $+1,062.62 | unrealized $+0.00
- IWM: +0.858513 shares | value $+261.11 | unrealized $+0.45
- QQQ: +0.20436 shares | value $+149.20 | unrealized $-0.87
- SPY: +0.371074 shares | value $+286.70 | unrealized $-1.16
- XLE: +3.46665 shares | value $+217.64 | unrealized $+2.32
- XLK: +0.328904 shares | value $+62.54 | unrealized $-0.49
- XLP: +1.72498 shares | value $+146.07 | unrealized $-0.21
- XLU: -1 shares | value $-44.18 | unrealized $-0.30
- XLV: +0.373424 shares | value $+62.38 | unrealized $-0.25
- XLY: -1 shares | value $-116.75 | unrealized $+0.51

## Forward-test gate
- WAIT/FAIL — minimum_126_trading_days
- WAIT/FAIL — minimum_6_rebalances
- WAIT/FAIL — cumulative_return_positive
- PASS — max_drawdown_at_most_5pct
- WAIT/FAIL — positive_month_rate_at_least_66_7pct
- WAIT/FAIL — worst_completed_month_above_minus_2_5pct
- PASS — fill_rate_at_least_99pct
- PASS — zero_failed_orders
- WAIT/FAIL — median_adverse_slippage_at_most_15bps
- WAIT/FAIL — p95_adverse_slippage_at_most_30bps
- PASS — executed_portfolio_tracking_at_most_5pct
- PASS — current_capital_fit_gate_pass
- PASS — equity_at_or_above_2200_operating_floor

## Rule
Phase 6F is read-only. It cannot place orders, modify Phase 5H, or enable live-money trading.

Paper trading is simulated and does not guarantee live results.
