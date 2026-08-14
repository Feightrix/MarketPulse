# MarketPulse Phase 5C — Long/Inverse Trend

**Gate: FAIL**

## Strategy
Trade medium-horizon trends in SPY, QQQ, IWM and TLT. Positive confirmed trends use the ordinary ETF; negative confirmed trends use a corresponding inverse ETF (SH, PSQ, RWM, TBF). BIL is the defensive fallback.

## Selected configuration
- Lookback: **252 days**
- Trend filter: **100-day SMA**
- Hold strongest: **4** confirmed trends
- Rebalance: **monthly**

## Development 2010–2015
- Valid candidates: **0 / 36**
- 2 bps: return +70.56% | CAGR +9.33% | DD 16.40% | positive months 60.6% | trades 64 | [2010:+7.29%, 2011:+18.00%, 2012:+3.79%, 2013:+29.21%, 2014:+11.03%, 2015:-9.52%]
- 10 bps: return +65.94% | CAGR +8.83% | DD 16.40% | positive months 59.2% | trades 64 | [2010:+6.95%, 2011:+17.45%, 2012:+3.13%, 2013:+28.86%, 2014:+10.65%, 2015:-10.17%]

## Validation 2016–2019
- 2 bps: return -8.87% | CAGR -2.30% | DD 29.45% | positive months 57.4% | trades 47 | [2016:-3.79%, 2017:+17.95%, 2018:-10.78%, 2019:-10.00%]
- 10 bps: return -11.21% | CAGR -2.94% | DD 30.39% | positive months 57.4% | trades 47 | [2016:-4.62%, 2017:+17.58%, 2018:-11.43%, 2019:-10.62%]

## Holdout 2020–2023
- 2 bps: return +27.11% | CAGR +6.20% | DD 20.60% | positive months 63.8% | trades 48 | [2020:+11.88%, 2021:+17.85%, 2022:+0.44%, 2023:-4.01%]
- 10 bps: return +23.00% | CAGR +5.33% | DD 21.06% | positive months 63.8% | trades 48 | [2020:+10.92%, 2021:+17.31%, 2022:-0.39%, 2023:-5.11%]

## Final holdout 2024–2026 YTD
- 2 bps: return +19.27% | CAGR +7.09% | DD 13.68% | positive months 63.3% | trades 31 | [2024:+13.41%, 2025:+5.71%, 2026:-0.51%]
- 10 bps: return +17.43% | CAGR +6.44% | DD 13.93% | positive months 63.3% | trades 31 | [2024:+12.83%, 2025:+5.13%, 2026:-1.00%]

## Gate checks
- FAIL — development_2010_2015_all_positive
- FAIL — validation_2016_positive_10bps
- PASS — validation_2017_positive_10bps
- FAIL — validation_2018_positive_10bps
- FAIL — validation_2019_positive_10bps
- FAIL — validation_drawdown
- PASS — holdout1_2020_positive_10bps
- PASS — holdout1_2021_positive_10bps
- FAIL — holdout1_2022_positive_10bps
- FAIL — holdout1_2023_positive_10bps
- FAIL — holdout1_drawdown
- PASS — holdout2_2024_positive_10bps
- PASS — holdout2_2025_positive_10bps
- FAIL — holdout2_2026_positive_10bps
- PASS — holdout2_drawdown

## Failure reasons
- development_2010_2015_all_positive
- validation_2016_positive_10bps
- validation_2018_positive_10bps
- validation_2019_positive_10bps
- validation_drawdown
- holdout1_2022_positive_10bps
- holdout1_2023_positive_10bps
- holdout1_drawdown
- holdout2_2026_positive_10bps

## Research status
Research only. A PASS would not guarantee future profit and would move only to independent validation plus paper trading.
