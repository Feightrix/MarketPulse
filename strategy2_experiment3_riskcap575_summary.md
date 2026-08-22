# MarketPulse Strategy 2 — Experiment 3: RISK_CAP 57.5%

**Single variable:** trend `RISK_CAP` 50% → 57.5%
**Gate: PASS**

Everything else remains frozen to Strategy 1. Results use 10 bps transaction-cost stress.

## development_2008_2014
- Control: return +34.932% | CAGR +4.376% | DD 4.490% | avg day $+0.496
- Candidate: return +40.895% | CAGR +5.023% | DD 5.046% | avg day $+0.580
- Candidate best/worst day: $+47.86 / $-43.70

## validation_2015_2019
- Control: return +6.744% | CAGR +1.316% | DD 6.836% | avg day $+0.134
- Candidate: return +7.583% | CAGR +1.474% | DD 7.725% | avg day $+0.151
- Candidate best/worst day: $+40.14 / $-43.67

## holdout1_2020_2023
- Control: return +13.809% | CAGR +3.296% | DD 3.802% | avg day $+0.344
- Candidate: return +14.279% | CAGR +3.403% | DD 4.555% | avg day $+0.355
- Candidate best/worst day: $+37.62 / $-36.02

## holdout2_2024_2026
- Control: return +21.642% | CAGR +7.901% | DD 2.993% | avg day $+0.838
- Candidate: return +23.302% | CAGR +8.470% | DD 3.414% | avg day $+0.902
- Candidate best/worst day: $+32.70 / $-39.24

## Predeclared activation checks
- PASS — recent_holdout_return_improves
- PASS — recent_holdout_drawdown_not_over_control_plus_1pp
- PASS — prior_holdout_return_not_worse_by_more_than_1pp
- PASS — prior_holdout_drawdown_not_over_control_plus_1pp

**Activation decision: PASS FOR FURTHER VALIDATION**
