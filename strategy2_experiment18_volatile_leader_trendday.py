import json
import math

import numpy as np
import pandas as pd

import strategy2_experiment17b_volatile_5m_pattern_scan as base

EXPERIMENT = "S2-E18-VOLATILE-LEADER-TRENDDAY"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_FOLLOWUP = True

# Reuse the already-declared high-volatility pool and dynamic prior-ATR selector.
CANDIDATES = list(base.CANDIDATES)
BENCHMARK = "QQQ"
BLOCKS = dict(base.BLOCKS)
TOP_VOL_NAMES = base.TOP_VOL_NAMES

# Locked before results. The opening move must already look like a trend-day leader.
OPEN_END = "09:55"
MIN_FIRST30_RETURN = 0.015
MIN_FIRST30_RELATIVE_STRENGTH = 0.0075
MIN_FIRST30_RELVOL = 1.50
MIN_CLOSE_LOCATION = 0.80
MIN_RANGE_ATR = 0.40

# Risk/execution remains conservative despite the aggressive-return research target.
START_EQ = base.START_EQ
RISK_PER_TRADE = 0.01
MAX_NOTIONAL_PCT = 1.00
COST_BPS_PER_FILL = 10.0
MIN_STOP_PCT = 0.0075
MAX_STOP_PCT = 0.030
STOP_ATR_FRACTION = 0.40
BREAKEVEN_R = 1.0
TRAIL_START_R = 1.5
TRAIL_LOOKBACK_BARS = 4   # 20-minute trailing low after trend proves itself.
FORCE_EXIT = base.FORCE_EXIT


def opening_stats(panel):
    rows = []
    for day, g in panel.groupby(panel.index.date):
        f = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(OPEN_END).time())]
        if len(f) < 6:
            continue
        rows.append({
            "date": day,
            "first30_volume": float(f["v"].sum()),
        })
    if not rows:
        return pd.DataFrame()
    x = pd.DataFrame(rows).set_index("date").sort_index()
    x["prior20_first30_median_volume"] = x["first30_volume"].rolling(20).median().shift(1)
    return x


def candidate_signal(sym, day, panels, dstats, ostats):
    g = base.day_slice(panels[sym], day)
    q = base.day_slice(panels[BENCHMARK], day)
    hist = dstats[sym][dstats[sym].index < day]
    if g.empty or q.empty or hist.empty or day not in ostats[sym].index:
        return None
    atr = float(hist.iloc[-1]["atr20"])
    if not np.isfinite(atr) or atr <= 0:
        return None

    first = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(OPEN_END).time())]
    qfirst = q[(q.index.time >= pd.Timestamp("09:30").time()) & (q.index.time <= pd.Timestamp(OPEN_END).time())]
    if len(first) < 6 or len(qfirst) < 6:
        return None

    op = float(first.iloc[0]["o"])
    cl = float(first.iloc[-1]["c"])
    hi = float(first["h"].max())
    lo = float(first["l"].min())
    ret = cl / op - 1.0
    qret = float(qfirst.iloc[-1]["c"] / qfirst.iloc[0]["o"] - 1.0)
    rs = ret - qret
    rng = hi - lo
    close_location = (cl - lo) / rng if rng > 0 else 0.0
    range_atr = rng / atr
    prior_med_vol = float(ostats[sym].loc[day, "prior20_first30_median_volume"])
    relvol = float(first["v"].sum()) / prior_med_vol if np.isfinite(prior_med_vol) and prior_med_vol > 0 else 0.0

    # Session VWAP known only through 09:55.
    px = first["vw"].astype(float) if "vw" in first else first["c"].astype(float)
    vol = first["v"].astype(float)
    vwap = float((px * vol).sum() / vol.sum()) if vol.sum() > 0 else cl

    if ret < MIN_FIRST30_RETURN:
        return None
    if rs < MIN_FIRST30_RELATIVE_STRENGTH:
        return None
    if relvol < MIN_FIRST30_RELVOL:
        return None
    if close_location < MIN_CLOSE_LOCATION:
        return None
    if range_atr < MIN_RANGE_ATR:
        return None
    if cl < vwap:
        return None

    signal_ts = first.index[-1]
    score = (ret * 100.0) + (rs * 100.0) + math.log(max(relvol, 1e-9)) + close_location + range_atr
    return {
        "symbol": sym,
        "day": str(day),
        "signal_ts": signal_ts,
        "atr": atr,
        "opening_low": lo,
        "first30_return_pct": ret * 100.0,
        "relative_strength_pct": rs * 100.0,
        "first30_relvol": relvol,
        "close_location": close_location,
        "range_atr": range_atr,
        "score": score,
    }


