import json
import os
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_BASE = "https://data.alpaca.markets"
SYMBOLS = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "AMD", "TSLA", "AMZN", "META", "MSFT",
    "GOOGL", "PLTR", "BAC", "INTC", "SOFI", "XLF", "XLE", "GLD", "SLV", "TLT",
]
LOOKBACK_DAYS = 180
TIMEFRAME = "5Min"
ET = ZoneInfo("America/New_York")
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 12.50
MAX_DAILY_TRADES = 3
LATEST_ENTRY = time(12, 30)
FORCE_EXIT = time(15, 45)
TICK = 0.01
RESULT_JSON = "options_pattern10_opening_pullback_results.json"
RESULT_MD = "options_pattern10_opening_pullback_results.md"

# Small, predeclared grid. No post-result tuning inside this run.
VARIANTS = [
    {"name": "drive045_r125", "min_drive_atr": 0.45, "target_r": 1.25, "timeout_bars": 9},
    {"name": "drive045_r150", "min_drive_atr": 0.45, "target_r": 1.50, "timeout_bars": 9},
    {"name": "drive060_r125", "min_drive_atr": 0.60, "target_r": 1.25, "timeout_bars": 9},
    {"name": "drive060_r150", "min_drive_atr": 0.60, "target_r": 1.50, "timeout_bars": 9},
]


def credentials():
    key = os.getenv("ALPACA_OPTIONS_API_KEY_ID")
    secret = os.getenv("ALPACA_OPTIONS_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Missing Alpaca options credentials")
    return key, secret


def get_json(url):
    key, secret = credentials()
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "MarketPulse-P10-opening-pullback/1.0",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_bars(symbol):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    params = {
        "timeframe": TIMEFRAME,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "adjustment": "raw",
        "feed": "iex",
        "limit": 10000,
        "sort": "asc",
    }
    out, token = [], None
    while True:
        q = dict(params)
        if token:
            q["page_token"] = token
        payload = get_json(f"{DATA_BASE}/v2/stocks/{symbol}/bars?{urllib.parse.urlencode(q)}")
        out.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            return out


def parse_sessions(raw):
    by_day = defaultdict(list)
    for x in raw:
        ts = datetime.fromisoformat(x["t"].replace("Z", "+00:00")).astimezone(ET)
        if time(9, 30) <= ts.time() < time(16, 0):
            by_day[ts.date()].append({
                "ts": ts,
                "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]),
                "c": float(x["c"]), "v": float(x.get("v") or 0.0),
            })
    return {d: sorted(v, key=lambda b: b["ts"]) for d, v in by_day.items() if len(v) >= 70}


def ema(values, period):
    alpha = 2.0 / (period + 1.0)
    out, cur = [], None
    for x in values:
        cur = x if cur is None else alpha * x + (1.0 - alpha) * cur
        out.append(cur)
    return out


def add_indicators(bars):
    closes = [b["c"] for b in bars]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    pv = vol = 0.0
    trs = []
    for i, b in enumerate(bars):
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        pv += typical * b["v"]
        vol += b["v"]
        b["vwap"] = pv / vol if vol else b["c"]
        b["ema9"], b["ema21"] = e9[i], e21[i]
        prev = bars[i - 1]["c"] if i else b["c"]
        tr = max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        b["atr"] = sum(trs[max(0, i - 13):i + 1]) / min(14, i + 1)
        vols = [z["v"] for z in bars[max(0, i - 19):i + 1]]
        b["vol_med20"] = statistics.median(vols) if vols else max(1.0, b["v"])


def opening_direction(bars, min_drive_atr):
    if len(bars) < 8:
        return None
    anchor = bars[5]  # 9:55 close; first 30 minutes are now known.
    atr = max(anchor["atr"], 1e-9)
    drive = (anchor["c"] - bars[0]["o"]) / atr
    recent = bars[2:6]
    if drive >= min_drive_atr:
        continuity = sum(1 for x in recent if x["c"] >= x["vwap"]) >= 3
        if continuity and anchor["c"] > anchor["vwap"] and anchor["ema9"] >= anchor["ema21"]:
            return "CALL", drive
    if drive <= -min_drive_atr:
        continuity = sum(1 for x in recent if x["c"] <= x["vwap"]) >= 3
        if continuity and anchor["c"] < anchor["vwap"] and anchor["ema9"] <= anchor["ema21"]:
            return "PUT", drive
    return None


