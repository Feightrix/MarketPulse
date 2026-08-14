# MarketPulse Phase 5G — Gated Volatility Trend

**Gate: FAIL**

## Locked Phase 5F engine
- 252-day momentum / 150-day trend SMA
- 42-day volatility estimator
- 8% target volatility / 50% max risky allocation
- Monthly rebalance

## Selected safety gate
- Minimum eligible breadth: **2 of 8**
- SPY must be above its **200-day SMA** and above its 252-day-ago close

## Development 2008–2014
- Valid gates: **10 / 10**
- 2 bps: return +45.37% | CAGR +5.49% | DD 4.58% | [2008:+1.59%, 2009:+3.31%, 2010:+3.90%, 2011:+6.03%, 2012:+3.61%, 2013:+12.58%, 2014:+7.79%]
- 10 bps: return +43.93% | CAGR +5.34% | DD 4.58% | [2008:+1.59%, 2009:+3.22%, 2010:+3.67%, 2011:+5.88%, 2012:+3.37%, 2013:+12.42%, 2014:+7.60%]

## Validation 2015–2019
- 2 bps: return +11.52% | CAGR +2.21% | DD 7.37% | [2015:-4.30%, 2016:+3.57%, 2017:+8.13%, 2018:-2.38%, 2019:+6.59%]
- 10 bps: return +9.90% | CAGR +1.91% | DD 7.52% | [2015:-4.66%, 2016:+3.28%, 2017:+7.95%, 2018:-2.66%, 2019:+6.21%]

## Holdout 2020–2023
- 2 bps: return +15.28% | CAGR +3.63% | DD 4.53% | [2020:+5.23%, 2021:+7.17%, 2022:-0.92%, 2023:+3.18%]
- 10 bps: return +13.79% | CAGR +3.29% | DD 4.59% | [2020:+4.84%, 2021:+6.87%, 2022:-1.21%, 2023:+2.80%]

## Final holdout 2024–2026 YTD
- 2 bps: return +25.50% | CAGR +9.22% | DD 3.01% | [2024:+11.83%, 2025:+9.09%, 2026:+2.87%]
- 10 bps: return +24.52% | CAGR +8.89% | DD 3.04% | [2024:+11.61%, 2025:+8.74%, 2026:+2.60%]

## Gate checks
- PASS — development_2008_2014_all_positive
- FAIL — validation_2015_positive_10bps
- PASS — validation_2016_positive_10bps
- PASS — validation_2017_positive_10bps
- FAIL — validation_2018_positive_10bps
- PASS — validation_2019_positive_10bps
- PASS — validation_drawdown
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
- validation_2015_positive_10bps
- validation_2018_positive_10bps
- holdout1_2022_positive_10bps

## Research status
Research only. PASS would mean historical consistency under this protocol, not guaranteed future profit.
