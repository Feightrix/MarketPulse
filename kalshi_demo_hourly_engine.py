import base64
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# HARD SAFETY INVARIANT: this engine may authenticate and submit orders ONLY to Kalshi DEMO.
KALSHI_BASE = "https://external-api.demo.kalshi.co"
KALSHI_ROOT = "/trade-api/v2"
ALPACA_DATA = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")

SERIES = {
    "KXINXHUD": {"label": "S&P 500 Hourly Up/Down", "proxy": "SPY"},
    "KXNDQHUD": {"label": "NASDAQ-100 Hourly Up/Down", "proxy": "QQQ"},
}

STATE_JSON = "kalshi_demo_hourly_state.json"
STATUS_MD = "kalshi_demo_hourly_status.md"

ENABLE_DEMO_TRADING = os.getenv("ENABLE_KALSHI_DEMO_TRADING", "false").lower() == "true"
MAX_RISK_PER_TRADE_DOLLARS = 20.0
MAX_DAILY_LOSS_DOLLARS = 75.0
MAX_DAILY_ENTRIES = 12
MAX_CONCURRENT_POSITIONS = 4
MAX_CONTRACTS_PER_ORDER = 100
ENTRY_MINUTES_AFTER_OPEN = 12
LATEST_MINUTES_BEFORE_CLOSE = 18
MIN_EDGE = 0.08
MIN_MODEL_PROB = 0.60
MAX_SPREAD = 0.08
MIN_DIRECTIONAL_PRICE = 0.20
MAX_DIRECTIONAL_PRICE = 0.72
MIN_ABS_Z = 0.35
VOL_LOOKBACK_MINUTES = 60
VOL_SAFETY_MULTIPLIER = 1.15
PROBABILITY_SHRINK = 0.85

API_KEY_ID = os.getenv("KALSHI_DEMO_API_KEY_ID")
PRIVATE_KEY_PEM = os.getenv("KALSHI_DEMO_PRIVATE_KEY")
ALPACA_KEY = os.getenv("ALPACA_OPTIONS_API_KEY_ID")
ALPACA_SECRET = os.getenv("ALPACA_OPTIONS_SECRET_KEY")

if "demo.kalshi.co" not in KALSHI_BASE or "external-api.kalshi.com" in KALSHI_BASE:
    raise RuntimeError("Refusing non-demo Kalshi endpoint")
if not API_KEY_ID or not PRIVATE_KEY_PEM:
    raise RuntimeError("Missing Kalshi demo credentials")
if not ALPACA_KEY or not ALPACA_SECRET:
    raise RuntimeError("Missing Alpaca paper/data credentials")

