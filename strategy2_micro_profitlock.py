import json
import os
from datetime import datetime, timezone
from pathlib import Path

import phase6b_paper_trader as core

EXPERIMENT = "CONTROL_CLONE_MICRO_PROFIT_LOCK"
STATE_FILE = "strategy2_micro_state.json"
STATUS_FILE = "strategy2_status.json"
STATUS_MD = "strategy2_status.md"
LOG_FILE = "strategy2_micro_log.jsonl"
CONTROL_LOG_FILE = "phase6e_2500_log.jsonl"


def credentials():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 paper credentials are not configured")
    os.environ["ALPACA_PAPER_API_KEY_ID"] = key
    os.environ["ALPACA_PAPER_API_SECRET_KEY"] = secret
    os.environ.pop("ALPACA_API_KEY_ID", None)
    os.environ.pop("ALPACA_API_SECRET_KEY", None)


def load_json(path, default):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def append_log(payload):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


def latest_control_rebalance():
    p = Path(CONTROL_LOG_FILE)
    if not p.exists():
        raise RuntimeError("Control log is missing")
    latest = None
    for line in p.read_text().splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("status") == "REBALANCE_COMPLETE" and isinstance(event.get("final_positions"), dict):
            latest = event
    if latest is None:
        raise RuntimeError("No successful Control rebalance found")
    return latest


def write_status(status):
    save_json(STATUS_FILE, status)
    lines = [
        "# MarketPulse Strategy 2 — Control Clone + Micro Profit Lock",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Experiment: **{EXPERIMENT}**",
        f"- Shadow equity: **${status.get('account_equity', 0):,.2f}**",
        f"- Broker cash equity: **${status.get('broker_cash_equity', 0):,.2f}**",
        "- Direction/holdings template: **Strategy 1 last successful rebalance**",
        "- Execution: **synthetic shadow only; broker account remains flat**",
        "- Live-money trading: **LOCKED**",
        "",
        "## Control template quantities",
    ]
    for sym, qty in sorted((status.get("control_template_quantities") or {}).items()):
        lines.append(f"- {sym}: {qty:+g}")
    lines += [
        "",
        "The only experimental variable is the intraday profit-lock/re-entry execution overlay.",
        "High trade count is a cap, not a quota; the system does not manufacture trades when no profit-lock trigger occurs.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    credentials()
    now = datetime.now(timezone.utc)
    account = core.api("GET", "/v2/account")
    clock = core.api("GET", "/v2/clock")
    broker_positions = core.current_positions()
    broker_orders = core.open_orders()
    broker_equity = float(account.get("equity") or 0.0)

    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        raise RuntimeError("Strategy 2 paper account is not active")
    if broker_positions:
        raise RuntimeError(f"Strategy 2 broker account must remain flat: {broker_positions}")
    if broker_orders:
        raise RuntimeError("Strategy 2 broker account has open orders")

    control = latest_control_rebalance()
    template = {str(s): float(q) for s, q in control["final_positions"].items() if abs(float(q)) > 1e-9}
    control_key = str(control.get("timestamp_utc") or control.get("signal_date") or "unknown")
    state = load_json(STATE_FILE, {})

    if not state.get("initialized"):
        state = {
            "initialized": True,
            "experiment": EXPERIMENT,
            "activation_utc": now.isoformat(),
            "activation_equity": broker_equity,
            "shadow_equity": broker_equity,
            "cash": broker_equity,
            "positions": {},
            "control_template_quantities": template,
            "control_rebalance_key": control_key,
            "control_rebalance_month": control.get("last_rebalance_month") or "2026-08",
            "cycle": {},
            "events": [],
            "daily": {},
        }
        event_status = "MICRO_PROFIT_LOCK_READY"
    else:
        event_status = "MICRO_PROFIT_LOCK_HOLD"
        if state.get("control_rebalance_key") != control_key:
            # The Control changed its monthly book. The monitor will stop opening the old
            # template and adopt the new quantities. Existing synthetic positions are
            # intentionally left for the monitor to close with modeled execution cost.
            state["pending_control_template_quantities"] = template
            state["pending_control_rebalance_key"] = control_key
            event_status = "CONTROL_REBALANCE_UPDATE_PENDING"

    state["last_manager_utc"] = now.isoformat()
    save_json(STATE_FILE, state)

    status = {
        "phase": "S2",
        "strategy": "2",
        "experiment": EXPERIMENT,
        "timestamp_utc": now.isoformat(),
        "status": event_status,
        "account_equity": float(state.get("shadow_equity", broker_equity)),
        "broker_cash_equity": broker_equity,
        "broker_cash": float(account.get("cash") or 0.0),
        "broker_positions": {},
        "broker_open_orders": [],
        "account_status": account.get("status"),
        "trading_blocked": bool(account.get("trading_blocked")),
        "market_open": bool(clock.get("is_open")),
        "paper_base": core.PAPER_BASE,
        "live_trading_locked": True,
        "execution_mode": "synthetic_control_clone_micro_cycles",
        "control_template_quantities": state.get("control_template_quantities", template),
        "control_rebalance_key": state.get("control_rebalance_key", control_key),
        "profit_lock_active": True,
    }
    write_status(status)
    append_log(status)


if __name__ == "__main__":
    main()

# Manual initialization trigger: 2026-08-21
