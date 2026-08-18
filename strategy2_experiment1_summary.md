# MarketPulse Strategy 2 — Experiment 1

**Single variable:** trend `RISK_CAP` 50% → 60%
**Gate: FAIL**

Everything else remains frozen to the control strategy. Results below use 10 bps transaction-cost stress.

## development_2008_2014
- Control: return +34.93% | CAGR +4.38% | DD 4.49%
- Candidate: return +42.70% | CAGR +5.21% | DD 5.23%

## validation_2015_2019
- Control: return +6.74% | CAGR +1.32% | DD 6.84%
- Candidate: return +8.01% | CAGR +1.56% | DD 7.86%

## holdout1_2020_2023
- Control: return +13.81% | CAGR +3.30% | DD 3.80%
- Candidate: return +14.29% | CAGR +3.40% | DD 4.80%

## holdout2_2024_2026
- Control: return +21.70% | CAGR +7.93% | DD 2.99%
- Candidate: return +23.94% | CAGR +8.70% | DD 3.55%

## Predeclared activation checks
- PASS — recent_holdout_return_improves
- PASS — recent_holdout_drawdown_not_over_control_plus_1pp
- PASS — prior_holdout_return_not_worse_by_more_than_1pp
- FAIL — prior_holdout_drawdown_not_over_control_plus_1pp

**Activation decision: KEEP Strategy 2 at control baseline**
