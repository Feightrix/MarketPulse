# MarketPulse Phase 6A — Independent Data Validation

**Gate: PASS**

## Frozen strategy
Phase 5H is unchanged. No parameter optimization is performed in Phase 6A.

## Data sources
- Source A: Yahoo Finance adjusted daily OHLC via yfinance
- Source B: Alpaca IEX adjusted daily OHLC (`adjustment=all`)
- Common evaluation window: **2022-01-01 through 2026-07-31**

## 10 bps results
- Yahoo: cumulative +24.24% | max DD 3.72%
- Alpaca: cumulative +23.91% | max DD 3.73%

### Annual returns
- 2022: Yahoo +0.53% | Alpaca +0.45% | abs diff 0.08 pp | sign MATCH
- 2023: Yahoo +2.34% | Alpaca +2.29% | abs diff 0.05 pp | sign MATCH
- 2024: Yahoo +9.70% | Alpaca +9.69% | abs diff 0.01 pp | sign MATCH
- 2025: Yahoo +6.66% | Alpaca +6.69% | abs diff 0.03 pp | sign MATCH
- 2026: Yahoo +3.21% | Alpaca +3.04% | abs diff 0.17 pp | sign MATCH

## Monthly signal agreement
- Compared rebalance months: **55**
- Overall portfolio-signal agreement: **96.4%**
- Risk-on/off agreement: **100.0%**
- Trend-weight closeness: **100.0%**
- Sector long/short side agreement: **96.4%**

## Gate checks
- PASS — all_annual_signs_match
- PASS — both_cumulative_positive
- PASS — annual_return_difference_within_3pp
- PASS — drawdown_difference_within_2pp
- PASS — monthly_signal_agreement_at_least_85pct
- PASS — risk_on_off_agreement_at_least_90pct
- PASS — sector_side_agreement_at_least_80pct

## Decision
Paper trading is authorized only if every independent-data gate above passes.

Historical agreement across two data sources does not guarantee future profit.
