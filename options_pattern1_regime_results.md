# MarketPulse — Options Pattern 1 Regime Refinement

**Research only. Order submission remains disabled.**

## What Changed
- Pattern rules stayed fixed; only market-regime filters were tested.
- Filters tested: entry cutoff, opening-range width, directional efficiency, VWAP chop, breakout relative volume.
- First 70% remained development data and was split into two folds.
- Final 30% remained untouched until after regime selection.
- CALL and PUT rules remain mirrored.

## Selected Regime
- Latest entry: **14:30 ET**
- Opening-range width: **0.00% to 0.60%**
- Minimum directional efficiency: **0.25**
- Maximum recent VWAP crosses: **4**
- Minimum breakout relative volume: **0.90x**

## Training 70%
- Trades: **21** | Win rate: **61.90%**
- P/L: **$272.82** | Return: **10.91%**
- Profit factor: **2.364** | Expectancy: **0.5197R/trade**
- Max drawdown: **$50.00**

## Untouched Holdout 30%
- Trades: **13** | Win rate: **38.46%**
- P/L: **$-12.50** | Return: **-0.50%**
- Profit factor: **0.938** | Expectancy: **-0.0385R/trade**
- Max drawdown: **$75.00**

## Full 180-Day Sample
- Trades: **34** | Win rate: **52.94%**
- P/L: **$260.32** | Ending balance: **$2,760.32**
- Return: **10.41%** | Profit factor: **1.651**
- Expectancy: **0.3063R/trade** | Max drawdown: **$75.00**

## Pass/Fail
- Holdout profitable: **NO**
- Holdout 60–80% win-rate target: **NO**

Dollar P/L is still risk-normalized underlying-pattern P/L at $25 per 1R. Actual option premium P/L comes after the underlying edge survives validation.