def simulate(sig, g, equity):
    future = g[g.index > sig["signal_ts"]]
    if future.empty:
        return None
    entry_ts = future.index[0]
    raw_entry = float(future.iloc[0]["o"])
    entry = raw_entry * (1 + COST_BPS_PER_FILL / 10000.0)

    recent = g[(g.index <= sig["signal_ts"])].tail(3)
    recent_low = float(recent["l"].min()) if not recent.empty else sig["opening_low"]
    atr_stop = raw_entry - STOP_ATR_FRACTION * sig["atr"]
    structure = max(recent_low, atr_stop)
    stop_pct = min(MAX_STOP_PCT, max(MIN_STOP_PCT, (raw_entry - structure) / raw_entry))
    stop = raw_entry * (1 - stop_pct)
    risk_share = entry - stop
    if risk_share <= 0:
        return None

    notional = min(equity * MAX_NOTIONAL_PCT, equity * RISK_PER_TRADE / (risk_share / entry))
    if notional <= 0:
        return None
    shares = notional / entry
    active_stop = stop
    peak = entry
    closes = []
    exit_px = exit_ts = reason = None

    for ts, r in future.iterrows():
        if ts.time() >= FORCE_EXIT:
            exit_px, exit_ts, reason = float(r["c"]), ts, "FORCE_CLOSE"
            break
        lo, hi, close = float(r["l"]), float(r["h"]), float(r["c"])
        if lo <= active_stop:
            exit_px, exit_ts, reason = active_stop, ts, "STOP"
            break
        peak = max(peak, hi)
        peak_r = (peak - entry) / risk_share
        closes.append(close)
        if peak_r >= BREAKEVEN_R:
            active_stop = max(active_stop, entry)
        if peak_r >= TRAIL_START_R and len(closes) >= TRAIL_LOOKBACK_BARS:
            trailing_window = future.loc[:ts].tail(TRAIL_LOOKBACK_BARS)
            active_stop = max(active_stop, float(trailing_window["l"].min()))

    if exit_px is None:
        exit_px, exit_ts, reason = float(future.iloc[-1]["c"]), future.index[-1], "DATA_END"
    exit_fill = exit_px * (1 - COST_BPS_PER_FILL / 10000.0)
    pnl = shares * (exit_fill - entry)
    account_return_pct = pnl / equity * 100.0 if equity > 0 else 0.0
    return {
        **{k: v for k, v in sig.items() if k != "signal_ts"},
        "signal_time": sig["signal_ts"].isoformat(),
        "entry_time": entry_ts.isoformat(),
        "exit_time": exit_ts.isoformat(),
        "entry": entry,
        "exit": exit_fill,
        "notional": notional,
        "stop_pct": stop_pct,
        "pnl": pnl,
        "r_multiple": pnl / (shares * risk_share),
        "account_return_pct": account_return_pct,
        "exit_reason": reason,
    }


