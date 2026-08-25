# MarketPulse — Options Pattern 2 Trend Refinement

**Research only. Order submission remains disabled.**

## Why this refinement exists
- Baseline Pattern 2 failed its recent holdout because mean reversion kept fading strong directional moves.
- The only new logic is mirrored trend-strength protection: do not fade when VWAP trend/price efficiency is too directional.
- RSI must also turn back toward the mean by a minimum amount before entry.

## Selected Trend Filter
- Max adverse 6-bar VWAP slope: **0.50 ATR**
- Max 12-bar price efficiency: **0.65**
- Minimum RSI turn: **3.0 points**

## Unseen Older Historical Validation
This block predates the prior 180-day research and was not used in those earlier selections.
- Trades: **14**
- Win rate: **42.86%**
- Net P/L: **$-12.50**
- Profit factor: **0.938**
- Max drawdown: **$118.75**

## Full 360-Day Sample
- Trades: **43**
- Win rate: **58.14%**
- Net P/L: **$331.25**
- Ending balance: **$2,831.25**
- Return: **13.25%**
- Profit factor: **1.736**
- Max drawdown: **$118.75**

## Validation
- Trend configurations tested: **27**
- Profitable in all 3 development folds: **2**
- Unseen older validation profitable: **NO**
- Unseen validation 60–80% win-rate target: **NO**

Dollar P/L remains risk-normalized underlying-pattern P/L at $25 per 1R, not actual option-premium P/L.
