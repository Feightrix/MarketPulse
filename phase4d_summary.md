# MarketPulse Phase 4D — High-Expectancy Regime Continuation

**Gate: FAIL**

## Objective
Trade fewer, stronger intraday moves and require the selected strategy to remain profitable under a 10 bps-per-side friction stress test.

## Selected setup
- Family: **NASDAQ**
- Signal: **QQQ**
- Bull ETF: **TQQQ**
- Bear ETF: **SQQQ**
- Impulse window: **30 min**
- Minimum impulse: **75.0 bps**
- Opening-range expansion: **1.25× prior 20-day median**
- EMA9 pullback tolerance: **5.0 bps**
- Stop: **1.1 ATR**
- Target: **3.0R**
- Time stop: **30 min**
- Maximum frequency: **1 trade/day**

## Development 2021–2023
- Valid candidates: **0 / 576**
- Base 2 bps: 38 trades | return +1.7479% | expectancy +11.18 bps/trade | PF 1.180 | max DD 2.265% | positive months 45.00%
- Stress 10 bps: 38 trades | return -3.1844% | expectancy -4.83 bps/trade | PF 0.755 | max DD 5.290% | positive months 35.00%

## 2024 validation
- 10 trades | return -2.1306% | expectancy -26.75 bps/trade | PF 0.285 | max DD 2.228% | positive months 16.67%

## 2025 validation
- 13 trades | return +1.3652% | expectancy +31.10 bps/trade | PF 1.468 | max DD 0.881% | positive months 42.86%

## 2024–2025 combined validation
- Base 2 bps: 23 trades | return -0.7585% | expectancy +5.95 bps/trade | PF 0.870 | max DD 2.851% | positive months 30.77%
- 5 bps stress: 23 trades | return -1.9881% | expectancy -0.05 bps/trade | PF 0.707 | max DD 3.637% | positive months 23.08%
- 10 bps stress: 23 trades | return -3.9626% | expectancy -10.06 bps/trade | PF 0.522 | max DD 4.897% | positive months 23.08%

## 2026 holdout (Jan–Jul)
- Base 2 bps: 13 trades | return +0.6352% | expectancy +5.21 bps/trade | PF 1.217 | max DD 1.284% | positive months 57.14%
- 5 bps stress: 13 trades | return -0.1042% | expectancy -0.79 bps/trade | PF 0.969 | max DD 1.459% | positive months 57.14%
- 10 bps stress: 13 trades | return -1.3409% | expectancy -10.80 bps/trade | PF 0.673 | max DD 2.152% | positive months 42.86%

## Gate checks
- FAIL — development_valid
- PASS — 2024_min_trades
- FAIL — 2024_profitable
- PASS — 2025_min_trades
- PASS — 2025_profitable
- FAIL — validation_min_trades
- PASS — validation_drawdown
- FAIL — validation_positive_months
- FAIL — validation_10bps_profitable
- PASS — 2026_min_trades
- PASS — 2026_profitable
- FAIL — 2026_10bps_profitable

## Failure reasons
- development_valid
- 2024_profitable
- validation_min_trades
- validation_positive_months
- validation_10bps_profitable
- 2026_10bps_profitable

## Research status
Research only. Even a PASS would not guarantee future profits. Do not promote to live trading without a separate paper-trading gate.
