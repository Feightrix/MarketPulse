import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import phase6b_paper_trader as core

EXPERIMENT = "CONTROL_CLONE_MICRO_PROFIT_LOCK"
STATE_FILE = "strategy2_micro_state.json"
STATUS_FILE = "strategy2_status.json"
STATUS_MD = "strategy2_status.md"
LOG_FILE = "strategy2_micro_log.jsonl"

ARM_PROFIT_PCT = 0.0035
BASE_TRAIL_PCT = 0.0010
TIGHTEN_PROFIT_PCT = 0.0075
TIGHT_TRAIL_PCT = 0.0007
EXECUTION_COST_BPS_PER_FILL = 10.0
POLL_SECONDS = 15
MONITOR_MINUTES = 55
REENTRY_COOLDOWN_SECONDS = 30
MAX_ROUND_TRIPS_PER_DAY = 200
MAX_ROUND_TRIPS_PER_SYMBOL_PER_DAY = 30
DAILY_DRAWDOWN_KILL_PCT = 0.0125
NY = ZoneInfo("America/New_York")


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


def latest_prices(symbols):
    if not symbols:
        return {}
    query = urllib.parse.urlencode({"symbols": ",".join(sorted(symbols)), "feed": "iex"})
    url = f"{core.DATA_BASE}/v2/stocks/trades/latest?{query}"
    req = urllib.request.Request(url, headers=core.data_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode())
    trades = payload.get("trades") or {}
    out = {}
    for sym in symbols:
        price = float((trades.get(sym) or {}).get("p") or 0.0)
        if price <= 0:
            raise RuntimeError(f"No valid latest IEX trade for {sym}")
        out[sym] = price
    return out


def current_equity(state, prices):
    return float(state.get("cash", 0.0)) + sum(
        float(q) * float(prices[s]) for s, q in (state.get("positions") or {}).items()
    )


def today_key(now=None):
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(NY).date().isoformat()


def ensure_daily(state, prices):
    key = today_key()
    daily = state.setdefault("daily", {})
    if daily.get("date") != key:
        start_eq = current_equity(state, prices) if prices else float(state.get("shadow_equity", state.get("cash", 0.0)))
        state["daily"] = {
            "date": key,
            "start_equity": start_eq,
            "entries": 0,
            "exits": 0,
            "round_trips": 0,
            "per_symbol_round_trips": {},
            "realized_pnl": 0.0,
            "modeled_costs": 0.0,
            "kill_switch": False,
        }
    return state["daily"]


def add_event(state, event):
    events = state.setdefault("events", [])
    events.append(event)
    if len(events) > 2000:
        del events[:-2000]


def entry_effective_price(qty, raw_price):
    c = EXECUTION_COST_BPS_PER_FILL / 10000.0
    return raw_price * (1.0 + c) if qty > 0 else raw_price * (1.0 - c)


def exit_effective_price(qty, raw_price):
    c = EXECUTION_COST_BPS_PER_FILL / 10000.0
    return raw_price * (1.0 - c) if qty > 0 else raw_price * (1.0 + c)


def enter_symbol(state, sym, raw_price, observed_at):
    template = state.get("control_template_quantities") or {}
    qty = float(template.get(sym, 0.0))
    if abs(qty) < 1e-12:
        return None
    daily = state["daily"]
    per_sym = daily.setdefault("per_symbol_round_trips", {})
    if daily.get("kill_switch"):
        return None
    if daily.get("round_trips", 0) >= MAX_ROUND_TRIPS_PER_DAY:
        return None
    if int(per_sym.get(sym, 0)) >= MAX_ROUND_TRIPS_PER_SYMBOL_PER_DAY:
        return None

    effective = entry_effective_price(qty, raw_price)
    state["cash"] = float(state.get("cash", 0.0)) - qty * effective
    state.setdefault("positions", {})[sym] = qty
    cycle = state.setdefault("cycle", {})
    cycle[sym] = {
        "entry_price": raw_price,
        "entry_effective_price": effective,
        "best_price": raw_price,
        "armed": False,
        "stop_price": None,
        "entry_utc": observed_at,
    }
    daily["entries"] = int(daily.get("entries", 0)) + 1
    daily["modeled_costs"] = float(daily.get("modeled_costs", 0.0)) + abs(qty) * abs(raw_price - effective)
    event = {
        "timestamp_utc": observed_at,
        "event": "ENTRY",
        "symbol": sym,
        "side": "long" if qty > 0 else "short",
        "qty": qty,
        "raw_price": raw_price,
        "effective_price": effective,
    }
    add_event(state, event)
    return event


