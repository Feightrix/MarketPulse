import json
import math
import os
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KALSHI_PUBLIC_BASE = "https://external-api.kalshi.com"
KALSHI_ROOT = "/trade-api/v2"
ALPACA_DATA = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")

SERIES = {
    "KXINXHUD": {"label": "S&P 500 Hourly Up/Down", "proxy": "SPY"},
    "KXNDQHUD": {"label": "NASDAQ-100 Hourly Up/Down", "proxy": "QQQ"},
}

STATE_JSON = "kalshi_hourly_shadow_state.json"
STATUS_MD = "kalshi_hourly_shadow_status.md"
ORDER_SUBMISSION_ENABLED = False
STARTING_SHADOW_BALANCE = 2500.0
MAX_RISK_PER_TRADE_DOLLARS = 20.0
MAX_DAILY_RISK_DOLLARS = 75.0
MAX_DAILY_ENTRIES = 12
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

ALPACA_KEY = os.getenv("ALPACA_OPTIONS_API_KEY_ID")
ALPACA_SECRET = os.getenv("ALPACA_OPTIONS_SECRET_KEY")
if not ALPACA_KEY or not ALPACA_SECRET:
    raise RuntimeError("Missing Alpaca paper/data credentials")


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def public_get(path, params=None):
    url = KALSHI_PUBLIC_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MarketPulse-Kalshi-Shadow/1.0"})
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
        "User-Agent": "MarketPulse-Kalshi-Shadow/1.0",
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
    state.setdefault("open_trades", {})
    state.setdefault("closed_trades", [])
    state.setdefault("daily", {})
    state.setdefault("events", [])
    return state


def save_state(state):
    Path(STATE_JSON).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def append_event(state, event):
    item = dict(event)
    item["ts_utc"] = now_utc().isoformat()
    state["events"].append(item)
    state["events"] = state["events"][-500:]


def get_open_market(series_ticker, now):
    data = public_get(KALSHI_ROOT + "/markets", {
        "series_ticker": series_ticker,
        "status": "open",
        "limit": 100,
    })
    candidates = []
    for market in data.get("markets") or []:
        try:
            ot = parse_ts(market["open_time"])
            ct = parse_ts(market.get("close_time") or market["expected_expiration_time"])
        except Exception:
            continue
        if ot <= now < ct:
            candidates.append((ct, market))
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1] if candidates else None


