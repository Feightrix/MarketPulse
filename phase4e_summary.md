# MarketPulse Phase 4E — Trend-Day Continuation

**Gate: FAIL**

## Objective
Capture fewer, materially larger intraday moves so that a 10 bps-per-side friction assumption is small relative to expected trade profit.

## Selected setup
- Family: **NASDAQ**
- Signal: **QQQ**
- Bull ETF: **TQQQ**
- Bear ETF: **SQQQ**
- Impulse window: **60 min**
- Minimum impulse: **35.0 bps**
- Opening range expansion: **1.0× prior 20-day median**
- Opening volume: **0.9× prior 20-day median**
- Trend lookback: **10 sessions**
- Stop: **85.0 bps on execution ETF**
- Target: **200.0 bps on execution ETF**
- Maximum frequency: **1 trade/day**

## Development 2021–2023
- Valid candidates: **1 / 1296**
- Base 2 bps: 67 trades | return +7.9695% | expectancy +26.74 bps/trade | PF 1.461 | max DD 2.130% | positive months 71.43%
- Stress 10 bps: 67 trades | return +2.6505% | expectancy +10.71 bps/trade | PF 1.132 | max DD 3.495% | positive months 60.71%

## 2024 validation
- 19 trades | return +5.4644% | expectancy +52.00 bps/trade | PF 2.294 | max DD 1.474% | positive months 75.00%

## 2025 validation
- 20 trades | return +1.0245% | expectancy +9.30 bps/trade | PF 1.182 | max DD 2.507% | positive months 60.00%

## 2024–2025 combined validation
- Base 2 bps: 39 trades | return +6.5758% | expectancy +30.10 bps/trade | PF 1.651 | max DD 2.425% | positive months 66.67%
- 5 bps stress: 39 trades | return +5.2377% | expectancy +24.09 bps/trade | PF 1.493 | max DD 2.604% | positive months 50.00%
- 10 bps stress: 39 trades | return +2.9240% | expectancy +14.08 bps/trade | PF 1.249 | max DD 3.187% | positive months 50.00%

## 2026 holdout (Jan–Jul)
- Base 2 bps: 13 trades | return +0.3369% | expectancy +5.78 bps/trade | PF 1.081 | max DD 1.538% | positive months 66.67%
- 5 bps stress: 13 trades | return -0.1134% | expectancy -0.22 bps/trade | PF 0.974 | max DD 1.647% | positive months 66.67%
- 10 bps stress: 13 trades | return -0.8639% | expectancy -10.23 bps/trade | PF 0.823 | max DD 1.830% | positive months 66.67%

## Gate checks
- PASS — development_valid
- PASS — 2024_min_trades
- PASS — 2024_profitable
- PASS — 2025_min_trades
- PASS — 2025_profitable
- PASS — validation_min_trades
- PASS — validation_profitable
- PASS — validation_drawdown
- PASS — validation_positive_months
- PASS — validation_10bps_profitable
- PASS — 2026_min_trades
- PASS — 2026_profitable
- FAIL — 2026_10bps_profitable

## Failure reasons
- 2026_10bps_profitable

## Research status
Research only. Even a PASS would not guarantee future profits. Do not promote to live trading without a separate paper-trading gate.
