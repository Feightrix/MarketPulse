# MarketPulse Phase 5E — Momentum/Reversal Ensemble

**Gate: FAIL**

## Architecture
- Protective momentum sleeve: **80%**
- Short-term reversal sleeve: **20%**
- Protective rules: locked from Phase 5D
- Reversal entry RSI(2): **<= 10.0**
- Reversal exit RSI(2): **>= 80.0** or **5 days**
- Reversal assets: **SPY / QQQ**, only above 200-day SMA

## Development 2008–2014
- Valid candidates: **0 / 81**
- 2 bps: return +42.88% | CAGR +5.23% | DD 8.18% | positive months 60.2% | [2008:+1.33%, 2009:+4.34%, 2010:+5.05%, 2011:+2.85%, 2012:+1.44%, 2013:+15.31%, 2014:+6.92%]
- 10 bps: return +31.83% | CAGR +4.03% | DD 8.66% | positive months 57.8% | [2008:+1.08%, 2009:+3.05%, 2010:+3.52%, 2011:+1.61%, 2012:-0.52%, 2013:+14.06%, 2014:+6.03%]

## Validation 2015–2019
- 2 bps: return -1.30% | CAGR -0.26% | DD 11.73% | positive months 57.6% | [2015:-5.31%, 2016:-4.19%, 2017:+6.41%, 2018:-3.89%, 2019:+6.36%]
- 10 bps: return -10.74% | CAGR -2.25% | DD 17.33% | positive months 49.2% | [2015:-7.50%, 2016:-6.07%, 2017:+4.11%, 2018:-5.31%, 2019:+4.20%]

## Holdout 2020–2023
- 2 bps: return +31.30% | CAGR +7.07% | DD 5.96% | positive months 70.2% | [2020:+7.71%, 2021:+13.93%, 2022:-0.11%, 2023:+7.11%]
- 10 bps: return +23.90% | CAGR +5.52% | DD 6.12% | positive months 66.0% | [2020:+6.08%, 2021:+11.64%, 2022:-0.36%, 2023:+5.00%]

## Final holdout 2024–2026 YTD
- 2 bps: return +38.58% | CAGR +13.50% | DD 4.27% | positive months 83.3% | [2024:+14.50%, 2025:+13.89%, 2026:+6.28%]
- 10 bps: return +32.93% | CAGR +11.68% | DD 4.22% | positive months 80.0% | [2024:+11.92%, 2025:+12.56%, 2026:+5.52%]

## Gate checks
- FAIL — development_2008_2014_all_positive
- FAIL — validation_2015_positive_10bps
- FAIL — validation_2016_positive_10bps
- PASS — validation_2017_positive_10bps
- FAIL — validation_2018_positive_10bps
- PASS — validation_2019_positive_10bps
- FAIL — validation_drawdown
- PASS — holdout1_2020_positive_10bps
- PASS — holdout1_2021_positive_10bps
- FAIL — holdout1_2022_positive_10bps
- PASS — holdout1_2023_positive_10bps
- PASS — holdout1_drawdown
- PASS — holdout2_2024_positive_10bps
- PASS — holdout2_2025_positive_10bps
- PASS — holdout2_2026_positive_10bps
- PASS — holdout2_drawdown

## Failure reasons
- development_2008_2014_all_positive
- validation_2015_positive_10bps
- validation_2016_positive_10bps
- validation_2018_positive_10bps
- validation_drawdown
- holdout1_2022_positive_10bps

## Research status
Research only. PASS means the historical protocol was satisfied, not guaranteed future profit. Independent data validation and paper trading remain mandatory.