def exit_symbol(state, sym, raw_price, observed_at, reason):
    positions = state.get("positions") or {}
    if sym not in positions:
        return None
    qty = float(positions[sym])
    cycle = state.setdefault("cycle", {}).get(sym, {})
    effective = exit_effective_price(qty, raw_price)
    state["cash"] = float(state.get("cash", 0.0)) + qty * effective

    entry_effective = float(cycle.get("entry_effective_price", cycle.get("entry_price", raw_price)))
    if qty > 0:
        pnl = qty * (effective - entry_effective)
        ret = effective / entry_effective - 1.0
    else:
        pnl = abs(qty) * (entry_effective - effective)
        ret = entry_effective / effective - 1.0

    daily = state["daily"]
    daily["exits"] = int(daily.get("exits", 0)) + 1
    daily["round_trips"] = int(daily.get("round_trips", 0)) + 1
    daily["realized_pnl"] = float(daily.get("realized_pnl", 0.0)) + pnl
    daily["modeled_costs"] = float(daily.get("modeled_costs", 0.0)) + abs(qty) * abs(raw_price - effective)
    per_sym = daily.setdefault("per_symbol_round_trips", {})
    per_sym[sym] = int(per_sym.get(sym, 0)) + 1

    event = {
        "timestamp_utc": observed_at,
        "event": "EXIT",
        "reason": reason,
        "symbol": sym,
        "side": "long" if qty > 0 else "short",
        "qty": qty,
        "entry_price": cycle.get("entry_price"),
        "best_price": cycle.get("best_price"),
        "stop_price": cycle.get("stop_price"),
        "raw_price": raw_price,
        "effective_price": effective,
        "net_realized_pnl": pnl,
        "net_realized_return_pct": ret * 100.0,
    }
    add_event(state, event)
    positions.pop(sym, None)
    state.setdefault("cycle", {}).pop(sym, None)
    state.setdefault("next_reentry_utc", {})[sym] = (datetime.now(timezone.utc) + timedelta(seconds=REENTRY_COOLDOWN_SECONDS)).isoformat()
    return event


def update_profit_locks(state, prices, observed_at):
    triggered = []
    c = EXECUTION_COST_BPS_PER_FILL / 10000.0
    for sym in sorted(list((state.get("positions") or {}).keys())):
        qty = float(state["positions"][sym])
        px = float(prices[sym])
        cycle = state.setdefault("cycle", {}).setdefault(sym, {
            "entry_price": px,
            "entry_effective_price": entry_effective_price(qty, px),
            "best_price": px,
            "armed": False,
            "stop_price": None,
            "entry_utc": observed_at,
        })
        entry = float(cycle["entry_price"])
        best = float(cycle.get("best_price", entry))
        best = max(best, px) if qty > 0 else min(best, px)
        cycle["best_price"] = best
        gain = best / entry - 1.0 if qty > 0 else entry / best - 1.0
        if not cycle.get("armed") and gain >= ARM_PROFIT_PCT:
            cycle["armed"] = True
        if not cycle.get("armed"):
            continue

        trail = TIGHT_TRAIL_PCT if gain >= TIGHTEN_PROFIT_PCT else BASE_TRAIL_PCT
        if qty > 0:
            break_even_trigger = entry * (1.0 + c) / (1.0 - c)
            candidate = best * (1.0 - trail)
            prior = float(cycle.get("stop_price") or 0.0)
            stop = max(prior, break_even_trigger, candidate)
            hit = px <= stop
        else:
            break_even_trigger = entry * (1.0 - c) / (1.0 + c)
            candidate = best * (1.0 + trail)
            prior = cycle.get("stop_price")
            stop = min(float(prior) if prior is not None else float("inf"), break_even_trigger, candidate)
            hit = px >= stop
        cycle["stop_price"] = stop
        if hit:
            event = exit_symbol(state, sym, px, observed_at, "MICRO_PROFIT_LOCK")
            if event:
                triggered.append(event)
    return triggered


def can_reenter(state, sym, now_dt):
    if sym in (state.get("positions") or {}):
        return False
    next_re = (state.get("next_reentry_utc") or {}).get(sym)
    if next_re:
        try:
            if now_dt < datetime.fromisoformat(next_re):
                return False
        except Exception:
            pass
    return True


def enter_available(state, prices, observed_at):
    events = []
    now_dt = datetime.fromisoformat(observed_at)
    for sym in sorted((state.get("control_template_quantities") or {}).keys()):
        if not can_reenter(state, sym, now_dt):
            continue
        event = enter_symbol(state, sym, float(prices[sym]), observed_at)
        if event:
            events.append(event)
    return events


def flatten_for_kill(state, prices, observed_at):
    events = []
    for sym in sorted(list((state.get("positions") or {}).keys())):
        event = exit_symbol(state, sym, float(prices[sym]), observed_at, "DAILY_DRAWDOWN_KILL")
        if event:
            events.append(event)
    state["daily"]["kill_switch"] = True
    return events


