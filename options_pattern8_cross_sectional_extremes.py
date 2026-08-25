import json
from collections import Counter, defaultdict
from datetime import datetime, time, timezone, timedelta
from pathlib import Path

import options_pattern3_fast_research as p3

p3.LOOKBACK_DAYS = 180
p3.SYMBOLS = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "AMD", "TSLA", "AMZN", "META", "MSFT",
    "GOOGL", "PLTR", "BAC", "INTC", "SOFI", "XLF", "XLE", "GLD", "SLV", "TLT",
]
p3.RESULT_JSON = "options_pattern8_cross_sectional_extremes_results.json"
p3.RESULT_MD = "options_pattern8_cross_sectional_extremes_results.md"
MIN_SYMBOLS_PER_EVAL_DAY = 14
MIN_SYMBOLS_PER_RANK = 10
MAX_DAILY_TRADES = 15

# Predeclared continuation vs mean-reversion families. No absolute score threshold.
p3.VARIANTS = [
    {"name": "momentum_050", "family": "momentum", "target_r": 0.50, "timeout": 2},
    {"name": "momentum_075", "family": "momentum", "target_r": 0.75, "timeout": 2},
    {"name": "momentum_100", "family": "momentum", "target_r": 1.00, "timeout": 3},
    {"name": "revert_050", "family": "revert", "target_r": 0.50, "timeout": 2},
    {"name": "revert_075", "family": "revert", "target_r": 0.75, "timeout": 2},
    {"name": "revert_100", "family": "revert", "target_r": 1.00, "timeout": 3},
]


def aggregate_5m(raw):
    # Clock-align bars to true 5-minute buckets so an IEX gap in one symbol does not
    # shift every later bar relative to the rest of the cross-section.
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


def prepare_day(raw_by_symbol):
    out = {}
    for symbol, raw in raw_by_symbol.items():
        bars = aggregate_5m(raw)
        if len(bars) < 50:
            continue
        p3.add_indicators(bars)
        out[symbol] = bars
    return out


def score(bars, i):
    if i < 20:
        return None
    b = bars[i]
    atr = b["atr"]
    if atr <= 0:
        return None
    vwap = (b["c"] - b["vwap"]) / atr
    ema = (b["ema9"] - b["ema21"]) / atr
    mom = (b["c"] - bars[i - 3]["c"]) / atr
    return 0.40 * vwap + 0.30 * ema + 0.30 * mom


def generate_variant(v, sessions):
    trades = []
    all_days = sorted(set().union(*(set(ds) for ds in sessions.values())))
    for day in all_days:
        raw = {s: ds[day] for s, ds in sessions.items() if day in ds}
        prepared = prepare_day(raw)
        if len(prepared) < MIN_SYMBOLS_PER_EVAL_DAY:
            continue
        # Use all clock-aligned timestamps; each ranking needs >=10 available symbols,
        # not a perfect-print intersection across the entire universe.
        timestamps = sorted(set().union(*(set(b["ts"] for b in bars) for bars in prepared.values())))
        index = {s: {b["ts"]: i for i, b in enumerate(bars)} for s, bars in prepared.items()}
        busy_until = None
        day_count = 0
        for ts in timestamps:
            if day_count >= MAX_DAILY_TRADES:
                break
            if not (time(10, 0) <= ts.time() <= time(15, 20)):
                continue
            if busy_until is not None and ts <= busy_until:
                continue
            ranked = []
            for symbol, bars in prepared.items():
                i = index[symbol].get(ts)
                if i is None or i < 20 or i + 1 >= len(bars):
                    continue
                # Require a true next 5-minute bar so the entry timing is executable.
                if bars[i + 1]["ts"] != ts + timedelta(minutes=5):
                    continue
                sc = score(bars, i)
                if sc is not None:
                    ranked.append((sc, symbol, i))
            if len(ranked) < MIN_SYMBOLS_PER_RANK:
                continue
            high = max(ranked, key=lambda x: x[0])
            low = min(ranked, key=lambda x: x[0])
            values = sorted(x[0] for x in ranked)
            median_score = values[len(values) // 2]
            hi_dist = high[0] - median_score
            lo_dist = median_score - low[0]
            extreme = high if hi_dist >= lo_dist else low
            sc, symbol, i = extreme
            bars = prepared[symbol]
            if v["family"] == "momentum":
                side = "CALL" if sc >= median_score else "PUT"
            else:
                side = "PUT" if sc >= median_score else "CALL"
            entry_i = i + 1
            entry = bars[entry_i]["o"]
            atr = bars[i]["atr"]
            stop = entry - 0.55 * atr if side == "CALL" else entry + 0.55 * atr
            sim = p3.simulate(bars, entry_i, side, entry, stop, v["target_r"], v["timeout"])
            if not sim:
                continue
            exit_i, r, reason, exit_price = sim
            exit_ts = bars[exit_i]["ts"]
            trades.append({
                "symbol": symbol, "date": str(day), "side": side, "score": round(sc, 4),
                "family": v["family"], "signal_ts": bars[i]["ts"].isoformat(),
                "entry_ts": bars[entry_i]["ts"].isoformat(), "exit_ts": exit_ts.isoformat(),
                "entry": round(entry, 4), "stop": round(stop, 4), "exit": exit_price,
                "exit_reason": reason, "r": r, "pl_dollars": round(r * p3.RISK_DOLLARS, 2),
            })
            day_count += 1
            busy_until = exit_ts
    return sorted(trades, key=lambda x: x["entry_ts"])


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
    sessions = {s: {d: b for d, b in ds.items() if d in es} for s, ds in sessions.items()}
    folds = p3.split_dates(eval_days)
    results, eligible = {}, []
    for v in p3.VARIANTS:
        print("testing", v["name"])
        stream = generate_variant(v, sessions)
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
        "strategy": "options_pattern8_cross_sectional_extremes_screen", "order_submission_enabled": False,
        "lookback_days": p3.LOOKBACK_DAYS, "symbols": p3.SYMBOLS, "evaluation_sessions": len(eval_days),
        "bar_size": "5Min", "minimum_symbols_per_evaluation_day": MIN_SYMBOLS_PER_EVAL_DAY,
        "minimum_symbols_per_rank": MIN_SYMBOLS_PER_RANK,
        "starting_balance": p3.STARTING_BALANCE, "risk_dollars_per_trade": p3.RISK_DOLLARS,
        "production_target_trades_per_day": "10-15", "daily_profit_target_dollars": "100-150 (research target; not guaranteed)",
        "variants_predeclared": p3.VARIANTS, "results": results, "selected_development_candidate": selected,
        "selection_note": "Clock-aligned relative-rank extremes; next-bar entry only. First 3 chronological folds select; fourth is untouched holdout.",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p3.RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# MarketPulse — Pattern #8 Cross-Sectional Extremes (Clock-Aligned)", "", "**Research only. No orders. Next-bar entries only.**", "", f"Evaluation sessions: **{len(eval_days)}**", f"Selected development candidate: **{selected or 'NONE'}**", ""]
    for n, x in results.items():
        s, h = x["full"], x["folds"][3]
        lines += [f"## {n}", f"- Trades/day: **{s['trades_per_day']}** | 10+ trade days: **{s['days_10plus_trades_pct']}%**", f"- Full risk-normalized P/L: **${s['net_pl_dollars']:,.2f}** | Avg/day: **${s['avg_daily_pl_dollars']:,.2f}** | PF: **{s['profit_factor']}**", f"- Holdout: **${h['net_pl_dollars']:,.2f}** | trades/day **{h['trades_per_day']}** | PF **{h['profit_factor']}**", ""]
    Path(p3.RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
