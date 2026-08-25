# MarketPulse — Pattern 2 Contract Policy A/B

**Research only. Underlying signals are frozen; order submission remains disabled.**

## Selected Contract Policy
- Policy: **itm_7d_delta60**
- Target DTE: **7**
- Target moneyness: **-0.50%** (positive=OTM, negative=ITM)
- Target absolute delta proxy: **0.60**

## Development Window (Sep 2024 onward)
- Trades: **9**
- Win rate: **11.11%**
- Net P/L: **$-183.72**
- Profit factor: **0.064**
- Max drawdown: **$196.30**

## Older External Validation (before Sep 4, 2024)
- Trades: **9**
- Win rate: **33.33%**
- Net P/L: **$-174.11**
- Profit factor: **0.267**
- Max drawdown: **$174.11**

## Full 900-Day Contract Simulation
- Trades: **18**
- Win rate: **22.22%**
- Net P/L: **$-357.83**
- Ending balance: **$2,142.17**
- Return: **-14.31%**
- Profit factor: **0.175**
- Max drawdown: **$357.83**

## Control Comparison
- Control full P/L: **$-188.10**
- Selected beats control: **NO**
- Selected profitable in all 3 development folds: **NO**
- Selected profitable in older external validation: **NO**

Fills use actual 1-minute historical option trade bars with the same conservative 0.5% execution haircut and estimated fees as the control simulation. Historical Greeks are not assumed; delta is reconstructed from the actual entry premium as a proxy.
