# Strategy 2 Experiment 4 — Controlled Pyramiding

**Gate: FAIL**

Base candidate is Strategy 1 with RISK_CAP 57.5%. Pyramiding is research-only unless every gate passes.

## Locked pyramid rule
- Trigger: prior close at least **+3.0%** from monthly entry
- Add: **25% of base position weight**, capped at **2.5 portfolio points**
- Funding: **reduce BIL by the same amount**
- Maximum: **one add per symbol per month**
- Original trend signal must still be valid
- No leverage / no increase in total portfolio weight
- Transaction-cost stress: **10 bps**

## development_2008_2014
- 57.5 base: return +40.895% | DD 5.046% | avg day $+0.580
- Pyramid: return +42.973% | DD 5.213% | avg day $+0.610
- Pyramid events: **135**

## validation_2015_2019
- 57.5 base: return +7.583% | DD 7.725% | avg day $+0.151
- Pyramid: return +7.417% | DD 7.788% | avg day $+0.148
- Pyramid events: **95**

## holdout1_2020_2023
- 57.5 base: return +14.280% | DD 4.555% | avg day $+0.355
- Pyramid: return +13.177% | DD 4.903% | avg day $+0.328
- Pyramid events: **70**

## holdout2_2024_2026
- 57.5 base: return +23.302% | DD 3.414% | avg day $+0.902
- Pyramid: return +22.792% | DD 3.788% | avg day $+0.882
- Pyramid events: **89**

## Predeclared checks
- FAIL — recent_holdout_return_beats_57_5_base
- FAIL — recent_holdout_avg_daily_pnl_beats_57_5_base
- PASS — recent_holdout_drawdown_within_original_control_plus_1pp
- FAIL — prior_holdout_return_not_worse_than_57_5_by_more_than_0_5pp
- FAIL — prior_holdout_drawdown_within_original_control_plus_1pp

**Decision: DO NOT ACTIVATE PYRAMIDING**
