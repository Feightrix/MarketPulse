# Experiment 10 — Entity-Relevant Abnormal News Momentum

**Gate: FAIL**

Long-only research. Direct headline entity relevance + stock-specific abnormal move/volume/relative-strength + hold/retest. No broker orders or leverage.

## 2024 development
- Entity-relevant events: 2019 | qualified/trades: 27/27
- Ending equity: $2506.13 | return +0.245% | max DD 3.204%
- Win rate 37.04% | PF 1.045 | avg trade $+0.23
- Avg winner/loser $14.16/$7.97 | ratio 1.78 | avg R -0.082
- Best/worst $+26.68/$-13.51

## 2025 validation
- Entity-relevant events: 2254 | qualified/trades: 24/24
- Ending equity: $2327.80 | return -6.888% | max DD 7.174%
- Win rate 16.67% | PF 0.124 | avg trade $-7.18
- Avg winner/loser $6.08/$9.83 | ratio 0.62 | avg R -0.648
- Best/worst $+9.00/$-19.99

## 2024 development checks
- PASS — development_trades_at_least_5
- PASS — development_positive_expectancy
- FAIL — development_profit_factor_at_least_1_20
- PASS — development_winner_loser_at_least_1_25
- PASS — development_drawdown_at_most_5pct

## 2025 validation checks
- PASS — validation_trades_at_least_5
- FAIL — validation_positive_after_costs
- FAIL — validation_positive_expectancy
- FAIL — validation_profit_factor_at_least_1_25
- FAIL — validation_winner_loser_at_least_1_50
- PASS — validation_drawdown_at_most_8pct
- PASS — combined_trades_at_least_15

Activation remains OFF regardless of result; a PASS only justifies separate forward shadow validation.
