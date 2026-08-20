import json
import os
from datetime import datetime, timezone
from pathlib import Path

import phase6b_paper_trader as core

STATUS_FILE = "strategy2_inverse_transition_status.json"


def write_status(status, **extra):
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "paper_base": core.PAPER_BASE,
        "live_trading_locked": True,
        "transition": "STRATEGY2_BASELINE_TO_EXACT_INVERSE_SHADOW",
        "status": status,
        **extra,
    }
    Path(STATUS_FILE).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))


def main():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 paper credentials are not configured")
    os.environ["ALPACA_PAPER_API_KEY_ID"] = key
    os.environ["ALPACA_PAPER_API_SECRET_KEY"] = secret
    os.environ.pop("ALPACA_API_KEY_ID", None)
    os.environ.pop("ALPACA_API_SECRET_KEY", None)

    account = core.api("GET", "/v2/account")
    clock = core.api("GET", "/v2/clock")
    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        write_status("BLOCKED_ACCOUNT", account_status=account.get("status"), trading_blocked=bool(account.get("trading_blocked")))
        return

    pending = core.open_orders()
    if pending:
        write_status("BLOCKED_OPEN_ORDERS", open_order_count=len(pending))
        return

    positions = core.current_positions()
    if not positions:
        write_status("ALREADY_FLAT", account_equity=float(account.get("equity") or 0.0), cash=float(account.get("cash") or 0.0), final_positions={})
        return

    if not clock.get("is_open"):
        write_status("WAITING_FOR_MARKET_OPEN", order_submitted=False, current_positions=positions)
        return

    actions = []
    for sym in sorted(positions):
        if sym not in core.SYMS:
            write_status("BLOCKED_UNEXPECTED_SYMBOL", unexpected_symbol=sym, current_positions=positions)
            return
        order = core.close_position(sym)
        final = core.wait_order(order["id"], seconds=45)
        actions.append({"symbol": sym, "action": "close_for_inverse_transition", "order_id": order["id"], "status": final.get("status")})
        if final.get("status") != "filled":
            write_status("FLATTEN_ERROR", failed_symbol=sym, actions=actions, remaining_positions=core.current_positions())
            return

    final_positions = core.current_positions()
    account_after = core.api("GET", "/v2/account")
    if final_positions:
        write_status("FLATTEN_NOT_CONFIRMED", actions=actions, final_positions=final_positions, account_equity=float(account_after.get("equity") or 0.0))
        return

    write_status(
        "FLAT_READY_FOR_EXACT_INVERSE_SHADOW",
        actions=actions,
        final_positions={},
        account_equity=float(account_after.get("equity") or 0.0),
        cash=float(account_after.get("cash") or 0.0),
        order_submitted=True,
    )


if __name__ == "__main__":
    main()
