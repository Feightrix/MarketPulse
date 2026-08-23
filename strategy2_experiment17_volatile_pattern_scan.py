import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

EXPERIMENT = "S2-E17-2026-VOLATILE-PATTERN-SCAN"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
START_EQ = 2500.0
FEED = "sip"
ET = ZoneInfo("America/New_York")
DATA_BASE = "https://data.alpaca.markets"

# High-volatility, high-liquidity candidate pool selected before results.
CANDIDATES = ["TSLA", "COIN", "MSTR", "HOOD", "PLTR", "SMCI", "AMD", "NVDA", "RIVN", "IONQ", "RKLB", "SOFI"]
BENCHMARKS = ["SPY", "QQQ"]
WARM_START = "2025-11-15"
END = "2026-08-21"
BLOCKS = {
    "discovery_jan_apr": ("2026-01-02", "2026-04-30"),
    "validation_may_jun": ("2026-05-01", "2026-06-30"),
    "holdout_jul_aug21": ("2026-07-01", "2026-08-21"),
}

# Volatility selection uses only information available before each session.
ATR_DAYS = 20
MIN_HISTORY_DAYS = 20
TOP_VOL_NAMES = 5
MIN_AVG_DOLLAR_VOLUME = 100_000_000.0

# Shared execution/risk protocol for fair pattern comparison.
RISK_PER_TRADE = 0.01
MAX_NOTIONAL_PCT = 1.00
COST_BPS_PER_FILL = 10.0
MIN_STOP_PCT = 0.006
MAX_STOP_PCT = 0.025
STOP_ATR_FRACTION = 0.35
TARGET_R = 2.5
BREAKEVEN_R = 1.0
TRAIL_START_R = 1.5
TRAIL_DISTANCE_R = 0.75
FORCE_EXIT = dtime(15, 55)

PATTERNS = [
    "opening_range_breakout",
    "first_pullback_rebreak",
    "vwap_reclaim",
    "midday_compression_breakout",
    "power_hour_breakout",
]


def headers():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca credentials required")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_json(url, params, tries=8):
    for attempt in range(tries):
        r = requests.get(url, headers=headers(), params=params, timeout=90)
        if r.status_code == 429:
            time.sleep(2 + 2 * attempt)
            continue
        if r.status_code == 403:
            raise PermissionError(r.text[:500])
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Repeated rate limit: {url}")


def fetch_symbol_bars(symbol):
    rows, token = [], None
    params = {
        "timeframe": "1Min",
        "start": f"{WARM_START}T13:00:00Z",
        "end": f"{END}T21:15:00Z",
        "limit": 10000,
        "adjustment": "all",
        "feed": FEED,
        "sort": "asc",
    }
    while True:
        p = dict(params)
        if token:
            p["page_token"] = token
        payload = get_json(f"{DATA_BASE}/v2/stocks/{symbol}/bars", p)
        rows.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    df = df.set_index("ts").sort_index()
    return df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))].copy()


def load_panels():
    symbols = CANDIDATES + BENCHMARKS
    out = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_symbol_bars, s): s for s in symbols}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


def daily_stats(df):
    rows = []
    for day, g in df.groupby(df.index.date):
        if len(g) < 300:
            continue
        rows.append({
            "date": day,
            "o": float(g.iloc[0]["o"]),
            "h": float(g["h"].max()),
            "l": float(g["l"].min()),
            "c": float(g.iloc[-1]["c"]),
            "v": float(g["v"].sum()),
        })
    if not rows:
        return pd.DataFrame()
    x = pd.DataFrame(rows).set_index("date").sort_index()
    prev = x["c"].shift(1)
    tr = pd.concat([(x["h"] - x["l"]), (x["h"] - prev).abs(), (x["l"] - prev).abs()], axis=1).max(axis=1)
    x["tr"] = tr
    x["atr20"] = tr.rolling(ATR_DAYS).mean()
    x["atr_pct"] = x["atr20"] / prev
    x["dollar_volume"] = x["c"] * x["v"]
    x["adv20"] = x["dollar_volume"].rolling(ATR_DAYS).mean()
    return x


