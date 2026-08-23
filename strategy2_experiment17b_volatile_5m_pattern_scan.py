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

EXPERIMENT = "S2-E17B-2026-VOLATILE-5M-PATTERN-SCAN"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
START_EQ = 2500.0
FEED = "sip"
ET = ZoneInfo("America/New_York")
DATA_BASE = "https://data.alpaca.markets"

CANDIDATES = ["TSLA", "COIN", "MSTR", "HOOD", "PLTR", "SMCI", "AMD", "NVDA", "RIVN", "IONQ", "RKLB", "SOFI"]
BENCHMARKS = ["SPY", "QQQ"]
WARM_START = "2025-11-15"
END = "2026-08-21"
BLOCKS = {
    "discovery_jan_apr": ("2026-01-02", "2026-04-30"),
    "validation_may_jun": ("2026-05-01", "2026-06-30"),
    "holdout_jul_aug21": ("2026-07-01", "2026-08-21"),
}

ATR_DAYS = 20
TOP_VOL_NAMES = 5
MIN_AVG_DOLLAR_VOLUME = 100_000_000.0
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
            time.sleep(1.5 + attempt * 1.5)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Repeated rate limit: {url}")


def fetch_symbol_bars(symbol):
    rows, token = [], None
    base = {
        "timeframe": "5Min",
        "start": f"{WARM_START}T13:00:00Z",
        "end": f"{END}T21:15:00Z",
        "limit": 10000,
        "adjustment": "all",
        "feed": FEED,
        "sort": "asc",
    }
    while True:
        p = dict(base)
        if token:
            p["page_token"] = token
        payload = get_json(f"{DATA_BASE}/v2/stocks/{symbol}/bars", p)
        rows.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    x = pd.DataFrame(rows)
    x["ts"] = pd.to_datetime(x["t"], utc=True).dt.tz_convert(ET)
    x = x.set_index("ts").sort_index()
    return x[(x.index.time >= dtime(9,30)) & (x.index.time <= dtime(16,0))].copy()


def load_panels():
    out = {}
    symbols = CANDIDATES + BENCHMARKS
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_symbol_bars, s): s for s in symbols}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


def daily_stats(df):
    rows = []
    for day, g in df.groupby(df.index.date):
        if len(g) < 60:
            continue
        rows.append({"date": day, "o": float(g.iloc[0]["o"]), "h": float(g["h"].max()), "l": float(g["l"].min()), "c": float(g.iloc[-1]["c"]), "v": float(g["v"].sum())})
    if not rows:
        return pd.DataFrame()
    x = pd.DataFrame(rows).set_index("date").sort_index()
    prev = x["c"].shift(1)
    tr = pd.concat([x["h"]-x["l"], (x["h"]-prev).abs(), (x["l"]-prev).abs()], axis=1).max(axis=1)
    x["atr20"] = tr.rolling(ATR_DAYS).mean()
    x["atr_pct"] = x["atr20"] / prev
    x["adv20"] = (x["c"] * x["v"]).rolling(ATR_DAYS).mean()
    return x


def selected_names(day, dstats):
    rows = []
    for sym in CANDIDATES:
        hist = dstats[sym][dstats[sym].index < day]
        if len(hist) < ATR_DAYS:
            continue
        r = hist.iloc[-1]
        if not np.isfinite(r["atr_pct"]) or not np.isfinite(r["adv20"]):
            continue
        if float(r["adv20"]) < MIN_AVG_DOLLAR_VOLUME:
            continue
        rows.append((float(r["atr_pct"]), sym))
    rows.sort(reverse=True)
    return [s for _, s in rows[:TOP_VOL_NAMES]]


def day_slice(df, day):
    return df[df.index.date == day].copy()


def add_features(g, atr):
    x = g.copy()
    px = x["vw"].astype(float) if "vw" in x else x["c"].astype(float)
    vol = x["v"].astype(float)
    x["vwap"] = (px*vol).cumsum() / vol.cumsum().replace(0, np.nan)
    x["ret1"] = x["c"].pct_change()
    x["ret3"] = x["c"].pct_change(3)
    x["vol_med12"] = x["v"].rolling(12, min_periods=6).median()
    x["relvol"] = x["v"] / x["vol_med12"].replace(0, np.nan)
    x["range4"] = x["h"].rolling(4).max() - x["l"].rolling(4).min()
    x["atr_frac_range4"] = x["range4"] / atr if atr > 0 else np.nan
    return x


