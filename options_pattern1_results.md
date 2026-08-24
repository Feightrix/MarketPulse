# MarketPulse — Options Pattern 1 Baseline

**Research only. Order submission is disabled.**

## Pattern
- Underlying: **SPY**
- Bars: **5 minute**
- Opening range: **9:30–9:45 ET**
- Bullish: opening-range breakout + retest/hold + momentum re-entry → CALL signal
- Bearish: exact mirrored rule → PUT signal
- Baseline reward:risk target: **1.5R : 1R**
- Conservative same-bar assumption: **stop before target**

## Baseline Results
- Trades: **126**
- Win rate: **38.89%**
- Net expectancy: **-0.0278R/trade**
- Net R: **-3.500R**
- Profit factor: **0.955**
- Average win: **1.500R**
- Average loss: **1.000R**
- Payoff ratio: **1.5**
- Max drawdown: **17.000R**

## Direction Split
- CALLS: **68 trades | 38.24% wins | -0.0441R expectancy**
- PUTS: **58 trades | 39.66% wins | -0.0086R expectancy**

## Target Check
- Desired win-rate band: **60–80%**
- Baseline inside band: **NO**

This stage validates the repeating underlying pattern only. Contract selection and real option premium P/L are intentionally not optimized yet.
