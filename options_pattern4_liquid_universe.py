import json
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path

import options_pattern3_fast_research as p3

# Pattern #4 — Liquid Universe Trend Reclaim.
# Production comes from breadth, not repeated low-quality re-entries in one ETF.
p3.LOOKBACK_DAYS = 180
p3.SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "TLT", "GLD", "SLV"]
p3.RESULT_JSON = "options_pattern4_liquid_universe_results.json"
p3.RESULT_MD = "options_pattern4_liquid_universe_results.md"
MAX_PER_SYMBOL_DAY = 2
MAX_NEXT_BAR_CHASE_ATR = 0.20
MIN_SYMBOLS_PER_EVAL_DAY = 7

p3.VARIANTS = [
    {"name": "reclaim_100", "family": "trend_reclaim", "target_r": 1.00, "timeout": 14, "min_sep": 0.10, "min_slope": 0.04, "min_vol": 0.80, "session": "all"},
    {"name": "reclaim_125", "family": "trend_reclaim", "target_r": 1.25, "timeout": 18, "min_sep": 0.10, "min_slope": 0.04, "min_vol": 0.80, "session": "all"},
    {"name": "reclaim_150", "family": "trend_reclaim", "target_r": 1.50, "timeout": 22, "min_sep": 0.10, "min_slope": 0.04, "min_vol": 0.80, "session": "all"},
    {"name": "edges_100", "family": "trend_reclaim", "target_r": 1.00, "timeout": 14, "min_sep": 0.08, "min_slope": 0.03, "min_vol": 0.75, "session": "edges"},
    {"name": "edges_125", "family": "trend_reclaim", "target_r": 1.25, "timeout": 18, "min_sep": 0.08, "min_slope": 0.03, "min_vol": 0.75, "session": "edges"},
    {"name": "edges_150", "family": "trend_reclaim", "target_r": 1.50, "timeout": 22, "min_sep": 0.08, "min_slope": 0.03, "min_vol": 0.75, "session": "edges"},
]


def allowed_session(ts, mode):
    t = ts.time()
    if mode == "edges":
        return (time(9, 45) <= t <= time(11, 45)) or (time(13, 15) <= t <= time(15, 20))
    return time(9, 45) <= t <= time(15, 20)


def reclaim_signal(v, bars, i, side):
    if i < 30 or not allowed_session(bars[i]["ts"], v["session"]):
        return None
    b, p1, p2 = bars[i], bars[i - 1], bars[i - 2]
    atr = b["atr"]
    if atr <= 0:
        return None
    sep = abs(b["ema9"] - b["ema21"]) / atr
    slope = (b["vwap"] - bars[i - 8]["vwap"]) / atr
    vol_ratio = b["v"] / b["vol_med20"] if b["vol_med20"] else 0.0
    if sep < v["min_sep"] or vol_ratio < v["min_vol"]:
        return None
    if side == "CALL":
        pullback = p2["c"] >= p1["c"] and (p1["l"] <= p1["ema9"] or p2["l"] <= p2["ema9"])
        valid = b["ema9"] > b["ema21"] and b["c"] > b["vwap"] and slope >= v["min_slope"] and pullback and b["c"] > b["o"] and b["c"] > p1["h"] and 50 <= b["rsi"] <= 74
        if not valid:
            return None
        return min(p1["l"], p2["l"]) - 0.01
    pullback = p2["c"] <= p1["c"] and (p1["h"] >= p1["ema9"] or p2["h"] >= p2["ema9"])
    valid = b["ema9"] < b["ema21"] and b["c"] < b["vwap"] and slope <= -v["min_slope"] and pullback and b["c"] < b["o"] and b["c"] < p1["l"] and 26 <= b["rsi"] <= 50
    if not valid:
        return None
    return max(p1["h"], p2["h"]) + 0.01