def simulate(bars, entry_i, side, entry, stop, target_r, timeout_bars):
    risk = entry - stop if side == "CALL" else stop - entry
    if risk <= 0:
        return None
    target = entry + target_r * risk if side == "CALL" else entry - target_r * risk
    last_i = min(len(bars) - 1, entry_i + timeout_bars)
    exit_price, exit_i, reason = bars[last_i]["c"], last_i, "TIME"
    for j in range(entry_i, last_i + 1):
        b = bars[j]
        if b["ts"].time() > FORCE_EXIT:
            exit_price, exit_i, reason = b["o"], j, "TIME"
            break
        if side == "CALL":
            stop_hit, target_hit = b["l"] <= stop, b["h"] >= target
        else:
            stop_hit, target_hit = b["h"] >= stop, b["l"] <= target
        # Conservative same-bar resolution: stop wins.
        if stop_hit:
            exit_price, exit_i, reason = stop, j, "STOP"
            break
        if target_hit:
            exit_price, exit_i, reason = target, j, "TARGET"
            break
    r = (exit_price - entry) / risk if side == "CALL" else (entry - exit_price) / risk
    return exit_i, round(r, 4), reason, round(exit_price, 4)


def first_pullback_signal(symbol, day, bars, variant):
    add_indicators(bars)
    od = opening_direction(bars, variant["min_drive_atr"])
    if not od:
        return None
    side, drive = od

    # First eligible pullback only; begin after 10:00 and stop at 12:30.
    for i in range(6, len(bars) - 1):
        b, nxt = bars[i], bars[i + 1]
        if b["ts"].time() > LATEST_ENTRY or nxt["ts"].time() > LATEST_ENTRY:
            break
        atr = max(b["atr"], 1e-9)
        if side == "CALL":
            touched = b["l"] <= b["ema9"] + 0.10 * atr
            held = b["c"] >= b["vwap"] and b["c"] >= b["ema21"]
            pullback = b["c"] <= bars[i - 1]["c"] or b["c"] < b["o"]
            trigger = nxt["h"] >= b["h"] + TICK and nxt["c"] >= nxt["ema9"]
            if not (touched and held and pullback and trigger):
                continue
            entry = max(b["h"] + TICK, nxt["o"])
            stop = b["l"] - TICK - 0.05 * atr
            risk = entry - stop
        else:
            touched = b["h"] >= b["ema9"] - 0.10 * atr
            held = b["c"] <= b["vwap"] and b["c"] <= b["ema21"]
            pullback = b["c"] >= bars[i - 1]["c"] or b["c"] > b["o"]
            trigger = nxt["l"] <= b["l"] - TICK and nxt["c"] <= nxt["ema9"]
            if not (touched and held and pullback and trigger):
                continue
            entry = min(b["l"] - TICK, nxt["o"])
            stop = b["h"] + TICK + 0.05 * atr
            risk = stop - entry
        if risk <= 0 or risk > 1.25 * atr:
            continue
        sim = simulate(bars, i + 1, side, entry, stop, variant["target_r"], variant["timeout_bars"])
        if not sim:
            continue
        exit_i, r, reason, exit_price = sim
        vol_ratio = nxt["v"] / max(nxt["vol_med20"], 1.0)
        ema_sep = abs(nxt["ema9"] - nxt["ema21"]) / max(nxt["atr"], 1e-9)
        quality = abs(drive) + 0.50 * ema_sep + 0.20 * min(vol_ratio, 3.0)
        return {
            "symbol": symbol, "date": str(day), "side": side,
            "entry_ts": nxt["ts"].isoformat(), "exit_ts": bars[exit_i]["ts"].isoformat(),
            "entry": round(entry, 4), "stop": round(stop, 4), "exit": exit_price,
            "exit_reason": reason, "r": r, "pl_dollars": round(r * RISK_DOLLARS, 2),
            "opening_drive_atr": round(drive, 4), "quality": round(quality, 4),
            "underlying_entry_spot": round(nxt["c"], 4),
        }
    return None


def generate_signals(variant, sessions_by_symbol):
    raw = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            sig = first_pullback_signal(symbol, day, bars, variant)
            if sig:
                raw.append(sig)
    return sorted(raw, key=lambda x: (x["entry_ts"], -x["quality"]))


def causal_account_stream(signals):
    # One open position at a time, max 3 entries/day. At the same timestamp take highest quality.
    grouped = defaultdict(list)
    for s in signals:
        grouped[s["entry_ts"]].append(s)
    out, busy_until = [], None
    day_counts = defaultdict(int)
    for entry_ts in sorted(grouped):
        candidates = sorted(grouped[entry_ts], key=lambda x: (-x["quality"], x["symbol"]))
        et = datetime.fromisoformat(entry_ts)
        if busy_until and et <= busy_until:
            continue
        chosen = next((x for x in candidates if day_counts[x["date"]] < MAX_DAILY_TRADES), None)
        if not chosen:
            continue
        out.append(chosen)
        day_counts[chosen["date"]] += 1
        busy_until = datetime.fromisoformat(chosen["exit_ts"])
    return out


