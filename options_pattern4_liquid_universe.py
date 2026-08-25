from datetime import time

import options_pattern3_fast_research as p3

# Pattern #4 — Liquid Universe Trend Reclaim
# Reach production by breadth, not repeated low-quality re-entries in one ETF.
p3.LOOKBACK_DAYS = 180
p3.SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "TLT", "GLD", "SLV"]
p3.RESULT_JSON = "options_pattern4_liquid_universe_results.json"
p3.RESULT_MD = "options_pattern4_liquid_universe_results.md"
MAX_PER_SYMBOL_DAY = 2
MAX_NEXT_BAR_CHASE_ATR = 0.20

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
        if not valid: return None
        stop = min(p1["l"], p2["l"]) - 0.01
    else:
        pullback = p2["c"] <= p1["c"] and (p1["h"] >= p1["ema9"] or p2["h"] >= p2["ema9"])
        valid = b["ema9"] < b["ema21"] and b["c"] < b["vwap"] and slope <= -v["min_slope"] and pullback and b["c"] < b["o"] and b["c"] < p1["l"] and 26 <= b["rsi"] <= 50
        if not valid: return None
        stop = max(p1["h"], p2["h"]) + 0.01
    return stop


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

p3.generate_variant = generate_variant

if __name__ == "__main__":
    p3.main()
