# MarketPulse Phase 5F — Volatility-Targeted Diversified Trend

**Gate: FAIL**

## Architecture
Each asset qualifies independently using its own trend. Qualified assets are inverse-volatility weighted, then the entire risky sleeve is scaled to a volatility target. Unused capital remains in BIL.

## Selected configuration
- Momentum lookback: **252 days**
- Trend SMA: **150 days**
- Volatility window: **42 days**
- Portfolio target volatility: **8.0% annualized**
- Maximum risky allocation: **50%**
- Rebalance: **monthly**

## Development 2008–2014
- Valid candidates: **41 / 72**
- 2 bps: return +54.92% | CAGR +6.46% | DD 7.00% | positive months 67.5% | [2008:+4.85%, 2009:+3.34%, 2010:+5.52%, 2011:+7.28%, 2012:+4.09%, 2013:+12.58%, 2014:+7.79%]
- 10 bps: return +52.49% | CAGR +6.22% | DD 7.18% | positive months 66.3% | [2008:+4.51%, 2009:+2.94%, 2010:+5.27%, 2011:+7.11%, 2012:+3.92%, 2013:+12.42%, 2014:+7.60%]

## Validation 2015–2019
- 2 bps: return +17.74% | CAGR +3.32% | DD 8.85% | positive months 69.5% | [2015:-4.58%, 2016:+4.03%, 2017:+8.13%, 2018:-1.11%, 2019:+10.93%]
- 10 bps: return +16.25% | CAGR +3.06% | DD 9.11% | positive months 67.8% | [2015:-5.00%, 2016:+3.74%, 2017:+7.95%, 2018:-1.30%, 2019:+10.70%]

## Holdout 2020–2023
- 2 bps: return +13.61% | CAGR +3.25% | DD 11.43% | positive months 59.6% | [2020:+7.30%, 2021:+7.17%, 2022:-4.26%, 2023:+3.19%]
- 10 bps: return +11.92% | CAGR +2.86% | DD 11.80% | positive months 59.6% | [2020:+6.95%, 2021:+6.87%, 2022:-4.62%, 2023:+2.66%]

## Final holdout 2024–2026 YTD
- 2 bps: return +26.21% | CAGR +9.46% | DD 4.93% | positive months 70.0% | [2024:+11.83%, 2025:+9.16%, 2026:+3.39%]
- 10 bps: return +25.40% | CAGR +9.18% | DD 4.98% | positive months 70.0% | [2024:+11.61%, 2025:+8.87%, 2026:+3.20%]

## Gate checks
- PASS — development_2008_2014_all_positive
- FAIL — validation_2015_positive_10bps
- PASS — validation_2016_positive_10bps
- PASS — validation_2017_positive_10bps
- FAIL — validation_2018_positive_10bps
- PASS — validation_2019_positive_10bps
- FAIL — validation_drawdown
- PASS — holdout1_2020_positive_10bps
- PASS — holdout1_2021_positive_10bps
- FAIL — holdout1_2022_positive_10bps
- PASS — holdout1_2023_positive_10bps
- FAIL — holdout1_drawdown
- PASS — holdout2_2024_positive_10bps
- PASS — holdout2_2025_positive_10bps
- PASS — holdout2_2026_positive_10bps
- PASS — holdout2_drawdown

## Failure reasons
- validation_2015_positive_10bps
- validation_2018_positive_10bps
- validation_drawdown
- holdout1_2022_positive_10bps
- holdout1_drawdown

## Research status
Research only. Historical consistency under this protocol does not guarantee future profit. Independent data validation and paper trading remain mandatory.