def get_market(ticker):
    data = public_get(KALSHI_ROOT + "/markets/" + urllib.parse.quote(ticker, safe=""))
    return data.get("market") or {}


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
    out = []
    for b in data.get("bars") or []:
        try:
            out.append({"ts": parse_ts(b["t"]), "o": float(b["o"]), "c": float(b["c"])})
        except Exception:
            pass
    return out


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def build_signal(series_ticker, meta, market, now):
    open_time = parse_ts(market["open_time"])
    close_time = parse_ts(market.get("close_time") or market["expected_expiration_time"])
    elapsed = (now - open_time).total_seconds() / 60.0
    remaining = (close_time - now).total_seconds() / 60.0
    if elapsed < ENTRY_MINUTES_AFTER_OPEN or remaining < LATEST_MINUTES_BEFORE_CLOSE:
        return {"qualified": False, "reason": "outside_entry_window", "elapsed_minutes": round(elapsed, 1), "remaining_minutes": round(remaining, 1)}

    try:
        yes_bid = float(market.get("yes_bid_dollars") or 0)
        yes_ask = float(market.get("yes_ask_dollars") or 0)
    except Exception:
        return {"qualified": False, "reason": "invalid_market_quote"}
    if not (0 < yes_bid < 1 and 0 < yes_ask < 1 and yes_ask >= yes_bid):
        return {"qualified": False, "reason": "invalid_market_quote"}
    spread = yes_ask - yes_bid
    if spread > MAX_SPREAD:
        return {"qualified": False, "reason": "spread_too_wide", "spread": round(spread, 4)}

    session_start_et = now.astimezone(ET).replace(hour=9, minute=30, second=0, microsecond=0)
    bars = fetch_proxy_bars(meta["proxy"], session_start_et.astimezone(timezone.utc), now)
    hourly = [b for b in bars if b["ts"] >= open_time]
    if len(bars) < 20 or len(hourly) < 3:
        return {"qualified": False, "reason": "insufficient_proxy_bars"}

    hour_open = hourly[0]["o"]
    current = hourly[-1]["c"]
    rets = []
    for a, b in zip(bars[:-1], bars[1:]):
        if a["c"] > 0 and b["c"] > 0:
            rets.append(math.log(b["c"] / a["c"]))
    recent = rets[-VOL_LOOKBACK_MINUTES:]
    if len(recent) < 15:
        return {"qualified": False, "reason": "insufficient_volatility_history"}
    sigma = statistics.stdev(recent)
    if sigma <= 1e-7:
        return {"qualified": False, "reason": "zero_volatility"}

    displacement = math.log(current / hour_open)
    z = displacement / (sigma * VOL_SAFETY_MULTIPLIER * math.sqrt(max(remaining, 1.0)))
    fair_yes = 0.5 + PROBABILITY_SHRINK * (normal_cdf(z) - 0.5)
    fair_yes = min(0.98, max(0.02, fair_yes))

    yes_cost = yes_ask
    no_cost = 1.0 - yes_bid
    edge_yes = fair_yes - yes_cost
    edge_no = (1.0 - fair_yes) - no_cost
    if edge_yes >= edge_no:
        direction, model_prob, cost, edge = "YES", fair_yes, yes_cost, edge_yes
    else:
        direction, model_prob, cost, edge = "NO", 1.0 - fair_yes, no_cost, edge_no

    qualified = (
        abs(z) >= MIN_ABS_Z
        and model_prob >= MIN_MODEL_PROB
        and edge >= MIN_EDGE
        and MIN_DIRECTIONAL_PRICE <= cost <= MAX_DIRECTIONAL_PRICE
    )
    return {
        "qualified": qualified,
        "reason": "qualified" if qualified else "edge_or_probability_below_gate",
        "series": series_ticker,
        "proxy": meta["proxy"],
        "market_ticker": market.get("ticker"),
        "direction": direction,
        "entry_price": round(cost, 4),
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


def estimated_fee(count, price):
    # Current INX/NASDAQ100 taker-fee form: 0.035 * C * P * (1-P), rounded up to the next cent.
    raw = 0.035 * count * price * (1.0 - price)
    return math.ceil(raw * 100.0 - 1e-12) / 100.0


def settle_open_trades(state):
    remaining = {}
    for ticker, trade in state["open_trades"].items():
        try:
            market = get_market(ticker)
        except Exception:
            remaining[ticker] = trade
            continue
        result = str(market.get("result") or "").lower()
        if market.get("status") != "settled" or result not in ("yes", "no"):
            remaining[ticker] = trade
            continue
        won = trade["direction"].lower() == result
        payout = float(trade["count"]) if won else 0.0
        pnl = payout - float(trade["entry_cost_dollars"]) - float(trade["estimated_fee_dollars"])
        closed = dict(trade)
        closed.update({
            "result": result.upper(),
            "won": won,
            "payout_dollars": round(payout, 2),
            "pl_dollars": round(pnl, 2),
            "settled_ts": market.get("settlement_ts"),
            "expiration_value": market.get("expiration_value"),
        })
        state["closed_trades"].append(closed)
        append_event(state, {"type": "shadow_settled", "ticker": ticker, "pl_dollars": round(pnl, 2), "won": won})
    state["open_trades"] = remaining
    state["closed_trades"] = state["closed_trades"][-1000:]


def performance(closed):
    pls = [float(t.get("pl_dollars") or 0) for t in closed]
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p <= 0]
    net = sum(pls)
    gp = sum(wins)
    gl = -sum(losses)
    pf = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)
    eq = STARTING_SHADOW_BALANCE
    peak = eq
    max_dd = 0.0
    for p in pls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return {
        "trades": len(pls),
        "wins": len(wins),
        "win_rate_pct": round(100.0 * len(wins) / len(pls), 2) if pls else 0.0,
        "net_pl_dollars": round(net, 2),
        "ending_balance_dollars": round(STARTING_SHADOW_BALANCE + net, 2),
        "profit_factor": round(pf, 3),
        "max_drawdown_dollars": round(max_dd, 2),
    }