def opening_range_breakout(x):
    orb = x[(x.index.time >= dtime(9,30)) & (x.index.time <= dtime(9,40))]
    scan = x[(x.index.time >= dtime(9,45)) & (x.index.time <= dtime(10,30))]
    if len(orb) < 3 or scan.empty:
        return None
    hi = float(orb["h"].max())
    for i, (_, r) in enumerate(scan.iterrows()):
        prev_close = float(scan.iloc[i-1]["c"]) if i else hi
        if float(r["c"]) > hi and prev_close <= hi and float(r["c"]) > float(r["vwap"]) and float(r["relvol"]) >= 1.4 and float(r["ret1"]) > 0:
            return scan.index[i], hi
    return None


def first_pullback_rebreak(x):
    impulse = x[(x.index.time >= dtime(9,30)) & (x.index.time <= dtime(9,55))]
    if len(impulse) < 5:
        return None
    op = float(impulse.iloc[0]["o"])
    hi = float(impulse["h"].max())
    if hi/op - 1 < 0.012:
        return None
    hi_time = impulse["h"].idxmax()
    after = x[(x.index > hi_time) & (x.index.time <= dtime(11,0))]
    running_low = math.inf
    for i, (_, r) in enumerate(after.iterrows()):
        running_low = min(running_low, float(r["l"]))
        retrace = (hi-running_low)/(hi-op) if hi>op else 1.0
        if running_low <= op or retrace > 0.60:
            return None
        if i < 1 or retrace < 0.20:
            continue
        if float(r["c"]) > hi and float(r["c"]) > float(r["vwap"]) and float(r["relvol"]) >= 1.2:
            return after.index[i], running_low
    return None


def vwap_reclaim(x):
    scan = x[(x.index.time >= dtime(10,0)) & (x.index.time <= dtime(14,30))]
    below = 0
    day_open = float(x.iloc[0]["o"])
    for i in range(1, len(scan)):
        p, r = scan.iloc[i-1], scan.iloc[i]
        below = below + 1 if float(p["c"]) < float(p["vwap"]) else 0
        if below >= 2 and float(r["c"]) > float(r["vwap"]) and float(r["c"]) > float(p["h"]) and float(r["c"]) > day_open and float(r["ret1"]) >= 0.004 and float(r["relvol"]) >= 1.2:
            lo = float(scan.iloc[max(0,i-3):i+1]["l"].min())
            return scan.index[i], lo
    return None


def midday_compression_breakout(x):
    scan = x[(x.index.time >= dtime(11,0)) & (x.index.time <= dtime(14,30))]
    for i in range(4, len(scan)):
        r = scan.iloc[i]
        prev4 = scan.iloc[i-4:i]
        hi = float(prev4["h"].max())
        compressed = float(scan.iloc[i-1]["atr_frac_range4"]) <= 0.30
        if compressed and float(r["c"]) > hi and float(r["c"]) > float(r["vwap"]) and float(r["relvol"]) >= 1.5 and float(r["ret1"]) > 0:
            return scan.index[i], float(prev4["l"].min())
    return None


def power_hour_breakout(x):
    base = x[(x.index.time >= dtime(13,30)) & (x.index.time < dtime(15,0))]
    scan = x[(x.index.time >= dtime(15,0)) & (x.index.time <= dtime(15,35))]
    if len(base) < 12:
        return None
    hi = float(base["h"].max())
    day_open = float(x.iloc[0]["o"])
    for i, (_, r) in enumerate(scan.iterrows()):
        if float(r["c"]) > hi and float(r["c"]) > float(r["vwap"]) and float(r["c"]) > day_open*1.005 and float(r["relvol"]) >= 1.2:
            return scan.index[i], float(base.tail(6)["l"].min())
    return None