def day_slice(df, day):
    return df[df.index.date == day].copy()


def minute_vwap(g):
    px = g["vw"].astype(float) if "vw" in g.columns else g["c"].astype(float)
    vol = g["v"].astype(float)
    den = vol.cumsum().replace(0, np.nan)
    return (px * vol).cumsum() / den


def selected_names(day, dstats):
    rows = []
    for sym in CANDIDATES:
        st = dstats[sym]
        hist = st[st.index < day]
        if len(hist) < MIN_HISTORY_DAYS:
            continue
        r = hist.iloc[-1]
        if not np.isfinite(r["atr_pct"]) or not np.isfinite(r["adv20"]):
            continue
        if float(r["adv20"]) < MIN_AVG_DOLLAR_VOLUME:
            continue
        rows.append((float(r["atr_pct"]), sym))
    rows.sort(reverse=True)
    return [s for _, s in rows[:TOP_VOL_NAMES]]


def base_features(g, atr):
    x = g.copy()
    x["vwap"] = minute_vwap(x)
    x["ret5"] = x["c"].pct_change(5)
    x["ret15"] = x["c"].pct_change(15)
    x["vol_med20"] = x["v"].rolling(20, min_periods=10).median()
    x["relvol"] = x["v"] / x["vol_med20"].replace(0, np.nan)
    x["range20"] = x["h"].rolling(20).max() - x["l"].rolling(20).min()
    x["atr_frac_range20"] = x["range20"] / atr if atr > 0 else np.nan
    return x


def signal_opening_range_breakout(x):
    orb = x[(x.index.time >= dtime(9,30)) & (x.index.time <= dtime(9,44))]
    scan = x[(x.index.time >= dtime(9,45)) & (x.index.time <= dtime(10,30))]
    if len(orb) < 15 or scan.empty:
        return None
    hi = float(orb["h"].max())
    for i in range(1, len(scan)):
        r = scan.iloc[i]
        prev = scan.iloc[i-1]
        if float(r["c"]) > hi and float(prev["c"]) <= hi and float(r["c"]) > float(r["vwap"]) and float(r["relvol"]) >= 1.4 and float(r["ret5"]) > 0:
            return scan.index[i], hi
    return None


def signal_first_pullback_rebreak(x):
    impulse = x[(x.index.time >= dtime(9,30)) & (x.index.time <= dtime(9,59))]
    if len(impulse) < 25:
        return None
    op = float(impulse.iloc[0]["o"])
    hi = float(impulse["h"].max())
    move = hi / op - 1.0
    if move < 0.012:
        return None
    hi_time = impulse["h"].idxmax()
    after = x[(x.index > hi_time) & (x.index.time <= dtime(11,0))]
    if len(after) < 5:
        return None
    pull_low = float(after["l"].cummin().iloc[-1])
    retrace = (hi - pull_low) / (hi - op) if hi > op else 1.0
    if retrace < 0.20 or retrace > 0.60 or pull_low <= op:
        return None
    for i in range(2, len(after)):
        r = after.iloc[i]
        if float(r["c"]) > hi and float(r["c"]) > float(r["vwap"]) and float(r["relvol"]) >= 1.2:
            return after.index[i], pull_low
    return None


def signal_vwap_reclaim(x):
    scan = x[(x.index.time >= dtime(10,0)) & (x.index.time <= dtime(14,30))]
    if len(scan) < 30:
        return None
    below_run = 0
    day_open = float(x.iloc[0]["o"])
    for i in range(2, len(scan)):
        r = scan.iloc[i]
        p = scan.iloc[i-1]
        if float(p["c"]) < float(p["vwap"]):
            below_run += 1
        else:
            below_run = 0
        if below_run >= 8 and float(r["c"]) > float(r["vwap"]) and float(r["c"]) > float(p["h"]) and float(r["c"]) > day_open and float(r["ret5"]) >= 0.004 and float(r["relvol"]) >= 1.2:
            structure = float(scan.iloc[max(0, i-10):i+1]["l"].min())
            return scan.index[i], structure
    return None


