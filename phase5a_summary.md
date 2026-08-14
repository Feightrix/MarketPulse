# MarketPulse Phase 5A — Multi-Asset Trend Rotation

**Gate: FAIL**

## Objective
Find a materially more consistent strategy by abandoning minute-level leveraged-ETF trading and testing low-turnover, long-only trend/momentum rotation across diversified liquid ETFs.

## Universe
- Risk assets: **SPY, QQQ, IWM, GLD, TLT**
- Defensive/cash proxy: **BIL**
- Starting equity: **$2,500**
- Fractional allocation assumed for research

## Selected configuration
- Momentum lookback: **63 trading days**
- Trend filter: **close above 100-day SMA**
- Hold top: **1** eligible asset(s)
- Rebalance: **monthly**
- If nothing qualifies: **100% BIL**

## Development 2017–2020
- Valid candidates: **0 / 36**
- 2 bps: return -0.01% | CAGR -0.03% | max DD 0.01% | positive months 0.0% | rebalance trades 0 | years [2020:-0.01%]
- 10 bps: return -0.01% | CAGR -0.03% | max DD 0.01% | positive months 0.0% | rebalance trades 0 | years [2020:-0.01%]

## Validation 2021–2023
- 2 bps: return +41.94% | CAGR +12.47% | max DD 16.00% | positive months 77.1% | rebalance trades 13 | years [2021:+27.78%, 2022:-11.20%, 2023:+25.09%]
- 10 bps: return +39.02% | CAGR +11.68% | max DD 16.27% | positive months 74.3% | rebalance trades 13 | years [2021:+26.76%, 2022:-11.62%, 2023:+24.09%]

## Final holdout 2024–2026 YTD
- 2 bps: return +44.33% | CAGR +15.31% | max DD 19.20% | positive months 56.7% | rebalance trades 15 | years [2024:-4.21%, 2025:+37.63%, 2026:+9.47%]
- 10 bps: return +40.90% | CAGR +14.24% | max DD 19.20% | positive months 56.7% | rebalance trades 15 | years [2024:-5.58%, 2025:+36.53%, 2026:+9.29%]

## Gate checks
- FAIL — development_all_years_positive_10bps
- PASS — validation_2021_positive_10bps
- FAIL — validation_2022_positive_10bps
- PASS — validation_2023_positive_10bps
- FAIL — validation_drawdown
- FAIL — holdout_2024_positive_10bps
- PASS — holdout_2025_positive_10bps
- PASS — holdout_2026_ytd_positive_10bps
- FAIL — holdout_drawdown

## Failure reasons
- development_all_years_positive_10bps
- validation_2022_positive_10bps
- validation_drawdown
- holdout_2024_positive_10bps
- holdout_drawdown

## Research status
Research only. Historical consistency does not guarantee future returns. A PASS still requires paper trading before live deployment.
