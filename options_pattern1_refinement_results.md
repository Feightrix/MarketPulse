# MarketPulse — Options Pattern 1 Refinement

**Research only. Order submission remains disabled.**

## Refinement
- Same underlying pattern: SPY 5-minute opening-range breakout + retest
- Added confirmation: breakout body strength and distance beyond the range
- Added confirmation: VWAP slope agrees with trade direction
- Added confirmation: retest holds the breakout boundary/VWAP and closes strongly
- Bullish CALL and bearish PUT rules remain exact mirrors
- Reward:risk target: **1.5R : 1R**
- Dollar reporting assumption: **1R = 1% of $2,500 = $25.00**

## Selected Confirmation Settings
- Minimum breakout body/range: **45%**
- Minimum breakout excursion: **0.04%**
- Minimum 3-bar VWAP slope: **0.010%**
- Retest close-location strength: **65%**
- Maximum retest penetration: **0.04%**

## Training Sample (first 70%)
- Trades: **36**
- Win rate: **47.22%**
- Net P/L: **$147.83**
- Ending balance: **$2,647.82**
- Return: **5.91%**
- Profit factor: **1.311**
- Expectancy: **0.1642R/trade**
- Max drawdown: **$125.00 (5.00R)**

## Holdout Sample (final 30%, untouched during selection)
- Trades: **19**
- Win rate: **42.11%**
- Net P/L: **$25.00**
- Ending balance: **$2,525.00**
- Return: **1.00%**
- Profit factor: **1.091**
- Expectancy: **0.0526R/trade**
- Max drawdown: **$100.00 (4.00R)**

## Full 180-Day Sample
- Trades: **55**
- Win rate: **45.45%**
- Net P/L: **$172.83**
- Ending balance: **$2,672.82**
- Return: **6.91%**
- Net R: **6.913R**
- Profit factor: **1.23**
- Expectancy: **0.1257R/trade**
- Max drawdown: **$150.00 (6.00R)**

## Pass/Fail
- Holdout profitable: **YES**
- Holdout 60–80% win-rate target: **NO**

Dollar P/L here is risk-normalized underlying-pattern P/L, not yet actual option-contract premium P/L. Contract selection, bid/ask spread, slippage, delta, theta, and IV are the next layer after the underlying pattern proves profitable out of sample.