def find_signal(pattern, g, atr):
    x = add_features(g, atr)
    return {
        "opening_range_breakout": opening_range_breakout,
        "first_pullback_rebreak": first_pullback_rebreak,
        "vwap_reclaim": vwap_reclaim,
        "midday_compression_breakout": midday_compression_breakout,
        "power_hour_breakout": power_hour_breakout,
    }[pattern](x)


def simulate(g, signal_ts, structure, atr, equity, symbol, pattern):
    fut = g[g.index > signal_ts]
    if fut.empty:
        return None
    ts = fut.index[0]
    raw_entry = float(fut.iloc[0]["o"])
    entry = raw_entry*(1+COST_BPS_PER_FILL/10000)
    atr_stop = raw_entry - STOP_ATR_FRACTION*atr
    stop_raw = max(float(structure), atr_stop)
    stop_pct = min(MAX_STOP_PCT, max(MIN_STOP_PCT, (raw_entry-stop_raw)/raw_entry))
    stop = raw_entry*(1-stop_pct)
    risk = entry-stop
    if risk <= 0:
        return None
    notional = min(equity*MAX_NOTIONAL_PCT, equity*RISK_PER_TRADE/(risk/entry))
    shares = notional/entry
    target = entry + TARGET_R*risk
    active_stop = stop
    peak = entry
    exit_px = exit_ts = reason = None
    for t, r in fut.iterrows():
        if t.time() >= FORCE_EXIT:
            exit_px, exit_ts, reason = float(r["c"]), t, "FORCE_CLOSE"; break
        lo, hi = float(r["l"]), float(r["h"])
        if lo <= active_stop:
            exit_px, exit_ts, reason = active_stop, t, "STOP"; break
        if hi >= target:
            exit_px, exit_ts, reason = target, t, "TARGET"; break
        peak = max(peak, hi)
        peak_r = (peak-entry)/risk
        if peak_r >= BREAKEVEN_R:
            active_stop = max(active_stop, entry)
        if peak_r >= TRAIL_START_R:
            active_stop = max(active_stop, peak-TRAIL_DISTANCE_R*risk)
    if exit_px is None:
        exit_px, exit_ts, reason = float(fut.iloc[-1]["c"]), fut.index[-1], "DATA_END"
    exit_fill = float(exit_px)*(1-COST_BPS_PER_FILL/10000)
    pnl = shares*(exit_fill-entry)
    return {"symbol":symbol,"pattern":pattern,"entry_time":ts.isoformat(),"exit_time":exit_ts.isoformat(),"pnl":pnl,"r_multiple":pnl/(shares*risk),"notional":notional,"exit_reason":reason}


def summarize(trades, start, end):
    eq=START_EQ; curve=[eq]; gp=gl=0.0; wins=0; rs=[]; daily={}
    for t in sorted(trades,key=lambda z:z["entry_time"]):
        p=float(t["pnl"]); eq+=p; curve.append(eq); rs.append(float(t["r_multiple"])); d=t["entry_time"][:10]; daily[d]=daily.get(d,0)+p
        if p>0: wins+=1; gp+=p
        elif p<0: gl+=-p
    s=pd.Series(curve,dtype=float); dd=float((1-s/s.cummax()).max()*100) if len(s) else 0
    bd=max(1,len(pd.bdate_range(start,end))); dayrets=[p/START_EQ*100 for p in daily.values()]
    return {"trades":len(trades),"wins":wins,"win_rate_pct":wins/len(trades)*100 if trades else 0,"ending_equity":eq,"return_pct":(eq/START_EQ-1)*100,"profit_factor":gp/gl if gl>0 else (999 if gp>0 else 0),"avg_pnl":float(np.mean([t["pnl"] for t in trades])) if trades else 0,"avg_r":float(np.mean(rs)) if rs else 0,"max_drawdown_pct":dd,"trades_per_business_day":len(trades)/bd,"avg_return_per_business_day_pct":((eq-START_EQ)/START_EQ*100)/bd,"avg_return_on_trade_days_pct":float(np.mean(dayrets)) if dayrets else 0,"median_return_on_trade_days_pct":float(np.median(dayrets)) if dayrets else 0,"days_ge_1pct":sum(x>=1 for x in dayrets),"days_ge_3pct":sum(x>=3 for x in dayrets)}


