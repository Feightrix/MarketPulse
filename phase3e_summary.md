# MarketPulse — Phase 3E Universe Robustness

## Status

**Universe robustness gate: PASS**  
**Era stability: MATERIAL WARNING**  
**Ready for paper execution: NO**

Phase 3E froze the Phase 3D winner and changed the universe rather than retuning the model. The locked rule remained 20-day relative-strength ranking, within 1% of a prior 20-day high, fast trend filter, 10-day maximum hold, 5% downside boundary, 6% upside boundary, 95% capital fraction, long-only and no leverage.

## Separate ETF-universe results

| Period | Return | Trades | Expectancy | PF | Max DD | Best month | Doubled months |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2021-2023 | **-21.84%** | 84 | -22.8 bps | 0.81 | 37.69% | 9.53% | 0/36 |
| 2024 | **+4.35%** | 29 | 20.8 bps | 1.10 | 20.30% | 10.91% | 0/12 |
| 2025 | **+9.80%** | 32 | 36.6 bps | 1.22 | 19.76% | 14.76% | 0/12 |
| 2024-2025 combined | **+22.63%** | 60 | 40.7 bps | 1.26 | 26.37% | 14.76% | 0/24 |
| 2026 through Jul | **+4.38%** | 23 | 29.1 bps | 1.08 | 24.19% | 13.33% | 0/7 |

At 20 bps one-way modeled friction, the 2024-2025 ETF-universe result remained positive at **+17.69%**, with **33.4 bps expectancy** and **1.20 profit factor**.

## Adversarial universe stress

Five leave-one-group-out tests were run. **4 of 5 remained positive** over 2024-2025. Removing the U.S. sector ETFs was the failure case: **-6.58%**, -4.2 bps expectancy and 0.93 profit factor. This shows that recent performance still depends materially on the U.S. sector opportunity set.

The model was also rerun on **200 deterministic random 12-ETF subsets** over 2024-2025:

- Positive-return subsets: **90.5%**
- Profit factor above 1: **90.5%**
- Median subset return: **+30.03%**
- 10th-percentile subset return: **+1.23%**
- 90th-percentile subset return: **+63.33%**
- Median max drawdown: **20.49%**
- Worst subset max drawdown: **43.08%**

## What Phase 3E actually proved

The Phase 3D result was not solely an artifact of owning today's biggest individual-stock winners: the frozen rule stayed profitable across a much broader ETF universe in 2024, 2025 and the 2026 check, survived 20 bps one-way friction, and passed most group-removal and random-subset tests.

However, the magnitude of the edge fell sharply relative to Phase 3D, and the same frozen rule lost 21.84% with a 37.69% drawdown on the ETF universe in 2021-2023. That is too large an era-instability warning to move directly into paper execution.

## Next research gate

**Phase 3F should test era / walk-forward robustness without changing the rule.** The goal is to determine whether the strategy works across multiple market regimes or whether the 2024-2026 period is unusually favorable.

The monthly 2× objective remains a scorecard only. Phase 3E produced **zero doubled months**, and the target never changes position size, leverage, trade frequency or loss tolerance.
