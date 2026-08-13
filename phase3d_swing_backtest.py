import json, os, time, urllib.parse, urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
    "AMD", "AVGO", "NFLX", "JPM", "XOM", "BA", "CAT", "COST", "HD", "WMT",
]
START = "2020-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
START_EQ = 100.0
CAPITAL = 0.95
BASE_FRICTION_BPS = 5.0


def headers():
    key = os.getenv("ALPACA_API_KEY_ID")
    sec = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch(symbol):
    params = {
        "timeframe":"1Day", "start":START, "end":END, "adjustment":"all",
        "feed":"iex", "limit":10000, "sort":"asc", "asof":"2026-08-01",
    }
    url = BASE.format(symbol=symbol) + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers()), timeout=60) as r:
        rows = json.loads(r.read().decode()).get("bars", [])
    if not rows:
        raise RuntimeError(f"No bars returned for {symbol}")
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["t"], utc=True).dt.date
    d = d.set_index("date").sort_index().rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    d = d[["open","high","low","close","volume"]].astype(float)
    prev = d.close.shift(1)
    tr = pd.concat([d.high-d.low, (d.high-prev).abs(), (d.low-prev).abs()], axis=1).max(axis=1)
    d["atr_pct"] = tr.rolling(14, min_periods=14).mean()/d.close
    d["ret20"] = d.close/d.close.shift(20)-1
    d["ret60"] = d.close/d.close.shift(60)-1
    d["sma50"] = d.close.rolling(50, min_periods=50).mean()
    d["sma100"] = d.close.rolling(100, min_periods=100).mean()
    d["hi20"] = d.high.shift(1).rolling(20, min_periods=20).max()
    d["hi50"] = d.high.shift(1).rolling(50, min_periods=50).max()
    return d


def load_data():
    data = {}
    for s in UNIVERSE:
        print("Downloading", s, flush=True)
        data[s] = fetch(s)
        print(s, len(data[s]), "daily bars", flush=True)
        time.sleep(0.02)
    return data


def pack(data, start, end):
    dates = sorted(set().union(*[set(d.index) for d in data.values()]))
    dates = [d for d in dates if start <= d <= end]
    idx = pd.Index(dates)
    n, m = len(idx), len(UNIVERSE)
    fields = {k: np.full((n,m), np.nan, dtype=float) for k in ["open","high","low","close","atr_pct","ret20","ret60","sma50","sma100","hi20","hi50"]}
    for j,s in enumerate(UNIVERSE):
        a = data[s].reindex(idx)
        for k in fields:
            fields[k][:,j] = a[k].to_numpy(float)
    choices = {}
    for rank_lb, breakout_lb, band, trend in product([20,60],[20,50],[0.0,0.01],["fast","strict"]):
        ret = fields[f"ret{rank_lb}"]
        hi = fields[f"hi{breakout_lb}"]
        valid = (
            np.isfinite(ret) & np.isfinite(hi) & np.isfinite(fields["atr_pct"])
            & np.isfinite(fields["sma50"]) & np.isfinite(fields["sma100"])
            & (fields["close"] > 5)
            & (fields["atr_pct"] >= 0.008) & (fields["atr_pct"] <= 0.10)
            & (fields["close"] >= hi*(1-band))
            & (fields["close"] > fields["sma50"])
        )
        if trend == "strict":
            valid &= fields["sma50"] > fields["sma100"]
        scores = np.where(valid, ret, -np.inf)
        best = np.argmax(scores, axis=1).astype(np.int16)
        none = ~np.isfinite(np.max(scores, axis=1))
        best[none] = -1
        choices[(rank_lb,breakout_lb,band,trend)] = best
    return {"dates":dates, **fields, "choices":choices}