def signal_midday_compression_breakout(x):
    scan = x[(x.index.time >= dtime(11,0)) & (x.index.time <= dtime(14,30))]
    if len(scan) < 25:
        return None
    for i in range(21, len(scan)):
        r = scan.iloc[i]
        prev20 = scan.iloc[i-20:i]
        hi = float(prev20["h"].max())
        if float(prev20["h"].max() - prev20["l"].min()) <= 0:
            continue
        compressed = float(scan.iloc[i-1]["atr_frac_range20"]) <= 0.30
        if compressed and float(r["c"]) > hi and float(r["c"]) > float(r["vwap"]) and float(r["relvol"]) >= 1.5 and float(r["ret5"]) > 0:
            return scan.index[i], float(prev20["l"].min())
    return None


def signal_power_hour_breakout(x):
    base = x[(x.index.time >= dtime(13,30)) & (x.index.time < dtime(15,0))]
    scan = x[(x.index.time >= dtime(15,0)) & (x.index.time <= dtime(15,35))]
    if len(base) < 60 or scan.empty:
        return None
    hi = float(base["h"].max())
    day_open = float(x.iloc[0]["o"])
    for i in range(len(scan)):
        r = scan.iloc[i]
        if float(r["c"]) > hi and float(r["c"]) > float(r["vwap"]) and float(r["c"]) > day_open * 1.005 and float(r["relvol"]) >= 1.2:
            return scan.index[i], float(base["l"].tail(30).min())
    return None


def find_signal(pattern, g, atr):
    x = base_features(g, atr)
    fn = {
        "opening_range_breakout": signal_opening_range_breakout,
        "first_pullback_rebreak": signal_first_pullback_rebreak,
        "vwap_reclaim": signal_vwap_reclaim,
        "midday_compression_breakout": signal_midday_compression_breakout,
        "power_hour_breakout": signal_power_hour_breakout,
    }[pattern]
    return fn(x)


def simulate_trade(g, signal_ts, structure_stop, atr, equity, symbol, pattern):
    future = g[g.index > signal_ts]
    if future.empty:
        return None
    entry_bar = future.iloc[0]
    entry_ts = future.index[0]
    if entry_ts.time() >= FORCE_EXIT:
        return None
    raw_entry = float(entry_bar["o"])
    entry = raw_entry * (1.0 + COST_BPS_PER_FILL / 10000.0)
    atr_stop = raw_entry - STOP_ATR_FRACTION * atr
    stop_raw = max(float(structure_stop), atr_stop)
    stop_pct = (raw_entry - stop_raw) / raw_entry
    stop_pct = min(MAX_STOP_PCT, max(MIN_STOP_PCT, stop_pct))
    stop = raw_entry * (1.0 - stop_pct)
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return None
    notional_by_risk = equity * RISK_PER_TRADE / (risk_per_share / entry)
    notional = min(equity * MAX_NOTIONAL_PCT, notional_by_risk)
    if notional <= 0:
        return None
    shares = notional / entry
    rdist = risk_per_share
    target = entry + TARGET_R * rdist
    active_stop = stop
    peak = entry
    exit_px = None
    exit_ts = None
    reason = None

    for ts, r in future.iterrows():
        if ts.time() >= FORCE_EXIT:
            exit_px = float(r["c"])
            exit_ts = ts
            reason = "FORCE_CLOSE"
            break
        lo = float(r["l"])
        hi = float(r["h"])
        # Conservative ordering when both could occur inside one minute.
        if lo <= active_stop:
            exit_px = active_stop
            exit_ts = ts
            reason = "STOP"
            break
        if hi >= target:
            exit_px = target
            exit_ts = ts
            reason = "TARGET"
            break
        peak = max(peak, hi)
        peak_r = (peak - entry) / rdist
        if peak_r >= BREAKEVEN_R:
            active_stop = max(active_stop, entry)
        if peak_r >= TRAIL_START_R:
            active_stop = max(active_stop, peak - TRAIL_DISTANCE_R * rdist)
    if exit_px is None:
        last = future.iloc[-1]
        exit_px = float(last["c"])
        exit_ts = future.index[-1]
        reason = "DATA_END"
    exit_fill = exit_px * (1.0 - COST_BPS_PER_FILL / 10000.0)
    pnl = shares * (exit_fill - entry)
    r_mult = pnl / (shares * rdist) if shares * rdist > 0 else 0.0
    return {
        "symbol": symbol,
        "pattern": pattern,
        "signal_time": signal_ts.isoformat(),
        "entry_time": entry_ts.isoformat(),
        "exit_time": exit_ts.isoformat(),
        "entry": entry,
        "exit": exit_fill,
        "stop_pct": stop_pct,
        "notional": notional,
        "pnl": pnl,
        "r_multiple": r_mult,
        "exit_reason": reason,
    }


