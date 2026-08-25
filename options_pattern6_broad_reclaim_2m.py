import json
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path

import options_pattern3_fast_research as p3

p3.LOOKBACK_DAYS = 180
p3.SYMBOLS = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "AMD", "TSLA", "AMZN", "META", "MSFT",
    "GOOGL", "PLTR", "BAC", "INTC", "SOFI", "XLF", "XLE", "GLD", "SLV", "TLT",
]
p3.RESULT_JSON = "options_pattern6_broad_reclaim_2m_results.json"
p3.RESULT_MD = "options_pattern6_broad_reclaim_2m_results.md"
MIN_SYMBOLS_PER_EVAL_DAY = 14
MAX_PER_SYMBOL_DAY = 1
MAX_NEXT_BAR_CHASE_ATR = 0.18

p3.VARIANTS = [
    {"name": "all_075", "target_r": 0.75, "timeout": 6, "session": "all", "min_sep": 0.08, "min_slope": 0.03, "min_vol": 0.75},
    {"name": "all_100", "target_r": 1.00, "timeout": 8, "session": "all", "min_sep": 0.08, "min_slope": 0.03, "min_vol": 0.75},
    {"name": "all_125", "target_r": 1.25, "timeout": 10, "session": "all", "min_sep": 0.08, "min_slope": 0.03, "min_vol": 0.75},
    {"name": "edges_075", "target_r": 0.75, "timeout": 6, "session": "edges", "min_sep": 0.06, "min_slope": 0.02, "min_vol": 0.70},
    {"name": "edges_100", "target_r": 1.00, "timeout": 8, "session": "edges", "min_sep": 0.06, "min_slope": 0.02, "min_vol": 0.70},
    {"name": "edges_125", "target_r": 1.25, "timeout": 10, "session": "edges", "min_sep": 0.06, "min_slope": 0.02, "min_vol": 0.70},
]


def aggregate_2m(bars):
    out = []
    for i in range(0, len(bars) - 1, 2):
        g = bars[i:i + 2]
        if len(g) < 2:
            continue
        out.append({
            "ts": g[0]["ts"], "o": g[0]["o"], "h": max(x["h"] for x in g),
            "l": min(x["l"] for x in g), "c": g[-1]["c"], "v": sum(x["v"] for x in g),
        })
    return out


def allowed(ts, mode):
    t = ts.time()
    if mode == "edges":
        return (time(9, 50) <= t <= time(11, 30)) or (time(13, 30) <= t <= time(15, 10))
    return time(9, 50) <= t <= time(15, 10)


def reclaim(v, bars, i, side):
    if i < 18 or not allowed(bars[i]["ts"], v["session"]):
        return None
    b, p1, p2 = bars[i], bars[i - 1], bars[i - 2]
    atr = b["atr"]
    if atr <= 0:
        return None
    sep = abs(b["ema9"] - b["ema21"]) / atr
    slope = (b["vwap"] - bars[i - 5]["vwap"]) / atr
    vol_ratio = b["v"] / b["vol_med20"] if b["vol_med20"] else 0.0
    if sep < v["min_sep"] or vol_ratio < v["min_vol"]:
        return None
    if side == "CALL":
        touched = p1["l"] <= p1["ema9"] or p2["l"] <= p2["ema9"]
        valid = (
            b["ema9"] > b["ema21"] and b["c"] > b["vwap"] and slope >= v["min_slope"]
            and touched and b["c"] > b["o"] and b["c"] > p1["h"]
            and 50 <= b["rsi"] <= 73 and (b["c"] - b["vwap"]) <= 1.05 * atr
        )
        if not valid:
            return None
        return min(p1["l"], p2["l"]) - 0.01
    touched = p1["h"] >= p1["ema9"] or p2["h"] >= p2["ema9"]
    valid = (
        b["ema9"] < b["ema21"] and b["c"] < b["vwap"] and slope <= -v["min_slope"]
        and touched and b["c"] < b["o"] and b["c"] < p1["l"]
        and 27 <= b["rsi"] <= 50 and (b["vwap"] - b["c"]) <= 1.05 * atr
    )
    if not valid:
        return None
    return max(p1["h"], p2["h"]) + 0.01