def month_metrics(dates, equity):
    if len(equity)==0:
        return {"months_total":0,"months_doubled":0,"monthly_positive_rate":0.0,"best_month_return":0.0,"median_month_return":0.0}
    s = pd.Series(equity, index=pd.to_datetime(dates))
    g = s.groupby(s.index.to_period("M"))
    rets = g.last()/g.first()-1
    return {
        "months_total":int(len(rets)), "months_doubled":int((rets>=1.0).sum()),
        "monthly_positive_rate":float((rets>0).mean()), "best_month_return":float(rets.max()),
        "median_month_return":float(rets.median()),
    }


def sim(p, signal_key, hold_days, stop_pct, target_pct, friction_bps):
    op,hi,lo,cl = p["open"],p["high"],p["low"],p["close"]
    choice = p["choices"][signal_key]
    fr = friction_bps/10000.0
    cash = START_EQ
    pos = None
    pending = -1
    trades = []
    eqs = []
    for i in range(len(p["dates"])):
        if pos is None and pending >= 0 and np.isfinite(op[i,pending]):
            entry = op[i,pending]*(1+fr)
            invest = cash*CAPITAL
            qty = invest/entry
            cash -= invest
            pos = [pending, entry, qty, cash+qty*entry, 0]
        pending = -1

        if pos is not None:
            j,entry,qty,eq0,bars = pos
            if np.isfinite(cl[i,j]):
                bars += 1
                stop = entry*(1-stop_pct)
                target = entry*(1+target_pct)
                raw = None; why = None
                if lo[i,j] <= stop:
                    raw,why = stop,"STOP"
                elif hi[i,j] >= target:
                    raw,why = target,"TARGET"
                elif bars >= hold_days:
                    raw,why = cl[i,j],"TIME"
                if raw is not None:
                    exit_px = raw*(1-fr)
                    cash += qty*exit_px
                    pnl = cash-eq0
                    trades.append((pnl,pnl/eq0,why))
                    pos = None
                else:
                    pos[4] = bars

        if pos is None:
            eq = cash
            c = int(choice[i])
            if c >= 0:
                pending = c
        else:
            j,entry,qty,eq0,bars = pos
            mark = cl[i,j] if np.isfinite(cl[i,j]) else entry
            eq = cash+qty*mark
        eqs.append(float(eq))

    if pos is not None:
        j,entry,qty,eq0,bars = pos
        raw = cl[-1,j] if np.isfinite(cl[-1,j]) else entry
        cash += qty*raw*(1-fr)
        pnl = cash-eq0
        trades.append((pnl,pnl/eq0,"FINAL"))
        eqs[-1]=float(cash)

    e=np.array(eqs,float)
    peak=np.maximum.accumulate(e) if len(e) else np.array([START_EQ])
    maxdd=float(np.max(1-e/peak)) if len(e) else 0.0
    mm=month_metrics(p["dates"],e)
    if trades:
        a=np.array([[x[0],x[1]] for x in trades],float)
        wins=a[a[:,0]>0,0].sum() if np.any(a[:,0]>0) else 0.0
        losses=-a[a[:,0]<0,0].sum() if np.any(a[:,0]<0) else 0.0
        pf=float(wins/losses) if losses>0 else 99.0
        wr=float(np.mean(a[:,0]>0))
        exp=float(np.mean(a[:,1])*10000)
    else:
        pf=wr=exp=0.0
    final=float(e[-1]) if len(e) else START_EQ
    return {
        "trades":len(trades), "final_equity":final, "total_return":final/START_EQ-1,
        "win_rate":wr, "expectancy_bps":exp, "profit_factor":pf, "max_drawdown":maxdd, **mm,
    }


def rounded(m):
    return {k:(round(float(v),6) if isinstance(v,(float,np.floating)) else v) for k,v in m.items()}


def dev_score(m):
    if m["trades"]<20 or m["total_return"]<=0 or m["expectancy_bps"]<=0 or m["profit_factor"]<=1.05:
        return -1e9
    return 3*m["total_return"] + .03*min(m["expectancy_bps"],100) + .75*min(m["profit_factor"],3) + .5*m["monthly_positive_rate"] - 3*max(0,m["max_drawdown"]-.20)


