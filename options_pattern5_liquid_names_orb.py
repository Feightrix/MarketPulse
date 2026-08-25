import json
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path

import options_pattern3_fast_research as p3

# Pattern #5 — Liquid Names Opening Drive / First Pullback.
# Goal: 10-15 total trades/day from breadth, max two entries per underlying/day.
p3.LOOKBACK_DAYS = 180
p3.SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "AMD", "TSLA", "AMZN", "META", "MSFT", "GOOGL", "PLTR"]
p3.RESULT_JSON = "options_pattern5_liquid_names_orb_results.json"
p3.RESULT_MD = "options_pattern5_liquid_names_orb_results.md"
MIN_SYMBOLS_PER_EVAL_DAY = 9
MAX_PER_SYMBOL_DAY = 2
MAX_NEXT_BAR_CHASE_ATR = 0.20

p3.VARIANTS = [
    {"name": "orb_075", "family": "fresh_orb", "target_r": 0.75, "timeout": 12, "min_vol": 1.00},
    {"name": "orb_100", "family": "fresh_orb", "target_r": 1.00, "timeout": 16, "min_vol": 1.00},
    {"name": "orb_125", "family": "fresh_orb", "target_r": 1.25, "timeout": 20, "min_vol": 1.00},
    {"name": "pullback_075", "family": "orb_pullback", "target_r": 0.75, "timeout": 12, "min_vol": 0.75},
    {"name": "pullback_100", "family": "orb_pullback", "target_r": 1.00, "timeout": 16, "min_vol": 0.75},
    {"name": "pullback_125", "family": "orb_pullback", "target_r": 1.25, "timeout": 20, "min_vol": 0.75},
]


def opening_range(bars):
    opening = [b for b in bars if time(9, 30) <= b["ts"].time() < time(9, 45)]
    if not opening:
        return None
    return max(b["h"] for b in opening), min(b["l"] for b in opening)


def orb_signal(v, bars, i, side, or_high, or_low):
    if i < 20:
        return None
    b, p = bars[i], bars[i - 1]
    if not (time(9, 45) <= b["ts"].time() <= time(15, 10)):
        return None
    atr = b["atr"]
    if atr <= 0:
        return None
    vol_ratio = b["v"] / b["vol_med20"] if b["vol_med20"] else 0.0
    body = abs(b["c"] - b["o"])
    if side == "CALL":
        valid = (
            p["c"] <= or_high and b["c"] > or_high and b["c"] > b["vwap"]
            and b["ema9"] >= b["ema21"] and body >= 0.18 * atr
            and vol_ratio >= v["min_vol"] and 54 <= b["rsi"] <= 80
        )
        if not valid:
            return None
        stop = max(or_high - 0.30 * atr, b["l"] - 0.01)
    else:
        valid = (
            p["c"] >= or_low and b["c"] < or_low and b["c"] < b["vwap"]
            and b["ema9"] <= b["ema21"] and body >= 0.18 * atr
            and vol_ratio >= v["min_vol"] and 20 <= b["rsi"] <= 46
        )
        if not valid:
            return None
        stop = min(or_low + 0.30 * atr, b["h"] + 0.01)
    return stop


