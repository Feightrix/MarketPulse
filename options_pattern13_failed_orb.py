import json
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path

import options_pattern10_opening_pullback as p10

RESULT_JSON = "options_pattern13_failed_orb_results.json"
RESULT_MD = "options_pattern13_failed_orb_results.md"
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 12.50
MAX_DAILY_TRADES = 3
SEARCH_START = time(10, 0)
LATEST_ENTRY = time(13, 0)
FORCE_EXIT = time(15, 45)
TICK = 0.01

# Predeclared sweep depths and targets. No post-result tuning.
VARIANTS = [
    {"name": "sweep005_r125", "min_sweep_atr": 0.05, "target_r": 1.25, "timeout_bars": 10},
    {"name": "sweep005_r150", "min_sweep_atr": 0.05, "target_r": 1.50, "timeout_bars": 10},
    {"name": "sweep015_r125", "min_sweep_atr": 0.15, "target_r": 1.25, "timeout_bars": 10},
    {"name": "sweep015_r150", "min_sweep_atr": 0.15, "target_r": 1.50, "timeout_bars": 10},
]


def simulate(bars, entry_i, side, entry, stop, target_r, timeout_bars):
    risk = entry - stop if side == "CALL" else stop - entry
    if risk <= 0:
        return None
    target = entry + target_r * risk if side == "CALL" else entry - target_r * risk
    last_i = min(len(bars) - 1, entry_i + timeout_bars)
    exit_i, exit_price, reason = last_i, bars[last_i]["c"], "TIME"
    for j in range(entry_i, last_i + 1):
        b = bars[j]
        if b["ts"].time() > FORCE_EXIT:
            exit_i, exit_price, reason = j, b["o"], "TIME"; break
        if side == "CALL":
            stop_hit, target_hit = b["l"] <= stop, b["h"] >= target
        else:
            stop_hit, target_hit = b["h"] >= stop, b["l"] <= target
        if stop_hit:
            exit_i, exit_price, reason = j, stop, "STOP"; break
        if target_hit:
            exit_i, exit_price, reason = j, target, "TARGET"; break
    r = (exit_price - entry) / risk if side == "CALL" else (entry - exit_price) / risk
    return exit_i, round(r, 4), reason, round(exit_price, 4)


def failed_breakout(symbol, day, bars, cfg):
    if len(bars) < 12:
        return None
    p10.add_indicators(bars)
    opening = bars[:6]  # first 30 minutes
    or_high = max(x["h"] for x in opening)
    or_low = min(x["l"] for x in opening)
    or_mid = 0.5 * (or_high + or_low)

    for i in range(6, len(bars) - 1):
        b, nxt = bars[i], bars[i + 1]
        if b["ts"].time() < SEARCH_START:
            continue
        if b["ts"].time() > LATEST_ENTRY or nxt["ts"].time() > LATEST_ENTRY:
            break
        atr = max(b["atr"], 1e-9)
        rng = max(b["h"] - b["l"], 1e-9)
        vol_ratio = b["v"] / max(b["vol_med20"], 1.0)
        if vol_ratio < 0.75:
            continue

        side = None
        sweep = 0.0
        if (
            b["h"] >= or_high + cfg["min_sweep_atr"] * atr
            and b["c"] <= or_high - 0.03 * atr
            and b["c"] < b["o"]
            and (b["c"] - b["l"]) / rng <= 0.45
            and nxt["l"] <= b["l"] - TICK
        ):
            side = "PUT"
            sweep = (b["h"] - or_high) / atr
            entry = min(b["l"] - TICK, nxt["o"])
            stop = min(b["h"] + TICK + 0.04 * atr, entry + 0.85 * atr)
            risk = stop - entry
        elif (
            b["l"] <= or_low - cfg["min_sweep_atr"] * atr
            and b["c"] >= or_low + 0.03 * atr
            and b["c"] > b["o"]
            and (b["c"] - b["l"]) / rng >= 0.55
            and nxt["h"] >= b["h"] + TICK
        ):
            side = "CALL"
            sweep = (or_low - b["l"]) / atr
            entry = max(b["h"] + TICK, nxt["o"])
            stop = max(b["l"] - TICK - 0.04 * atr, entry - 0.85 * atr)
            risk = entry - stop
        if side is None or risk <= 0 or risk > 0.90 * atr:
            continue

        sim = simulate(bars, i + 1, side, entry, stop, cfg["target_r"], cfg["timeout_bars"])
        if not sim:
            continue
        exit_i, r, reason, exit_price = sim
        # Prefer deeper sweeps and stronger rejection; quality only breaks simultaneous timestamps.
        mid_reclaim = abs(b["c"] - or_mid) / atr
        quality = min(sweep, 2.0) + 0.35 * min(vol_ratio, 3.0) + 0.15 * mid_reclaim
        return {
            "symbol": symbol, "date": str(day), "side": side,
            "entry_ts": nxt["ts"].isoformat(), "exit_ts": bars[exit_i]["ts"].isoformat(),
            "entry": round(entry, 4), "stop": round(stop, 4), "exit": exit_price,
            "exit_reason": reason, "r": r, "pl_dollars": round(r * RISK_DOLLARS, 2),
            "sweep_atr": round(sweep, 4), "quality": round(quality, 4),
            "underlying_entry_spot": round(nxt["c"], 4),
        }
    return None