def evaluate(pattern,start,end,panels,dstats):
    trades=[]
    days=[d for d in dstats["SPY"].index if pd.Timestamp(start).date()<=d<=pd.Timestamp(end).date()]
    for day in days:
        signals=[]
        for sym in selected_names(day,dstats):
            g=day_slice(panels[sym],day); hist=dstats[sym][dstats[sym].index<day]
            if g.empty or hist.empty: continue
            atr=float(hist.iloc[-1]["atr20"])
            if not np.isfinite(atr) or atr<=0: continue
            sig=find_signal(pattern,g,atr)
            if sig is not None: signals.append((sig[0],sym,sig[1],atr,g))
        if not signals: continue
        signals.sort(key=lambda z:(z[0],z[1])); st,sym,structure,atr,g=signals[0]
        equity=START_EQ+sum(float(t["pnl"]) for t in trades)
        tr=simulate(g,st,structure,atr,equity,sym,pattern)
        if tr: trades.append(tr)
    return trades,summarize(trades,start,end)


def main():
    panels=load_panels(); missing=[s for s in CANDIDATES+BENCHMARKS if s not in panels or panels[s].empty]
    if missing: raise RuntimeError(f"Missing data {missing}")
    dstats={s:daily_stats(panels[s]) for s in CANDIDATES+BENCHMARKS}
    out={"patterns":{}}
    for p in PATTERNS:
        out["patterns"][p]={}
        for block,(a,b) in BLOCKS.items():
            tr,st=evaluate(p,a,b,panels,dstats); out["patterns"][p][block]={"stats":st,"trades":tr}
    def score(item):
        st=item[1]["discovery_jan_apr"]["stats"]
        return (-999,-999,-999) if st["trades"]<15 else (st["profit_factor"],st["avg_r"],st["return_pct"])
    ranked=sorted(out["patterns"].items(),key=score,reverse=True)
    selected=ranked[0][0] if ranked and score(ranked[0])[0]>-999 else None
    out.update({"experiment":EXPERIMENT,"research_only":True,"feed":FEED,"candidate_pool":CANDIDATES,"blocks":BLOCKS,"selected_on_discovery_only":selected,"discovery_rank":[p for p,_ in ranked],"execution":{"risk_per_trade_pct":1.0,"max_notional_pct":100,"cost_bps_per_fill":10,"target_r":2.5},"activate":False})
    with open("strategy2_experiment17b_volatile_5m_pattern_scan_results.json","w") as f: json.dump(out,f,indent=2)
    lines=["# Experiment 17B — 2026 Volatile 5-Minute Pattern Scan","",f"**Discovery-selected pattern: {selected}**","","| Pattern | Discovery PF | Discovery return | Validation PF | Validation return | Holdout PF | Holdout return |","|---|---:|---:|---:|---:|---:|---:|"]
    for p in PATTERNS:
        q=out["patterns"][p]; d=q["discovery_jan_apr"]["stats"]; v=q["validation_may_jun"]["stats"]; h=q["holdout_jul_aug21"]["stats"]
        lines.append(f"| {p} | {d['profit_factor']:.2f} | {d['return_pct']:+.2f}% | {v['profit_factor']:.2f} | {v['return_pct']:+.2f}% | {h['profit_factor']:.2f} | {h['return_pct']:+.2f}% |")
    if selected:
        lines += ["", "## Selected detail"]
        for block in BLOCKS:
            s=out["patterns"][selected][block]["stats"]; lines.append(f"- {block}: {s['trades']} trades, win {s['win_rate_pct']:.1f}%, PF {s['profit_factor']:.2f}, return {s['return_pct']:+.2f}%, DD {s['max_drawdown_pct']:.2f}%, avg R {s['avg_r']:+.2f}")
    lines += ["", "Activation OFF. Aggressive sizing is tested only if validation and holdout remain positive."]
    with open("strategy2_experiment17b_volatile_5m_pattern_scan_summary.md","w") as f: f.write("\n".join(lines)+"\n")

if __name__=="__main__": main()
