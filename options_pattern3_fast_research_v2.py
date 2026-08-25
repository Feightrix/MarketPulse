from datetime import datetime

import options_pattern3_fast_research as p3

# Corrected research pass: a setup is only actionable after its signal candle closes.
# Entry occurs at the NEXT 1-minute bar open, with a 0.30 ATR anti-chase limit.
MAX_NEXT_BAR_CHASE_ATR = 0.30


def corrected_generate_variant(variant, sessions_by_symbol):
    trades = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            p3.add_indicators(bars)
            next_ok = 0
            day_count = 0
            for i in range(25, len(bars) - 1):
                if i < next_ok or day_count >= p3.MAX_DAILY_TRADES:
                    continue
                t = bars[i]["ts"].time()
                if t < p3.START or t > p3.LATEST_ENTRY:
                    continue
                candidates = []
                for side in ("CALL", "PUT"):
                    sig = p3.family_signal(variant["family"], bars, i, side)
                    if sig:
                        candidates.append((side, sig[1]))
                if not candidates:
                    continue
                side, stop = candidates[0]
                entry_i = i + 1
                entry = bars[entry_i]["o"]
                atr = bars[i]["atr"]
                if atr <= 0:
                    continue
                if abs(entry - bars[i]["c"]) > MAX_NEXT_BAR_CHASE_ATR * atr:
                    continue
                risk = entry - stop if side == "CALL" else stop - entry
                if risk <= 0 or risk > 1.25 * atr:
                    continue
                sim = p3.simulate(bars, entry_i, side, entry, stop, variant["target_r"], variant["timeout"])
                if not sim:
                    continue
                exit_i, r, reason, exit_price = sim
                trades.append({
                    "symbol": symbol, "date": str(day), "side": side,
                    "signal_ts": bars[i]["ts"].isoformat(),
                    "entry_ts": bars[entry_i]["ts"].isoformat(),
                    "exit_ts": bars[exit_i]["ts"].isoformat(),
                    "entry": round(entry, 4), "stop": round(stop, 4), "exit": exit_price,
                    "exit_reason": reason, "r": r,
                    "pl_dollars": round(r * p3.RISK_DOLLARS, 2),
                })
                day_count += 1
                next_ok = exit_i + 1
    return sorted(trades, key=lambda x: x["entry_ts"])


p3.generate_variant = corrected_generate_variant
p3.RESULT_JSON = "options_pattern3_fast_v2_results.json"
p3.RESULT_MD = "options_pattern3_fast_v2_results.md"

if __name__ == "__main__":
    p3.main()
