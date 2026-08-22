import json
import os
from datetime import datetime, timezone
from pathlib import Path

import phase6b_paper_trader as core

EXPERIMENT = "CONTROL_CLONE_RISK_CAP_57_5"
EXECUTION_MODE = "synthetic_exact_weight_shadow"
RISK_CAP = 0.575
STATE_FILE = "strategy2_riskcap575_state.json"
STATUS_FILE = "strategy2_status.json"
STATUS_MD = "strategy2_status.md"
LOG_FILE = "strategy2_riskcap575_log.jsonl"


def dedicated_credentials():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 paper credentials are not configured")
    os.environ["ALPACA_PAPER_API_KEY_ID"] = key
    os.environ["ALPACA_PAPER_API_SECRET_KEY"] = secret
    os.environ.pop("ALPACA_API_KEY_ID", None)
    os.environ.pop("ALPACA_API_SECRET_KEY", None)


def load_state():
    p = Path(STATE_FILE)
    return json.loads(p.read_text()) if p.exists() else {"initialized": False}


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def append_log(event):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def book_value(cash, quantities, prices):
    return float(cash + sum(float(q) * float(prices[s]) for s, q in quantities.items()))


def rebalance_book(equity, weights, prices):
    quantities = {s: float(equity * float(w) / float(prices[s])) for s, w in weights.items()}
    position_value = sum(float(quantities[s]) * float(prices[s]) for s in quantities)
    return quantities, float(equity - position_value)


def candidate_target(closes):
    original = core.RISK_CAP
    core.RISK_CAP = RISK_CAP
    try:
        return core.combined_target(closes)
    finally:
        core.RISK_CAP = original


def write_status(status):
    Path(STATUS_FILE).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    lines = [
        "# MarketPulse Strategy 2 — Strategy 1 Clone at 57.5% Risk Cap",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Experiment: **{EXPERIMENT}**",
        f"- Risk cap: **{RISK_CAP*100:.1f}%**",
        f"- Shadow equity: **${status.get('account_equity', 0):,.2f}**",
        f"- Flat broker cash equity: **${status.get('broker_cash_equity', 0):,.2f}**",
        f"- Signal date: **{status.get('signal_date')}**",
        f"- Gross exposure: **{status.get('gross_exposure_pct', 0):.2f}%**",
        f"- Net exposure: **{status.get('net_exposure_pct', 0):+.2f}%**",
        "- Profit-lock overlay: **OFF**",
        "- Pyramiding overlay: **OFF (research only)**",
        "- Live-money trading: **LOCKED**",
        "",
        "## Current target weights",
    ]
    for sym, w in sorted((status.get("target_weights") or {}).items(), key=lambda x: x[0]):
        lines.append(f"- {sym}: {w*100:+.3f}%")
    lines += [
        "",
        "Only RISK_CAP differs from frozen Strategy 1: 50.0% → 57.5%.",
        "The Alpaca Strategy 2 broker account remains flat; performance is measured by the synthetic shadow NAV.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    dedicated_credentials()
    account = core.api("GET", "/v2/account")
    clock = core.api("GET", "/v2/clock")
    broker_positions = core.current_positions()
    broker_orders = core.open_orders()
    broker_equity = float(account.get("equity") or 0.0)

    base_status = {
        "phase": "S2",
        "strategy": "2",
        "experiment": EXPERIMENT,
        "execution_mode": EXECUTION_MODE,
        "risk_cap": RISK_CAP,
        "timestamp_utc": now.isoformat(),
        "paper_base": core.PAPER_BASE,
        "live_trading_locked": True,
        "profit_lock_active": False,
        "pyramiding_active": False,
    }

    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        status = {**base_status, "status": "BLOCKED_BROKER_ACCOUNT", "broker_cash_equity": broker_equity}
        write_status(status); append_log(status); return
    if broker_positions:
        status = {**base_status, "status": "BLOCKED_BROKER_NOT_FLAT", "broker_cash_equity": broker_equity, "broker_positions": broker_positions}
        write_status(status); append_log(status); return
    if broker_orders:
        status = {**base_status, "status": "BLOCKED_BROKER_OPEN_ORDERS", "broker_cash_equity": broker_equity, "open_order_count": len(broker_orders)}
        write_status(status); append_log(status); return

    _, closes = core.load_panel()
    prices = {s: float(v) for s, v in closes.iloc[-1].items()}
    weights, trend, neutral = candidate_target(closes)
    signal_date = str(closes.index[-1])
    current_month = signal_date[:7]

    state = load_state()
    if not state.get("initialized"):
        quantities, cash = rebalance_book(broker_equity, weights, prices)
        state = {
            "initialized": True,
            "activation_utc": now.isoformat(),
            "activation_equity": broker_equity,
            "shadow_equity": broker_equity,
            "cash": cash,
            "quantities": quantities,
            "target_weights": weights,
            "last_prices": {s: prices[s] for s in quantities},
            "last_rebalance_month": current_month,
            "last_signal_date": signal_date,
            "rebalance_count": 1,
            "experiment": EXPERIMENT,
            "risk_cap": RISK_CAP,
        }
        event_status = "RISK_CAP_57_5_INITIALIZED"
    else:
        quantities = {s: float(q) for s, q in state.get("quantities", {}).items()}
        cash = float(state.get("cash", 0.0))
        missing = [s for s in quantities if s not in prices]
        if missing:
            status = {**base_status, "status": "BLOCKED_MISSING_PRICE", "missing_symbols": missing, "broker_cash_equity": broker_equity}
            write_status(status); append_log(status); return
        shadow_equity = book_value(cash, quantities, prices)
        event_status = "RISK_CAP_57_5_HOLD"
        if state.get("last_rebalance_month") != current_month:
            quantities, cash = rebalance_book(shadow_equity, weights, prices)
            state["rebalance_count"] = int(state.get("rebalance_count", 0)) + 1
            state["last_rebalance_month"] = current_month
            event_status = "RISK_CAP_57_5_REBALANCED"
        state.update({
            "shadow_equity": shadow_equity,
            "cash": cash,
            "quantities": quantities,
            "target_weights": weights,
            "last_prices": {s: prices[s] for s in quantities},
            "last_signal_date": signal_date,
            "experiment": EXPERIMENT,
            "risk_cap": RISK_CAP,
        })

    save_state(state)
    gross = sum(abs(float(w)) for w in weights.values()) * 100.0
    net = sum(float(w) for w in weights.values()) * 100.0
    status = {
        **base_status,
        "status": event_status,
        "account_equity": float(state["shadow_equity"]),
        "broker_cash_equity": broker_equity,
        "broker_cash": float(account.get("cash") or 0.0),
        "broker_positions": {},
        "broker_open_orders": [],
        "account_status": account.get("status"),
        "trading_blocked": bool(account.get("trading_blocked")),
        "market_open": bool(clock.get("is_open")),
        "signal_date": signal_date,
        "target_weights": weights,
        "trend_sleeve": trend,
        "neutral_sleeve": neutral,
        "shadow_quantities": state["quantities"],
        "shadow_cash": float(state["cash"]),
        "gross_exposure_pct": gross,
        "net_exposure_pct": net,
        "last_rebalance_month": state.get("last_rebalance_month"),
        "rebalance_count": int(state.get("rebalance_count", 0)),
    }
    write_status(status)
    append_log(status)


if __name__ == "__main__":
    main()
