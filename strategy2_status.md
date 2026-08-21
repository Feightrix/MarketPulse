# MarketPulse Strategy 2 — Exact Control Inverse Shadow

**Status: EXACT_INVERSE_HOLD**

- Timestamp UTC: 2026-08-21T15:43:58.266468+00:00
- Experiment: **EXACT_CONTROL_INVERSE_SHADOW**
- Execution mode: **synthetic_fractional_short_shadow**
- Exact inverse shadow equity: **$2,491.47**
- Flat broker cash equity: **$2,495.10**
- Signal date: **2026-08-21**
- Gross exposure: **95.00%**
- Net exposure: **-85.00%**
- Live-money trading: **LOCKED**

## Inversion rule
Every Control target weight is multiplied by **-1.0**. Longs become equal-sized synthetic shorts; shorts become equal-sized synthetic longs.
Fractional synthetic shorts are allowed in the shadow ledger so the inversion is exact rather than distorted by whole-share broker constraints.

## Exit-rule inversion
- Control stop-loss rule: **NONE**
- Control profit-target rule: **NONE**
- Therefore there are no stop-loss / profit-target rules to reverse in this version.

## Current exact inverse target weights
- BIL: -42.500%
- SPY: -10.442%
- IWM: -9.263%
- XLE: -7.982%
- QQQ: -5.396%
- GLD: -5.181%
- XLP: -4.236%
- XLK: -2.500%
- XLV: -2.500%
- XLY: +2.500%
- XLU: +2.500%

The broker account is intentionally flat; Strategy 2 performance is measured by the synthetic exact-inverse NAV.
This is a paper-research experiment, not a hedge and not a live-money strategy.
