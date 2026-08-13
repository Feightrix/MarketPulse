# MarketPulse — Phase 4A Rapid Long/Short Strategy

## Objective

Research a rapid intraday, HFT-inspired but non-HFT retail strategy using a **$2,000 starting equity** account. The performance scorecard target is **+10% to +15% per trading day** (**+$200 to +$300** at the starting balance), pursued through many small qualified trades rather than one oversized position. The research win-rate target is **76%–80%**, but a candidate only passes if it also has positive out-of-sample expectancy and profit factor after friction. Neither the daily profit target nor win rate may override the risk engine.

## Broker / account constraints

- Starting equity for research: **$2,000**.
- Long and short positions are allowed in the model.
- Runtime short eligibility must be confirmed from Alpaca asset metadata (`shortable` and current `borrow_status`).
- Fractional short orders are not supported; short-side sizing must resolve to whole shares.
- If account equity falls below **$2,000**, new short entries must be disabled automatically.
- No options, leveraged ETFs, penny stocks, or OTC.
- No self-crossing / opposing simultaneous orders.

## Trading-speed constraint

This is **not true HFT**. It is a rapid systematic retail-API strategy:

- Market-data decisions may update every **1 minute**.
- Maximum **1 open position at a time** during initial validation.
- Target activity: **20–30 completed trades/day when qualified setups exist**.
- Hard maximum: **30 completed trades/day**.
- Minimum **1-minute cooldown** after an exit.
- Internal order-management ceiling: **20 Trading API requests/minute**.
- Primary order-state tracking should use streaming updates rather than REST polling.
- No forced trades to reach the daily target.

## Universe

Initial research universe:

- SPY
- QQQ
- IWM
- AAPL
- MSFT
- NVDA
- AMD
- AMZN
- META
- TSLA

The runtime engine must skip any symbol that is not tradable/marginable; short entries additionally require current borrow availability.

## Signal architecture

Two market regimes are tested, both symmetrically long and short.

### 1. Trend Pullback Continuation

Long candidate:
- EMA 9 > EMA 21 > EMA 50 on 1-minute bars.
- Price above session VWAP.
- Pullback touches/crosses EMA 9 or EMA 21 without breaking EMA 50 trend.
- RSI(7) recovers through a configurable reclaim level.
- Entry on the next bar after confirmation.

Short candidate:
- EMA 9 < EMA 21 < EMA 50.
- Price below session VWAP.
- Bounce into EMA 9/21 without breaking EMA 50 downtrend.
- RSI(7) rolls back through the symmetric reclaim level.
- Entry on the next bar after confirmation.

### 2. VWAP Mean Reversion in Range Regime

Only active when trend separation/slope is below the trend threshold.

Long candidate:
- Price trades 1.0–2.0 intraday standard deviations below VWAP.
- RSI(7) reaches oversold and then reclaims.
- Short-term volatility filter confirms adequate movement.

Short candidate:
- Price trades 1.0–2.0 intraday standard deviations above VWAP.
- RSI(7) reaches overbought and then rolls back.
- Same volatility filter.

## Hard stops

These are fixed and are never optimized away:

- **Absolute maximum hold: 2 trading days.**
- Normal target hold: **2–30 minutes**; research may test time stops up to 60 minutes.
- Default intent is same-day flat; no new entries late enough to intentionally require an overnight hold.
- Hard price stop on every trade.
- Maximum risk per trade: **0.35% of current equity** during research.
- Maximum daily realized loss: **1.50% of start-of-day equity**; stop trading for the day when hit.
- Maximum **3 consecutive losses** in a day; stop trading for the day.
- Maximum portfolio exposure: **95% of equity**; no leverage in strategy logic during the first research pass.
- No averaging down, martingale sizing, recovery sizing, or doubling after losses.

## Daily profit protection

The +10% to +15% target is a scorecard and profit-lock mechanism, not a mandate to chase trades.

- At **+5%** on the day: activate trailing daily protection; no more than **2%** of start-of-day equity may be given back from the intraday equity high.
- At **+10%** on the day: enter **Profit Protection Mode**; cut per-trade risk in half and allow at most **5 additional qualified trades**.
- At **+15%** on the day: **stop trading for the day**.
- The system never increases risk because it is behind the daily target.

## Parameter search

The grid may vary:

- Regime type: trend-pullback / VWAP mean-reversion / combined selector.
- Take-profit: **0.20%, 0.30%, 0.40%, 0.50%, 0.60%, 0.80%**.
- Stop: **0.20%, 0.30%, 0.40%, 0.50%, 0.65%**.
- Normal hold: **2, 5, 10, 15, 20, 30, 45, 60 minutes**.
- RSI reclaim thresholds.
- VWAP deviation thresholds.
- Minimum intraday volatility.

The 2-day absolute hold, daily loss stop, consecutive-loss stop, no-martingale rule, 30-trade cap, and request-rate ceiling are fixed.

## Test design

- Data: Alpaca **1-minute** historical bars.
- Development: **2021–2023**.
- Validation A: **2024**.
- Validation B: **2025**.
- Final check: **2026 through the latest complete research date** available to the dataset.
- Long and short results reported separately and combined.
- Base friction plus harsher friction scenarios.
- If a 1-minute bar contains both target and stop, assume **stop first**.
- Historical short borrow fees/availability cannot be reconstructed perfectly from ordinary bars and must remain a stated limitation.

## Phase 4A pass gate

A candidate is a PASS only if all of the following hold:

1. Combined 2024–2025 validation win rate **>=76%**, with no loss/target asymmetry that destroys expectancy.
2. Positive expectancy after base friction in **2024 and 2025 separately**.
3. Profit factor > **1.20** in **2024 and 2025 separately**.
4. Combined 2024–2025 max drawdown < **15%**.
5. Positive expectancy under the designated high-friction stress case.
6. Both long and short sides contribute meaningful sample size.
7. At least **200 combined validation trades**.
8. 2026 check remains positive in expectancy and profit factor.
9. Daily-return report must show the historical percentage of days reaching **+2%, +5%, +10%, and +15%**.
10. The strategy may not achieve the win-rate target by accepting catastrophic tail losses.

If no candidate passes, the result is FAIL. The gate will not be weakened to manufacture a 76%–80% win rate or a 10%–15% daily return.

## Important

A high win rate does not guarantee profitability. A strategy that wins 80% of trades can still lose money if losses are materially larger than wins or execution costs consume the edge. Likewise, +10% to +15% per day is an exceptionally aggressive target and is not an expected or guaranteed outcome. Phase 4A treats both as research objectives while hard risk limits remain dominant.