def generate_variant(v, sessions_by_symbol):
    trades = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, raw in sessions.items():
            bars = aggregate_2m(raw)
            if len(bars) < 120:
                continue
            p3.add_indicators(bars)
            day_count = 0
            for i in range(18, len(bars) - 1):
                if day_count >= MAX_PER_SYMBOL_DAY:
                    break
                found = []
                for side in ("CALL", "PUT"):
                    stop = reclaim(v, bars, i, side)
                    if stop is not None:
                        found.append((side, stop))
                if not found:
                    continue
                side, stop = found[0]
                entry_i = i + 1
                entry = bars[entry_i]["o"]
                atr = bars[i]["atr"]
                if abs(entry - bars[i]["c"]) > MAX_NEXT_BAR_CHASE_ATR * atr:
                    continue
                risk = entry - stop if side == "CALL" else stop - entry
                if risk <= 0 or risk > 1.10 * atr:
                    continue
                sim = p3.simulate(bars, entry_i, side, entry, stop, v["target_r"], v["timeout"])
                if not sim:
                    continue
                exit_i, r, reason, exit_price = sim
                trades.append({
                    "symbol": symbol, "date": str(day), "side": side,
                    "signal_ts": bars[i]["ts"].isoformat(), "entry_ts": bars[entry_i]["ts"].isoformat(),
                    "exit_ts": bars[exit_i]["ts"].isoformat(), "entry": round(entry, 4), "stop": round(stop, 4),
                    "exit": exit_price, "exit_reason": reason, "r": r,
                    "pl_dollars": round(r * p3.RISK_DOLLARS, 2),
                })
                day_count += 1
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
    eval_set = set(eval_days)
    sessions = {s: {d: b for d, b in ds.items() if d in eval_set} for s, ds in sessions.items()}
    folds = p3.split_dates(eval_days)
    results, eligible = {}, []
    for v in p3.VARIANTS:
        print("testing", v["name"])
        stream = p3.one_account_stream(generate_variant(v, sessions))
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
        "strategy": "options_pattern6_broad_reclaim_2m_screen", "order_submission_enabled": False,
        "lookback_days": p3.LOOKBACK_DAYS, "symbols": p3.SYMBOLS, "evaluation_sessions": len(eval_days),
        "minimum_symbols_per_evaluation_day": MIN_SYMBOLS_PER_EVAL_DAY, "bar_size": "2Min",
        "starting_balance": p3.STARTING_BALANCE, "risk_dollars_per_trade": p3.RISK_DOLLARS,
        "production_target_trades_per_day": "10-15", "daily_profit_target_dollars": "100-150 (research target; not guaranteed)",
        "variants_predeclared": p3.VARIANTS, "results": results, "selected_development_candidate": selected,
        "selection_note": "First three chronological folds select; fourth fold is untouched holdout. One trade max per symbol/day; one account position at a time.",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p3.RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# MarketPulse — Pattern #6 Broad 2-Minute Reclaim Screen", "", "**Research only. No orders. Next-bar entries only.**", "", f"Evaluation sessions: **{len(eval_days)}**", f"Selected development candidate: **{selected or 'NONE'}**", ""]
    for n, x in results.items():
        s, h = x["full"], x["folds"][3]
        lines += [f"## {n}", f"- Trades/day: **{s['trades_per_day']}** | 10+ trade days: **{s['days_10plus_trades_pct']}%**", f"- Full risk-normalized P/L: **${s['net_pl_dollars']:,.2f}** | Avg/day: **${s['avg_daily_pl_dollars']:,.2f}** | PF: **{s['profit_factor']}**", f"- Holdout: **${h['net_pl_dollars']:,.2f}** | trades/day **{h['trades_per_day']}** | PF **{h['profit_factor']}**", ""]
    Path(p3.RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
