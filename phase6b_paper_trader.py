import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# SECURITY: this module has NO live trading endpoint and accepts no endpoint override.
PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"

TREND_ASSETS = ["SPY", "QQQ", "IWM", "XLE", "XLP", "XLU", "GLD", "TLT"]
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
DEFENSIVE = "BIL"
SYMS = sorted(set(TREND_ASSETS + SECTORS + [DEFENSIVE]))

# Exact frozen 5H / 6A strategy.
LOOKBACK = 252
TREND_SMA = 150
VOL_WINDOW = 42
TARGET_VOL = 0.08
RISK_CAP = 0.50
BREADTH_MIN = 2
SPY_GATE_SMA = 200
SECTOR_LOOKBACK = 252
TOP_N = 3
TREND_WEIGHT = 0.85
NEUTRAL_WEIGHT = 0.15

STATE_FILE = "phase6b_paper_state.json"
STATUS_FILE = "phase6b_paper_status.json"
STATUS_MD = "phase6b_paper_status.md"
LOG_FILE = "phase6b_paper_log.jsonl"


def credentials():
    # Prefer dedicated paper secrets. Existing Alpaca secrets are fallback only so
    # current users can test whether their saved keys are already paper keys.
    key = os.getenv("ALPACA_PAPER_API_KEY_ID") or os.getenv("ALPACA_API_KEY_ID")
    secret = os.getenv("ALPACA_PAPER_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Missing Alpaca paper credentials")
    return key, secret


def headers():
    key, secret = credentials()
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}


