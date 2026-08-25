import json
import math
import os
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_BASE = "https://data.alpaca.markets"
SYMBOLS = ["SPY", "QQQ", "IWM"]
LOOKBACK_DAYS = 120
TIMEFRAME = "1Min"
ET = ZoneInfo("America/New_York")
START = time(9, 45)
LATEST_ENTRY = time(15, 30)
FORCE_EXIT = time(15, 50)
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 12.50
MAX_DAILY_TRADES = 15
RESULT_JSON = "options_pattern3_fast_results.json"
RESULT_MD = "options_pattern3_fast_results.md"

# Predeclared, fixed variants. No post-result tuning in this run.
VARIANTS = [
    {"name": "trend_pullback_075", "family": "trend_pullback", "target_r": 0.75, "timeout": 12},
    {"name": "trend_pullback_100", "family": "trend_pullback", "target_r": 1.00, "timeout": 15},
    {"name": "vwap_snapback_060", "family": "vwap_snapback", "target_r": 0.60, "timeout": 10},
    {"name": "vwap_snapback_080", "family": "vwap_snapback", "target_r": 0.80, "timeout": 14},
    {"name": "momentum_burst_075", "family": "momentum_burst", "target_r": 0.75, "timeout": 10},
    {"name": "momentum_burst_100", "family": "momentum_burst", "target_r": 1.00, "timeout": 14},
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
        "User-Agent": "MarketPulse-P3-fast-research/1.0",
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
        url = f"{DATA_BASE}/v2/stocks/{symbol}/bars?{urllib.parse.urlencode(q)}"
        payload = get_json(url)
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
                "ts": ts, "o": float(x["o"]), "h": float(x["h"]),
                "l": float(x["l"]), "c": float(x["c"]), "v": float(x.get("v") or 0.0),
            })
    return {d: sorted(v, key=lambda b: b["ts"]) for d, v in by_day.items() if len(v) >= 300}


def ema(values, period):
    a = 2.0 / (period + 1.0)
    out, cur = [], None
    for x in values:
        cur = x if cur is None else a * x + (1 - a) * cur
        out.append(cur)
    return out


def add_indicators(bars):
    closes = [b["c"] for b in bars]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    pv = vol = 0.0
    trs = []
    gains, losses = [], []
    for i, b in enumerate(bars):
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        pv += typical * b["v"]
        vol += b["v"]
        b["vwap"] = pv / vol if vol else b["c"]
        b["ema9"], b["ema21"] = e9[i], e21[i]
        prev = bars[i - 1]["c"] if i else b["c"]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev)))
        ch = b["c"] - prev
        gains.append(max(ch, 0.0)); losses.append(max(-ch, 0.0))
        b["atr"] = sum(trs[max(0, i - 13):i + 1]) / min(14, i + 1)
        if i < 7:
            b["rsi"] = 50.0
        else:
            ag = sum(gains[i - 6:i + 1]) / 7.0
            al = sum(losses[i - 6:i + 1]) / 7.0
            b["rsi"] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
        vols = [z["v"] for z in bars[max(0, i - 19):i + 1]]
        b["vol_med20"] = statistics.median(vols) if vols else b["v"]


def simulate(bars, i, side, entry, stop, target_r, timeout):
    risk = entry - stop if side == "CALL" else stop - entry
    if risk <= 0:
        return None
    target = entry + target_r * risk if side == "CALL" else entry - target_r * risk
    last_i = min(len(bars) - 1, i + timeout)
    exit_price = bars[last_i]["c"]
    reason = "TIME"
    exit_i = last_i
    for j in range(i, last_i + 1):
        b = bars[j]
        if b["ts"].time() > FORCE_EXIT:
            exit_price, exit_i, reason = b["o"], j, "TIME"
            break
        if side == "CALL":
            stop_hit, target_hit = b["l"] <= stop, b["h"] >= target
        else:
            stop_hit, target_hit = b["h"] >= stop, b["l"] <= target
        if stop_hit:
            exit_price, exit_i, reason = stop, j, "STOP"; break
        if target_hit:
            exit_price, exit_i, reason = target, j, "TARGET"; break
    r = (exit_price - entry) / risk if side == "CALL" else (entry - exit_price) / risk
    return exit_i, round(r, 4), reason, round(exit_price, 4)


def signal_trend_pullback(bars, i, side):
    if i < 25:
        return None
    b, p = bars[i], bars[i - 1]
    atr = b["atr"]
    if atr <= 0:
        return None
    if side == "CALL":
        valid = b["ema9"] > b["ema21"] and b["c"] > b["vwap"] and p["l"] <= p["ema9"] and p["c"] >= p["ema21"] and b["c"] > b["o"] and b["h"] > p["h"] and 48 <= b["rsi"] <= 72
        if not valid: return None
        entry = max(p["h"] + 0.01, b["o"])
        stop = min(p["l"] - 0.01, entry - 0.35 * atr)
    else:
        valid = b["ema9"] < b["ema21"] and b["c"] < b["vwap"] and p["h"] >= p["ema9"] and p["c"] <= p["ema21"] and b["c"] < b["o"] and b["l"] < p["l"] and 28 <= b["rsi"] <= 52
        if not valid: return None
        entry = min(p["l"] - 0.01, b["o"])
        stop = max(p["h"] + 0.01, entry + 0.35 * atr)
    return entry, stop


