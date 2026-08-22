# MarketPulse Strategy 2 — Experiment 6: Adaptive Risk Cap

**Gate: FAIL**

Locked rule before results:
- Defensive cap: **50.0%** when breadth <= 3 or estimated portfolio vol >= 20%
- Normal cap: **57.5%**
- Offensive cap: **60.0%** when breadth >= 5 and estimated portfolio vol <= 15%
- Target volatility remains **8.0%**
- Signals use prior-close data; rebalance remains monthly; 10 bps transaction-cost stress.

## development_2008_2014
- Control 50%: return +34.932% | CAGR +4.376% | DD 4.490% | avg day $+0.496
- Base 57.5%: return +40.895% | CAGR +5.023% | DD 5.046% | avg day $+0.580
- Adaptive: return +42.239% | CAGR +5.166% | DD 5.231% | avg day $+0.599
- Adaptive best/worst day: $+49.80 / $-45.57
- Monthly regimes: {'defensive_50': 0, 'normal_57_5': 7, 'offensive_60': 47}

## validation_2015_2019
- Control 50%: return +6.744% | CAGR +1.316% | DD 6.836% | avg day $+0.134
- Base 57.5%: return +7.583% | CAGR +1.475% | DD 7.725% | avg day $+0.151
- Adaptive: return +8.612% | CAGR +1.668% | DD 7.485% | avg day $+0.171
- Adaptive best/worst day: $+40.13 / $-45.69
- Monthly regimes: {'defensive_50': 2, 'normal_57_5': 9, 'offensive_60': 40}

## holdout1_2020_2023
- Control 50%: return +13.809% | CAGR +3.296% | DD 3.802% | avg day $+0.344
- Base 57.5%: return +14.280% | CAGR +3.403% | DD 4.555% | avg day $+0.355
- Adaptive: return +13.074% | CAGR +3.128% | DD 4.442% | avg day $+0.325
- Adaptive best/worst day: $+37.27 / $-37.60
- Monthly regimes: {'defensive_50': 5, 'normal_57_5': 9, 'offensive_60': 17}

## holdout2_2024_2026
- Control 50%: return +21.642% | CAGR +7.901% | DD 2.993% | avg day $+0.838
- Base 57.5%: return +23.302% | CAGR +8.470% | DD 3.414% | avg day $+0.902
- Adaptive: return +23.859% | CAGR +8.660% | DD 3.554% | avg day $+0.923
- Adaptive best/worst day: $+34.13 / $-40.91
- Monthly regimes: {'defensive_50': 1, 'normal_57_5': 0, 'offensive_60': 27}

## Predeclared checks
- PASS — recent_holdout_return_beats_57_5_base
- PASS — recent_holdout_avg_daily_pnl_beats_57_5_base
- PASS — recent_holdout_drawdown_within_original_control_plus_1pp
- FAIL — prior_holdout_return_not_worse_than_57_5_by_more_than_0_5pp
- PASS — prior_holdout_drawdown_within_original_control_plus_1pp
- PASS — validation_return_not_worse_than_57_5_by_more_than_0_5pp
- PASS — validation_drawdown_at_most_8pct

**Decision: REJECT ADAPTIVE CAP CANDIDATE**
