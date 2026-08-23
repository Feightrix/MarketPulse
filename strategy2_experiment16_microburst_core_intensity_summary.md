# Experiment 16 — MicroBurst Core Consensus + Intensity v3

**Gate: FAIL**

Feed: **sip** (SIP_FULL_MARKET) | untouched test dates: 2026-08-05, 2026-08-06, 2026-08-07, 2026-08-10, 2026-08-11
Windows loaded: 90 | quotes/trades: 467,373 / 486,805

## Signal
- Trades 12 | barrier wins 0 | losses 5 | timeouts 7
- Barrier win rate 0.00% vs baseline 9.68% | edge -9.68pp
- Ending equity $2495.66 | return -0.174% | avg trade $-0.362
- PF 0.355 | DD 0.268% | avg return -2.89bps | avg hold 24.8s

## Checks
- PASS — sip_required_for_valid_pass
- PASS — at_least_10_signal_trades
- FAIL — barrier_win_rate_beats_baseline_by_8pp
- FAIL — positive_avg_trade_after_spread_and_impact
- FAIL — profit_factor_at_least_1_25
- PASS — max_drawdown_at_most_3pct

Activation remains OFF. A PASS only justifies a larger walk-forward SIP test.
