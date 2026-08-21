# MarketPulse Phase 6D — $2,500 Capital Fit

**Gate: PASS**

- Design capital: **$2,500.00**
- Source signal date: **2026-08-14**
- Target net exposure: **85.00%**
- Represented net exposure: **83.50%**
- Target gross exposure: **95.00%**
- Represented gross exposure: **96.50%**
- Capital-fit L1 tracking error: **2.96%**
- Cash-equivalent allocation: **16.50%**
- Largest single short: **4.73%**

## $2,500 shadow mark
- Mark date: **2026-08-21**
- Estimated equity: **$2,498.09**
- Estimated P/L: **$-1.91 (-0.0765%)**
- This uses the $100k paper account's observed prices/fills to estimate the $2,500-sized portfolio; it is not a separate $2,500 broker account fill record.

## $2,500 quantities
- BIL: +11.6069 shares | target +42.50% | represented +42.50%
- IWM: +0.862347 shares | target +10.49% | represented +10.49%
- QQQ: +0.204745 shares | target +5.97% | represented +5.97%
- SPY: +0.37098 shares | target +11.52% | represented +11.52%
- XLE: +3.46898 shares | target +8.59% | represented +8.59%
- XLK: +0.329763 shares | target +2.50% | represented +2.50%
- XLP: +1.723 shares | target +5.94% | represented +5.94%
- XLU: -1 shares | target -2.50% | represented -1.77%
- XLV: +0.373068 shares | target +2.50% | represented +2.50%
- XLY: -1 shares | target -2.50% | represented -4.73%

## Capital-fit checks
- PASS — capital_meets_alpaca_short_minimum
- PASS — capital_above_operating_floor
- PASS — l1_tracking_error_at_most_5pct
- PASS — net_exposure_drift_at_most_3pct
- PASS — gross_exposure_at_most_100pct
- PASS — single_short_weight_at_most_5pct
- PASS — all_intended_net_shorts_represented

## Design rule
Long positions use fractional shares. Net short positions use nearest whole-share sizing because Alpaca does not support fractional short sales. If the capital-fit gate fails, MarketPulse must not pretend the $2,500 account can reproduce the frozen signal safely.

This is a paper/shadow implementation test, not a profit guarantee.
