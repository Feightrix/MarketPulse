# MarketPulse — Options Pattern 2: VWAP Stretch Reversal

**Research only. Order submission remains disabled.**

## Pattern
- Underlying: **SPY**
- Bars: **5 minute**
- Distinct from Pattern 1: this is a **mean-reversion** setup, not a breakout continuation
- CALL: stretched below VWAP + oversold/exhaustion reversal + snapback trigger
- PUT: exact mirrored setup above VWAP
- Fixed-R target must remain inside the path back toward VWAP

## Selected Configuration
- Minimum stretch: **1.00 ATR**
- Minimum exhaustion wick: **40% of bar range**
- RSI extreme: **35 / 65**
- Target: **1.25R**
- Stop padding: **0.10 ATR**

## Untouched Holdout
- Trades: **28**
- Win rate: **35.71%**
- Net P/L: **$-137.50**
- Ending balance: **$2,362.50**
- Profit factor: **0.694**
- Expectancy: **-0.1964R/trade**
- Max drawdown: **$237.50**

## Full 180-Day Sample
- Trades: **79**
- Win rate: **41.77%**
- Net P/L: **$-108.70**
- Ending balance: **$2,391.30**
- Return: **-4.35%**
- Profit factor: **0.905**
- Expectancy: **-0.0550R/trade**
- Max drawdown: **$237.50**

## Direction Split
- CALLS: **35 trades | 48.57% wins | $91.30 P/L**
- PUTS: **44 trades | 36.36% wins | $-200.00 P/L**

## Validation
- Configurations tested: **48**
- Profitable in both development folds: **1**
- Holdout profitable: **NO**
- Holdout 60–80% win-rate target: **NO**

Dollar P/L is risk-normalized underlying-pattern P/L at $25 per 1R, not yet actual option-premium P/L.
