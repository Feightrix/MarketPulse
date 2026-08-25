import json
import math
import os
import time as time_module
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2

PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
UNDERLYING = "SPY"
ET = ZoneInfo("America/New_York")
STATE_FILE = Path("options_pattern2_forward_state.json")
STATUS_FILE = Path("options_pattern2_forward_status.md")

# Frozen Pattern #2 signal configuration.
DEVIATION_ATR = 1.0
WICK_RATIO_MIN = 0.40
RSI_EXTREME = 35.0
TARGET_R = 1.25
STOP_PAD_ATR = 0.10
MAX_VWAP_SLOPE_ATR = 0.50
MAX_EFFICIENCY = 0.65
MIN_RSI_TURN = 3.0
TRIGGER_WINDOW_MINUTES = 10
START_TIME = time(10, 0)
LATEST_ENTRY = time(14, 30)
FORCE_EXIT = time(15, 45)
EMERGENCY_EXIT = time(15, 55)

# Frozen contract policy from the historical option test.
TARGET_DTE = 4
TARGET_SIDE_MONEYNESS = -0.005  # slightly ITM for both calls and puts
TARGET_ABS_DELTA = 0.60
MIN_DTE = 1
MAX_DTE = 10
MAX_PREMIUM_FRACTION = 0.20
HARD_MAX_PREMIUM_DOLLARS = 500.0
MIN_PREMIUM_DOLLARS = 30.0
MAX_SPREAD_PCT = 0.12

# Forward-paper controls.
MAX_CONCURRENT_POSITIONS = 1
MAX_ENTRIES_PER_DAY = 2
DAILY_LOSS_LOCK_DOLLARS = 75.0
ENTRY_ORDER_STALE_MINUTES = 4
EXIT_ORDER_STALE_MINUTES = 4
MAX_CHASE_ATR = 0.15

API_KEY = os.environ.get("ALPACA_OPTIONS_API_KEY_ID")
SECRET_KEY = os.environ.get("ALPACA_OPTIONS_SECRET_KEY")
ENABLE = os.environ.get("ENABLE_OPTIONS_FORWARD_PAPER", "false").lower() == "true"
if not API_KEY or not SECRET_KEY:
    raise RuntimeError("Missing ALPACA_OPTIONS_API_KEY_ID / ALPACA_OPTIONS_SECRET_KEY")
if PAPER_BASE != "https://paper-api.alpaca.markets":
    raise RuntimeError("Paper endpoint invariant violated")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY,
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "MarketPulse-P2-forward-paper/1.0",
}


def request_json(method, url, params=None, payload=None, timeout=30, allow_status=()):
    if params:
        url = f"{url}?{urlencode(params)}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(url, headers=HEADERS, data=data, method=method)
    for attempt in range(5):
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code in allow_status:
                return {"_http_status": exc.code}
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            if exc.code == 429 and attempt < 4:
                time_module.sleep(1.0 + 1.5 * attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code} {method} {url}: {body}") from exc
        except URLError:
            if attempt < 4:
                time_module.sleep(1.0 + attempt)
                continue
            raise
    raise RuntimeError(f"Request failed: {method} {url}")


def now_et():
    return datetime.now(timezone.utc).astimezone(ET)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def default_state():
    return {
        "strategy": "options_pattern2_forward_paper",
        "trading_mode": "paper",
        "order_submission_enabled": True,
        "enabled": ENABLE,
        "daily": {"date": None, "entries": 0, "closed_pl_dollars": 0.0, "locked": False},
        "position": None,
        "last_signal_key": None,
        "closed_trades": [],
        "events": [],
    }


def load_state():
    if not STATE_FILE.exists():
        return default_state()
    state = json.loads(STATE_FILE.read_text())
    state["enabled"] = ENABLE
    return state