def main():
    state = load_state()
    now = now_utc()
    today = now.astimezone(ET).date().isoformat()
    settle_open_trades(state)

    daily = state["daily"].get(today)
    if not daily:
        daily = {"entries": 0, "risk_used_dollars": 0.0}
        state["daily"] = {today: daily}

    run_signals = []
    for series_ticker, meta in SERIES.items():
        market = get_open_market(series_ticker, now)
        if not market:
            run_signals.append({"series": series_ticker, "proxy": meta["proxy"], "qualified": False, "reason": "no_open_market"})
            continue
        sig = build_signal(series_ticker, meta, market, now)
        run_signals.append(sig)
        ticker = sig.get("market_ticker") or market.get("ticker")
        if not sig.get("qualified") or not ticker:
            continue
        if ticker in state["traded_tickers"]:
            continue
        if int(daily.get("entries", 0)) >= MAX_DAILY_ENTRIES:
            continue
        remaining_risk = MAX_DAILY_RISK_DOLLARS - float(daily.get("risk_used_dollars", 0))
        budget = min(MAX_RISK_PER_TRADE_DOLLARS, max(0.0, remaining_risk))
        price = float(sig["entry_price"])
        count = int(budget // max(price, 0.01))
        if count < 1:
            continue
        entry_cost = count * price
        fee = estimated_fee(count, price)
        trade = {
            "ticker": ticker,
            "series": series_ticker,
            "proxy": meta["proxy"],
            "direction": sig["direction"],
            "count": count,
            "entry_price": price,
            "entry_cost_dollars": round(entry_cost, 2),
            "estimated_fee_dollars": round(fee, 2),
            "model_probability": sig["model_probability"],
            "edge": sig["edge"],
            "z_score": sig["z_score"],
            "proxy_hour_return_pct": sig["proxy_hour_return_pct"],
            "entry_ts_utc": now.isoformat(),
            "market_close_time": sig["market_close_time"],
        }
        state["open_trades"][ticker] = trade
        state["traded_tickers"].append(ticker)
        daily["entries"] = int(daily.get("entries", 0)) + 1
        daily["risk_used_dollars"] = round(float(daily.get("risk_used_dollars", 0)) + entry_cost + fee, 2)
        append_event(state, {"type": "shadow_entry", **trade})

    perf = performance(state["closed_trades"])
    state["last_run"] = {
        "ts_utc": now.isoformat(),
        "order_submission_enabled": ORDER_SUBMISSION_ENABLED,
        "production_market_data_only": True,
        "signals": run_signals,
        "open_shadow_trades": len(state["open_trades"]),
        "performance": perf,
    }
    save_state(state)

    lines = [
        "# MarketPulse — Kalshi Hourly Production Shadow",
        "",
        "**REAL production market data, SHADOW trades only. No Kalshi authentication and no order submission.**",
        "",
        f"- Shadow starting balance: **${STARTING_SHADOW_BALANCE:,.2f}**",
        f"- Shadow ending balance: **${perf['ending_balance_dollars']:,.2f}**",
        f"- Settled shadow trades: **{perf['trades']}**",
        f"- Win rate: **{perf['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${perf['net_pl_dollars']:,.2f}**",
        f"- Profit factor: **{perf['profit_factor']}**",
        f"- Max drawdown: **${perf['max_drawdown_dollars']:,.2f}**",
        f"- Open shadow trades: **{len(state['open_trades'])}**",
        f"- Today's entries: **{daily.get('entries',0)} / {MAX_DAILY_ENTRIES}**",
        f"- Today's risk used: **${float(daily.get('risk_used_dollars',0)):,.2f} / ${MAX_DAILY_RISK_DOLLARS:,.2f}**",
        "",
        "## Current signals",
    ]
    for s in run_signals:
        if s.get("market_ticker"):
            lines.append(
                f"- **{s.get('series')} / {s.get('proxy')}** `{s.get('market_ticker')}` — {s.get('direction','—')} | qualified **{'YES' if s.get('qualified') else 'NO'}** | reason `{s.get('reason')}` | model {s.get('model_probability','—')} | edge {s.get('edge','—')} | price {s.get('entry_price','—')}"
            )
        else:
            lines.append(f"- **{s.get('series')} / {s.get('proxy')}** — qualified **NO** | reason `{s.get('reason')}`")
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")
    print(json.dumps(state["last_run"], indent=2))


if __name__ == "__main__":
    main()
