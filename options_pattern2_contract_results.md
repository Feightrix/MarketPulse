# MarketPulse — Pattern 2 Real Options Contract Simulation

**Research only. No orders are submitted.**

## Fixed Contract Policy
- SPY long CALL/PUT contract, **2-8 calendar DTE**, target **4 DTE**
- Target strike: **0.50% OTM**
- Maximum premium debit: **20% of $2,500 = $500**
- Underlying Pattern 2 rules are frozen; option contract layer does not alter the signal
- Exit occurs when the frozen underlying signal exits
- Historical bid/ask is used if Alpaca exposes it; otherwise actual 1-minute option trade bars receive a conservative execution haircut

## Full Option Simulation
- Signals generated: **101**
- Contracts simulated: **99**
- Win rate: **47.47%**
- Net P/L: **$16.96**
- Ending balance: **$2,516.96**
- Return: **0.68%**
- Profit factor: **1.017**
- Average win: **$21.87**
- Average loss: **$-19.44**
- Max drawdown: **$181.45**
- Average premium: **$248.28**

## Older External Block
- Trades: **56**, win rate **42.86%**, P/L **$-20.30**, PF **0.963**

## Recent Block
- Trades: **43**, win rate **53.49%**, P/L **$37.26**, PF **1.082**

## Data Quality
- Fill modes: **{'trade_bar_conservative': 99}**
- Skipped signals: **2**
- Skip reasons: **{'no_liquid_affordable_contract': 2}**
- Historical delta is not provided by Alpaca; delta/IV fields are Black-Scholes proxies inferred from the selected contract premium.
