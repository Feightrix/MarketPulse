import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import phase6b_paper_trader as core
import phase6e_2500_paper_trader as phase6e

STATE_FILE = Path("phase6e_2500_state.json")
STATUS_FILE = Path("phase6e_adaptive_gate_status.json")

# Keep normal monthly turnover, but permit a controlled mid-month refresh when
# the current signal has materially diverged from the portfolio established at
# the prior rebalance. This is intentionally conservative to avoid daily churn.
MIN_DAYS_BETWEEN_REBALANCES = 7
MIN_TARGET_L1_SHIFT_PCT = 7.5


def write_status(payload):
    STATUS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def dedicated_credentials():
    key = os.getenv("ALPACA_2500_PAPER_API_KEY_ID")
    secret = os.getenv("ALPACA_2500_PAPER_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Dedicated $2,500 Alpaca paper credentials are not configured")
    os.environ["ALPACA_PAPER_API_KEY_ID"] = key
    os.environ["ALPACA_PAPER_API_SECRET_KEY"] = secret
    os.environ.pop("ALPACA_API_KEY_ID", None)
    os.environ.pop("ALPACA_API_SECRET_KEY", None)


def target_l1_shift_pct(previous, current):
    syms = set(previous) | set(current)
    return 100.0 * sum(abs(float(current.get(s, 0.0)) - float(previous.get(s, 0.0))) for s in syms)


def main():
    now = datetime.now(timezone.utc)
    base = {
        "phase": "6E",
        "timestamp_utc": now.isoformat(),
        "paper_only": True,
        "min_days_between_rebalances": MIN_DAYS_BETWEEN_REBALANCES,
        "min_target_l1_shift_pct": MIN_TARGET_L1_SHIFT_PCT,
    }

    try:
        if not STATE_FILE.exists():
            write_status({**base, "status": "NO_STATE_YET"})
            return
        state = json.loads(STATE_FILE.read_text())
        if not state.get("initialized") or not state.get("last_rebalance_utc"):
            write_status({**base, "status": "INITIAL_REBALANCE_NOT_COMPLETE"})
            return

        dedicated_credentials()
        account = core.api("GET", "/v2/account")
        clock = core.api("GET", "/v2/clock")
        equity = float(account.get("equity") or 0.0)
        if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
            write_status({**base, "status": "ACCOUNT_BLOCKED", "account_equity": equity})
            return
        if equity < phase6e.OPERATING_FLOOR:
            write_status({**base, "status": "OPERATING_FLOOR_BLOCK", "account_equity": equity})
            return

        last_rebalance = pd.Timestamp(state["last_rebalance_utc"])
        current_time = pd.Timestamp(clock.get("timestamp", now.isoformat()))
        days_since = (current_time - last_rebalance).total_seconds() / 86400.0
        if days_since < MIN_DAYS_BETWEEN_REBALANCES:
            write_status({**base, "status": "COOLDOWN", "days_since_rebalance": days_since, "account_equity": equity})
            return

        _, closes = core.load_panel()
        current_weights, _, _ = core.combined_target(closes)

        # Rebuild the exact target that was in force on the prior signal date.
        prior_signal = state.get("last_signal_date")
        if not prior_signal:
            write_status({**base, "status": "NO_PRIOR_SIGNAL_DATE", "account_equity": equity})
            return
        prior_panel = closes.loc[:pd.Timestamp(prior_signal)]
        if prior_panel.empty:
            write_status({**base, "status": "PRIOR_SIGNAL_UNAVAILABLE", "account_equity": equity})
            return
        previous_weights, _, _ = core.combined_target(prior_panel)
        shift = target_l1_shift_pct(previous_weights, current_weights)

        payload = {
            **base,
            "account_equity": equity,
            "days_since_rebalance": days_since,
            "last_signal_date": prior_signal,
            "current_signal_date": str(closes.index[-1]),
            "target_l1_shift_pct": shift,
        }

        if shift < MIN_TARGET_L1_SHIFT_PCT:
            write_status({**payload, "status": "MONTHLY_HOLD"})
            return
        if not bool(clock.get("is_open")):
            write_status({**payload, "status": "ADAPTIVE_REBALANCE_WAITING_FOR_MARKET_OPEN"})
            return

        # Clear only the month lock. The existing Phase 6E trader still performs
        # all capital-fit, short-sizing, exposure, pending-order, and execution checks.
        state["last_rebalance_month"] = None
        state["adaptive_rebalance_authorized_utc"] = now.isoformat()
        state["adaptive_rebalance_reason"] = f"target_l1_shift={shift:.2f}% >= {MIN_TARGET_L1_SHIFT_PCT:.2f}%"
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        write_status({**payload, "status": "ADAPTIVE_REBALANCE_AUTHORIZED"})
    except Exception as exc:
        write_status({**base, "status": "ADAPTIVE_GATE_ERROR", "problem": str(exc)})
        raise


if __name__ == "__main__":
    main()
