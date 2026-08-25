import json
from collections import Counter, defaultdict
from datetime import datetime, time, timezone, timedelta
from pathlib import Path

import options_pattern3_fast_research as p3

# Pattern #9 — Gap-Aligned Opening Drive across a broad liquid-options universe.
# Breadth supplies production; each name is allowed at most one trade per day.
p3.LOOKBACK_DAYS = 180
p3.SYMBOLS = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "AMD", "TSLA", "AMZN", "META", "MSFT",
    "GOOGL", "PLTR", "NFLX", "AVGO", "MU", "COIN", "HOOD", "UBER", "BAC", "JPM",
    "WFC", "C", "INTC", "SOFI", "RIVN", "SMH", "XLF", "XLE", "XBI", "GLD", "SLV", "TLT",
]
p3.RESULT_JSON = "options_pattern9_gap_orb_results.json"
p3.RESULT_MD = "options_pattern9_gap_orb_results.md"
MIN_SYMBOLS_PER_EVAL_DAY = 20
MAX_PER_SYMBOL_DAY = 1
MAX_DAILY_TRADES = 15
MAX_NEXT_BAR_CHASE_ATR = 0.25

# Predeclared before seeing results.
p3.VARIANTS = [
    {"name": "gap025_r100", "min_gap": 0.0025, "target_r": 1.00, "timeout": 6, "min_vol": 0.90},
    {"name": "gap025_r125", "min_gap": 0.0025, "target_r": 1.25, "timeout": 8, "min_vol": 0.90},
    {"name": "gap025_r150", "min_gap": 0.0025, "target_r": 1.50, "timeout": 10, "min_vol": 0.90},
    {"name": "gap050_r100", "min_gap": 0.0050, "target_r": 1.00, "timeout": 6, "min_vol": 0.85},
    {"name": "gap050_r125", "min_gap": 0.0050, "target_r": 1.25, "timeout": 8, "min_vol": 0.85},
    {"name": "gap050_r150", "min_gap": 0.0050, "target_r": 1.50, "timeout": 10, "min_vol": 0.85},
]


def aggregate_5m(raw):
    buckets = defaultdict(list)
    for b in raw:
        ts = b["ts"]
        key = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
        buckets[key].append(b)
    out = []
    for key in sorted(buckets):
        g = sorted(buckets[key], key=lambda x: x["ts"])
        if len(g) < 3:
            continue
        out.append({
            "ts": key, "o": g[0]["o"], "h": max(x["h"] for x in g),
            "l": min(x["l"] for x in g), "c": g[-1]["c"], "v": sum(x["v"] for x in g),
        })
    return out


def opening_range(raw):
    bars = [b for b in raw if time(9, 30) <= b["ts"].time() < time(9, 45)]
    if len(bars) < 8:
        return None
    return max(b["h"] for b in bars), min(b["l"] for b in bars), bars[0]["o"], bars[-1]["c"]


def generate_variant(v, sessions):
    trades = []
    for symbol, ds in sessions.items():
        dates = sorted(ds)
        for di in range(1, len(dates)):
            day = dates[di]
            prev_day = dates[di - 1]
            raw = ds[day]
            prev = ds[prev_day]
            if (day - prev_day).days > 4:
                continue
            rng = opening_range(raw)
            if not rng or not prev:
                continue
            or_high, or_low, open_px, or_close = rng
            prev_close = prev[-1]["c"]
            if prev_close <= 0:
                continue
            gap = open_px / prev_close - 1.0
            if abs(gap) < v["min_gap"]:
                continue
            gap_side = "CALL" if gap > 0 else "PUT"
            # Require the first 15 minutes to agree with the overnight direction.
            if gap_side == "CALL" and or_close <= open_px:
                continue
            if gap_side == "PUT" and or_close >= open_px:
                continue
            bars = aggregate_5m(raw)
            if len(bars) < 50:
                continue
            p3.add_indicators(bars)
            for i in range(4, len(bars) - 1):
                b, p = bars[i], bars[i - 1]
                if not (time(9, 45) <= b["ts"].time() <= time(12, 0)):
                    continue
                atr = b["atr"]
                if atr <= 0:
                    continue
                vr = b["v"] / b["vol_med20"] if b["vol_med20"] else 0.0
                body = abs(b["c"] - b["o"])
                if gap_side == "CALL":
                    valid = (
                        p["c"] <= or_high and b["c"] > or_high and b["c"] > b["vwap"]
                        and b["ema9"] >= b["ema21"] and b["c"] > b["o"]
                        and body >= 0.15 * atr and vr >= v["min_vol"] and b["rsi"] >= 54
                    )
                    if not valid:
                        continue
                    stop = max(or_high - 0.35 * atr, b["l"] - 0.01)
                else:
                    valid = (
                        p["c"] >= or_low and b["c"] < or_low and b["c"] < b["vwap"]
                        and b["ema9"] <= b["ema21"] and b["c"] < b["o"]
                        and body >= 0.15 * atr and vr >= v["min_vol"] and b["rsi"] <= 46
                    )
                    if not valid:
                        continue
                    stop = min(or_low + 0.35 * atr, b["h"] + 0.01)
                entry_i = i + 1
                if bars[entry_i]["ts"] != b["ts"] + timedelta(minutes=5):
                    continue
                entry = bars[entry_i]["o"]
                if abs(entry - b["c"]) > MAX_NEXT_BAR_CHASE_ATR * atr:
                    continue
                risk = entry - stop if gap_side == "CALL" else stop - entry
                if risk <= 0 or risk > 1.10 * atr:
                    continue
                sim = p3.simulate(bars, entry_i, gap_side, entry, stop, v["target_r"], v["timeout"])
                if not sim:
                    continue
                exit_i, r, reason, exit_px = sim
                trades.append({
                    "symbol": symbol, "date": str(day), "side": gap_side, "gap_pct": round(gap * 100, 3),
                    "signal_ts": b["ts"].isoformat(), "entry_ts": bars[entry_i]["ts"].isoformat(),
                    "exit_ts": bars[exit_i]["ts"].isoformat(), "entry": round(entry, 4),
                    "stop": round(stop, 4), "exit": exit_px, "exit_reason": reason,
                    "r": r, "pl_dollars": round(r * p3.RISK_DOLLARS, 2),
                })
                break
    return sorted(trades, key=lambda x: x["entry_ts"])