def write_markdown(status):
    d = status.get("daily") or {}
    cfg = status.get("micro_profit_lock_overlay") or {}
    lines = [
        "# MarketPulse Strategy 2 — Control Clone + Micro Profit Lock",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Shadow equity: **${status.get('account_equity', 0):,.2f}**",
        f"- Broker cash equity: **${status.get('broker_cash_equity', 0):,.2f}**",
        f"- Active symbols: **{status.get('active_symbol_count', 0)}**",
        f"- Today's entries: **{d.get('entries', 0)}**",
        f"- Today's exits / round trips: **{d.get('round_trips', 0)}**",
        f"- Today's realized P&L: **${d.get('realized_pnl', 0):+.2f}**",
        f"- Today's modeled execution costs: **${d.get('modeled_costs', 0):.2f}**",
        f"- Arm after: **+{cfg.get('arm_profit_pct', 0)*100:.2f}%**",
        f"- Base trail: **{cfg.get('base_trail_pct', 0)*100:.2f}%**",
        f"- Re-entry cooldown: **{cfg.get('reentry_cooldown_seconds', 0)} sec**",
        f"- Daily round-trip cap: **{cfg.get('max_round_trips_per_day', 0)}**",
        f"- Daily drawdown kill: **{cfg.get('daily_drawdown_kill_pct', 0)*100:.2f}%**",
        "",
        "High turnover is opportunity-driven, not guaranteed. Modeled costs can overwhelm small gross wins.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    credentials()
    state = load_json(STATE_FILE, {})
    if not state.get("initialized"):
        raise RuntimeError("Strategy 2 micro profit-lock state is not initialized")

    account = core.api("GET", "/v2/account")
    clock = core.api("GET", "/v2/clock")
    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        raise RuntimeError("Strategy 2 paper account is not active")
    if core.current_positions():
        raise RuntimeError("Strategy 2 broker account must remain flat")
    if core.open_orders():
        raise RuntimeError("Strategy 2 broker account has open orders")

    all_symbols = sorted((state.get("control_template_quantities") or {}).keys())
    prices = latest_prices(all_symbols)
    daily = ensure_daily(state, prices)
    samples = 0
    run_events = []
    deadline = time.time() + MONITOR_MINUTES * 60
    is_open = bool(clock.get("is_open"))

    while is_open and time.time() < deadline:
        observed_at = datetime.now(timezone.utc).isoformat()
        prices = latest_prices(all_symbols)
        ensure_daily(state, prices)

        if not state["daily"].get("kill_switch"):
            run_events.extend(enter_available(state, prices, observed_at))
            run_events.extend(update_profit_locks(state, prices, observed_at))

            equity = current_equity(state, prices)
            start_eq = float(state["daily"].get("start_equity", equity))
            if start_eq > 0 and equity <= start_eq * (1.0 - DAILY_DRAWDOWN_KILL_PCT):
                run_events.extend(flatten_for_kill(state, prices, observed_at))

        samples += 1
        is_open = bool(core.api("GET", "/v2/clock").get("is_open"))
        if is_open and time.time() + POLL_SECONDS < deadline:
            time.sleep(POLL_SECONDS)
        else:
            break

    prices = latest_prices(all_symbols)
    shadow_equity = current_equity(state, prices)
    state["shadow_equity"] = shadow_equity
    state["last_prices"] = prices
    state["last_monitor_utc"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_FILE, state)

    status = {
        "phase": "S2",
        "strategy": "2",
        "experiment": EXPERIMENT,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "MICRO_PROFIT_LOCK_KILLED_FOR_DAY" if state["daily"].get("kill_switch") else ("MICRO_PROFIT_LOCK_EVENTS" if run_events else "MICRO_PROFIT_LOCK_MONITOR_OK"),
        "account_equity": shadow_equity,
        "broker_cash_equity": float(account.get("equity") or 0.0),
        "broker_cash": float(account.get("cash") or 0.0),
        "broker_positions": {},
        "broker_open_orders": [],
        "market_open": bool(clock.get("is_open")),
        "account_status": account.get("status"),
        "trading_blocked": bool(account.get("trading_blocked")),
        "paper_base": core.PAPER_BASE,
        "live_trading_locked": True,
        "execution_mode": "synthetic_control_clone_micro_cycles",
        "control_template_quantities": state.get("control_template_quantities", {}),
        "active_symbol_count": len(state.get("positions", {})),
        "monitor_samples": samples,
        "daily": state.get("daily", {}),
        "recent_events": state.get("events", [])[-50:],
        "events_this_run": run_events,
        "micro_profit_lock_overlay": {
            "arm_profit_pct": ARM_PROFIT_PCT,
            "base_trail_pct": BASE_TRAIL_PCT,
            "tighten_profit_pct": TIGHTEN_PROFIT_PCT,
            "tight_trail_pct": TIGHT_TRAIL_PCT,
            "execution_cost_bps_per_fill": EXECUTION_COST_BPS_PER_FILL,
            "reentry_cooldown_seconds": REENTRY_COOLDOWN_SECONDS,
            "max_round_trips_per_day": MAX_ROUND_TRIPS_PER_DAY,
            "max_round_trips_per_symbol_per_day": MAX_ROUND_TRIPS_PER_SYMBOL_PER_DAY,
            "daily_drawdown_kill_pct": DAILY_DRAWDOWN_KILL_PCT,
            "poll_seconds": POLL_SECONDS,
            "trade_count_policy": "OPPORTUNITY_DRIVEN_CAP_NOT_QUOTA",
        },
        "break_even_guaranteed": False,
    }
    save_json(STATUS_FILE, status)
    write_markdown(status)
    append_log(status)


if __name__ == "__main__":
    main()