PRIVATE_KEY = serialization.load_pem_private_key(
    PRIVATE_KEY_PEM.replace("\\n", "\n").encode("utf-8"), password=None
)


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def signed_headers(method, path, content_type=False):
    timestamp = str(int(time.time() * 1000))
    sign_path = path.split("?")[0]
    message = f"{timestamp}{method}{sign_path}".encode("utf-8")
    signature = PRIVATE_KEY.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    headers = {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        "Accept": "application/json",
        "User-Agent": "MarketPulse-Kalshi-Demo-Hourly/1.0",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def kalshi_get(path, params=None, authenticated=True):
    full_path = path
    if params:
        full_path += "?" + urllib.parse.urlencode(params)
    headers = signed_headers("GET", full_path) if authenticated else {"Accept": "application/json"}
    req = urllib.request.Request(KALSHI_BASE + full_path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def kalshi_post(path, payload):
    if not ENABLE_DEMO_TRADING:
        raise RuntimeError("Demo trading is disabled")
    if "demo.kalshi.co" not in KALSHI_BASE:
        raise RuntimeError("Refusing POST outside Kalshi demo")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        KALSHI_BASE + path,
        data=body,
        headers=signed_headers("POST", path, content_type=True),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def alpaca_get(path, params=None):
    url = ALPACA_DATA + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Accept": "application/json",
        "User-Agent": "MarketPulse-Kalshi-Demo-Hourly/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state():
    if Path(STATE_JSON).exists():
        try:
            state = json.loads(Path(STATE_JSON).read_text())
        except Exception:
            state = {}
    else:
        state = {}
    state.setdefault("traded_tickers", [])
    state.setdefault("orders", [])
    state.setdefault("events", [])
    state.setdefault("daily", {})
    return state


def save_state(state):
    Path(STATE_JSON).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def balance_equity_dollars(balance_payload):
    cash = float(balance_payload.get("balance") or 0) / 100.0
    portfolio = float(balance_payload.get("portfolio_value") or 0) / 100.0
    return cash, portfolio, cash + portfolio


def active_position_count(positions_payload):
    count = 0
    for p in positions_payload.get("market_positions") or []:
        raw = p.get("position_fp", p.get("position", 0))
        try:
            if abs(float(raw or 0)) > 1e-9:
                count += 1
        except (TypeError, ValueError):
            pass
    return count


def get_open_market(series_ticker, now):
    data = kalshi_get(KALSHI_ROOT + "/markets", {
        "series_ticker": series_ticker,
        "status": "open",
        "limit": 100,
    }, authenticated=False)
    candidates = []
    for market in data.get("markets") or []:
        try:
            open_time = parse_ts(market["open_time"])
            close_time = parse_ts(market.get("close_time") or market["expected_expiration_time"])
        except Exception:
            continue
        if open_time <= now < close_time:
            candidates.append((close_time, market))
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1] if candidates else None


def fetch_proxy_bars(symbol, start_utc, end_utc):
    data = alpaca_get(f"/v2/stocks/{symbol}/bars", {
        "timeframe": "1Min",
        "start": start_utc.isoformat().replace("+00:00", "Z"),
        "end": end_utc.isoformat().replace("+00:00", "Z"),
        "adjustment": "raw",
        "feed": "iex",
        "limit": 10000,
        "sort": "asc",
    })
    bars = []
    for b in data.get("bars") or []:
        try:
            ts = parse_ts(b["t"])
            bars.append({"ts": ts, "o": float(b["o"]), "c": float(b["c"])})
        except Exception:
            continue
    return bars


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def build_signal(series_ticker, meta, market, now):
    open_time = parse_ts(market["open_time"])
    close_time = parse_ts(market.get("close_time") or market["expected_expiration_time"])
    elapsed = (now - open_time).total_seconds() / 60.0
    remaining = (close_time - now).total_seconds() / 60.0
    if elapsed < ENTRY_MINUTES_AFTER_OPEN or remaining < LATEST_MINUTES_BEFORE_CLOSE:
        return {"qualified": False, "reason": "outside_entry_window", "elapsed_minutes": round(elapsed, 1), "remaining_minutes": round(remaining, 1)}

    yes_bid = float(market.get("yes_bid_dollars") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or 0)
    if not (0 < yes_bid < 1 and 0 < yes_ask < 1 and yes_ask >= yes_bid):
        return {"qualified": False, "reason": "invalid_market_quote"}
    spread = yes_ask - yes_bid
    if spread > MAX_SPREAD:
        return {"qualified": False, "reason": "spread_too_wide", "spread": round(spread, 4)}

    proxy = meta["proxy"]
    session_start_et = now.astimezone(ET).replace(hour=9, minute=30, second=0, microsecond=0)
    bars = fetch_proxy_bars(proxy, session_start_et.astimezone(timezone.utc), now)
    if len(bars) < 20:
        return {"qualified": False, "reason": "insufficient_proxy_bars"}

    hourly = [b for b in bars if b["ts"] >= open_time]
    if len(hourly) < 3:
        return {"qualified": False, "reason": "insufficient_hour_bars"}
    hour_open = hourly[0]["o"]
    current = hourly[-1]["c"]
    if hour_open <= 0 or current <= 0:
        return {"qualified": False, "reason": "invalid_proxy_price"}

    log_rets = []
    for a, b in zip(bars[:-1], bars[1:]):
        if a["c"] > 0 and b["c"] > 0:
            log_rets.append(math.log(b["c"] / a["c"]))
    recent = log_rets[-VOL_LOOKBACK_MINUTES:]
    if len(recent) < 15:
        return {"qualified": False, "reason": "insufficient_volatility_history"}
    sigma_1m = statistics.stdev(recent) if len(recent) > 1 else 0.0
    if sigma_1m <= 1e-7:
        return {"qualified": False, "reason": "zero_volatility"}

    displacement = math.log(current / hour_open)
    denom = sigma_1m * VOL_SAFETY_MULTIPLIER * math.sqrt(max(remaining, 1.0))
    z = displacement / denom
    raw_fair_yes = normal_cdf(z)
    fair_yes = 0.5 + PROBABILITY_SHRINK * (raw_fair_yes - 0.5)
    fair_yes = min(0.98, max(0.02, fair_yes))

    yes_cost = yes_ask
    no_cost = 1.0 - yes_bid
    edge_yes = fair_yes - yes_cost
    edge_no = (1.0 - fair_yes) - no_cost

    if edge_yes >= edge_no:
        direction = "YES"
        model_prob = fair_yes
        directional_price = yes_cost
        edge = edge_yes
        order_side = "bid"
        order_price_yes_scale = yes_ask
    else:
        direction = "NO"
        model_prob = 1.0 - fair_yes
        directional_price = no_cost
        edge = edge_no
        order_side = "ask"
        order_price_yes_scale = yes_bid

    qualified = (
        abs(z) >= MIN_ABS_Z
        and model_prob >= MIN_MODEL_PROB
        and edge >= MIN_EDGE
        and MIN_DIRECTIONAL_PRICE <= directional_price <= MAX_DIRECTIONAL_PRICE
    )
    reason = "qualified" if qualified else "edge_or_probability_below_gate"

    return {
        "qualified": qualified,
        "reason": reason,
        "series": series_ticker,
        "proxy": proxy,
        "market_ticker": market.get("ticker"),
        "direction": direction,
        "order_side": order_side,
        "order_price_yes_scale": round(order_price_yes_scale, 4),
        "directional_price": round(directional_price, 4),
        "yes_bid": round(yes_bid, 4),
        "yes_ask": round(yes_ask, 4),
        "spread": round(spread, 4),
        "model_probability": round(model_prob, 4),
        "model_fair_yes": round(fair_yes, 4),
        "edge": round(edge, 4),
        "z_score": round(z, 4),
        "proxy_hour_return_pct": round((current / hour_open - 1.0) * 100.0, 4),
        "elapsed_minutes": round(elapsed, 1),
        "remaining_minutes": round(remaining, 1),
        "market_open_time": market.get("open_time"),
        "market_close_time": market.get("close_time") or market.get("expected_expiration_time"),
    }


def append_event(state, event):
    item = dict(event)
    item["ts_utc"] = now_utc().isoformat()
    state["events"].append(item)
    state["events"] = state["events"][-500:]


def main():
    state = load_state()
    now = now_utc()
    et_date = now.astimezone(ET).date().isoformat()

    balance = kalshi_get(KALSHI_ROOT + "/portfolio/balance")
    positions = kalshi_get(KALSHI_ROOT + "/portfolio/positions", {"limit": 100})
    resting = kalshi_get(KALSHI_ROOT + "/portfolio/orders", {"status": "resting", "limit": 100})
    cash, portfolio_value, equity = balance_equity_dollars(balance)
    position_count = active_position_count(positions)

    daily = state["daily"].get(et_date)
    if not daily:
        daily = {"start_equity": equity, "entries": 0, "estimated_risk": 0.0}
        state["daily"] = {et_date: daily}
    day_pl = equity - float(daily.get("start_equity", equity))
    daily_locked = day_pl <= -MAX_DAILY_LOSS_DOLLARS

    run_signals = []
    for series_ticker, meta in SERIES.items():
        market = get_open_market(series_ticker, now)
        if not market:
            sig = {"series": series_ticker, "proxy": meta["proxy"], "qualified": False, "reason": "no_open_market"}
            run_signals.append(sig)
            continue

        sig = build_signal(series_ticker, meta, market, now)
        run_signals.append(sig)
        ticker = sig.get("market_ticker") or market.get("ticker")

        if not sig.get("qualified"):
            continue
        if ticker in state["traded_tickers"]:
            append_event(state, {"type": "skip", "reason": "ticker_already_traded", "ticker": ticker})
            continue
        if daily_locked:
            append_event(state, {"type": "skip", "reason": "daily_loss_lock", "ticker": ticker})
            continue
        if int(daily.get("entries", 0)) >= MAX_DAILY_ENTRIES:
            append_event(state, {"type": "skip", "reason": "daily_entry_cap", "ticker": ticker})
            continue
        if position_count >= MAX_CONCURRENT_POSITIONS:
            append_event(state, {"type": "skip", "reason": "concurrent_position_cap", "ticker": ticker})
            continue
        if len(resting.get("orders") or []) > 0:
            append_event(state, {"type": "skip", "reason": "resting_order_exists", "ticker": ticker})
            continue

        directional_price = float(sig["directional_price"])
        remaining_daily_risk = max(0.0, MAX_DAILY_LOSS_DOLLARS - float(daily.get("estimated_risk", 0.0)))
        risk_budget = min(MAX_RISK_PER_TRADE_DOLLARS, remaining_daily_risk)
        count = min(MAX_CONTRACTS_PER_ORDER, int(risk_budget // max(directional_price, 0.01)))
        if count < 1:
            append_event(state, {"type": "skip", "reason": "risk_budget_exhausted", "ticker": ticker})
            continue

        estimated_risk = count * directional_price
        order_record = {
            "ticker": ticker,
            "series": series_ticker,
            "proxy": meta["proxy"],
            "direction": sig["direction"],
            "count": count,
            "directional_price": directional_price,
            "estimated_risk_dollars": round(estimated_risk, 2),
            "model_probability": sig["model_probability"],
            "edge": sig["edge"],
            "submitted": False,
            "ts_utc": now.isoformat(),
        }

        if ENABLE_DEMO_TRADING:
            client_order_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"marketpulse-demo:{ticker}:{sig['direction']}"))
            payload = {
                "ticker": ticker,
                "client_order_id": client_order_id,
                "side": sig["order_side"],
                "count": f"{count:.2f}",
                "price": f"{float(sig['order_price_yes_scale']):.4f}",
                "time_in_force": "immediate_or_cancel",
                "self_trade_prevention_type": "taker_at_cross",
                "post_only": False,
                "cancel_order_on_pause": True,
                "reduce_only": False,
                "subaccount": 0,
                "exchange_index": -1,
            }
            response = kalshi_post(KALSHI_ROOT + "/portfolio/events/orders", payload)
            order_record.update({
                "submitted": True,
                "client_order_id": client_order_id,
                "order_id": response.get("order_id"),
                "fill_count": response.get("fill_count"),
                "remaining_count": response.get("remaining_count"),
                "average_fill_price": response.get("average_fill_price"),
                "average_fee_paid": response.get("average_fee_paid"),
            })
            state["traded_tickers"].append(ticker)
            daily["entries"] = int(daily.get("entries", 0)) + 1
            daily["estimated_risk"] = round(float(daily.get("estimated_risk", 0.0)) + estimated_risk, 2)
            position_count += 1
            append_event(state, {"type": "demo_order_submitted", **order_record})
        else:
            append_event(state, {"type": "qualified_signal_only", **order_record})

        state["orders"].append(order_record)
        state["orders"] = state["orders"][-500:]

    state["last_run"] = {
        "ts_utc": now.isoformat(),
        "environment": "demo",
        "demo_trading_enabled": ENABLE_DEMO_TRADING,
        "cash_dollars": round(cash, 2),
        "portfolio_value_dollars": round(portfolio_value, 2),
        "equity_dollars": round(equity, 2),
        "day_pl_dollars": round(day_pl, 2),
        "daily_loss_lock": daily_locked,
        "positions": position_count,
        "resting_orders": len(resting.get("orders") or []),
        "signals": run_signals,
    }
    save_state(state)

    lines = [
        "# MarketPulse — Kalshi Hourly Demo Engine",
        "",
        "**DEMO / MOCK MONEY ONLY. Production endpoint is blocked in code.**",
        "",
        f"- Demo trading enabled: **{'YES' if ENABLE_DEMO_TRADING else 'NO'}**",
        f"- Demo cash: **${cash:,.2f}**",
        f"- Demo portfolio value: **${portfolio_value:,.2f}**",
        f"- Demo equity: **${equity:,.2f}**",
        f"- Today's P/L: **${day_pl:,.2f}**",
        f"- Today's entries: **{daily.get('entries', 0)} / {MAX_DAILY_ENTRIES}**",
        f"- Estimated risk used today: **${float(daily.get('estimated_risk', 0.0)):,.2f} / ${MAX_DAILY_LOSS_DOLLARS:,.2f}**",
        f"- Daily loss lock: **{'ON' if daily_locked else 'OFF'}**",
        f"- Active positions: **{position_count} / {MAX_CONCURRENT_POSITIONS}**",
        "",
        "## Current signals",
    ]
    for s in run_signals:
        if s.get("market_ticker"):
            lines.append(
                f"- **{s.get('series')} / {s.get('proxy')}** `{s.get('market_ticker')}` — {s.get('direction','—')} | qualified **{'YES' if s.get('qualified') else 'NO'}** | reason `{s.get('reason')}` | model {s.get('model_probability','—')} | edge {s.get('edge','—')} | price {s.get('directional_price','—')}"
            )
        else:
            lines.append(f"- **{s.get('series')} / {s.get('proxy')}** — qualified **NO** | reason `{s.get('reason')}`")
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")
    print(json.dumps(state["last_run"], indent=2))


if __name__ == "__main__":
    main()
