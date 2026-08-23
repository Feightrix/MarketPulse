# Experiment 11 — High-Information Catalyst Filter

**Development gate: FAIL**
**Overall gate: FAIL**

Only catalyst-quality filtering changed from Experiment 10. Tape, entry, stop, sizing and cost rules are identical.

## 2024 development
- High-information entity-relevant events: 869
- Trades: 13 | return -0.219% | ending equity $2494.53
- Win rate 30.77% | PF 0.916 | avg trade $-0.42
- Avg winner/loser $14.96 / $7.26 | ratio 2.06
- Max DD 2.065% | best/worst $+23.06 / $-13.68

## Development checks
- PASS — development_trades_at_least_5
- FAIL — development_positive_expectancy
- FAIL — development_profit_factor_at_least_1_20
- PASS — development_winner_loser_at_least_1_25
- PASS — development_drawdown_at_most_5pct

2025 validation was not opened because the predeclared 2024 development gate failed.

Activation remains OFF; this is research only.
