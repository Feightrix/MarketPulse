# MarketPulse Phase 5D — Protective Momentum

**Gate: FAIL**

## Selected configuration
- Lookback: **252 days**
- SMA: **200 days**
- Top assets: **3**
- Minimum breadth: **2 of 8**
- Risk allocation when qualified: **60%**
- SPY confirmation gate: **True**
- Rebalance: **monthly**

## Development 2008–2014
- Valid candidates: **4 / 144**
- 2 bps: return +49.92% | CAGR +5.96% | DD 7.61% | positive months 61.4% | [2008:+1.59%, 2009:+4.38%, 2010:+7.56%, 2011:+3.69%, 2012:+0.72%, 2013:+16.39%, 2014:+8.15%]
- 10 bps: return +47.75% | CAGR +5.74% | DD 7.61% | positive months 62.7% | [2008:+1.59%, 2009:+4.24%, 2010:+7.31%, 2011:+3.41%, 2012:+0.25%, 2013:+16.11%, 2014:+8.00%]

## Validation 2015–2019
- 2 bps: return -4.30% | CAGR -0.88% | DD 14.50% | positive months 52.5% | [2015:-6.54%, 2016:-5.33%, 2017:+6.45%, 2018:-3.34%, 2019:+5.13%]
- 10 bps: return -6.24% | CAGR -1.28% | DD 15.27% | positive months 52.5% | [2015:-6.99%, 2016:-5.82%, 2017:+6.23%, 2018:-3.66%, 2019:+4.58%]

## Holdout 2020–2023
- 2 bps: return +33.85% | CAGR +7.58% | DD 5.35% | positive months 74.5% | [2020:+10.02%, 2021:+12.10%, 2022:+2.31%, 2023:+6.07%]
- 10 bps: return +31.92% | CAGR +7.19% | DD 5.35% | positive months 72.3% | [2020:+9.55%, 2021:+11.75%, 2022:+1.98%, 2023:+5.66%]

## Final holdout 2024–2026 YTD
- 2 bps: return +44.40% | CAGR +15.33% | DD 4.74% | positive months 83.3% | [2024:+13.04%, 2025:+17.70%, 2026:+8.54%]
- 10 bps: return +43.02% | CAGR +14.90% | DD 4.74% | positive months 83.3% | [2024:+12.62%, 2025:+17.31%, 2026:+8.25%]

## Gate checks
- PASS — development_2008_2014_all_positive
- FAIL — validation_2015_positive_10bps
- FAIL — validation_2016_positive_10bps
- PASS — validation_2017_positive_10bps
- FAIL — validation_2018_positive_10bps
- PASS — validation_2019_positive_10bps
- FAIL — validation_drawdown
- PASS — holdout1_2020_positive_10bps
- PASS — holdout1_2021_positive_10bps
- PASS — holdout1_2022_positive_10bps
- PASS — holdout1_2023_positive_10bps
- PASS — holdout1_drawdown
- PASS — holdout2_2024_positive_10bps
- PASS — holdout2_2025_positive_10bps
- PASS — holdout2_2026_positive_10bps
- PASS — holdout2_drawdown

## Failure reasons
- validation_2015_positive_10bps
- validation_2016_positive_10bps
- validation_2018_positive_10bps
- validation_drawdown

## Research status
Research only. PASS would mean historical consistency under this protocol, not guaranteed future profit. Independent data validation and paper trading remain mandatory.
