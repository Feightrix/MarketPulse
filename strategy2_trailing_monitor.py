import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import phase6b_paper_trader as core

EXPERIMENT = "EXACT_CONTROL_INVERSE_WITH_BREAKEVEN_TRAIL"
STATE_FILE = "strategy2_inverse_state.json"
STATUS_FILE = "strategy2_status.json"
STATUS_MD = "strategy2_status.md"
LOG_FILE = "strategy2_inverse_log.jsonl"

ARM_PROFIT_PCT = 0.004
BASE_TRAIL_PCT = 0.003
TIGHTEN_PROFIT_PCT = 0.010
TIGHT_TRAIL_PCT = 0.002
STOP_EXECUTION_COST_BPS = 10.0
POLL_SECONDS = 60
MONITOR_MINUTES = 55


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


def append_log(event):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def latest_trade_price(sym):
    url = (
        f"{core.DATA_BASE}/v2/stocks/{urllib.parse.quote(sym)}/trades/latest?"
        + urllib.parse.urlencode({"feed": "iex"})
    )
    req = urllib.request.Request(url, headers=core.data_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode())
    price = float((payload.get("trade") or {}).get("p") or 0.0)
    if price <= 0:
        raise RuntimeError(f"No valid latest IEX trade for {sym}")
    return price


def latest_prices(symbols):
    return {sym: latest_trade_price(sym) for sym in symbols}


def reset_cycle(state, prices):
    quantities = {s: float(q) for s, q in state.get("quantities", {}).items()}
    state["trailing_cycle_month"] = state.get("last_rebalance_month")
    state["trailing_entry_prices"] = {s: float(prices[s]) for s in quantities}
    state["trailing_best_prices"] = {s: float(prices[s]) for s in quantities}
    state["trailing_armed"] = {s: False for s in quantities}
    state["trailing_stop_prices"] = {}
    state["trailing_stopped_symbols"] = {}
    state["trailing_stop_events"] = []


def ensure_cycle(state, prices):
    if state.get("trailing_cycle_month") != state.get("last_rebalance_month"):
        reset_cycle(state, prices)
        return
    quantities = {s: float(q) for s, q in state.get("quantities", {}).items()}
    entries = state.setdefault("trailing_entry_prices", {})
    best = state.setdefault("trailing_best_prices", {})
    armed = state.setdefault("trailing_armed", {})
    for sym in quantities:
        seed = float(prices[sym])
        entries.setdefault(sym, seed)
        best.setdefault(sym, seed)
        armed.setdefault(sym, False)


def best_gain(qty, entry, best):
    return best / entry - 1.0 if qty > 0 else entry / best - 1.0


def realized_return(qty, entry, fill):
    return fill / entry - 1.0 if qty > 0 else entry / fill - 1.0


def update_once(state, prices, observed_at):
    quantities = {s: float(q) for s, q in state.get("quantities", {}).items()}
    cash = float(state.get("cash", 0.0))
    entries = {s: float(v) for s, v in state.get("trailing_entry_prices", {}).items()}
    best = {s: float(v) for s, v in state.get("trailing_best_prices", {}).items()}
    armed = {s: bool(v) for s, v in state.get("trailing_armed", {}).items()}
    stops = {s: float(v) for s, v in state.get("trailing_stop_prices", {}).items()}
    stopped = dict(state.get("trailing_stopped_symbols", {}))
    events = list(state.get("trailing_stop_events", []))
    triggered = []
    execution_cost = STOP_EXECUTION_COST_BPS / 10000.0

    for sym in sorted(list(quantities)):
        qty = quantities[sym]
        px = float(prices[sym])
        entry = float(entries[sym])

        best[sym] = max(best.get(sym, entry), px) if qty > 0 else min(best.get(sym, entry), px)
        gain = best_gain(qty, entry, best[sym])
        if not armed.get(sym, False) and gain >= ARM_PROFIT_PCT:
            armed[sym] = True

        if not armed.get(sym, False):
            continue

        trail = TIGHT_TRAIL_PCT if gain >= TIGHTEN_PROFIT_PCT else BASE_TRAIL_PCT
        if qty > 0:
            break_even = entry * (1.0 + execution_cost)
            candidate = best[sym] * (1.0 - trail)
            stops[sym] = max(stops.get(sym, 0.0), break_even, candidate)
            hit = px <= stops[sym]
            fill = px * (1.0 - execution_cost)
        else:
            break_even = entry * (1.0 - execution_cost)
            candidate = best[sym] * (1.0 + trail)
            stops[sym] = min(stops.get(sym, float("inf")), break_even, candidate)
            hit = px >= stops[sym]
            fill = px * (1.0 + execution_cost)

        if not hit:
            continue

        cash += qty * fill
        event = {
            "timestamp_utc": observed_at,
            "symbol": sym,
            "side": "long" if qty > 0 else "short",
            "entry_price": entry,
            "best_price": best[sym],
            "stop_price": stops[sym],
            "observed_trigger_price": px,
            "modeled_exit_price": fill,
            "modeled_execution_cost_bps": STOP_EXECUTION_COST_BPS,
            "realized_return_pct": realized_return(qty, entry, fill) * 100.0,
            "reason": "BREAKEVEN_TRAILING_STOP",
        }
        triggered.append(event)
        events.append(event)
        stopped[sym] = event
        quantities.pop(sym, None)
        entries.pop(sym, None)
        best.pop(sym, None)
        armed.pop(sym, None)
        stops.pop(sym, None)

    state["cash"] = cash
    state["quantities"] = quantities
    state["trailing_entry_prices"] = entries
    state["trailing_best_prices"] = best
    state["trailing_armed"] = armed
    state["trailing_stop_prices"] = stops
    state["trailing_stopped_symbols"] = stopped
    state["trailing_stop_events"] = events
    return triggered


