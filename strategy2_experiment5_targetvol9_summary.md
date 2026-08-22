# MarketPulse Strategy 2 — Experiment 5: TARGET_VOL 9%

**Single variable:** `TARGET_VOL` 8% → 9% with `RISK_CAP = 57.5%` fixed.
**Gate: PASS**

Everything else remains frozen. Results use 10 bps transaction-cost stress.

## development_2008_2014
- Control 50% / 8% vol: return +34.932% | CAGR +4.376% | DD 4.490% | avg day $+0.496
- Base 57.5% / 8% vol: return +40.895% | CAGR +5.023% | DD 5.046% | avg day $+0.580
- Candidate 57.5% / 9% vol: return +41.378% | CAGR +5.075% | DD 5.046% | avg day $+0.587
- Candidate best/worst day: $+48.02 / $-43.86

## validation_2015_2019
- Control 50% / 8% vol: return +6.744% | CAGR +1.316% | DD 6.836% | avg day $+0.134
- Base 57.5% / 8% vol: return +7.583% | CAGR +1.474% | DD 7.725% | avg day $+0.151
- Candidate 57.5% / 9% vol: return +7.426% | CAGR +1.445% | DD 7.879% | avg day $+0.148
- Candidate best/worst day: $+40.14 / $-43.67

## holdout1_2020_2023
- Control 50% / 8% vol: return +13.809% | CAGR +3.296% | DD 3.802% | avg day $+0.344
- Base 57.5% / 8% vol: return +14.280% | CAGR +3.403% | DD 4.555% | avg day $+0.355
- Candidate 57.5% / 9% vol: return +14.674% | CAGR +3.492% | DD 4.558% | avg day $+0.365
- Candidate best/worst day: $+37.78 / $-37.05

## holdout2_2024_2026
- Control 50% / 8% vol: return +21.642% | CAGR +7.901% | DD 2.993% | avg day $+0.838
- Base 57.5% / 8% vol: return +23.302% | CAGR +8.470% | DD 3.414% | avg day $+0.902
- Candidate 57.5% / 9% vol: return +23.353% | CAGR +8.488% | DD 3.414% | avg day $+0.904
- Candidate best/worst day: $+32.71 / $-39.25

## Predeclared checks
- PASS — recent_holdout_return_beats_57_5_base
- PASS — recent_holdout_avg_daily_pnl_beats_57_5_base
- PASS — recent_holdout_drawdown_within_original_control_plus_1pp
- PASS — prior_holdout_return_not_worse_than_57_5_by_more_than_0_5pp
- PASS — prior_holdout_drawdown_within_original_control_plus_1pp

**Decision: PASS FOR FURTHER VALIDATION**
