from datetime import time

import options_pattern3_fast_research as p3

# Pattern #3B: distinct fast continuation screen after the corrected Pattern #3 failed.
# All entries remain NEXT-BAR only. We broaden the liquid ETF basket to preserve
# production while demanding stronger trend structure.
p3.LOOKBACK_DAYS = 240
p3.SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "XLK"]
p3.RESULT_JSON = "options_pattern3_trend_strength_results.json"
p3.RESULT_MD = "options_pattern3_trend_strength_results.md"

MAX_NEXT_BAR_CHASE_ATR = 0.25

p3.VARIANTS = [
    {"name": "edges_100", "family": "trend_strength", "target_r": 1.00, "timeout": 12, "session": "edges", "min_sep": 0.08, "min_slope": 0.04, "min_vol": 0.80},
    {"name": "edges_125", "family": "trend_strength", "target_r": 1.25, "timeout": 15, "session": "edges", "min_sep": 0.08, "min_slope": 0.04, "min_vol": 0.80},
    {"name": "open_100", "family": "trend_strength", "target_r": 1.00, "timeout": 12, "session": "open", "min_sep": 0.10, "min_slope": 0.05, "min_vol": 0.85},
    {"name": "open_125", "family": "trend_strength", "target_r": 1.25, "timeout": 15, "session": "open", "min_sep": 0.10, "min_slope": 0.05, "min_vol": 0.85},
    {"name": "all_strong_100", "family": "trend_strength", "target_r": 1.00, "timeout": 12, "session": "all", "min_sep": 0.15, "min_slope": 0.08, "min_vol": 1.00},
    {"name": "all_strong_125", "family": "trend_strength", "target_r": 1.25, "timeout": 15, "session": "all", "min_sep": 0.15, "min_slope": 0.08, "min_vol": 1.00},
]


def in_session(ts, mode):
    t = ts.time()
    if mode == "open":
        return time(9, 45) <= t <= time(11, 30)
    if mode == "edges":
        return (time(9, 45) <= t <= time(11, 30)) or (time(13, 30) <= t <= time(15, 30))
    return time(9, 45) <= t <= time(15, 30)


def strong_trend_signal(variant, bars, i, side):
    if i < 25 or not in_session(bars[i]["ts"], variant["session"]):
        return None
    base_sig = p3.signal_trend_pullback(bars, i, side)
    if not base_sig:
        return None
    b = bars[i]
    atr = b["atr"]
    if atr <= 0:
        return None
    sep = abs(b["ema9"] - b["ema21"]) / atr
    slope = (b["vwap"] - bars[i - 10]["vwap"]) / atr
    vol_ratio = b["v"] / b["vol_med20"] if b["vol_med20"] > 0 else 0.0
    if sep < variant["min_sep"] or vol_ratio < variant["min_vol"]:
        return None
    if side == "CALL" and slope < variant["min_slope"]:
        return None
    if side == "PUT" and slope > -variant["min_slope"]:
        return None
    return base_sig


def generate_variant(variant, sessions_by_symbol):
    trades = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            p3.add_indicators(bars)
            next_ok = 0
            day_count = 0
            for i in range(25, len(bars) - 1):
                if i < next_ok or day_count >= p3.MAX_DAILY_TRADES:
                    continue
                candidates = []
                for side in ("CALL", "PUT"):
                    sig = strong_trend_signal(variant, bars, i, side)
                    if sig:
                        candidates.append((side, sig[1]))
                if not candidates:
                    continue
                side, stop = candidates[0]
                entry_i = i + 1
                entry = bars[entry_i]["o"]
                atr = bars[i]["atr"]
                if abs(entry - bars[i]["c"]) > MAX_NEXT_BAR_CHASE_ATR * atr:
                    continue
                risk = entry - stop if side == "CALL" else stop - entry
                if risk <= 0 or risk > 1.20 * atr:
                    continue
                sim = p3.simulate(bars, entry_i, side, entry, stop, variant["target_r"], variant["timeout"])
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