def summarize(trades, start, end):
    eq = START_EQ
    curve = [eq]
    gross_profit = gross_loss = 0.0
    daily = {}
    rs = []
    wins = 0
    for t in sorted(trades, key=lambda z: z["entry_time"]):
        pnl = float(t["pnl"])
        eq += pnl
        curve.append(eq)
        d = str(t["entry_time"])[:10]
        daily[d] = daily.get(d, 0.0) + pnl
        rs.append(float(t["r_multiple"]))
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += -pnl
    s = pd.Series(curve, dtype=float)
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) else 0.0
    business_days = max(1, len(pd.bdate_range(start, end)))
    daily_rets = [p / START_EQ * 100.0 for p in daily.values()]
    return {
        "trades": len(trades),
        "wins": wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "ending_equity": eq,
        "return_pct": (eq / START_EQ - 1.0) * 100.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "avg_pnl": float(np.mean([t["pnl"] for t in trades])) if trades else 0.0,
        "avg_r": float(np.mean(rs)) if rs else 0.0,
        "max_drawdown_pct": dd,
        "trade_days": len(daily),
        "trades_per_business_day": len(trades) / business_days,
        "avg_return_per_business_day_pct": ((eq - START_EQ) / START_EQ * 100.0) / business_days,
        "avg_return_on_trade_days_pct": float(np.mean(daily_rets)) if daily_rets else 0.0,
        "median_return_on_trade_days_pct": float(np.median(daily_rets)) if daily_rets else 0.0,
        "days_ge_1pct": int(sum(x >= 1.0 for x in daily_rets)),
        "days_ge_3pct": int(sum(x >= 3.0 for x in daily_rets)),
        "best_trade": max([t["pnl"] for t in trades], default=0.0),
        "worst_trade": min([t["pnl"] for t in trades], default=0.0),
    }


def block_days(start, end, spy_stats):
    return [d for d in spy_stats.index if pd.Timestamp(start).date() <= d <= pd.Timestamp(end).date()]


def evaluate_pattern(pattern, start, end, panels, dstats):
    trades = []
    for day in block_days(start, end, dstats["SPY"]):
        names = selected_names(day, dstats)
        signals = []
        for sym in names:
            g = day_slice(panels[sym], day)
            hist = dstats[sym][dstats[sym].index < day]
            if g.empty or hist.empty:
                continue
            atr = float(hist.iloc[-1]["atr20"])
            if not np.isfinite(atr) or atr <= 0:
                continue
            sig = find_signal(pattern, g, atr)
            if sig is not None:
                ts, structure = sig
                signals.append((ts, sym, structure, atr, g))
        if not signals:
            continue
        # One best/earliest setup per day; ties resolved by symbol to avoid outcome-based selection.
        signals.sort(key=lambda z: (z[0], z[1]))
        ts, sym, structure, atr, g = signals[0]
        equity = START_EQ + sum(float(t["pnl"]) for t in trades)
        tr = simulate_trade(g, ts, structure, atr, equity, sym, pattern)
        if tr is not None:
            trades.append(tr)
    return trades, summarize(trades, start, end)


