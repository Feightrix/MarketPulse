# MarketPulse Phase 4F — Regime Filter

**Gate: FAIL**

## Objective
Keep the Phase 4E trade logic completely unchanged and test whether a simple pre-trade market-regime filter can preserve the 2024–2025 friction-resistant edge while repairing the 2026 holdout failure. Filter selection uses 2021–2023 only.

## Locked Phase 4E setup
- Family: **NASDAQ** (QQQ → TQQQ/SQQQ)
- Impulse: **60 min / 35.0 bps**
- Stop / target: **85.0 / 200.0 bps**
- Entry, exit, sizing and friction model: **unchanged from Phase 4E**

## Selected regime filter
- Rule: **trend>=100bps + vol<=1.30x**
- Active constraints: **2**
- Valid development filters: **17 / 50 non-baseline candidates**

## Development 2021–2023
- Phase 4E baseline at 10 bps: 67 trades | return +2.6505% | expectancy +10.71 bps/trade | PF 1.132 | max DD 3.495% | positive months 60.71%
- Phase 4F filtered at 2 bps: 54 trades | return +9.8655% | expectancy +39.26 bps/trade | PF 1.780 | max DD 1.796% | positive months 70.83%
- Phase 4F filtered at 10 bps: 54 trades | return +5.7470% | expectancy +23.22 bps/trade | PF 1.396 | max DD 2.683% | positive months 70.83%

## 2024–2025 untouched validation
- 2024 at 10 bps: 13 trades | return +1.8502% | expectancy +26.00 bps/trade | PF 1.499 | max DD 1.833% | positive months 57.14%
- 2025 at 10 bps: 13 trades | return -0.7183% | expectancy -9.90 bps/trade | PF 0.830 | max DD 1.807% | positive months 42.86%
- Combined 2 bps: 26 trades | return +3.5281% | expectancy +24.08 bps/trade | PF 1.517 | max DD 1.543% | positive months 57.14%
- Combined 5 bps: 26 trades | return +2.6421% | expectancy +18.07 bps/trade | PF 1.367 | max DD 1.640% | positive months 57.14%
- Combined 10 bps: 26 trades | return +1.1142% | expectancy +8.05 bps/trade | PF 1.140 | max DD 2.077% | positive months 50.00%
- Phase 4E baseline combined 10 bps: 39 trades | return +2.9240% | expectancy +14.08 bps/trade | PF 1.249 | max DD 3.187% | positive months 50.00%

## 2026 untouched holdout (Jan–Jul)
- 2 bps: 9 trades | return +0.0388% | expectancy +2.59 bps/trade | PF 1.013 | max DD 1.036% | positive months 50.00%
- 5 bps: 9 trades | return -0.2712% | expectancy -3.41 bps/trade | PF 0.918 | max DD 1.135% | positive months 50.00%
- 10 bps: 9 trades | return -0.7879% | expectancy -13.41 bps/trade | PF 0.784 | max DD 1.420% | positive months 50.00%
- Phase 4E baseline 10 bps: 13 trades | return -0.8639% | expectancy -10.23 bps/trade | PF 0.823 | max DD 1.830% | positive months 66.67%

## Gate checks
- PASS — development_filter_valid
- PASS — 2024_10bps_min_trades
- PASS — 2024_10bps_profitable
- PASS — 2025_10bps_min_trades
- FAIL — 2025_10bps_profitable
- PASS — validation_min_trades
- PASS — validation_base_profitable
- PASS — validation_10bps_profitable
- PASS — validation_10bps_drawdown
- PASS — 2026_min_trades
- PASS — 2026_base_profitable
- FAIL — 2026_10bps_profitable

## Failure reasons
- 2025_10bps_profitable
- 2026_10bps_profitable

## Research status
Research only. A PASS does not guarantee future profits and does not authorize live trading.
