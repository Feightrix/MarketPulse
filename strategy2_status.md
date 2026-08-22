# MarketPulse Strategy 2 — Control Clone + Micro Profit Lock

**Status: MICRO_PROFIT_LOCK_READY**

- Experiment: **CONTROL_CLONE_MICRO_PROFIT_LOCK**
- Shadow equity: **$2,495.10**
- Broker cash equity: **$2,495.10**
- Direction/holdings template: **Strategy 1 last successful rebalance**
- Execution: **synthetic shadow only; broker account remains flat**
- Live-money trading: **LOCKED**

## Control template quantities
- BIL: +11.6069
- IWM: +0.858513
- QQQ: +0.20436
- SPY: +0.371074
- XLE: +3.46665
- XLK: +0.328904
- XLP: +1.72498
- XLU: -1
- XLV: +0.373424
- XLY: -1

The only experimental variable is the intraday profit-lock/re-entry execution overlay.
High trade count is a cap, not a quota; the system does not manufacture trades when no profit-lock trigger occurs.