def main():
    data=load_data()
    periods={
        "dev":pack(data,pd.Timestamp("2021-01-01").date(),pd.Timestamp("2023-12-31").date()),
        "y24":pack(data,pd.Timestamp("2024-01-01").date(),pd.Timestamp("2024-12-31").date()),
        "y25":pack(data,pd.Timestamp("2025-01-01").date(),pd.Timestamp("2025-12-31").date()),
        "v2425":pack(data,pd.Timestamp("2024-01-01").date(),pd.Timestamp("2025-12-31").date()),
        "y26":pack(data,pd.Timestamp("2026-01-01").date(),pd.Timestamp("2026-07-31").date()),
    }
    signal_cfgs=list(product([20,60],[20,50],[0.0,0.01],["fast","strict"]))
    exits=list(product([3,5,10],[0.03,0.05],[0.06,0.10,0.15]))
    configs=[(sig,*ex) for sig in signal_cfgs for ex in exits]
    candidates=[]
    for i,(sig,hold,stop,target) in enumerate(configs,1):
        m=sim(periods["dev"],sig,hold,stop,target,BASE_FRICTION_BPS)
        candidates.append({"sig":sig,"hold":hold,"stop":stop,"target":target,"development":m,"score":dev_score(m)})
        if i%48==0 or i==len(configs): print("development",i,"/",len(configs),flush=True)
    dev_ok=[c for c in candidates if c["score"]>-1e8]
    finalists=sorted(dev_ok,key=lambda x:x["score"],reverse=True)[:30]
    checked=[]
    for i,c in enumerate(finalists,1):
        m24=sim(periods["y24"],c["sig"],c["hold"],c["stop"],c["target"],BASE_FRICTION_BPS)
        m25=sim(periods["y25"],c["sig"],c["hold"],c["stop"],c["target"],BASE_FRICTION_BPS)
        robust=(m24["trades"]>=5 and m25["trades"]>=5 and m24["total_return"]>0 and m25["total_return"]>0 and m24["expectancy_bps"]>0 and m25["expectancy_bps"]>0 and m24["profit_factor"]>1.05 and m25["profit_factor"]>1.05 and m24["max_drawdown"]<.25 and m25["max_drawdown"]<.25)
        vscore=(5*min(m24["total_return"],m25["total_return"])+.02*min(m24["expectancy_bps"],m25["expectancy_bps"])+min(m24["profit_factor"],m25["profit_factor"])) if robust else -1e9
        checked.append({**c,"validation_2024":m24,"validation_2025":m25,"robust":robust,"vscore":vscore})
        print("validation",i,"/",len(finalists),robust,flush=True)
    robusts=[x for x in checked if x["robust"]]
    if robusts:
        best=max(robusts,key=lambda x:x["vscore"])
    elif checked:
        best=max(checked,key=lambda x:(min(x["validation_2024"]["total_return"],x["validation_2025"]["total_return"]),min(x["validation_2024"]["expectancy_bps"],x["validation_2025"]["expectancy_bps"])))
    else:
        best=max(candidates,key=lambda x:(x["development"]["total_return"],x["development"]["expectancy_bps"]))
        z={"trades":0,"final_equity":100.0,"total_return":0.0,"win_rate":0.0,"expectancy_bps":0.0,"profit_factor":0.0,"max_drawdown":0.0,"months_total":0,"months_doubled":0,"monthly_positive_rate":0.0,"best_month_return":0.0,"median_month_return":0.0}
        best={**best,"validation_2024":z,"validation_2025":z,"robust":False,"vscore":-1e9}
    combined=sim(periods["v2425"],best["sig"],best["hold"],best["stop"],best["target"],BASE_FRICTION_BPS)
    check26=sim(periods["y26"],best["sig"],best["hold"],best["stop"],best["target"],BASE_FRICTION_BPS)
    stress={str(b):sim(periods["v2425"],best["sig"],best["hold"],best["stop"],best["target"],b) for b in [5.0,10.0,20.0,30.0]}
    gate=(best.get("robust",False) and combined["total_return"]>0 and combined["profit_factor"]>1.10 and stress["20.0"]["expectancy_bps"]>0 and check26["trades"]>=3 and check26["total_return"]>0 and check26["profit_factor"]>1.05)
    rank_lb,breakout_lb,band,trend=best["sig"]
    result={
        "phase":"3D","goal":"Track a 2x first-of-month balance objective without changing risk to chase it.",
        "method":"Cross-sectional swing momentum over a fixed liquid large-cap/ETF universe; close signal, next-open entry; multi-day holds.",
        "universe":UNIVERSE,"candidate_count":len(configs),"development_valid_candidates":len(dev_ok),"robust_2024_2025_candidates":len(robusts),
        "selected":{"ranking_lookback_days":rank_lb,"breakout_lookback_days":breakout_lb,"breakout_band_pct":band,"trend_mode":trend,"max_hold_days":best["hold"],"stop_loss_pct":best["stop"],"take_profit_pct":best["target"],"capital_fraction":CAPITAL},
        "development":rounded(best["development"]),"validation_2024":rounded(best["validation_2024"]),"validation_2025":rounded(best["validation_2025"]),"validation_2024_2025":rounded(combined),"check_2026":rounded(check26),"friction_stress_2024_2025":{k:rounded(v) for k,v in stress.items()},"gate":"PASS" if gate else "FAIL",
        "warning":"This fixed current universe can introduce survivorship bias. Even a PASS is research evidence only and does not authorize real-money trading."
    }
    with open("phase3d_results.json","w") as f: json.dump(result,f,indent=2)
    lines=["# MarketPulse — Phase 3D Cross-Sectional Swing Momentum","","**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)",f"**Universe:** {len(UNIVERSE)} liquid ETFs / large-cap stocks",f"**Candidates tested:** {len(configs)}",f"**Development-valid candidates:** {len(dev_ok)}",f"**Candidates positive in both 2024 and 2025:** {len(robusts)}","","## Selected setup","",f"- Rank by prior **{rank_lb}-day** return",f"- Require price within **{band:.1%}** of a prior **{breakout_lb}-day high**",f"- Trend gate: **{trend}**",f"- Hold up to **{best['hold']} trading days**",f"- Stop: **{best['stop']:.1%}**",f"- Target: **{best['target']:.1%}**",f"- Capital deployed: **{CAPITAL:.0%}**, long-only, no leverage","","## Results","","| Period | Trades | Return | Win rate | Expectancy | PF | Max DD | Positive months | Best month | Median month | Doubled months |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label,m in [("Development 2021-2023",result["development"]),("Validation 2024",result["validation_2024"]),("Validation 2025",result["validation_2025"]),("Validation 2024-2025",result["validation_2024_2025"]),("2026 check through Jul",result["check_2026"])]:
        lines.append(f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | {m['expectancy_bps']:.1f} bps | {m['profit_factor']:.2f} | {m['max_drawdown']:.2%} | {m['monthly_positive_rate']:.2%} | {m['best_month_return']:.2%} | {m['median_month_return']:.2%} | {m['months_doubled']}/{m['months_total']} |")
    lines += ["","## Validation friction stress","","| One-way friction | Expectancy | Return | PF |","|---:|---:|---:|---:|"]
    for b,m in result["friction_stress_2024_2025"].items(): lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.1f} bps | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += ["",f"**Phase 3D gate: {result['gate']}**","","## Important","","This is a genuinely different strategy class from the earlier intraday micro tests. It reduces trading frequency and seeks larger multi-day moves. The fixed present-day universe may create survivorship bias, so even a PASS would require a separate universe-robustness test and paper execution before any real-money use. The 2× monthly objective is a scorecard, not an expected or guaranteed return."]
    with open("phase3d_summary.md","w") as f: f.write("\n".join(lines)+"\n")
    print(json.dumps(result,indent=2),flush=True)

if __name__=="__main__": main()
