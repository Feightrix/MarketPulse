# MarketPulse — Phase 4A Rapid Long/Short Strategy

## Objective

Research a rapid intraday, HFT-inspired but non-HFT retail strategy using a **$2,000 starting equity** account. The research target is a **76%–80% win rate**, but a candidate only passes if it also has positive out-of-sample expectancy and profit factor after friction. Win rate is never optimized in isolation.

## Broker / account constraints

- Starting equity for research: **$2,000**.
- Long and short positions are allowed in the model.
- Runtime short eligibility must be confirmed from Alpaca asset metadata (`shortable` and current `borrow_status`).
- Fractional short orders are not supported; short-side sizing must resolve to whole shares.
- If account equity falls below Alpaca's short/margin eligibility threshold, short entries must be disabled automatically.
- No options, no leveraged ETFs, no penny stocks, no OTC.
- No simultaneous opposing orders that could self-cross; exits should use broker-supported bracket/OCO logic where practical.

## Trading-speed constraint

This is **not true HFT**. It is a rapid systematic strategy built for a retail API:

- Market-data decisions may update every 1 minute.
- Maximum **1 open position at a time** during initial validation.
- Maximum **12 completed trades per day**.
- Minimum **3-minute cooldown** after an exit.
- Internal order-management ceiling: **20 Trading API requests/minute**, including submits/cancels/status fallbacks.
- Primary order-state tracking should use streaming updates rather than REST polling.

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

The runtime engine must skip any symbol that is not tradable/marginable; short entries additionally require current short availability.

## Signal architecture

Two market regimes are tested, both symmetrically long and short.

### 1. Trend Pullback Continuation

Long candidate:
- EMA 9 > EMA 21 > EMA 50 on 1-minute bars.
- Price above session VWAP.
- A pullback touches or crosses EMA 9/EMA 21 without breaking the EMA 50 trend.
- RSI(7) recovers through a configurable reclaim level.
- Entry occurs on the next bar after confirmation.

Short candidate:
- EMA 9 < EMA 21 < EMA 50.
- Price below session VWAP.
- Bounce into EMA 9/EMA 21 without breaking the EMA 50 downtrend.
- RSI(7) falls back through the symmetric reclaim level.
- Entry occurs on the next bar after confirmation.

### 2. VWAP Mean Reversion in Range Regime

Only active when EMA slope / trend separation is below the trend threshold.

Long candidate:
- Price trades 1.0–2.0 intraday standard deviations below VWAP.
- RSI(7) reaches an oversold band and then reclaims.
- Short-term range / volatility filter confirms adequate movement.

Short candidate:
- Price trades 1.0–2.0 intraday standard deviations above VWAP.
- RSI(7) reaches an overbought band and then rolls back.
- Same volatility filter.

## Hard stops

These are non-negotiable and are not optimized away:

- **Absolute maximum hold: 2 trading days.**
- Normal time stop: candidates tested between **10 and 90 minutes**.
- Hard price stop on every trade immediately after entry.
- Maximum risk per trade: **0.35% of current equity** during research.
- Maximum daily realized loss: **1.50% of start-of-day equity**; stop trading for the day when hit.
- Maximum **3 consecutive losses** in a day; stop trading for the day.
- Maximum portfolio exposure: **95% of equity** in the first research pass; no leverage in the strategy logic even though the account is margin-enabled for shorting.
- No averaging down, martingale sizing, recovery sizing, or doubling after losses.

## Parameter search

The grid may vary:

- Regime type: trend-pullback / VWAP mean-reversion / combined selector.
- Take-profit: **0.15%, 0.20%, 0.25%, 0.35%, 0.50%**.
- Stop: **0.20%, 0.30%, 0.40%, 0.50%, 0.65%**.
- Normal hold: **10, 20, 30, 45, 60, 90 minutes**.
- RSI reclaim thresholds.
- VWAP deviation thresholds.
- Minimum intraday volatility.

The 2-day absolute hold, daily loss stop, consecutive-loss stop, no-martingale rule, and request-rate ceiling are fixed.

## Test design

- Data: Alpaca 1-minute historical bars.
- Development: 2021–2023.
- Validation A: 2024.
- Validation B: 2025.
- Final check: 2026 through the latest complete research date available to the dataset.
- Long and short results must be reported separately as well as combined.
- Base friction and harsher friction scenarios must be tested.
- If a bar contains both target and stop, use the conservative stop-first assumption.
- Short borrow fees / historical borrow availability are not fully reconstructable from ordinary bar data and must be listed as a limitation.

## Phase 4A pass gate

A candidate may be called a PASS only if all of the following hold:

1. Win rate between **76% and 80%** on the combined 2024–2025 validation set, OR above 76% without using a loss/target ratio that destroys expectancy.
2. Positive expectancy after base friction in 2024 and 2025 separately.
3. Profit factor > **1.20** in 2024 and 2025 separately.
4. Combined 2024–2025 max drawdown < **15%**.
5. Positive expectancy under the designated high-friction stress case.
6. Both long and short sides contribute meaningful sample size; neither side may be retained solely because the other side carries all results.
7. At least **200 combined validation trades** to make the target win-rate estimate meaningful.
8. 2026 check remains positive in expectancy and profit factor.

If no candidate passes, the result is FAIL. The gate will not be weakened to manufacture a 76%–80% win rate.

## Important

A high win rate does not guarantee profitability. A strategy that wins 80% of trades can still lose money if losses are materially larger than wins or if execution costs consume the edge. Phase 4A therefore treats win rate as one requirement among several, not as the sole objective.