def generate(cfg, sessions_by_symbol):
    signals = []
    for symbol, sessions in sessions_by_symbol.items():
        for day, bars in sessions.items():
            s = failed_breakout(symbol, day, bars, cfg)
            if s:
                signals.append(s)
    return sorted(signals, key=lambda x: (x["entry_ts"], -x["quality"], x["symbol"]))


def causal_stream(signals):
    grouped = defaultdict(list)
    for s in signals:
        grouped[s["entry_ts"]].append(s)
    out, busy_until = [], None
    counts = defaultdict(int)
    for ts in sorted(grouped):
        et = datetime.fromisoformat(ts)
        if busy_until and et <= busy_until:
            continue
        candidates = sorted(grouped[ts], key=lambda x: (-x["quality"], x["symbol"]))
        chosen = next((x for x in candidates if counts[x["date"]] < MAX_DAILY_TRADES), None)
        if not chosen:
            continue
        out.append(chosen); counts[chosen["date"]] += 1
        busy_until = datetime.fromisoformat(chosen["exit_ts"])
    return out


def summarize(trades, days):
    pls = [float(t["pl_dollars"]) for t in trades]
    wins, losses = [x for x in pls if x > 0], [x for x in pls if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    equity = peak = STARTING_BALANCE; dd = 0.0
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        equity += t["pl_dollars"]; peak = max(peak, equity); dd = max(dd, peak - equity)
    net, n_days = sum(pls), max(1, len(days))
    return {
        "trades": len(trades), "trading_days": len(days), "trades_per_day": round(len(trades)/n_days, 2),
        "wins": len(wins), "losses": len(losses), "win_rate_pct": round(100*len(wins)/len(pls), 2) if pls else 0.0,
        "net_pl_dollars": round(net, 2), "avg_daily_pl_dollars": round(net/n_days, 2),
        "ending_balance_dollars": round(STARTING_BALANCE+net, 2), "return_pct": round(100*net/STARTING_BALANCE, 2),
        "profit_factor": round(gp/gl, 3) if gl > 0 else None, "expectancy_dollars": round(net/len(pls), 2) if pls else 0.0,
        "max_drawdown_dollars": round(dd, 2),
    }


def slice_dates(trades, days):
    days = set(days)
    return [t for t in trades if datetime.fromisoformat(t["date"]).date() in days]


def main():
    sessions_by_symbol = {}
    for symbol in p10.SYMBOLS:
        print(f"fetch {symbol}")
        sessions_by_symbol[symbol] = p10.parse_sessions(p10.fetch_bars(symbol))
    reference_days = sorted(sessions_by_symbol["SPY"].keys())
    if len(reference_days) < 60: raise RuntimeError("Not enough sessions")
    cut = int(len(reference_days)*0.70)
    dev_days, holdout_days = reference_days[:cut], reference_days[cut:]
    mid = len(dev_days)//2; a_days, b_days = dev_days[:mid], dev_days[mid:]

    results, streams, robust = {}, {}, []
    for cfg in VARIANTS:
        stream = causal_stream(generate(cfg, sessions_by_symbol)); streams[cfg["name"]] = stream
        a = summarize(slice_dates(stream,a_days),a_days); b = summarize(slice_dates(stream,b_days),b_days)
        dev = summarize(slice_dates(stream,dev_days),dev_days); h = summarize(slice_dates(stream,holdout_days),holdout_days)
        full = summarize(stream,reference_days)
        item={"variant":cfg,"development_fold_a":a,"development_fold_b":b,"development":dev,"holdout":h,"full":full}
        results[cfg["name"]]=item
        if a["trades"]>=12 and b["trades"]>=12 and a["net_pl_dollars"]>0 and b["net_pl_dollars"]>0 and (a["profit_factor"] or 0)>=1.10 and (b["profit_factor"] or 0)>=1.10:
            robust.append(item)
    selected=max(robust,key=lambda x:(min(x["development_fold_a"]["profit_factor"] or 0,x["development_fold_b"]["profit_factor"] or 0),x["development"]["net_pl_dollars"],-x["development"]["max_drawdown_dollars"])) if robust else None
    advance=False
    if selected:
        h=selected["holdout"]
        advance=h["trades"]>=10 and h["net_pl_dollars"]>0 and (h["profit_factor"] or 0)>=1.20 and 0.25<=h["trades_per_day"]<=3.0
    result={"strategy":"pattern13_failed_opening_range_breakout","generated_utc":datetime.now(timezone.utc).isoformat(),"order_submission_enabled":False,"reference_sessions":len(reference_days),"holdout_start":holdout_days[0].isoformat(),"symbols":p10.SYMBOLS,"max_daily_trades":MAX_DAILY_TRADES,"one_position_at_a_time":True,"variants_predeclared":VARIANTS,"results":results,"selected_development_candidate":selected,"advance_to_actual_options":advance,"selected_trade_stream":streams[selected["variant"]["name"]] if selected else []}
    Path(RESULT_JSON).write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    lines=["# MarketPulse — Pattern #13 Failed Opening-Range Breakout","","**Research only. No orders. Sweep outside first 30-minute range, fail back inside, next-bar reversal entry.**","",f"Sessions: **{len(reference_days)}** | Holdout starts: **{holdout_days[0]}** | Max entries/day: **{MAX_DAILY_TRADES}**",f"Selected on development only: **{selected['variant']['name'] if selected else 'NONE'}**",""]
    for cfg in VARIANTS:
        f=results[cfg["name"]]["full"]; h=results[cfg["name"]]["holdout"]
        lines += [f"## {cfg['name']}",f"- Full: **{f['trades']} trades | {f['trades_per_day']}/day | ${f['net_pl_dollars']:.2f} | PF {f['profit_factor']} | DD ${f['max_drawdown_dollars']:.2f}**",f"- Holdout: **{h['trades']} trades | {h['trades_per_day']}/day | ${h['net_pl_dollars']:.2f} | PF {h['profit_factor']}**",""]
    lines += [f"**Advance to actual option-contract simulation: {'YES' if advance else 'NO'}**","","Underlying P/L is risk-normalized at $12.50 per 1R and is not option-premium P/L."]
    Path(RESULT_MD).write_text("\n".join(lines)+"\n")

if __name__=="__main__": main()
