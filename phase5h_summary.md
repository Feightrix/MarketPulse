# MarketPulse Phase 5H — Defensive Trend + Sector-Neutral Momentum

**Gate: FAIL**

## Architecture
- Locked Phase 5G defensive trend sleeve: **85%**
- Market-neutral sector momentum sleeve: **15%**
- Sector momentum: long winners / short losers across 9 SPDR sectors
- Short borrow stress: **50 bps/year on short notional**

## Selected sector sleeve
- Lookback: **252 days**
- Skip most recent: **0 days**
- Long/short: **top 3 / bottom 3**
- Rebalance: **monthly**

## Development 2008–2014
- Valid ensembles: **10 / 36**
- 2 bps: return +36.51% | CAGR +4.55% | DD 4.34% | [2008:+1.57%, 2009:+1.31%, 2010:+3.40%, 2011:+4.64%, 2012:+2.71%, 2013:+11.77%, 2014:+6.80%]
- 10 bps: return +34.93% | CAGR +4.38% | DD 4.49% | [2008:+1.53%, 2009:+1.19%, 2010:+3.15%, 2011:+4.46%, 2012:+2.46%, 2013:+11.60%, 2014:+6.60%]

## Validation 2015–2019
- 2 bps: return +8.30% | CAGR +1.61% | DD 6.69% | [2015:-3.05%, 2016:+1.28%, 2017:+6.51%, 2018:-2.19%, 2019:+5.88%]
- 10 bps: return +6.74% | CAGR +1.32% | DD 6.84% | [2015:-3.35%, 2016:+0.97%, 2017:+6.32%, 2018:-2.48%, 2019:+5.51%]

## Holdout 2020–2023
- 2 bps: return +15.25% | CAGR +3.62% | DD 3.74% | [2020:+5.57%, 2021:+5.14%, 2022:+1.02%, 2023:+2.78%]
- 10 bps: return +13.81% | CAGR +3.30% | DD 3.80% | [2020:+5.27%, 2021:+4.84%, 2022:+0.72%, 2023:+2.39%]

## Final holdout 2024–2026 YTD
- 2 bps: return +22.55% | CAGR +8.21% | DD 2.96% | [2024:+10.56%, 2025:+7.17%, 2026:+3.43%]
- 10 bps: return +21.64% | CAGR +7.90% | DD 2.99% | [2024:+10.39%, 2025:+6.82%, 2026:+3.16%]

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
- PASS — holdout1_2022_positive_10bps
- PASS — holdout1_2023_positive_10bps
- PASS — holdout1_drawdown
- PASS — holdout2_2024_positive_10bps
- PASS — holdout2_2025_positive_10bps
- PASS — holdout2_2026_positive_10bps
- PASS — holdout2_drawdown

## Failure reasons
- validation_2015_positive_10bps
- validation_2018_positive_10bps

## Research status
Research only. If this fails, continued parameter tuning to force every calendar year positive would be treated as overfitting rather than evidence of a robust strategy.
