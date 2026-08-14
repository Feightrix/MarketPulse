# MarketPulse Phase 4G — Direction Split

**Gate: FAIL**

## Objective
Determine whether the locked Phase 4E edge is direction-dependent by evaluating bull-only and bear-only trades as separate strategies. No parameter search or regime filter is used.

## Locked Phase 4E setup
- Family: **NASDAQ** (QQQ → TQQQ/SQQQ)
- Impulse: **60 min / 35.0 bps**
- Stop / target: **85.0 / 200.0 bps**
- Entry, exit, sizing and friction model: **unchanged from Phase 4E**

## BULL ONLY — FAIL
- Development 2 bps: 28 trades | return +4.1349% | expectancy +25.09 bps/trade | PF 1.512 | max DD 3.017% | win rate 42.86%
- Development 10 bps: 28 trades | return +1.4297% | expectancy +9.07 bps/trade | PF 1.151 | max DD 4.014% | win rate 42.86%
- 2024 10 bps: 11 trades | return -1.1014% | expectancy -16.90 bps/trade | PF 0.703 | max DD 2.566% | win rate 36.36%
- 2025 10 bps: 13 trades | return +1.8023% | expectancy +24.27 bps/trade | PF 1.582 | max DD 1.503% | win rate 61.54%
- 2024–2025 combined 10 bps: 24 trades | return +0.7097% | expectancy +5.40 bps/trade | PF 1.105 | max DD 2.711% | win rate 50.00%
- 2026 2 bps: 7 trades | return +0.1720% | expectancy +5.59 bps/trade | PF 1.083 | max DD 1.032% | win rate 42.86%
- 2026 5 bps: 7 trades | return -0.0707% | expectancy -0.41 bps/trade | PF 0.968 | max DD 1.103% | win rate 42.86%
- 2026 10 bps: 7 trades | return -0.4751% | expectancy -10.41 bps/trade | PF 0.806 | max DD 1.222% | win rate 42.86%
- Gate checks:
  - PASS — development_min_trades
  - PASS — development_10bps_profitable
  - PASS — 2024_min_trades
  - FAIL — 2024_10bps_profitable
  - PASS — 2025_min_trades
  - PASS — 2025_10bps_profitable
  - PASS — validation_min_trades
  - PASS — validation_10bps_profitable
  - PASS — validation_drawdown
  - PASS — 2026_min_trades
  - FAIL — 2026_10bps_profitable

## BEAR ONLY — FAIL
- Development 2 bps: 39 trades | return +3.7913% | expectancy +27.92 bps/trade | PF 1.424 | max DD 1.606% | win rate 41.03%
- Development 10 bps: 39 trades | return +1.1402% | expectancy +11.89 bps/trade | PF 1.108 | max DD 2.179% | win rate 41.03%
- 2024 10 bps: 8 trades | return +4.8450% | expectancy +108.62 bps/trade | PF 4.993 | max DD 0.605% | win rate 75.00%
- 2025 10 bps: 7 trades | return -2.6084% | expectancy -64.24 bps/trade | PF 0.268 | max DD 2.974% | win rate 14.29%
- 2024–2025 combined 10 bps: 15 trades | return +2.2038% | expectancy +27.95 bps/trade | PF 1.458 | max DD 3.425% | win rate 46.67%
- 2026 2 bps: 6 trades | return +0.1650% | expectancy +6.00 bps/trade | PF 1.080 | max DD 1.034% | win rate 33.33%
- 2026 5 bps: 6 trades | return -0.0427% | expectancy -0.00 bps/trade | PF 0.981 | max DD 1.105% | win rate 33.33%
- 2026 10 bps: 6 trades | return -0.3888% | expectancy -10.01 bps/trade | PF 0.840 | max DD 1.224% | win rate 33.33%
- Gate checks:
  - PASS — development_min_trades
  - PASS — development_10bps_profitable
  - PASS — 2024_min_trades
  - PASS — 2024_10bps_profitable
  - PASS — 2025_min_trades
  - FAIL — 2025_10bps_profitable
  - PASS — validation_min_trades
  - PASS — validation_10bps_profitable
  - PASS — validation_drawdown
  - PASS — 2026_min_trades
  - FAIL — 2026_10bps_profitable

## Conclusion
- Neither direction independently passed the full robustness gate.

## Research status
Research only. A PASS does not guarantee future profits and does not authorize live trading.