def main():
    panels = load_panels()
    missing = [s for s in CANDIDATES + BENCHMARKS if s not in panels or panels[s].empty]
    if missing:
        raise RuntimeError(f"Missing SIP minute data: {missing}")
    dstats = {s: daily_stats(panels[s]) for s in CANDIDATES + BENCHMARKS}

    results = {"patterns": {}}
    for pattern in PATTERNS:
        results["patterns"][pattern] = {}
        for block, (start, end) in BLOCKS.items():
            trades, stats = evaluate_pattern(pattern, start, end, panels, dstats)
            results["patterns"][pattern][block] = {"stats": stats, "trades": trades}

    # Rank only on discovery; validation and holdout are not used to choose the winner.
    def discovery_score(item):
        p, blocks = item
        s = blocks["discovery_jan_apr"]["stats"]
        if s["trades"] < 15:
            return (-999.0, -999.0, -999.0)
        return (s["profit_factor"], s["avg_r"], s["return_pct"])

    ranked = sorted(results["patterns"].items(), key=discovery_score, reverse=True)
    selected = ranked[0][0] if ranked else None
    results.update({
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "feed": FEED,
        "candidate_pool": CANDIDATES,
        "dynamic_selection": {"top_names": TOP_VOL_NAMES, "atr_days": ATR_DAYS, "min_adv": MIN_AVG_DOLLAR_VOLUME},
        "blocks": BLOCKS,
        "execution": {
            "risk_per_trade_pct": RISK_PER_TRADE * 100,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100,
            "cost_bps_per_fill": COST_BPS_PER_FILL,
            "target_r": TARGET_R,
            "breakeven_r": BREAKEVEN_R,
            "trail_start_r": TRAIL_START_R,
            "trail_distance_r": TRAIL_DISTANCE_R,
            "one_trade_per_day": True,
        },
        "discovery_rank": [p for p, _ in ranked],
        "selected_on_discovery_only": selected,
        "activate": False,
    })

    with open("strategy2_experiment17_volatile_pattern_scan_results.json", "w") as f:
        json.dump(results, f, indent=2)

    lines = [
        "# Experiment 17 — 2026 Volatile Pattern Scan",
        "",
        "Research-only chart-pattern discovery on SIP 1-minute data. Candidate names are dynamically ranked each day by prior 20-day ATR%, subject to liquidity.",
        "",
        f"**Discovery-selected pattern: {selected}**",
        "",
        "| Pattern | Discovery PF | Discovery return | Validation PF | Validation return | Holdout PF | Holdout return |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in PATTERNS:
        b = results["patterns"][p]
        d = b["discovery_jan_apr"]["stats"]
        v = b["validation_may_jun"]["stats"]
        h = b["holdout_jul_aug21"]["stats"]
        lines.append(f"| {p} | {d['profit_factor']:.2f} | {d['return_pct']:+.2f}% | {v['profit_factor']:.2f} | {v['return_pct']:+.2f}% | {h['profit_factor']:.2f} | {h['return_pct']:+.2f}% |")
    if selected:
        lines += ["", "## Selected pattern detail"]
        for block in BLOCKS:
            s = results["patterns"][selected][block]["stats"]
            lines.append(f"- {block}: {s['trades']} trades, win {s['win_rate_pct']:.1f}%, PF {s['profit_factor']:.2f}, return {s['return_pct']:+.2f}%, DD {s['max_drawdown_pct']:.2f}%, avg R {s['avg_r']:+.2f}")
    lines += ["", "Activation remains OFF. Aggressive sizing is not tested unless the pattern survives validation and holdout."]
    with open("strategy2_experiment17_volatile_pattern_scan_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