def signal_vwap_snapback(bars, i, side):
    if i < 20:
        return None
    b, p = bars[i], bars[i - 1]
    atr = b["atr"]
    if atr <= 0:
        return None
    if side == "CALL":
        stretched = (p["vwap"] - p["l"]) >= 0.65 * atr
        valid = stretched and p["rsi"] <= 32 and b["c"] > b["o"] and b["c"] > (b["l"] + 0.65 * (b["h"] - b["l"])) and b["rsi"] > p["rsi"]
        if not valid: return None
        entry = b["h"] + 0.01; stop = min(p["l"], b["l"]) - 0.01
    else:
        stretched = (p["h"] - p["vwap"]) >= 0.65 * atr
        valid = stretched and p["rsi"] >= 68 and b["c"] < b["o"] and b["c"] < (b["l"] + 0.35 * (b["h"] - b["l"])) and b["rsi"] < p["rsi"]
        if not valid: return None
        entry = b["l"] - 0.01; stop = max(p["h"], b["h"]) + 0.01
    if abs(entry - stop) > 1.2 * atr:
        return None
    return entry, stop


def signal_momentum_burst(bars, i, side):
    if i < 25:
        return None
    b = bars[i]; atr = b["atr"]
    if atr <= 0:
        return None
    recent = bars[i - 5:i]
    rh, rl = max(x["h"] for x in recent), min(x["l"] for x in recent)
    body = abs(b["c"] - b["o"])
    if side == "CALL":
        valid = b["c"] > rh and b["c"] > b["vwap"] and b["ema9"] >= b["ema21"] and body >= 0.28 * atr and b["v"] >= 1.15 * b["vol_med20"] and b["rsi"] <= 78
        if not valid: return None
        entry = max(rh + 0.01, b["o"]); stop = max(rl, entry - 0.55 * atr)
    else:
        valid = b["c"] < rl and b["c"] < b["vwap"] and b["ema9"] <= b["ema21"] and body >= 0.28 * atr and b["v"] >= 1.15 * b["vol_med20"] and b["rsi"] >= 22
        if not valid: return None
        entry = min(rl - 0.01, b["o"]); stop = min(rh, entry + 0.55 * atr)
    return entry, stop


def family_signal(family, bars, i, side):
    if family == "trend_pullback": return signal_trend_pullback(bars, i, side)
    if family == "vwap_snapback": return signal_vwap_snapback(bars, i, side)
    return signal_momentum_burst(bars, i, side)


def generate_variant(variant, sessions_by_symbol):
    trades = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            add_indicators(bars)
            next_ok = 0
            day_count = 0
            for i in range(25, len(bars)):
                if i < next_ok or day_count >= MAX_DAILY_TRADES:
                    continue
                t = bars[i]["ts"].time()
                if t < START or t > LATEST_ENTRY:
                    continue
                candidates = []
                for side in ("CALL", "PUT"):
                    sig = family_signal(variant["family"], bars, i, side)
                    if sig:
                        candidates.append((side, sig[0], sig[1]))
                if not candidates:
                    continue
                side, entry, stop = candidates[0]
                sim = simulate(bars, i, side, entry, stop, variant["target_r"], variant["timeout"])
                if not sim:
                    continue
                exit_i, r, reason, exit_price = sim
                trades.append({
                    "symbol": symbol, "date": str(day), "side": side,
                    "entry_ts": bars[i]["ts"].isoformat(), "exit_ts": bars[exit_i]["ts"].isoformat(),
                    "entry": round(entry, 4), "stop": round(stop, 4), "exit": exit_price,
                    "exit_reason": reason, "r": r, "pl_dollars": round(r * RISK_DOLLARS, 2),
                })
                day_count += 1
                next_ok = exit_i + 1
    return sorted(trades, key=lambda x: x["entry_ts"])


def one_account_stream(trades):
    # Merge all ETF signals into one $2,500 account: only one position at a time, <=15 entries/day.
    out, busy_until = [], None
    counts = defaultdict(int)
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        d = t["date"]
        if counts[d] >= MAX_DAILY_TRADES:
            continue
        et = datetime.fromisoformat(t["entry_ts"])
        xt = datetime.fromisoformat(t["exit_ts"])
        if busy_until and et <= busy_until:
            continue
        out.append(t); counts[d] += 1; busy_until = xt
    return out