def book_value(state, prices):
    return float(state.get("cash", 0.0)) + sum(
        float(q) * float(prices[s]) for s, q in state.get("quantities", {}).items()
    )


def write_markdown(status):
    cfg = status["trailing_stop_overlay"]
    lines = [
        "# MarketPulse Strategy 2 — Exact Inverse + Break-Even Trail",
        "",
        f"**Status: {status['status']}**",
        "",
        f"- Shadow equity: **${status['account_equity']:,.2f}**",
        f"- Broker cash equity: **${status['broker_cash_equity']:,.2f}**",
        f"- Active positions: **{status['active_symbol_count']}**",
        f"- Stopped this monthly cycle: **{status['stopped_symbol_count']}**",
        f"- Arm threshold: **{cfg['arm_profit_pct']*100:.2f}%**",
        f"- Trail: **{cfg['base_trail_pct']*100:.2f}%**",
        f"- Tight trail after +{cfg['tighten_profit_pct']*100:.2f}%: **{cfg['tight_trail_pct']*100:.2f}%**",
        f"- Modeled stop execution cost: **{cfg['modeled_execution_cost_bps']:.0f} bps**",
        "- Re-entry after a stop: **next monthly rebalance only**",
        "- Break-even is a target, not a guarantee; a gap can fill through the stop.",
        "",
        "## Armed stops",
    ]
    if status.get("trailing_stop_prices"):
        for sym, stop in sorted(status["trailing_stop_prices"].items()):
            lines.append(f"- {sym}: {stop:.4f}")
    else:
        lines.append("- None")
    if status.get("trailing_stop_events"):
        lines += ["", "## Stop events"]
        for e in status["trailing_stop_events"][-20:]:
            lines.append(f"- {e['symbol']} {e['side']}: {e['realized_return_pct']:+.3f}%")
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    credentials()
    state = load_json(STATE_FILE, {"initialized": False})
    prior_status = load_json(STATUS_FILE, {})

    if not state.get("initialized"):
        raise RuntimeError("Exact inverse shadow has not been initialized")

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

    active = sorted(state.get("quantities", {}))
    seed = latest_prices(active) if active else {}
    ensure_cycle(state, seed)

    samples = 0
    triggered_this_run = []
    latest = dict(seed)
    deadline = time.time() + MONITOR_MINUTES * 60
    is_open = bool(clock.get("is_open"))

    while is_open and time.time() < deadline and state.get("quantities"):
        active = sorted(state.get("quantities", {}))
        latest = latest_prices(active)
        triggered = update_once(state, latest, datetime.now(timezone.utc).isoformat())
        triggered_this_run.extend(triggered)
        samples += 1
        if triggered:
            save_json(STATE_FILE, state)
        is_open = bool(core.api("GET", "/v2/clock").get("is_open"))
        if is_open and time.time() + POLL_SECONDS < deadline:
            time.sleep(POLL_SECONDS)
        else:
            break

    active = sorted(state.get("quantities", {}))
    if active:
        latest = latest_prices(active)
    shadow_equity = book_value(state, latest)
    state["shadow_equity"] = shadow_equity
    state["last_prices"] = latest
    state["last_monitor_utc"] = datetime.now(timezone.utc).isoformat()
    state["experiment"] = EXPERIMENT
    save_json(STATE_FILE, state)

    status = {
        **prior_status,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "TRAILING_STOP_TRIGGERED" if triggered_this_run else "TRAILING_MONITOR_OK",
        "experiment": EXPERIMENT,
        "account_equity": shadow_equity,
        "broker_cash_equity": broker_equity,
        "broker_cash": float(account.get("cash") or 0.0),
        "broker_positions": {},
        "broker_open_orders": [],
        "market_open": bool(clock.get("is_open")),
        "monitor_samples": samples,
        "shadow_quantities": state.get("quantities", {}),
        "shadow_cash": float(state.get("cash", 0.0)),
        "active_symbol_count": len(state.get("quantities", {})),
        "stopped_symbol_count": len(state.get("trailing_stopped_symbols", {})),
        "trailing_stop_overlay": {
            "arm_profit_pct": ARM_PROFIT_PCT,
            "base_trail_pct": BASE_TRAIL_PCT,
            "tighten_profit_pct": TIGHTEN_PROFIT_PCT,
            "tight_trail_pct": TIGHT_TRAIL_PCT,
            "modeled_execution_cost_bps": STOP_EXECUTION_COST_BPS,
            "reentry": "NEXT_MONTHLY_REBALANCE_ONLY",
        },
        "trailing_entry_prices": state.get("trailing_entry_prices", {}),
        "trailing_best_prices": state.get("trailing_best_prices", {}),
        "trailing_armed": state.get("trailing_armed", {}),
        "trailing_stop_prices": state.get("trailing_stop_prices", {}),
        "trailing_stopped_symbols": state.get("trailing_stopped_symbols", {}),
        "trailing_stop_events": state.get("trailing_stop_events", []),
        "triggered_this_run": triggered_this_run,
        "profit_target_rule": "NONE_FIXED_TRAILING_EXIT_ONLY",
        "break_even_guaranteed": False,
    }
    save_json(STATUS_FILE, status)
    write_markdown(status)
    append_log(status)


if __name__ == "__main__":
    main()