def append_event(state, event, **fields):
    item = {"ts_utc": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    state.setdefault("events", []).append(item)
    state["events"] = state["events"][-200:]


def rollover_day(state, today):
    d = state.setdefault("daily", {})
    if d.get("date") != today:
        state["daily"] = {"date": today, "entries": 0, "closed_pl_dollars": 0.0, "locked": False}
        append_event(state, "NEW_TRADING_DAY", date=today)
        return True
    return False


def account():
    return request_json("GET", f"{PAPER_BASE}/v2/account")


def clock():
    return request_json("GET", f"{PAPER_BASE}/v2/clock")


def positions():
    return request_json("GET", f"{PAPER_BASE}/v2/positions")


def get_order(order_id):
    return request_json("GET", f"{PAPER_BASE}/v2/orders/{order_id}")


def cancel_order(order_id):
    return request_json("DELETE", f"{PAPER_BASE}/v2/orders/{order_id}", allow_status=(404, 422))


def submit_order(payload):
    if not ENABLE:
        raise RuntimeError("Forward paper order submission is disabled")
    if not payload.get("client_order_id", "").startswith("mp-p2-paper-"):
        raise RuntimeError("Client-order-id safety invariant violated")
    return request_json("POST", f"{PAPER_BASE}/v2/orders", payload=payload)


def fetch_stock_bars(timeframe="5Min"):
    n = now_et()
    start = n.replace(hour=9, minute=25, second=0, microsecond=0)
    data = request_json(
        "GET",
        f"{DATA_BASE}/v2/stocks/{UNDERLYING}/bars",
        params={
            "timeframe": timeframe,
            "start": iso(start),
            "end": iso(n + timedelta(minutes=1)),
            "feed": "iex",
            "adjustment": "raw",
            "limit": 1000,
            "sort": "asc",
        },
    )
    out = []
    for raw in data.get("bars", []):
        ts = parse_ts(raw["t"]).astimezone(ET)
        out.append({
            "ts": ts,
            "o": float(raw["o"]), "h": float(raw["h"]), "l": float(raw["l"]),
            "c": float(raw["c"]), "v": float(raw.get("v") or 0.0),
        })
    return out


def latest_stock_spot():
    data = request_json(
        "GET", f"{DATA_BASE}/v2/stocks/{UNDERLYING}/snapshot", params={"feed": "iex"}
    )
    q = data.get("latestQuote") or data.get("latest_quote") or {}
    t = data.get("latestTrade") or data.get("latest_trade") or {}
    bid = float(q.get("bp") or q.get("bid_price") or 0.0)
    ask = float(q.get("ap") or q.get("ask_price") or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return float(t.get("p") or t.get("price") or 0.0)


def complete_5m_bars(bars):
    n = now_et()
    return [b for b in bars if b["ts"] + timedelta(minutes=5) <= n - timedelta(seconds=10)]


def prepare_indicators(bars):
    base.add_session_vwap(bars)
    p2.add_atr_rsi(bars)


def trend_features(bars, i, slope_lookback=6, efficiency_lookback=12):
    if i < max(slope_lookback, efficiency_lookback):
        return None
    atr = bars[i].get("atr", 0.0)
    if atr <= 0:
        return None
    slope = (bars[i]["vwap"] - bars[i - slope_lookback]["vwap"]) / atr
    window = bars[i - efficiency_lookback:i + 1]
    path = sum(abs(window[j]["c"] - window[j - 1]["c"]) for j in range(1, len(window)))
    net = abs(window[-1]["c"] - window[0]["c"])
    efficiency = net / path if path > 0 else 0.0
    return slope, efficiency


def find_live_signal(bars, spot, last_signal_key):
    n = now_et()
    if not bars or n.time() < START_TIME or n.time() > LATEST_ENTRY:
        return None
    # Only setup candles that have completed; trigger can happen live for ten minutes after setup close.
    for i in range(len(bars) - 1, 14, -1):
        b = bars[i]
        setup_end = b["ts"] + timedelta(minutes=5)
        if n < setup_end or n > setup_end + timedelta(minutes=TRIGGER_WINDOW_MINUTES):
            continue
        if b["ts"].time() < START_TIME:
            continue
        atr = b.get("atr", 0.0)
        if atr <= 0:
            continue
        feat = trend_features(bars, i)
        if feat is None:
            continue
        slope_atr, efficiency = feat
        upper_wick, lower_wick, close_loc = p2.wick_ratios(b)
        prev_rsi = bars[i - 1].get("rsi", 50.0)

        for side in ("CALL", "PUT"):
            key = f"{b['ts'].date()}-{side}-{b['ts'].strftime('%H%M')}"
            if key == last_signal_key:
                continue
            if side == "CALL":
                deviation = b["vwap"] - b["l"]
                valid = (
                    deviation >= DEVIATION_ATR * atr
                    and b["c"] > b["o"]
                    and lower_wick >= WICK_RATIO_MIN
                    and close_loc >= 0.60
                    and b["rsi"] <= RSI_EXTREME
                    and b["rsi"] - prev_rsi >= MIN_RSI_TURN
                    and all(x["c"] < x["vwap"] for x in bars[max(0, i - 2):i + 1])
                    and slope_atr >= -MAX_VWAP_SLOPE_ATR
                    and efficiency <= MAX_EFFICIENCY
                )
                trigger = b["h"] + base.TICK
                stop = b["l"] - base.TICK - STOP_PAD_ATR * atr
                triggered = spot >= trigger
                chase = spot - trigger
                risk = spot - stop
                target = spot + TARGET_R * risk if risk > 0 else 0.0
                target_ok = target <= bars[-1]["vwap"]
            else:
                deviation = b["h"] - b["vwap"]
                valid = (
                    deviation >= DEVIATION_ATR * atr
                    and b["c"] < b["o"]
                    and upper_wick >= WICK_RATIO_MIN
                    and close_loc <= 0.40
                    and b["rsi"] >= 100.0 - RSI_EXTREME
                    and prev_rsi - b["rsi"] >= MIN_RSI_TURN
                    and all(x["c"] > x["vwap"] for x in bars[max(0, i - 2):i + 1])
                    and slope_atr <= MAX_VWAP_SLOPE_ATR
                    and efficiency <= MAX_EFFICIENCY
                )
                trigger = b["l"] - base.TICK
                stop = b["h"] + base.TICK + STOP_PAD_ATR * atr
                triggered = spot <= trigger
                chase = trigger - spot
                risk = stop - spot
                target = spot - TARGET_R * risk if risk > 0 else 0.0
                target_ok = target >= bars[-1]["vwap"]

            if valid and triggered and risk > 0 and target_ok and chase <= MAX_CHASE_ATR * atr:
                return {
                    "signal_key": key,
                    "side": side,
                    "setup_ts": b["ts"].isoformat(),
                    "trigger": round(trigger, 4),
                    "underlying_entry_spot": round(spot, 4),
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    "atr": round(atr, 4),
                    "rsi": round(b["rsi"], 2),
                    "vwap": round(bars[-1]["vwap"], 4),
                    "vwap_slope_atr": round(slope_atr, 4),
                    "efficiency": round(efficiency, 4),
                }
    return None


def side_moneyness(side, strike, spot):
    return strike / spot - 1.0 if side == "CALL" else 1.0 - strike / spot


def fetch_contracts(signal_date, side, spot):
    day = datetime.fromisoformat(signal_date).date()
    option_type = "call" if side == "CALL" else "put"
    data = request_json(
        "GET",
        f"{PAPER_BASE}/v2/options/contracts",
        params={
            "underlying_symbols": UNDERLYING,
            "status": "active",
            "expiration_date_gte": (day + timedelta(days=MIN_DTE)).isoformat(),
            "expiration_date_lte": (day + timedelta(days=MAX_DTE)).isoformat(),
            "type": option_type,
            "strike_price_gte": f"{spot * 0.94:.2f}",
            "strike_price_lte": f"{spot * 1.06:.2f}",
            "limit": 1000,
        },
    )
    return data.get("option_contracts", [])


def fetch_option_snapshots(symbols):
    snapshots = {}
    feed_used = None
    for feed in ("opra", "indicative"):
        failed = False
        snapshots.clear()
        for start in range(0, len(symbols), 100):
            batch = symbols[start:start + 100]
            data = request_json(
                "GET",
                f"{DATA_BASE}/v1beta1/options/snapshots",
                params={"symbols": ",".join(batch), "feed": feed, "limit": 1000},
                allow_status=(403,),
            )
            if data.get("_http_status") == 403:
                failed = True
                break
            raw = data.get("snapshots", data)
            if isinstance(raw, dict):
                snapshots.update(raw)
        if not failed:
            feed_used = feed
            break
    return snapshots, feed_used


def choose_contract(side, spot, acct):
    signal_date = now_et().date().isoformat()
    contracts = fetch_contracts(signal_date, side, spot)
    metas = []
    for c in contracts:
        try:
            exp = datetime.fromisoformat(c["expiration_date"]).date()
            strike = float(c["strike_price"])
            dte = (exp - now_et().date()).days
            money = side_moneyness(side, strike, spot)
            metas.append({"symbol": c["symbol"], "expiration_date": c["expiration_date"], "strike": strike, "dte": dte, "money": money})
        except Exception:
            continue
    metas.sort(key=lambda m: (abs(m["dte"] - TARGET_DTE), abs(m["money"] - TARGET_SIDE_MONEYNESS)))
    metas = metas[:60]
    snaps, feed = fetch_option_snapshots([m["symbol"] for m in metas])
    max_premium = min(HARD_MAX_PREMIUM_DOLLARS, float(acct["equity"]) * MAX_PREMIUM_FRACTION)
    best = None
    for m in metas:
        s = snaps.get(m["symbol"], {})
        q = s.get("latestQuote") or s.get("latest_quote") or {}
        g = s.get("greeks") or {}
        try:
            ask = float(q.get("ap") or q.get("ask_price") or 0.0)
            bid = float(q.get("bp") or q.get("bid_price") or 0.0)
            delta = float(g.get("delta"))
        except Exception:
            continue
        if ask <= 0 or bid <= 0 or ask < bid:
            continue
        premium = ask * 100.0
        if premium < MIN_PREMIUM_DOLLARS or premium > max_premium:
            continue
        spread_pct = (ask - bid) / ((ask + bid) / 2.0)
        if spread_pct > MAX_SPREAD_PCT:
            continue
        score = (
            0.30 * abs(m["dte"] - TARGET_DTE) / 3.0
            + 0.30 * abs(m["money"] - TARGET_SIDE_MONEYNESS) / 0.005
            + 0.40 * abs(abs(delta) - TARGET_ABS_DELTA) / 0.15
        )
        candidate = {**m, "ask": ask, "bid": bid, "delta": delta, "spread_pct": spread_pct, "premium_dollars": premium, "score": score, "feed": feed}
        if best is None or candidate["score"] < best["score"]:
            best = candidate
    return best


def latest_option_quote(symbol):
    snaps, feed = fetch_option_snapshots([symbol])
    s = snaps.get(symbol, {})
    q = s.get("latestQuote") or s.get("latest_quote") or {}
    bid = float(q.get("bp") or q.get("bid_price") or 0.0)
    ask = float(q.get("ap") or q.get("ask_price") or 0.0)
    return bid, ask, feed


def client_id(kind, signal_key):
    safe = signal_key.replace(":", "").replace("-", "")[-24:]
    return f"mp-p2-paper-{kind}-{safe}"[:48]


def submit_entry(state, signal, contract):
    limit_price = round(contract["ask"] + 1e-9, 2)
    payload = {
        "symbol": contract["symbol"], "qty": "1", "side": "buy", "type": "limit",
        "time_in_force": "day", "limit_price": f"{limit_price:.2f}",
        "position_intent": "buy_to_open", "client_order_id": client_id("entry", signal["signal_key"]),
    }
    order = submit_order(payload)
    state["position"] = {
        "status": "entry_pending", "side": signal["side"], "signal_key": signal["signal_key"],
        "setup_ts": signal["setup_ts"], "order_id": order["id"], "order_submitted_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": contract["symbol"], "expiration_date": contract["expiration_date"], "dte": contract["dte"],
        "strike": contract["strike"], "delta": round(contract["delta"], 4), "option_feed": contract["feed"],
        "quoted_bid": round(contract["bid"], 4), "quoted_ask": round(contract["ask"], 4),
        "quoted_premium_dollars": round(contract["premium_dollars"], 2), "spread_pct": round(contract["spread_pct"] * 100.0, 2),
        "underlying_entry_spot": signal["underlying_entry_spot"], "underlying_stop": signal["stop"], "underlying_target": signal["target"],
        "entry_limit": limit_price,
    }
    state["last_signal_key"] = signal["signal_key"]
    state["daily"]["entries"] += 1
    append_event(state, "ENTRY_SUBMITTED", symbol=contract["symbol"], side=signal["side"], limit_price=limit_price, delta=round(contract["delta"], 4))


def submit_exit(state, reason, emergency=False):
    pos = state["position"]
    bid, ask, feed = latest_option_quote(pos["symbol"])
    payload = {
        "symbol": pos["symbol"], "qty": "1", "side": "sell", "time_in_force": "day",
        "position_intent": "sell_to_close", "client_order_id": client_id("exit", pos["signal_key"]),
    }
    if emergency or bid <= 0:
        payload["type"] = "market"
        exit_limit = None
    else:
        payload["type"] = "limit"
        exit_limit = max(0.01, round(bid, 2))
        payload["limit_price"] = f"{exit_limit:.2f}"
    order = submit_order(payload)
    pos.update({
        "status": "exit_pending", "exit_order_id": order["id"], "exit_submitted_utc": datetime.now(timezone.utc).isoformat(),
        "exit_reason": reason, "exit_limit": exit_limit, "exit_feed": feed,
    })
    append_event(state, "EXIT_SUBMITTED", symbol=pos["symbol"], reason=reason, order_type=payload["type"], limit_price=exit_limit)


def option_position_exists(symbol):
    for p in positions():
        if p.get("symbol") == symbol and abs(float(p.get("qty") or 0.0)) > 0:
            return True
    return False


def exit_reason_from_underlying(pos):
    n = now_et()
    if n.time() >= FORCE_EXIT:
        return "TIME"
    bars = fetch_stock_bars("1Min")
    entry_ts = parse_ts(pos.get("entry_filled_utc") or pos["order_submitted_utc"]).astimezone(ET)
    for b in bars:
        if b["ts"] < entry_ts:
            continue
        if pos["side"] == "CALL":
            stop_hit = b["l"] <= pos["underlying_stop"]
            target_hit = b["h"] >= pos["underlying_target"]
        else:
            stop_hit = b["h"] >= pos["underlying_stop"]
            target_hit = b["l"] <= pos["underlying_target"]
        if stop_hit:
            return "STOP"
        if target_hit:
            return "TARGET"
    spot = latest_stock_spot()
    if pos["side"] == "CALL":
        if spot <= pos["underlying_stop"]: return "STOP"
        if spot >= pos["underlying_target"]: return "TARGET"
    else:
        if spot >= pos["underlying_stop"]: return "STOP"
        if spot <= pos["underlying_target"]: return "TARGET"
    return None


def reconcile_position(state):
    pos = state.get("position")
    if not pos:
        return False
    changed = False
    n = now_et()
    if pos["status"] == "entry_pending":
        order = get_order(pos["order_id"])
        status = order.get("status")
        if status == "filled":
            pos["status"] = "open"
            pos["entry_fill"] = float(order.get("filled_avg_price") or pos["entry_limit"])
            pos["entry_filled_utc"] = order.get("filled_at") or datetime.now(timezone.utc).isoformat()
            append_event(state, "ENTRY_FILLED", symbol=pos["symbol"], fill=pos["entry_fill"])
            changed = True
        elif status in {"canceled", "expired", "rejected", "done_for_day"}:
            append_event(state, "ENTRY_CLOSED_UNFILLED", symbol=pos["symbol"], status=status)
            state["position"] = None
            return True
        else:
            age = datetime.now(timezone.utc) - parse_ts(pos["order_submitted_utc"])
            if age >= timedelta(minutes=ENTRY_ORDER_STALE_MINUTES):
                cancel_order(pos["order_id"])
                append_event(state, "ENTRY_CANCEL_REQUESTED", symbol=pos["symbol"])
                changed = True
        return changed

    if pos["status"] == "open":
        if not option_position_exists(pos["symbol"]):
            append_event(state, "POSITION_NOT_FOUND", symbol=pos["symbol"])
            state["position"] = None
            return True
        reason = exit_reason_from_underlying(pos)
        if reason:
            submit_exit(state, reason, emergency=n.time() >= EMERGENCY_EXIT)
            return True
        return changed

    if pos["status"] == "exit_pending":
        order = get_order(pos["exit_order_id"])
        status = order.get("status")
        if status == "filled":
            exit_fill = float(order.get("filled_avg_price") or pos.get("exit_limit") or 0.0)
            entry_fill = float(pos.get("entry_fill") or 0.0)
            pl = round((exit_fill - entry_fill) * 100.0, 2)
            trade = {
                "signal_key": pos["signal_key"], "side": pos["side"], "symbol": pos["symbol"],
                "entry_fill": entry_fill, "exit_fill": exit_fill, "pl_dollars": pl,
                "exit_reason": pos.get("exit_reason"), "closed_utc": order.get("filled_at") or datetime.now(timezone.utc).isoformat(),
                "underlying_entry_spot": pos["underlying_entry_spot"], "underlying_stop": pos["underlying_stop"], "underlying_target": pos["underlying_target"],
                "delta": pos.get("delta"), "dte": pos.get("dte"), "premium_dollars": round(entry_fill * 100.0, 2),
            }
            state.setdefault("closed_trades", []).append(trade)
            state["closed_trades"] = state["closed_trades"][-500:]
            state["daily"]["closed_pl_dollars"] = round(state["daily"].get("closed_pl_dollars", 0.0) + pl, 2)
            if state["daily"]["closed_pl_dollars"] <= -DAILY_LOSS_LOCK_DOLLARS:
                state["daily"]["locked"] = True
            append_event(state, "EXIT_FILLED", symbol=pos["symbol"], fill=exit_fill, pl_dollars=pl, reason=pos.get("exit_reason"))
            state["position"] = None
            return True
        if status in {"canceled", "expired", "rejected", "done_for_day"}:
            pos["status"] = "open"
            append_event(state, "EXIT_UNFILLED_RETRY", symbol=pos["symbol"], status=status)
            return True
        age = datetime.now(timezone.utc) - parse_ts(pos["exit_submitted_utc"])
        if age >= timedelta(minutes=EXIT_ORDER_STALE_MINUTES):
            cancel_order(pos["exit_order_id"])
            pos["status"] = "open"
            append_event(state, "EXIT_CANCEL_REQUESTED", symbol=pos["symbol"])
            if n.time() >= EMERGENCY_EXIT and option_position_exists(pos["symbol"]):
                submit_exit(state, pos.get("exit_reason") or "TIME", emergency=True)
            return True
    return changed


def can_open_new(state, acct, market_clock):
    n = now_et()
    if not ENABLE:
        return False, "disabled"
    if not market_clock.get("is_open", False):
        return False, "market_closed"
    if n.time() < START_TIME or n.time() > LATEST_ENTRY:
        return False, "outside_entry_window"
    if state.get("position"):
        return False, "position_active"
    if state["daily"].get("locked") or state["daily"].get("closed_pl_dollars", 0.0) <= -DAILY_LOSS_LOCK_DOLLARS:
        return False, "daily_loss_lock"
    if state["daily"].get("entries", 0) >= MAX_ENTRIES_PER_DAY:
        return False, "daily_entry_cap"
    if acct.get("trading_blocked"):
        return False, "trading_blocked"
    if int(acct.get("options_trading_level") or 0) < 3:
        return False, "options_level_below_3"
    open_positions = [p for p in positions() if abs(float(p.get("qty") or 0.0)) > 0]
    if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
        return False, "existing_position"
    return True, "ok"


def write_files(state, acct=None, market_clock=None):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    closed = state.get("closed_trades", [])
    wins = [t for t in closed if t["pl_dollars"] > 0]
    net = round(sum(t["pl_dollars"] for t in closed), 2)
    win_rate = (len(wins) / len(closed) * 100.0) if closed else 0.0
    pos = state.get("position")
    lines = [
        "# MarketPulse — Pattern #2 Forward Paper Status", "",
        "**PAPER ONLY — Alpaca paper endpoint is hard-coded.**", "",
        f"- Enabled: **{'YES' if ENABLE else 'NO'}**",
        f"- Closed forward trades: **{len(closed)}**",
        f"- Forward win rate: **{win_rate:.2f}%**",
        f"- Forward realized P/L: **${net:,.2f}**",
        f"- Today's entries: **{state['daily'].get('entries', 0)} / {MAX_ENTRIES_PER_DAY}**",
        f"- Today's realized P/L: **${state['daily'].get('closed_pl_dollars', 0.0):,.2f}**",
        f"- Daily loss lock: **{'ON' if state['daily'].get('locked') else 'OFF'}**",
        f"- Active state: **{pos['status'] if pos else 'FLAT'}**",
    ]
    if acct:
        lines += [f"- Paper equity: **${float(acct.get('equity') or 0):,.2f}**", f"- Options buying power: **${float(acct.get('options_buying_power') or 0):,.2f}**"]
    if market_clock:
        lines += [f"- Market open at last state change: **{'YES' if market_clock.get('is_open') else 'NO'}**"]
    if pos:
        lines += ["", "## Active Position", f"- {pos['side']} **{pos['symbol']}**", f"- Contract delta: **{pos.get('delta')}** | DTE: **{pos.get('dte')}**", f"- Underlying stop: **{pos['underlying_stop']}** | target: **{pos['underlying_target']}**"]
    if closed:
        lines += ["", "## Latest Closed Trade", f"- {closed[-1]['side']} **{closed[-1]['symbol']}**", f"- P/L: **${closed[-1]['pl_dollars']:,.2f}** | Exit: **{closed[-1]['exit_reason']}**"]
    STATUS_FILE.write_text("\n".join(lines) + "\n")


def main():
    state = load_state()
    n = now_et()
    changed = rollover_day(state, n.date().isoformat())
    acct = account()
    market_clock = clock()

    # Reconcile an existing order/position even if entries are disabled.
    if state.get("position"):
        if reconcile_position(state):
            changed = True

    allowed, reason = can_open_new(state, acct, market_clock)
    if allowed:
        bars = complete_5m_bars(fetch_stock_bars("5Min"))
        if len(bars) >= 20:
            prepare_indicators(bars)
            spot = latest_stock_spot()
            signal = find_live_signal(bars, spot, state.get("last_signal_key"))
            if signal:
                contract = choose_contract(signal["side"], spot, acct)
                if contract:
                    if float(acct.get("options_buying_power") or 0.0) >= contract["premium_dollars"]:
                        submit_entry(state, signal, contract)
                        changed = True
                    else:
                        state["last_signal_key"] = signal["signal_key"]
                        append_event(state, "SIGNAL_SKIPPED_BUYING_POWER", signal_key=signal["signal_key"])
                        changed = True
                else:
                    state["last_signal_key"] = signal["signal_key"]
                    append_event(state, "SIGNAL_SKIPPED_NO_CONTRACT", signal_key=signal["signal_key"])
                    changed = True
    elif reason == "daily_loss_lock" and not state["daily"].get("locked"):
        state["daily"]["locked"] = True
        append_event(state, "DAILY_LOSS_LOCK", pl_dollars=state["daily"].get("closed_pl_dollars", 0.0))
        changed = True

    # Create initial files, otherwise only mutate repository state when something changed.
    if changed or not STATE_FILE.exists() or not STATUS_FILE.exists():
        write_files(state, acct, market_clock)

    print(json.dumps({
        "strategy": state["strategy"], "paper": True, "enabled": ENABLE,
        "market_open": bool(market_clock.get("is_open")), "entry_allowed": allowed,
        "entry_block_reason": reason, "active_state": state["position"]["status"] if state.get("position") else "FLAT",
        "daily": state["daily"], "closed_trades": len(state.get("closed_trades", [])),
    }, indent=2))


if __name__ == "__main__":
    main()