def summarize(trades, days):
    pls = [float(t["pl_dollars"]) for t in trades]
    wins = [x for x in pls if x > 0]
    losses = [x for x in pls if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    equity, peak, dd = STARTING_BALANCE, STARTING_BALANCE, 0.0
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        equity += t["pl_dollars"]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    n_days = max(1, len(days))
    net = sum(pls)
    return {
        "trades": len(trades),
        "trading_days": len(days),
        "trades_per_day": round(len(trades) / n_days, 2),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(pls) * 100.0, 2) if pls else 0.0,
        "net_pl_dollars": round(net, 2),
        "avg_daily_pl_dollars": round(net / n_days, 2),
        "ending_balance_dollars": round(STARTING_BALANCE + net, 2),
        "return_pct": round(net / STARTING_BALANCE * 100.0, 2),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "expectancy_dollars": round(net / len(pls), 2) if pls else 0.0,
        "max_drawdown_dollars": round(dd, 2),
    }


def by_date(trades, allowed):
    allowed = set(allowed)
    return [t for t in trades if datetime.fromisoformat(t["date"]).date() in allowed]


def main():
    sessions_by_symbol = {}
    for symbol in SYMBOLS:
        print(f"fetch {symbol}")
        sessions_by_symbol[symbol] = parse_sessions(fetch_bars(symbol))

    reference_days = sorted(sessions_by_symbol.get("SPY", {}).keys())
    if len(reference_days) < 60:
        raise RuntimeError("Not enough complete sessions")
    cut = int(len(reference_days) * 0.70)
    dev_days, holdout_days = reference_days[:cut], reference_days[cut:]
    mid = len(dev_days) // 2
    dev_a, dev_b = dev_days[:mid], dev_days[mid:]

    results = {}
    robust = []
    all_streams = {}
    for v in VARIANTS:
        signals = generate_signals(v, sessions_by_symbol)
        stream = causal_account_stream(signals)
        all_streams[v["name"]] = stream
        a = summarize(by_date(stream, dev_a), dev_a)
        b = summarize(by_date(stream, dev_b), dev_b)
        dev = summarize(by_date(stream, dev_days), dev_days)
        holdout = summarize(by_date(stream, holdout_days), holdout_days)
        full = summarize(stream, reference_days)
        item = {"variant": v, "development_fold_a": a, "development_fold_b": b, "development": dev, "holdout": holdout, "full": full}
        results[v["name"]] = item
        if (
            a["trades"] >= 20 and b["trades"] >= 20
            and a["net_pl_dollars"] > 0 and b["net_pl_dollars"] > 0
            and (a["profit_factor"] or 0) > 1.05 and (b["profit_factor"] or 0) > 1.05
        ):
            robust.append(item)

    selected = None
    if robust:
        selected = max(robust, key=lambda x: (
            min(x["development_fold_a"]["profit_factor"] or 0, x["development_fold_b"]["profit_factor"] or 0),
            x["development"]["net_pl_dollars"],
            -x["development"]["max_drawdown_dollars"],
        ))

    promoted = False
    if selected:
        h = selected["holdout"]
        promoted = (
            h["trades"] >= 15 and h["net_pl_dollars"] > 0
            and (h["profit_factor"] or 0) >= 1.20
            and 0.75 <= h["trades_per_day"] <= 3.0
        )

    result = {
        "strategy": "pattern10_opening_drive_first_pullback",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "lookback_days": LOOKBACK_DAYS,
        "symbols": SYMBOLS,
        "reference_sessions": len(reference_days),
        "holdout_start": holdout_days[0].isoformat(),
        "max_daily_trades": MAX_DAILY_TRADES,
        "one_position_at_a_time": True,
        "variants_predeclared": VARIANTS,
        "results": results,
        "selected_development_candidate": selected,
        "advance_to_actual_options": promoted,
        "selected_trade_stream": all_streams[selected["variant"]["name"]] if selected else [],
    }
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# MarketPulse — Pattern #10 Opening-Drive First Pullback",
        "",
        "**Research only. No orders. Next-bar logic; one position at a time.**",
        "",
        f"Sessions: **{len(reference_days)}** | Holdout starts: **{holdout_days[0]}** | Max entries/day: **{MAX_DAILY_TRADES}**",
        f"Selected on development only: **{selected['variant']['name'] if selected else 'NONE'}**",
        "",
    ]
    for v in VARIANTS:
        r = results[v["name"]]
        f, h = r["full"], r["holdout"]
        lines += [
            f"## {v['name']}",
            f"- Full: **{f['trades']} trades | {f['trades_per_day']}/day | ${f['net_pl_dollars']:.2f} | PF {f['profit_factor']} | DD ${f['max_drawdown_dollars']:.2f}**",
            f"- Holdout: **{h['trades']} trades | {h['trades_per_day']}/day | ${h['net_pl_dollars']:.2f} | PF {h['profit_factor']}**",
            "",
        ]
    lines += [
        f"**Advance to actual option-contract simulation: {'YES' if promoted else 'NO'}**",
        "",
        "Underlying P/L is risk-normalized at $12.50 per 1R and is not option-premium P/L.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
