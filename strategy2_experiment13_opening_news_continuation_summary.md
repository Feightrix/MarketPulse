# Experiment 13 — Overnight Primary-Event Opening Continuation

**Gate: FAIL**

Research only; long-only; no leverage or broker orders. This is a new opening-continuation branch, not an independent OOS test.

## 2024 development
- Primary-event sessions 99 | qualified setups 10 | trades 8
- Ending equity $2435.45 | return -2.582% | DD 2.582%
- Win rate 12.50% | PF 0.188 | avg trade $-8.07
- Avg winner/loser $14.97/$11.36 | ratio 1.32

## Development checks
- PASS — development_trades_at_least_8
- FAIL — development_positive_after_costs
- FAIL — development_positive_expectancy
- FAIL — development_profit_factor_at_least_1_2
- FAIL — development_winner_loser_at_least_1_50
- PASS — development_drawdown_within_limit

2025 robustness was not opened because 2024 failed.

Activation remains OFF. Historical PASS would only justify forward shadow testing.
