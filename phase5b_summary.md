# MarketPulse Phase 5B — Consistency Tournament

**Gate: FAIL**

## Standard
A strategy is rejected if any required calendar year is negative at 10 bps per side. Parameters are chosen only from 2008–2014. Later periods are evaluation blocks.

## Universe
- Risk/rotation assets: **SPY, QQQ, IWM, XLE, XLP, XLU, GLD, TLT**
- Defensive fallback: **BIL**
- Starting equity: **$2,500**
- Historical data: adjusted daily OHLC

## Selected configuration
- Momentum: **252 trading days**
- Trend filter: **150-day SMA**
- Hold top: **3**
- Rebalance: **monthly**

## Development 2008–2014
- Valid candidates: **0 / 54**
- 2 bps: return +121.27% | CAGR +12.02% | DD 15.99% | positive months 59.0% | rebalance trades 75 | [2008:+13.57%, 2009:+9.46%, 2010:+14.64%, 2011:+2.65%, 2012:+3.00%, 2013:+28.49%, 2014:+14.28%]
- 10 bps: return +113.45% | CAGR +11.45% | DD 16.30% | positive months 59.0% | rebalance trades 75 | [2008:+12.88%, 2009:+8.52%, 2010:+14.15%, 2011:+2.20%, 2012:+2.28%, 2013:+28.01%, 2014:+14.09%]

## Validation 2015–2019
- 2 bps: return +5.66% | CAGR +1.11% | DD 20.83% | positive months 55.9% | rebalance trades 60 | [2015:-11.29%, 2016:-1.67%, 2017:+10.51%, 2018:-3.24%, 2019:+13.28%]
- 10 bps: return +2.40% | CAGR +0.48% | DD 21.51% | positive months 52.5% | rebalance trades 60 | [2015:-12.19%, 2016:-2.51%, 2017:+10.20%, 2018:-3.62%, 2019:+12.61%]

## Holdout 2020–2023
- 2 bps: return +48.58% | CAGR +10.44% | DD 27.57% | positive months 63.8% | rebalance trades 48 | [2020:+21.79%, 2021:+14.50%, 2022:-1.35%, 2023:+8.01%]
- 10 bps: return +44.70% | CAGR +9.71% | DD 28.11% | positive months 63.8% | rebalance trades 48 | [2020:+21.13%, 2021:+13.94%, 2022:-2.04%, 2023:+7.03%]

## Final holdout 2024–2026 YTD
- 2 bps: return +73.93% | CAGR +23.97% | DD 8.01% | positive months 83.3% | rebalance trades 31 | [2024:+18.19%, 2025:+31.57%, 2026:+11.85%]
- 10 bps: return +71.93% | CAGR +23.41% | DD 8.01% | positive months 83.3% | rebalance trades 31 | [2024:+17.49%, 2025:+31.13%, 2026:+11.60%]

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
- FAIL — holdout1_2022_positive_10bps
- PASS — holdout1_2023_positive_10bps
- FAIL — holdout1_drawdown
- PASS — holdout2_2024_positive_10bps
- PASS — holdout2_2025_positive_10bps
- PASS — holdout2_2026_positive_10bps
- PASS — holdout2_drawdown

## Failure reasons
- validation_2015_positive_10bps
- validation_2016_positive_10bps
- validation_2018_positive_10bps
- validation_drawdown
- holdout1_2022_positive_10bps
- holdout1_drawdown

## Research status
Research only. Even a PASS does not guarantee future profit; it would move only to independent data validation and paper trading.