def pullback_signal(v, bars, i, side, or_high, or_low):
    if i < 30:
        return None
    b, p1, p2 = bars[i], bars[i - 1], bars[i - 2]
    if not (time(10, 0) <= b["ts"].time() <= time(15, 10)):
        return None
    atr = b["atr"]
    if atr <= 0:
        return None
    vol_ratio = b["v"] / b["vol_med20"] if b["vol_med20"] else 0.0
    last12 = bars[max(0, i - 12):i]
    if side == "CALL":
        breakout_seen = any(x["c"] > or_high for x in last12)
        touched = p1["l"] <= max(or_high + 0.15 * atr, p1["ema9"])
        valid = (
            breakout_seen and touched and b["c"] > or_high and b["c"] > b["vwap"]
            and b["ema9"] > b["ema21"] and b["c"] > b["o"] and b["c"] > p1["h"]
            and vol_ratio >= v["min_vol"] and 50 <= b["rsi"] <= 76
        )
        if not valid:
            return None
        stop = min(p1["l"], p2["l"]) - 0.01
    else:
        breakout_seen = any(x["c"] < or_low for x in last12)
        touched = p1["h"] >= min(or_low - 0.15 * atr, p1["ema9"])
        valid = (
            breakout_seen and touched and b["c"] < or_low and b["c"] < b["vwap"]
            and b["ema9"] < b["ema21"] and b["c"] < b["o"] and b["c"] < p1["l"]
            and vol_ratio >= v["min_vol"] and 24 <= b["rsi"] <= 50
        )
        if not valid:
            return None
        stop = max(p1["h"], p2["h"]) + 0.01
    return stop


def generate_variant(v, sessions_by_symbol):
    trades = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            p3.add_indicators(bars)
            rng = opening_range(bars)
            if not rng:
                continue
            or_high, or_low = rng
            next_ok = 0
            day_count = 0
            for i in range(20, len(bars) - 1):
                if i < next_ok or day_count >= MAX_PER_SYMBOL_DAY:
                    continue
                found = []
                for side in ("CALL", "PUT"):
                    if v["family"] == "fresh_orb":
                        stop = orb_signal(v, bars, i, side, or_high, or_low)
                    else:
                        stop = pullback_signal(v, bars, i, side, or_high, or_low)
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
        development_robust = all(x["net_pl_dollars"] > 0 and (x["profit_factor"] or 0) > 1.0 for x in fs[:3])
        production = full["trades_per_day"] >= 8.0 and full["days_10plus_trades_pct"] >= 30.0
        results[v["name"]] = {"variant": v, "full": full, "folds": fs, "production_screen_pass": production, "development_robust": development_robust}
        if production and development_robust:
            eligible.append(v["name"])
    selected = None
    if eligible:
        selected = max(eligible, key=lambda n: (min(results[n]["folds"][i]["avg_daily_pl_dollars"] for i in range(3)), results[n]["full"]["avg_daily_pl_dollars"]))
    result = {
        "strategy": "options_pattern5_liquid_names_orb_screen", "order_submission_enabled": False,
        "lookback_days": p3.LOOKBACK_DAYS, "symbols": p3.SYMBOLS, "evaluation_sessions": len(eval_days),
        "minimum_symbols_per_evaluation_day": MIN_SYMBOLS_PER_EVAL_DAY,
        "starting_balance": p3.STARTING_BALANCE, "risk_dollars_per_trade": p3.RISK_DOLLARS,
        "production_target_trades_per_day": "10-15", "daily_profit_target_dollars": "100-150 (research target; not guaranteed)",
        "variants_predeclared": p3.VARIANTS, "results": results, "selected_development_candidate": selected,
        "selection_note": "First three chronological folds select; fourth fold is untouched holdout. All entries are next-bar only.",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p3.RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# MarketPulse — Pattern #5 Liquid Names ORB Screen", "", "**Research only. No orders. Next-bar entries only.**", "", f"Evaluation sessions: **{len(eval_days)}**", f"Selected development candidate: **{selected or 'NONE'}**", ""]
    for n, x in results.items():
        s, h = x["full"], x["folds"][3]
        lines += [f"## {n}", f"- Trades/day: **{s['trades_per_day']}** | 10+ trade days: **{s['days_10plus_trades_pct']}%**", f"- Full risk-normalized P/L: **${s['net_pl_dollars']:,.2f}** | Avg/day: **${s['avg_daily_pl_dollars']:,.2f}** | PF: **{s['profit_factor']}**", f"- Holdout: **${h['net_pl_dollars']:,.2f}** | trades/day **{h['trades_per_day']}** | PF **{h['profit_factor']}**", ""]
    Path(p3.RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