def summarize(trades, all_days):
    pls = [t["pl_dollars"] for t in trades]
    wins = [p for p in pls if p > 0]; losses = [p for p in pls if p <= 0]
    net = sum(pls); gp = sum(wins); gl = -sum(losses)
    eq = STARTING_BALANCE; peak = eq; dd = 0.0
    by_day = defaultdict(float); nday = defaultdict(int)
    for t in trades:
        eq += t["pl_dollars"]; peak = max(peak, eq); dd = max(dd, peak - eq)
        by_day[t["date"]] += t["pl_dollars"]; nday[t["date"]] += 1
    active_days = len(all_days)
    daily_vals = [by_day[str(d)] for d in sorted(all_days)]
    counts = [nday[str(d)] for d in sorted(all_days)]
    return {
        "trades": len(trades), "trading_days": active_days,
        "trades_per_day": round(len(trades) / active_days, 2) if active_days else 0.0,
        "median_trades_per_day": round(statistics.median(counts), 2) if counts else 0.0,
        "days_10plus_trades_pct": round(sum(1 for x in counts if x >= 10) / active_days * 100, 2) if active_days else 0.0,
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(pls) * 100, 2) if pls else 0.0,
        "net_pl_dollars": round(net, 2), "ending_balance_dollars": round(STARTING_BALANCE + net, 2),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "expectancy_dollars": round(net / len(pls), 2) if pls else 0.0,
        "avg_daily_pl_dollars": round(sum(daily_vals) / active_days, 2) if active_days else 0.0,
        "positive_day_pct": round(sum(1 for x in daily_vals if x > 0) / active_days * 100, 2) if active_days else 0.0,
        "max_drawdown_dollars": round(dd, 2),
    }


def split_dates(days):
    ds = sorted(days); n = len(ds); a = n // 4
    return [set(ds[:a]), set(ds[a:2*a]), set(ds[2*a:3*a]), set(ds[3*a:])]


def main():
    sessions = {}
    common_days = None
    for sym in SYMBOLS:
        print("fetching", sym)
        sessions[sym] = parse_sessions(fetch_bars(sym))
        d = set(sessions[sym])
        common_days = d if common_days is None else common_days & d
    common_days = sorted(common_days or [])
    if len(common_days) < 40:
        raise RuntimeError(f"Insufficient common sessions: {len(common_days)}")
    folds = split_dates(common_days)
    results = {}
    eligible = []
    for v in VARIANTS:
        print("testing", v["name"])
        raw = generate_variant(v, sessions)
        stream = one_account_stream(raw)
        full = summarize(stream, common_days)
        fold_summaries = []
        for f in folds:
            ft = [t for t in stream if datetime.fromisoformat(t["date"]).date() in f]
            fold_summaries.append(summarize(ft, f))
        dev = fold_summaries[:3]; hold = fold_summaries[3]
        prod_ok = full["trades_per_day"] >= 8.0 and full["days_10plus_trades_pct"] >= 30.0
        robust_dev = all(x["net_pl_dollars"] > 0 and (x["profit_factor"] or 0) > 1.0 for x in dev)
        results[v["name"]] = {"variant": v, "full": full, "folds": fold_summaries, "production_screen_pass": prod_ok, "development_robust": robust_dev}
        if prod_ok and robust_dev:
            eligible.append(v["name"])
    selected = None
    if eligible:
        selected = max(eligible, key=lambda n: (min(results[n]["folds"][i]["avg_daily_pl_dollars"] for i in range(3)), results[n]["full"]["avg_daily_pl_dollars"]))
    result = {
        "strategy": "options_pattern3_fast_signal_screen", "order_submission_enabled": False,
        "lookback_days": LOOKBACK_DAYS, "symbols": SYMBOLS, "common_sessions": len(common_days),
        "starting_balance": STARTING_BALANCE, "risk_dollars_per_trade": RISK_DOLLARS,
        "production_target_trades_per_day": "10-15", "daily_profit_target_dollars": "100-150 (research target; not guaranteed)",
        "variants_predeclared": VARIANTS, "results": results, "selected_development_candidate": selected,
        "selection_note": "Selection uses only first three chronological folds. Fourth fold is reported as holdout and is not used to pick a variant.",
    }
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# MarketPulse — Pattern #3 Fast Production Screen", "", "**Research only. No orders.**", "", f"Common sessions: **{len(common_days)}**", f"Selected development candidate: **{selected or 'NONE'}**", ""]
    for n, x in results.items():
        s = x["full"]; h = x["folds"][3]
        lines += [f"## {n}", f"- Trades/day: **{s['trades_per_day']}** | 10+ trade days: **{s['days_10plus_trades_pct']}%**", f"- Full P/L (risk-normalized): **${s['net_pl_dollars']:,.2f}** | Avg/day: **${s['avg_daily_pl_dollars']:,.2f}** | PF: **{s['profit_factor']}**", f"- Holdout: **${h['net_pl_dollars']:,.2f}** | trades/day **{h['trades_per_day']}** | PF **{h['profit_factor']}**", ""]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