def api(method, path, body=None, timeout=45):
    # The base is a constant. Never accept a live URL through env/config.
    if PAPER_BASE != "https://paper-api.alpaca.markets":
        raise RuntimeError("Paper endpoint security invariant violated")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(PAPER_BASE + path, data=data, headers=headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Paper API {method} {path} failed: HTTP {e.code}: {detail}") from e


def data_headers():
    key, secret = credentials()
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch_daily(sym):
    q = {
        "timeframe": "1Day",
        "start": "2024-01-01T00:00:00Z",
        "end": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjustment": "all",
        "feed": "iex",
        "limit": 10000,
        "sort": "asc",
    }
    url = f"{DATA_BASE}/v2/stocks/{sym}/bars?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers=data_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode())
    rows = payload.get("bars", [])
    if not rows:
        raise RuntimeError(f"No market data for {sym}")
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert("America/New_York").dt.date
    return pd.DataFrame({"Open": d["o"].astype(float).values, "Close": d["c"].astype(float).values}, index=d["date"]).drop_duplicates().sort_index()


def load_panel():
    raw = {s: fetch_daily(s) for s in SYMS}
    common = None
    for s in SYMS:
        idx = set(raw[s].index)
        common = idx if common is None else common & idx
    common = sorted(common)
    if len(common) < 300:
        raise RuntimeError(f"Insufficient common market history: {len(common)} bars")
    o = pd.DataFrame({s: raw[s].loc[common, "Open"] for s in SYMS}, index=common).astype(float)
    c = pd.DataFrame({s: raw[s].loc[common, "Close"] for s in SYMS}, index=common).astype(float)
    return o, c


def trend_target(c, i):
    need = max(LOOKBACK, TREND_SMA, VOL_WINDOW + 1, SPY_GATE_SMA)
    if i < need:
        return {DEFENSIVE: 1.0}
    eligible = []
    for s in TREND_ASSETS:
        px = float(c.iloc[i][s])
        old = float(c.iloc[i - LOOKBACK][s])
        sma = float(c[s].iloc[i - TREND_SMA + 1:i + 1].mean())
        mom = px / old - 1.0 if old > 0 else np.nan
        if np.all(np.isfinite([px, old, sma, mom])) and px > sma and mom > 0:
            eligible.append(s)
    if len(eligible) < BREADTH_MIN:
        return {DEFENSIVE: 1.0}
    spy = float(c.iloc[i]["SPY"])
    spy_old = float(c.iloc[i - LOOKBACK]["SPY"])
    spy_sma = float(c["SPY"].iloc[i - SPY_GATE_SMA + 1:i + 1].mean())
    if not (spy > spy_sma and spy > spy_old):
        return {DEFENSIVE: 1.0}
    r = c[eligible].pct_change().iloc[i - VOL_WINDOW + 1:i + 1].dropna()
    vols = (r.std(ddof=1) * np.sqrt(252)).replace([np.inf, -np.inf], np.nan).dropna()
    vols = vols[vols > 0]
    eligible = [s for s in eligible if s in vols.index]
    if not eligible:
        return {DEFENSIVE: 1.0}
    inv = 1.0 / vols[eligible]
    base = inv / inv.sum()
    cov = r[eligible].cov().to_numpy() * 252.0
    wv = base.to_numpy(float)
    pvol = float(np.sqrt(max(float(wv @ cov @ wv), 0.0)))
    if not np.isfinite(pvol) or pvol <= 0:
        return {DEFENSIVE: 1.0}
    scale = max(0.0, min(RISK_CAP, TARGET_VOL / pvol))
    out = {s: float(base[s] * scale) for s in eligible}
    out[DEFENSIVE] = 1.0 - scale
    return out


def neutral_target(c, i):
    if i < SECTOR_LOOKBACK:
        return {}
    scores = []
    for s in SECTORS:
        now = float(c.iloc[i][s])
        old = float(c.iloc[i - SECTOR_LOOKBACK][s])
        if np.isfinite(now) and np.isfinite(old) and old > 0:
            scores.append((s, now / old - 1.0))
    scores.sort(key=lambda x: x[1], reverse=True)
    if len(scores) < 2 * TOP_N:
        return {}
    longs = [s for s, _ in scores[:TOP_N]]
    shorts = [s for s, _ in scores[-TOP_N:]]
    out = {s: 0.5 / TOP_N for s in longs}
    for s in shorts:
        out[s] = out.get(s, 0.0) - 0.5 / TOP_N
    return out


def combined_target(c):
    i = len(c) - 1
    t = trend_target(c, i)
    n = neutral_target(c, i)
    out = {}
    for s, w in t.items():
        out[s] = out.get(s, 0.0) + TREND_WEIGHT * w
    for s, w in n.items():
        out[s] = out.get(s, 0.0) + NEUTRAL_WEIGHT * w
    return {s: float(w) for s, w in out.items() if abs(w) > 1e-9}, t, n


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_rebalance_month": None, "rebalance_count": 0}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def append_log(event):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def current_positions():
    rows = api("GET", "/v2/positions") or []
    return {r["symbol"]: float(r["qty"]) for r in rows}


def open_orders():
    q = urllib.parse.urlencode({"status": "open", "limit": 500, "direction": "desc"})
    return api("GET", "/v2/orders?" + q) or []


def asset(sym):
    return api("GET", "/v2/assets/" + urllib.parse.quote(sym))


def submit_market(sym, qty, side, tag):
    body = {
        "symbol": sym,
        "qty": str(abs(qty)),
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": tag[:48],
    }
    return api("POST", "/v2/orders", body)


def close_position(sym):
    return api("DELETE", "/v2/positions/" + urllib.parse.quote(sym) + "?percentage=100")


def wait_order(order_id, seconds=35):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        last = api("GET", "/v2/orders/" + order_id)
        if last.get("status") in {"filled", "canceled", "expired", "rejected", "suspended"}:
            return last
        time.sleep(1.5)
    return last


def desired_quantities(weights, equity, prices, metadata):
    desired = {}
    tracking = {}
    for sym, w in weights.items():
        px = float(prices[sym])
        dollars = equity * w
        a = metadata[sym]
        if w >= 0:
            if a.get("fractionable", False):
                qty = round(dollars / px, 6)
            else:
                qty = math.floor(dollars / px)
        else:
            # Alpaca does not permit fractional opening short sales. Use whole shares.
            qty = -math.floor(abs(dollars) / px)
        desired[sym] = float(qty)
        actual = qty * px
        tracking[sym] = {"weight": w, "target_dollars": dollars, "reference_price": px,
                         "desired_qty": qty, "represented_dollars": actual}
    return desired, tracking


def preflight(weights, equity, metadata, desired):
    problems = []
    if equity < 2000:
        problems.append("paper equity below $2,000; strategy requires short-sale capability")
    for sym, w in weights.items():
        a = metadata[sym]
        if not a.get("tradable", False):
            problems.append(f"{sym} is not tradable")
        if w < 0:
            if not a.get("shortable", False):
                problems.append(f"{sym} is not shortable")
            if not a.get("easy_to_borrow", False):
                problems.append(f"{sym} is not easy-to-borrow")
            if desired.get(sym, 0) == 0:
                problems.append(f"{sym} short target is too small for one whole share")
    return problems


def execute_rebalance(desired, positions, now_tag):
    actions = []
    all_syms = sorted(set(positions) | set(desired))
    for sym in all_syms:
        if sym not in SYMS:
            continue
        cur = float(positions.get(sym, 0.0))
        tgt = float(desired.get(sym, 0.0))
        if abs(cur - tgt) < 1e-5:
            continue

        # Sign flips are two-stage to avoid accidental fractional short behavior.
        if cur * tgt < 0:
            close = close_position(sym)
            final = wait_order(close["id"])
            actions.append({"symbol": sym, "action": "close_for_flip", "order_id": close["id"], "status": final.get("status")})
            if final.get("status") != "filled":
                raise RuntimeError(f"Could not close {sym} before sign flip: {final.get('status')}")
            cur = 0.0

        if abs(tgt) < 1e-9:
            if abs(cur) > 1e-9:
                close = close_position(sym)
                final = wait_order(close["id"])
                actions.append({"symbol": sym, "action": "close", "order_id": close["id"], "status": final.get("status")})
                if final.get("status") != "filled":
                    raise RuntimeError(f"Could not close {sym}: {final.get('status')}")
            continue

        delta = tgt - cur
        if abs(delta) < 1e-5:
            continue
        # Shorts must remain whole-share. Long/fractional deltas may be decimal.
        if tgt < 0 or cur < 0:
            delta = int(round(delta))
            if delta == 0:
                continue
        side = "buy" if delta > 0 else "sell"
        tag = f"mp6b-{now_tag}-{sym}-{side}"
        order = submit_market(sym, abs(delta), side, tag)
        final = wait_order(order["id"])
        actions.append({"symbol": sym, "action": side, "qty": abs(delta), "order_id": order["id"], "status": final.get("status")})
        if final.get("status") != "filled":
            raise RuntimeError(f"Order did not fill for {sym}: {final.get('status')}")
    return actions


def write_status(status):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)
    lines = [
        "# MarketPulse Phase 6B — Paper Trading Status",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Timestamp UTC: {status.get('timestamp_utc')}",
        f"- Paper endpoint only: **{PAPER_BASE}**",
    ]
    if "account_equity" in status:
        lines.append(f"- Paper equity: **${status['account_equity']:,.2f}**")
    if "market_open" in status:
        lines.append(f"- Market open: **{status['market_open']}**")
    if "signal_date" in status:
        lines.append(f"- Signal date: **{status['signal_date']}**")
    if status.get("problems"):
        lines += ["", "## Blockers"] + [f"- {x}" for x in status["problems"]]
    if status.get("target_weights"):
        lines += ["", "## Target weights"]
        for s, w in sorted(status["target_weights"].items(), key=lambda x: -x[1]):
            lines.append(f"- {s}: {w*100:+.2f}%")
    if status.get("actions"):
        lines += ["", "## Orders"]
        for a in status["actions"]:
            lines.append(f"- {a['symbol']}: {a['action']} {a.get('qty','')} — {a.get('status')}")
    lines += ["", "Paper trading is simulated and does not guarantee live-trading results."]
    with open(STATUS_MD, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    base_status = {"phase": "6B", "timestamp_utc": now.isoformat(), "paper_base": PAPER_BASE}

    # Research gate is a hard prerequisite.
    try:
        with open("phase6a_results.json") as f:
            sixa = json.load(f)
        if sixa.get("gate") != "PASS" or not sixa.get("paper_trading_authorized"):
            raise RuntimeError("Phase 6A PASS authorization is missing")
    except Exception as e:
        status = {**base_status, "status": "BLOCKED_RESEARCH_GATE", "problems": [str(e)]}
        write_status(status); append_log(status); return

    # Paper authentication only. A live key will fail here rather than switching endpoints.
    try:
        account = api("GET", "/v2/account")
        clock = api("GET", "/v2/clock")
    except Exception as e:
        status = {**base_status, "status": "PAPER_AUTH_FAILED", "problems": [str(e)]}
        write_status(status); append_log(status); return

    equity = float(account["equity"])
    status_common = {**base_status, "account_equity": equity, "market_open": bool(clock.get("is_open")),
                     "account_status": account.get("status"), "trading_blocked": bool(account.get("trading_blocked"))}
    if account.get("trading_blocked") or account.get("status") != "ACTIVE":
        status = {**status_common, "status": "PAPER_ACCOUNT_BLOCKED", "problems": ["Paper account is not active/tradable"]}
        write_status(status); append_log(status); return

    try:
        o, c = load_panel()
        weights, trend, neutral = combined_target(c)
        signal_date = str(c.index[-1])
        prices = c.iloc[-1]
        metadata = {s: asset(s) for s in weights}
        desired, tracking = desired_quantities(weights, equity, prices, metadata)
        problems = preflight(weights, equity, metadata, desired)
    except Exception as e:
        status = {**status_common, "status": "TARGET_BUILD_FAILED", "problems": [str(e)]}
        write_status(status); append_log(status); return

    current_month = pd.Timestamp(clock.get("timestamp", now.isoformat())).strftime("%Y-%m")
    state = load_state()
    status_common.update({"signal_date": signal_date, "target_weights": weights, "trend_sleeve": trend,
                          "neutral_sleeve": neutral, "desired_quantities": desired, "tracking": tracking,
                          "last_rebalance_month": state.get("last_rebalance_month")})

    if problems:
        status = {**status_common, "status": "PREFLIGHT_BLOCKED", "problems": problems}
        write_status(status); append_log(status); return

    try:
        pending = [x for x in open_orders() if str(x.get("client_order_id", "")).startswith("mp6b-")]
    except Exception as e:
        status = {**status_common, "status": "OPEN_ORDER_CHECK_FAILED", "problems": [str(e)]}
        write_status(status); append_log(status); return
    if pending:
        status = {**status_common, "status": "PENDING_MARKETPULSE_ORDERS", "pending_orders": pending}
        write_status(status); append_log(status); return

    if state.get("last_rebalance_month") == current_month:
        status = {**status_common, "status": "CURRENT_MONTH_ALREADY_REBALANCED"}
        write_status(status); append_log(status); return

    if not clock.get("is_open"):
        status = {**status_common, "status": "READY_WAITING_FOR_MARKET_OPEN"}
        write_status(status); append_log(status); return

    try:
        positions = current_positions()
        actions = execute_rebalance(desired, positions, now.strftime("%Y%m%d%H%M"))
        final_positions = current_positions()
        state["last_rebalance_month"] = current_month
        state["last_rebalance_utc"] = now.isoformat()
        state["rebalance_count"] = int(state.get("rebalance_count", 0)) + 1
        state["last_signal_date"] = signal_date
        save_state(state)
        status = {**status_common, "status": "REBALANCE_COMPLETE", "actions": actions,
                  "final_positions": final_positions, "rebalance_count": state["rebalance_count"]}
    except Exception as e:
        status = {**status_common, "status": "REBALANCE_ERROR", "problems": [str(e)]}

    write_status(status)
    append_log(status)


if __name__ == "__main__":
    main()
