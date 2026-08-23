# Experiment 15 — MicroBurst Consensus v2

**Gate: FAIL**

Feed: **sip** (SIP_FULL_MARKET) | untouched test dates: 2026-08-12, 2026-08-13, 2026-08-14, 2026-08-17, 2026-08-18
Windows loaded: 90 | quotes/trades: 388,491 / 403,694

## Signal
- Trades 1 | barrier wins 0 | losses 0 | timeouts 1
- Barrier win rate 0.00% vs baseline 3.10% | edge -3.10pp
- Ending equity $2500.81 | return +0.032% | avg trade $+0.811
- PF 999.000 | DD 0.000% | avg return +6.49bps | avg hold 30.0s

## Checks
- PASS — sip_required_for_valid_pass
- FAIL — at_least_10_consensus_trades
- FAIL — barrier_win_rate_beats_baseline_by_8pp
- PASS — positive_avg_trade_after_spread_and_impact
- PASS — profit_factor_at_least_1_25
- PASS — max_drawdown_at_most_3pct

Activation remains OFF. A PASS would justify a larger walk-forward SIP test, not live trading.