def capped_account_stream(trades):
    out = []
    counts = defaultdict(int)
    busy_until = None
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        if counts[t["date"]] >= MAX_DAILY_TRADES:
            continue
        et = datetime.fromisoformat(t["entry_ts"])
        xt = datetime.fromisoformat(t["exit_ts"])
        if busy_until is not None and et <= busy_until:
            continue
        out.append(t)
        counts[t["date"]] += 1
        busy_until = xt
    return out


def main():
    sessions, coverage = {}, Counter()
    for symbol in p3.SYMBOLS:
        print("fetching", symbol)
        sessions[symbol] = p3.parse_sessions(p3.fetch_bars(symbol))
        for d in sessions[symbol]:
            coverage[d] += 1
    eval_days = sorted(d for d, n in coverage.items() if n >= MIN_SYMBOLS_PER_EVAL_DAY)
    if len(eval_days) < 50:
        raise RuntimeError(f"Insufficient eval sessions: {len(eval_days)}")
    es = set(eval_days)
    # Keep one prior session for gap calculation even if that prior day is below coverage threshold.
    folds = p3.split_dates(eval_days)
    results, eligible = {}, []
    for v in p3.VARIANTS:
        print("testing", v["name"])
        raw_trades = generate_variant(v, sessions)
        stream = capped_account_stream([t for t in raw_trades if datetime.fromisoformat(t["date"]).date() in es])
        full = p3.summarize(stream, eval_days)
        fs = []
        for f in folds:
            ft = [t for t in stream if datetime.fromisoformat(t["date"]).date() in f]
            fs.append(p3.summarize(ft, f))
        robust = all(x["net_pl_dollars"] > 0 and (x["profit_factor"] or 0) > 1.0 for x in fs[:3])
        production = full["trades_per_day"] >= 8.0 and full["days_10plus_trades_pct"] >= 30.0
        results[v["name"]] = {"variant": v, "full": full, "folds": fs, "production_screen_pass": production, "development_robust": robust}
        if production and robust:
            eligible.append(v["name"])
    selected = None
    if eligible:
        selected = max(eligible, key=lambda n: (min(results[n]["folds"][i]["avg_daily_pl_dollars"] for i in range(3)), results[n]["full"]["avg_daily_pl_dollars"]))
    result = {
        "strategy": "options_pattern9_gap_aligned_orb_screen", "order_submission_enabled": False,
        "lookback_days": p3.LOOKBACK_DAYS, "symbols": p3.SYMBOLS, "evaluation_sessions": len(eval_days),
        "bar_size": "5Min", "minimum_symbols_per_evaluation_day": MIN_SYMBOLS_PER_EVAL_DAY,
        "max_daily_trades": MAX_DAILY_TRADES, "starting_balance": p3.STARTING_BALANCE,
        "risk_dollars_per_trade": p3.RISK_DOLLARS,
        "production_target_trades_per_day": "10-15", "daily_profit_target_dollars": "100-150 (research target; not guaranteed)",
        "variants_predeclared": p3.VARIANTS, "results": results, "selected_development_candidate": selected,
        "selection_note": "Gap-aligned opening-range breakout; next 5-minute bar entry only. First 3 chronological folds select; fourth is untouched holdout.",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p3.RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# MarketPulse — Pattern #9 Gap-Aligned Opening Drive", "", "**Research only. No orders. Next-bar entries only.**", "", f"Evaluation sessions: **{len(eval_days)}**", f"Selected development candidate: **{selected or 'NONE'}**", ""]
    for n, x in results.items():
        s, h = x["full"], x["folds"][3]
        lines += [f"## {n}", f"- Trades/day: **{s['trades_per_day']}** | 10+ trade days: **{s['days_10plus_trades_pct']}%**", f"- Full risk-normalized P/L: **${s['net_pl_dollars']:,.2f}** | Avg/day: **${s['avg_daily_pl_dollars']:,.2f}** | PF: **{s['profit_factor']}**", f"- Holdout: **${h['net_pl_dollars']:,.2f}** | trades/day **{h['trades_per_day']}** | PF **{h['profit_factor']}**", ""]
    Path(p3.RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
