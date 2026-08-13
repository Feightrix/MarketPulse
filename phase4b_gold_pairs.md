# MarketPulse — Phase 4B Hedged Gold Relative-Value Research

## Mission

Test whether a hedged gold-relative-value strategy can produce smoother and more repeatable net daily profits than the Phase 4A directional strategy. **Profit consistency, expectancy and drawdown outrank win rate.**

The +10% to +15% daily objective remains a stretch scorecard only. It never increases leverage, position size, trade count or loss tolerance.

## Starting account and broker feasibility

- Research starting equity: **$2,000**.
- Signal-quality research evaluates paired long/short trades symmetrically so the short edge can be measured even if the modeled account later falls below Alpaca's $2,000 short-eligibility threshold.
- A separate account-feasibility replay reports how often a $2,000 account would lose the ability to initiate a new short leg.
- Runtime implementation, if ever reached, must verify each short leg is currently `shortable` and use current `borrow_status`; historical bars cannot reconstruct historical borrow inventory or fees.
- No options, no leveraged ETFs, no martingale, no averaging down.

## Instruments

- GLD — gold ETF
- IAU — gold ETF
- GDX — gold-miner ETF
- SLV — silver ETF

## Candidate pairs

Primary:
- GLD / GDX
- GLD / SLV
- IAU / GDX
- IAU / SLV

Control:
- GLD / IAU

The GLD/IAU control is intentionally highly similar; if apparent profitability vanishes after friction it helps detect cost-driven false edges.

## Signal architecture

For each pair, align 1-minute bars by timestamp and compute log returns.

1. Estimate a rolling hedge ratio using return covariance / variance over the lookback window.
2. Clip the hedge ratio to a conservative range so unstable estimates cannot create extreme leg weights.
3. Build a rolling relative-value residual from demeaned log prices using the hedge ratio.
4. Convert the residual to a rolling z-score.
5. Enter only when the spread is statistically stretched and both legs have adequate recent activity.

If z-score is positive:
- short leg A
- long hedge-ratio-adjusted leg B

If z-score is negative:
- long leg A
- short hedge-ratio-adjusted leg B

The objective is convergence of the spread, not an outright forecast for gold.

## Exits

Exit both legs when any of these occurs:

- spread mean-reverts to the configured exit z-score;
- adverse spread reaches the hard z-stop;
- pair-level dollar loss reaches the fixed risk stop;
- time stop is reached;
- daily portfolio loss stop is reached;
- end-of-day flatten rule is reached in the first research pass.

Absolute maximum hold remains **2 trading days**, but Phase 4B's initial research is intraday-flat by design.

## Hard risk controls

These are not tuned away:

- Maximum pair risk per trade: **0.35% of current equity**.
- Maximum daily realized loss: **1.50% of start-of-day equity**.
- Stop after **3 consecutive losing pair trades**.
- Maximum **30 completed pair trades/day**.
- Maximum **1 open pair at a time** in the first pass.
- Maximum gross pair exposure: **95% of equity** in signal-quality research.
- No recovery sizing, martingale sizing or averaging down.
- +10% day: profit-protection mode; halve risk and permit at most 5 further A+ entries.
- +15% day: stop trading for the day.

## Parameter grid

The research grid may vary:

- Pair: GLD/GDX, GLD/SLV, IAU/GDX, IAU/SLV, GLD/IAU.
- Hedge / z-score lookback: **60, 120, 240 minutes**.
- Entry z-score: **1.5, 2.0, 2.5, 3.0**.
- Exit z-score: **0.0, 0.25, 0.50**.
- Adverse z-stop: **3.5, 4.5**.
- Time stop: **15, 30, 60, 120 minutes**.

Development selection is based on profitability and stability, not win rate.

## Research periods

- Development: 2021–2023.
- Validation A: 2024.
- Validation B: 2025.
- Diagnostic check: 2026 through July.

## Primary metrics

Rank candidates primarily by:

1. Net return after modeled friction.
2. Expectancy per pair trade.
3. Profit factor.
4. Percentage of positive trading days.
5. Median daily return.
6. Maximum drawdown.
7. Worst trading day.
8. Return / drawdown efficiency.
9. Number of trades and median trades/day.

Win rate is reported but is **not a pass criterion**.

The report must also show the percentage of days reaching +1%, +2%, +5%, +10% and +15%.

## Pass gate

A candidate can PASS only if:

1. 2024 expectancy is positive.
2. 2025 expectancy is positive.
3. Profit factor is > 1.20 in 2024 and 2025 separately.
4. Combined 2024–2025 maximum drawdown is < 15%.
5. At least 55% of validation trading days are positive.
6. Combined validation has at least 200 pair trades.
7. The strategy remains positive after a higher-friction same-signal repricing test.
8. 2026 diagnostic expectancy and profit factor remain positive.
9. Performance is not dependent on the GLD/IAU control pair alone.

If no candidate passes, Phase 4B is a FAIL. The gate will not be weakened to manufacture a result.

## Important limitations

- Alpaca Basic historical equity data uses the IEX feed rather than consolidated SIP market data.
- One-minute bars do not provide exact bid/ask spreads, queue position or legging risk between the two paired fills.
- Historical borrow availability and borrow fees cannot be reconstructed from ordinary bars.
- Pair execution introduces two legs and therefore more transaction-cost exposure than a single-leg trade.
- Backtest results are research only and do not guarantee future profit.