def summarize(trades, start, end):
    eq = START_EQ
    curve = [eq]
    gp = gl = 0.0
    wins = 0
    daily_returns = []
    rs = []
    for t in sorted(trades, key=lambda x: x["entry_time"]):
        pnl = float(t["pnl"])
        prev = eq
        eq += pnl
        curve.append(eq)
        daily_returns.append(pnl / prev * 100.0)
        rs.append(float(t["r_multiple"]))
        if pnl > 0:
            wins += 1; gp += pnl
        elif pnl < 0:
            gl += -pnl
    s = pd.Series(curve, dtype=float)
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) else 0.0
    business_days = max(1, len(pd.bdate_range(start, end)))
    return {
        "trades": len(trades),
        "wins": wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "ending_equity": eq,
        "return_pct": (eq / START_EQ - 1) * 100.0,
        "profit_factor": gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0),
        "avg_trade_pnl": float(np.mean([t["pnl"] for t in trades])) if trades else 0.0,
        "avg_r": float(np.mean(rs)) if rs else 0.0,
        "max_drawdown_pct": dd,
        "avg_return_per_business_day_pct": ((eq / START_EQ - 1) * 100.0) / business_days,
        "avg_return_on_trade_days_pct": float(np.mean(daily_returns)) if daily_returns else 0.0,
        "median_return_on_trade_days_pct": float(np.median(daily_returns)) if daily_returns else 0.0,
        "days_ge_1pct": int(sum(x >= 1.0 for x in daily_returns)),
        "days_ge_3pct": int(sum(x >= 3.0 for x in daily_returns)),
        "days_ge_5pct": int(sum(x >= 5.0 for x in daily_returns)),
        "best_trade_return_pct": max(daily_returns, default=0.0),
        "worst_trade_return_pct": min(daily_returns, default=0.0),
    }


def evaluate(start, end, panels, dstats, ostats):
    trades = []
    days = [d for d in dstats["SPY"].index if pd.Timestamp(start).date() <= d <= pd.Timestamp(end).date()]
    for day in days:
        signals = []
        for sym in base.selected_names(day, dstats):
            sig = candidate_signal(sym, day, panels, dstats, ostats)
            if sig is not None:
                signals.append(sig)
        if not signals:
            continue
        # Highest opening-strength score, based only on data through 09:55.
        sig = max(signals, key=lambda x: (x["score"], x["symbol"]))
        g = base.day_slice(panels[sig["symbol"]], day)
        equity = START_EQ + sum(float(t["pnl"]) for t in trades)
        tr = simulate(sig, g, equity)
        if tr:
            trades.append(tr)
    return trades, summarize(trades, start, end)


def main():
    panels = base.load_panels()
    dstats = {s: base.daily_stats(panels[s]) for s in base.CANDIDATES + base.BENCHMARKS}
    ostats = {s: opening_stats(panels[s]) for s in CANDIDATES}
    blocks = {}
    for name, (a, b) in BLOCKS.items():
        trades, stats = evaluate(a, b, panels, dstats, ostats)
        blocks[name] = {"stats": stats, "trades": trades}

    result = {
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "nonblind_followup": True,
        "candidate_pool": CANDIDATES,
        "dynamic_top_atr_names": TOP_VOL_NAMES,
        "locked_signal": {
            "min_first30_return_pct": MIN_FIRST30_RETURN * 100,
            "min_first30_relative_strength_pct": MIN_FIRST30_RELATIVE_STRENGTH * 100,
            "min_first30_relvol": MIN_FIRST30_RELVOL,
            "min_close_location": MIN_CLOSE_LOCATION,
            "min_range_atr": MIN_RANGE_ATR,
        },
        "execution": {
            "risk_per_trade_pct": RISK_PER_TRADE * 100,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100,
            "cost_bps_per_fill": COST_BPS_PER_FILL,
            "breakeven_r": BREAKEVEN_R,
            "trail_start_r": TRAIL_START_R,
            "trail_lookback_minutes": TRAIL_LOOKBACK_BARS * 5,
        },
        "blocks": blocks,
        "activate": False,
    }
    with open("strategy2_experiment18_volatile_leader_trendday_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = ["# Experiment 18 — Volatile Leader Trend-Day", "", "Research-only, nonblind follow-up. Uses only first-30-minute information to select one long leader, then trails the move.", ""]
    for name in BLOCKS:
        s = blocks[name]["stats"]
        lines.append(f"- {name}: {s['trades']} trades | win {s['win_rate_pct']:.1f}% | PF {s['profit_factor']:.2f} | return {s['return_pct']:+.2f}% | DD {s['max_drawdown_pct']:.2f}% | avg trade-day {s['avg_return_on_trade_days_pct']:+.2f}% | >=1% days {s['days_ge_1pct']} | >=3% days {s['days_ge_3pct']}")
    lines += ["", "Activation OFF. A favorable historical result would still require forward shadow testing before any aggressive sizing."]
    with open("strategy2_experiment18_volatile_leader_trendday_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
