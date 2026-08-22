# MarketPulse Strategy 2 — Experiment 2: RISK_CAP 55%

**Single variable:** trend `RISK_CAP` 50% → 55%
**Gate: PASS**

Everything else remains frozen to Strategy 1. Results use 10 bps transaction-cost stress.

## development_2008_2014
- Control: return +34.932% | CAGR +4.376% | DD 4.490% | avg day $+0.496
- Candidate: return +38.945% | CAGR +4.814% | DD 4.861% | avg day $+0.553
- Candidate best/worst day: $+45.92 / $-41.84

## validation_2015_2019
- Control: return +6.744% | CAGR +1.316% | DD 6.836% | avg day $+0.134
- Candidate: return +7.210% | CAGR +1.404% | DD 7.532% | avg day $+0.143
- Candidate best/worst day: $+38.46 / $-41.72

## holdout1_2020_2023
- Control: return +13.809% | CAGR +3.296% | DD 3.802% | avg day $+0.344
- Candidate: return +14.148% | CAGR +3.373% | DD 4.305% | avg day $+0.352
- Candidate best/worst day: $+35.83 / $-34.44

## holdout2_2024_2026
- Control: return +21.642% | CAGR +7.901% | DD 2.993% | avg day $+0.838
- Candidate: return +22.747% | CAGR +8.280% | DD 3.274% | avg day $+0.880
- Candidate best/worst day: $+31.28 / $-37.58

## Predeclared activation checks
- PASS — recent_holdout_return_improves
- PASS — recent_holdout_drawdown_not_over_control_plus_1pp
- PASS — prior_holdout_return_not_worse_by_more_than_1pp
- PASS — prior_holdout_drawdown_not_over_control_plus_1pp

**Activation decision: PASS FOR FURTHER VALIDATION**