def generate_variant(v, sessions_by_symbol):
    trades = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            p3.add_indicators(bars)
            next_ok = 0
            day_count = 0
            for i in range(30, len(bars) - 1):
                if i < next_ok or day_count >= MAX_PER_SYMBOL_DAY:
                    continue
                found = []
                for side in ("CALL", "PUT"):
                    stop = reclaim_signal(v, bars, i, side)
                    if stop is not None:
                        found.append((side, stop))
                if not found:
                    continue
                side, stop = found[0]
                entry_i = i + 1  # setup candle is fully closed before entry
                entry = bars[entry_i]["o"]
                atr = bars[i]["atr"]
                if abs(entry - bars[i]["c"]) > MAX_NEXT_BAR_CHASE_ATR * atr:
                    continue
                risk = entry - stop if side == "CALL" else stop - entry
                if risk <= 0 or risk > 1.15 * atr:
                    continue
                sim = p3.simulate(bars, entry_i, side, entry, stop, v["target_r"], v["timeout"])
                if not sim:
                    continue
                exit_i, r, reason, exit_price = sim
                trades.append({
                    "symbol": symbol, "date": str(day), "side": side,
                    "signal_ts": bars[i]["ts"].isoformat(), "entry_ts": bars[entry_i]["ts"].isoformat(),
                    "exit_ts": bars[exit_i]["ts"].isoformat(), "entry": round(entry, 4),
                    "stop": round(stop, 4), "exit": exit_price, "exit_reason": reason,
                    "r": r, "pl_dollars": round(r * p3.RISK_DOLLARS, 2),
                })
                day_count += 1
                next_ok = exit_i + 1
    return sorted(trades, key=lambda x: x["entry_ts"])


def main():
    sessions = {}
    coverage = Counter()
    for symbol in p3.SYMBOLS:
        print("fetching", symbol)
        sessions[symbol] = p3.parse_sessions(p3.fetch_bars(symbol))
        for d in sessions[symbol]:
            coverage[d] += 1

    eval_days = sorted(d for d, n in coverage.items() if n >= MIN_SYMBOLS_PER_EVAL_DAY)
    if len(eval_days) < 40:
        raise RuntimeError(f"Insufficient evaluation sessions with >= {MIN_SYMBOLS_PER_EVAL_DAY} symbols: {len(eval_days)}")
    eval_set = set(eval_days)
    sessions = {s: {d: bars for d, bars in ds.items() if d in eval_set} for s, ds in sessions.items()}
    folds = p3.split_dates(eval_days)

    results, eligible = {}, []
    for v in p3.VARIANTS:
        print("testing", v["name"])
        stream = p3.one_account_stream(generate_variant(v, sessions))
        full = p3.summarize(stream, eval_days)
        fold_summaries = []
        for f in folds:
            ft = [t for t in stream if datetime.fromisoformat(t["date"]).date() in f]
            fold_summaries.append(p3.summarize(ft, f))
        dev, hold = fold_summaries[:3], fold_summaries[3]
        production = full["trades_per_day"] >= 10.0 and full["days_10plus_trades_pct"] >= 50.0
        robust = all(x["net_pl_dollars"] > 0 and (x["profit_factor"] or 0) > 1.0 for x in dev)
        results[v["name"]] = {"variant": v, "full": full, "folds": fold_summaries, "production_screen_pass": production, "development_robust": robust}
        if production and robust:
            eligible.append(v["name"])

    selected = None
    if eligible:
        selected = max(eligible, key=lambda n: (min(results[n]["folds"][i]["avg_daily_pl_dollars"] for i in range(3)), results[n]["full"]["avg_daily_pl_dollars"]))

    result = {
        "strategy": "options_pattern4_liquid_universe_screen", "order_submission_enabled": False,
        "lookback_days": p3.LOOKBACK_DAYS, "symbols": p3.SYMBOLS,
        "common_sessions": len(eval_days), "evaluation_sessions": len(eval_days),
        "minimum_symbols_per_evaluation_day": MIN_SYMBOLS_PER_EVAL_DAY,
        "starting_balance": p3.STARTING_BALANCE, "risk_dollars_per_trade": p3.RISK_DOLLARS,
        "production_target_trades_per_day": "10-15", "daily_profit_target_dollars": "100-150 (research target; not guaranteed)",
        "variants_predeclared": p3.VARIANTS, "results": results,
        "selected_development_candidate": selected,
        "selection_note": "Selection uses only first three chronological folds. Fourth fold is untouched holdout.",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p3.RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# MarketPulse — Pattern #4 Liquid Universe Screen", "", "**Research only. No orders. Next-bar entries only.**", "", f"Evaluation sessions: **{len(eval_days)}**", f"Selected development candidate: **{selected or 'NONE'}**", ""]
    for n, x in results.items():
        s, h = x["full"], x["folds"][3]
        lines += [f"## {n}", f"- Trades/day: **{s['trades_per_day']}** | 10+ trade days: **{s['days_10plus_trades_pct']}%**", f"- Full risk-normalized P/L: **${s['net_pl_dollars']:,.2f}** | Avg/day: **${s['avg_daily_pl_dollars']:,.2f}** | PF: **{s['profit_factor']}**", f"- Holdout: **${h['net_pl_dollars']:,.2f}** | trades/day **{h['trades_per_day']}** | PF **{h['profit_factor']}**", ""]
    Path(p3.RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
