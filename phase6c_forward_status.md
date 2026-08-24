# MarketPulse Phase 6C — Forward-Test Governance

**Status: FORWARD_TEST_ACCUMULATING**

- Timestamp UTC: 2026-08-24T21:40:10.230967+00:00
- Paper endpoint only: **https://paper-api.alpaca.markets**
- Real-money trading: **LOCKED**
- Baseline: **2026-08-14T15:33:00.739322+00:00** at **$100,000.00**
- Current equity: **$99,763.29**
- Trading days observed: **7 / 126**
- Rebalances observed: **1 / 6**

## Forward performance
- Cumulative return: **-0.24%**
- Max drawdown: **0.34%**
- Positive completed months: **0.0%**
- Worst completed month: **+0.00%**

## Execution
- Filled orders: **10 / 10**
- Fill rate: **100.0%**
- Failed orders: **0**
- Median adverse slippage: **0.03 bps**
- 95th percentile adverse slippage: **2.28 bps**
- Current target tracking L1 error: **10.55%**

## Promotion gate
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
- WAIT/FAIL — tracking_l1_error_at_most_8pct

## Rule
Phase 6C can only mark the strategy **PROMOTION_REVIEW_ELIGIBLE**. It cannot enable live trading or modify Phase 5H parameters.

Paper trading is simulated and does not guarantee live-trading results.
