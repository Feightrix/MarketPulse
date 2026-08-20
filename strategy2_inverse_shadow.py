import json
import os
from datetime import datetime, timezone
from pathlib import Path

import phase6b_paper_trader as core

EXPERIMENT = "EXACT_CONTROL_INVERSE_SHADOW"
EXECUTION_MODE = "synthetic_fractional_short_shadow"
STATE_FILE = "strategy2_inverse_state.json"
STATUS_FILE = "strategy2_status.json"
STATUS_MD = "strategy2_status.md"
LOG_FILE = "strategy2_inverse_log.jsonl"
QTY_TOLERANCE = 1e-9


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


def invert(weights):
    return {s: -float(w) for s, w in weights.items() if abs(float(w)) > QTY_TOLERANCE}


def book_value(cash, quantities, prices):
    return float(cash + sum(float(q) * float(prices[s]) for s, q in quantities.items()))


def rebalance_book(equity, weights, prices):
    quantities = {s: float(equity * float(w) / float(prices[s])) for s, w in weights.items()}
    position_value = sum(float(quantities[s]) * float(prices[s]) for s in quantities)
    cash = float(equity - position_value)
    return quantities, cash


def write_status(status):
    Path(STATUS_FILE).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    lines = [
        "# MarketPulse Strategy 2 — Exact Control Inverse Shadow",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Timestamp UTC: {status.get('timestamp_utc')}",
        f"- Experiment: **{EXPERIMENT}**",
        f"- Execution mode: **{EXECUTION_MODE}**",
        f"- Exact inverse shadow equity: **${status.get('account_equity', 0):,.2f}**",
        f"- Flat broker cash equity: **${status.get('broker_cash_equity', 0):,.2f}**",
        f"- Signal date: **{status.get('signal_date')}**",
        f"- Gross exposure: **{status.get('gross_exposure_pct', 0):.2f}%**",
        f"- Net exposure: **{status.get('net_exposure_pct', 0):+.2f}%**",
        "- Live-money trading: **LOCKED**",
        "",
        "## Inversion rule",
        "Every Control target weight is multiplied by **-1.0**. Longs become equal-sized synthetic shorts; shorts become equal-sized synthetic longs.",
        "Fractional synthetic shorts are allowed in the shadow ledger so the inversion is exact rather than distorted by whole-share broker constraints.",
        "",
        "## Exit-rule inversion",
        "- Control stop-loss rule: **NONE**",
        "- Control profit-target rule: **NONE**",
        "- Therefore there are no stop-loss / profit-target rules to reverse in this version.",
        "",
        "## Current exact inverse target weights",
    ]
    for sym, w in sorted((status.get("target_weights") or {}).items(), key=lambda x: x[1]):
        lines.append(f"- {sym}: {w*100:+.3f}%")
    lines += [
        "",
        "The broker account is intentionally flat; Strategy 2 performance is measured by the synthetic exact-inverse NAV.",
        "This is a paper-research experiment, not a hedge and not a live-money strategy.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    base = {
        "phase": "S2",
        "strategy": "2",
        "timestamp_utc": now.isoformat(),
        "paper_base": core.PAPER_BASE,
        "live_trading_locked": True,
        "experiment": EXPERIMENT,
        "execution_mode": EXECUTION_MODE,
        "stop_loss_rule": "NONE_IN_CONTROL",
        "profit_target_rule": "NONE_IN_CONTROL",
    }

    dedicated_credentials()
    account = core.api("GET", "/v2/account")
    clock = core.api("GET", "/v2/clock")
    broker_positions = core.current_positions()
    broker_open_orders = core.open_orders()
    broker_equity = float(account.get("equity") or 0.0)

    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        status = {**base, "status": "BLOCKED_BROKER_ACCOUNT", "broker_cash_equity": broker_equity}
        write_status(status); append_log(status); return

    if broker_open_orders:
        status = {**base, "status": "BLOCKED_BROKER_OPEN_ORDERS", "broker_cash_equity": broker_equity, "open_order_count": len(broker_open_orders)}
        write_status(status); append_log(status); return

    if broker_positions:
        status = {**base, "status": "BLOCKED_BROKER_NOT_FLAT", "broker_cash_equity": broker_equity, "broker_positions": broker_positions}
        write_status(status); append_log(status); return

    _, closes = core.load_panel()
    prices = {s: float(v) for s, v in closes.iloc[-1].items()}
    control_weights, control_trend, control_neutral = core.combined_target(closes)
    inverse_weights = invert(control_weights)
    signal_date = str(closes.index[-1])
    current_month = str(signal_date)[:7]

    state = load_state()
    if not state.get("initialized"):
        quantities, cash = rebalance_book(broker_equity, inverse_weights, prices)
        shadow_equity = broker_equity
        state = {
            "initialized": True,
            "activation_utc": now.isoformat(),
            "activation_equity": broker_equity,
            "shadow_equity": shadow_equity,
            "cash": cash,
            "quantities": quantities,
            "target_weights": inverse_weights,
            "last_prices": {s: prices[s] for s in quantities},
            "last_rebalance_month": current_month,
            "last_signal_date": signal_date,
            "rebalance_count": 1,
            "experiment": EXPERIMENT,
        }
        event_status = "EXACT_INVERSE_INITIALIZED"
    else:
        quantities = {s: float(q) for s, q in state.get("quantities", {}).items()}
        cash = float(state.get("cash", 0.0))
        missing = [s for s in quantities if s not in prices]
        if missing:
            status = {**base, "status": "BLOCKED_MISSING_PRICE", "missing_symbols": missing, "broker_cash_equity": broker_equity}
            write_status(status); append_log(status); return
        shadow_equity = book_value(cash, quantities, prices)
        event_status = "EXACT_INVERSE_HOLD"
        if state.get("last_rebalance_month") != current_month:
            quantities, cash = rebalance_book(shadow_equity, inverse_weights, prices)
            state["rebalance_count"] = int(state.get("rebalance_count", 0)) + 1
            state["last_rebalance_month"] = current_month
            event_status = "EXACT_INVERSE_REBALANCED"
        state.update({
            "shadow_equity": shadow_equity,
            "cash": cash,
            "quantities": quantities,
            "target_weights": inverse_weights,
            "last_prices": {s: prices[s] for s in quantities},
            "last_signal_date": signal_date,
            "experiment": EXPERIMENT,
        })

    save_state(state)
    gross = sum(abs(float(w)) for w in inverse_weights.values()) * 100.0
    net = sum(float(w) for w in inverse_weights.values()) * 100.0
    control_gross = sum(abs(float(w)) for w in control_weights.values()) * 100.0
    control_net = sum(float(w) for w in control_weights.values()) * 100.0

    status = {
        **base,
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
        "target_weights": inverse_weights,
        "control_target_weights": control_weights,
        "control_trend_sleeve": control_trend,
        "control_neutral_sleeve": control_neutral,
        "shadow_quantities": state["quantities"],
        "shadow_cash": float(state["cash"]),
        "gross_exposure_pct": gross,
        "net_exposure_pct": net,
        "control_gross_exposure_pct": control_gross,
        "control_net_exposure_pct": control_net,
        "last_rebalance_month": state.get("last_rebalance_month"),
        "rebalance_count": int(state.get("rebalance_count", 0)),
        "inversion_check": {
            "gate": "PASS" if all(abs(float(inverse_weights.get(s, 0.0)) + float(w)) < 1e-12 for s, w in control_weights.items()) else "FAIL",
            "rule": "inverse_weight = -1 * control_weight",
        },
    }
    write_status(status)
    append_log(status)


if __name__ == "__main__":
    main()

# Manual initialization trigger: 2026-08-20T13:50-04:00
