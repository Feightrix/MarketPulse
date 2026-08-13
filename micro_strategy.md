# MarketPulse Micro — Phase 3 Strategy Framework

## Objective
Build and validate a small-account intraday strategy designed to capture modest price moves repeatedly while strictly controlling losses. The objective is consistency and capital preservation first; no strategy is represented as guaranteeing profit.

## Account assumptions
- Starting capital: $100
- Buying power: 1x
- No leverage
- No short selling
- Fractional shares allowed
- Long-only U.S. equities/ETFs
- Maximum 95% of equity committed to any single trade

## Initial universe
- SPY
- QQQ
- IWM

These are intentionally highly liquid ETFs. The first version will not chase thin small-cap stocks, penny stocks, options, or leveraged ETFs.

## Bar size
5-minute bars.

## Entry idea — trend pullback/reclaim
A trade can only trigger when all of the following are true:
1. Fast EMA is above slow EMA.
2. Price is above session VWAP.
3. RSI has pulled back and then reclaimed a threshold.
4. Price reclaims the fast EMA after the pullback.
5. Entry occurs only during approved trading windows.
6. Daily trade and cooldown limits have not been reached.

The backtester enters on the following bar, not on the signal bar, to avoid look-ahead bias.

## Initial test grid
- Take profit: 0.20% to 0.40%
- Stop loss: 0.15% to 0.30%
- Maximum holding time: 20 to 60 minutes
- RSI reclaim threshold: 48 to 52
- Volume confirmation: tested at several levels
- Maximum trades per day: 3
- Cooldown after exit: 15 minutes

## Trading windows
- 9:45 a.m. – 11:30 a.m. ET
- 2:00 p.m. – 3:30 p.m. ET

The first 15 minutes and the midday low-liquidity period are intentionally excluded.

## Execution assumptions
The test will include explicit one-way friction for spread/slippage and then rerun the selected strategy under harsher friction. If a strategy only works with perfect fills, it fails.

## Validation structure
- Training period: strategy parameters may be selected here.
- Validation period: used to reject overfit candidates.
- Holdout period: remains untouched until the strategy is locked.

## Pass criteria
A candidate does not advance merely because total return is positive. It must also show:
- Positive out-of-sample expectancy after friction
- Controlled maximum drawdown
- A sufficient number of trades
- Stability when take-profit/stop parameters are changed slightly
- Survival under harsher slippage assumptions
- No dependence on a single symbol or a handful of exceptional days

## Live-money gate
Passing historical tests is not permission to trade real money. A separate paper-trading phase using the exact same rules is required before any live brokerage connection is considered.
