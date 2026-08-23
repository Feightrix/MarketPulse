# Experiment 14 — MicroBurst Long v1

**Gate: FAIL**

Feed: **sip** (SIP_FULL_MARKET) | windows loaded: 54
Quotes/trades processed: 311,951 / 271,646

## Signal
- Trades 20 | barrier wins 2 | losses 9 | timeouts 9
- Barrier win rate 10.00% vs baseline 8.11% | edge +1.89pp
- Ending equity $2493.82 | return -0.247% | avg trade $-0.309
- PF 0.491 | DD 0.274% | avg return -2.48bps | avg hold 22.1s

## Checks
- PASS — sip_required_for_valid_pass
- PASS — at_least_20_signal_trades
- FAIL — barrier_win_rate_beats_baseline_by_8pp
- FAIL — positive_avg_trade_after_spread_and_impact
- FAIL — profit_factor_at_least_1_25
- PASS — max_drawdown_at_most_3pct

Activation remains OFF. This is a small pilot; any promising result requires a larger walk-forward SIP test.
