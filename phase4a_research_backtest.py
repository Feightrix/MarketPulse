import json, os, time, urllib.parse, urllib.request
from itertools import product
import numpy as np
import pandas as pd

BASE="https://data.alpaca.markets/v2/stocks/{symbol}/bars"
SYMS=["SPY","QQQ","IWM","AAPL","MSFT","NVDA","AMD","AMZN","META","TSLA"]
START="2021-01-01T00:00:00Z"; END="2026-08-01T00:00:00Z"
START_EQ=2000.0; CAP=0.95; RISK=.0035; DAILY_STOP=.015
MAX_TRADES=30; COOLDOWN_MIN=1
TZ="America/New_York"
BASE_BPS=2.0

def hdr():
    k=os.getenv("ALPACA_API_KEY_ID"); s=os.getenv("ALPACA_API_SECRET_KEY")
    if not k or not s: raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID":k,"APCA-API-SECRET-KEY":s}

def fetch(sym):
    out=[]; token=None
    while True:
        p={"timeframe":"1Min","start":START,"end":END,"adjustment":"all","feed":"iex","limit":10000,"sort":"asc"}
        if token: p["page_token"]=token
        u=BASE.format(symbol=sym)+"?"+urllib.parse.urlencode(p)
        with urllib.request.urlopen(urllib.request.Request(u,headers=hdr()),timeout=60) as r:
            z=json.loads(r.read().decode())
        out += z.get("bars",[])
        token=z.get("next_page_token")
        if not token: break
        time.sleep(.32)
    d=pd.DataFrame(out)
    if d.empty: raise RuntimeError("No bars "+sym)
    t=pd.to_datetime(d.t,utc=True).dt.tz_convert(TZ)
    d["ts"]=t; d["date"]=t.dt.date; d["minute"]=t.dt.hour*60+t.dt.minute
    d=d[(d.minute>=570)&(d.minute<=960)].copy()
    d=d.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return d[["ts","date","minute","open","high","low","close","volume"]].reset_index(drop=True)

def rsi(s,n=7):
    x=s.diff(); up=x.clip(lower=0); dn=(-x.clip(upper=0))
    au=up.ewm(alpha=1/n,adjust=False).mean(); ad=dn.ewm(alpha=1/n,adjust=False).mean()
    rs=au/ad.replace(0,np.nan)
    return 100-100/(1+rs)

def prep(d):
    def one(g):
        g=g.copy(); c=g.close
        g["e9"]=c.ewm(span=9,adjust=False).mean(); g["e21"]=c.ewm(span=21,adjust=False).mean(); g["e50"]=c.ewm(span=50,adjust=False).mean()
        tp=(g.high+g.low+g.close)/3; cv=g.volume.cumsum(); g["vwap"]=(tp*g.volume).cumsum()/cv.replace(0,np.nan)
        g["rsi"]=rsi(c,7); resid=(c-g.vwap)/g.vwap; sd=resid.rolling(20,min_periods=15).std(); g["z"]=resid/sd.replace(0,np.nan)
        g["vr"]=g.volume/g.volume.rolling(20,min_periods=10).median(); g["sep"]=(g.e9-g.e50).abs()/c
        return g
    return d.groupby("date",group_keys=False).apply(one).reset_index(drop=True)

def build_events(data, mode):
    ev=[]
    for j,s in enumerate(SYMS):
        d=data[s]; r=d.rsi; prev=r.shift(1)
        tl=(d.e9>d.e21)&(d.e21>d.e50)&(d.close>d.vwap)&(d.low<=d.e21*1.001)&(d.close>d.e9)&(prev<50)&(r>=50)&(d.vr>=.8)
        ts=(d.e9<d.e21)&(d.e21<d.e50)&(d.close<d.vwap)&(d.high>=d.e21*.999)&(d.close<d.e9)&(prev>50)&(r<=50)&(d.vr>=.8)
        rng=d.sep<.0025
        ml=rng&(d.z<=-1.25)&(prev<35)&(r>=35)&(d.vr>=.7)
        ms=rng&(d.z>=1.25)&(prev>65)&(r<=65)&(d.vr>=.7)
        masks=[(tl,1),(ts,-1)] if mode=="trend" else ([(ml,1),(ms,-1)] if mode=="mean" else [(tl,1),(ts,-1),(ml,1),(ms,-1)])
        for m,side in masks:
            for i in np.flatnonzero(m.to_numpy(bool)):
                if i+1>=len(d) or d.date.iloc[i+1]!=d.date.iloc[i] or d.minute.iloc[i]>=940: continue
                score=(abs(float(d.z.iloc[i])) if np.isfinite(d.z.iloc[i]) else 0)+float(d.sep.iloc[i])*100
                ev.append((d.ts.iloc[i],j,int(i),side,score))
    ev.sort(key=lambda x:(x[0],-x[4]))
    out=[]; last=None
    for e in ev:
        if e[0]!=last: out.append(e); last=e[0]
    return out

def outcome(data,event,tp,sl,hold,bps,eq,risk_frac):
    ts,j,i,side,score=event; d=data[SYMS[j]]; k=i+1; px=float(d.open.iloc[k])
    qty=min(eq*CAP/px, eq*risk_frac/(px*sl))
    if side<0: qty=np.floor(qty)
    if qty<=0: return None
    fr=bps/10000; entry=px*(1+fr*side); last=min(k+hold-1,len(d)-1); exit_raw=float(d.close.iloc[last]); reason="TIME"; exit_i=last
    for q in range(k,last+1):
        if d.date.iloc[q]!=d.date.iloc[k]:
            last=q-1; exit_raw=float(d.close.iloc[last]); exit_i=last; break
        if side>0:
            stop=entry*(1-sl); target=entry*(1+tp)
            if d.low.iloc[q]<=stop: exit_raw=stop;reason="STOP";exit_i=q;break
            if d.high.iloc[q]>=target: exit_raw=target;reason="TARGET";exit_i=q;break
        else:
            stop=entry*(1+sl); target=entry*(1-tp)
            if d.high.iloc[q]>=stop: exit_raw=stop;reason="STOP";exit_i=q;break
            if d.low.iloc[q]<=target: exit_raw=target;reason="TARGET";exit_i=q;break
        if d.minute.iloc[q]>=950:
            exit_raw=float(d.close.iloc[q]);reason="EOD";exit_i=q;break
    exit_px=exit_raw*(1-fr*side); pnl=(exit_px-entry)*qty*side; ret=pnl/eq
    return float(pnl),float(ret),d.ts.iloc[exit_i],reason,side,float(qty)

def simulate(data,events,start,end,tp,sl,hold,bps):
    ev=[e for e in events if start<=e[0].date()<=end]
    eq=START_EQ; day=None; day0=eq; day_high=eq; day_n=0; day_losses=0
    protect=False; extra=0; cooldown_until=None; stopped=False
    trades=[]; daily=[]
    for e in ev:
        dt=e[0]; dd=dt.date()
        if dd!=day:
            if day is not None: daily.append((day,eq/day0-1))
            day=dd; day0=eq; day_high=eq; day_n=0; day_losses=0
            protect=False; extra=0; cooldown_until=None; stopped=False
        if stopped or day_n>=MAX_TRADES: continue
        if cooldown_until is not None and dt<=cooldown_until: continue
        if e[3]<0 and eq<2000: continue
        risk=RISK/2 if protect else RISK
        o=outcome(data,e,tp,sl,hold,bps,eq,risk)
        if not o: continue
        pnl,ret,exit_ts,reason,side,qty=o
        eq+=pnl; trades.append((dd,pnl,ret,side,reason)); day_n+=1
        cooldown_until=exit_ts+pd.Timedelta(minutes=COOLDOWN_MIN)
        day_losses=day_losses+1 if pnl<0 else 0; day_high=max(day_high,eq); dg=eq/day0-1
        if dg>=.05 and (day_high-eq)/day0>=.02: stopped=True
        if dg>=.10 and not protect: protect=True; extra=5
        elif protect:
            extra-=1
            if extra<=0: stopped=True
        if dg>=.15 or dg<=-DAILY_STOP or day_losses>=3: stopped=True
    if day is not None: daily.append((day,eq/day0-1))
    arr=np.array([x[2] for x in trades],float) if trades else np.array([])
    pnl=np.array([x[1] for x in trades],float) if trades else np.array([])
    wins=pnl[pnl>0].sum() if len(pnl) else 0; losses=-pnl[pnl<0].sum() if len(pnl) else 0
    pf=wins/losses if losses>0 else (99 if wins>0 else 0)
    win=float(np.mean(pnl>0)) if len(pnl) else 0; exp=float(np.mean(arr)*10000) if len(arr) else 0
    eqcurve=[START_EQ]; q=START_EQ
    for p in pnl: q+=p; eqcurve.append(q)
    a=np.array(eqcurve); peak=np.maximum.accumulate(a); dd=float(np.max(1-a/peak))
    dr=np.array([r for _,r in daily],float) if daily else np.array([])
    sides={}
    for side,name in [(1,"long"),(-1,"short")]:
        x=np.array([t[2] for t in trades if t[3]==side],float); pp=np.array([t[1] for t in trades if t[3]==side],float)
        sides[name]={"trades":len(x),"win_rate":float(np.mean(pp>0)) if len(pp) else 0,"expectancy_bps":float(np.mean(x)*10000) if len(x) else 0}
    return {"trades":len(trades),"final_equity":round(eq,2),"total_return":eq/START_EQ-1,
            "win_rate":win,"expectancy_bps":exp,"profit_factor":float(pf),"max_drawdown":dd,
            "days":len(dr),"day_pos_rate":float(np.mean(dr>0)) if len(dr) else 0,
            "day_2pct":float(np.mean(dr>=.02)) if len(dr) else 0,
            "day_5pct":float(np.mean(dr>=.05)) if len(dr) else 0,
            "day_10pct":float(np.mean(dr>=.10)) if len(dr) else 0,
            "day_15pct":float(np.mean(dr>=.15)) if len(dr) else 0,
            "best_day":float(dr.max()) if len(dr) else 0,
            "median_day":float(np.median(dr)) if len(dr) else 0,"sides":sides}

def devscore(m):
    if m["trades"]<300 or m["expectancy_bps"]<=0 or m["profit_factor"]<=1.05: return -1e9
    return -abs(m["win_rate"]-.78)*15 + min(m["expectancy_bps"],50)/10 + min(m["profit_factor"],2)*2 + m["day_5pct"]*4 - m["max_drawdown"]*5

def main():
    data={}
    for s in SYMS:
        print("Downloading",s,flush=True); data[s]=prep(fetch(s)); print(s,len(data[s]),flush=True)
    modes=["trend","mean","combo"]; eventsets={m:build_events(data,m) for m in modes}
    print({m:len(v) for m,v in eventsets.items()},flush=True)
    cfgs=list(product(modes,[.002,.004,.006,.008],[.002,.003,.004,.005],[5,10,20,30]))
    dev=[]
    for n,(mode,tp,sl,hold) in enumerate(cfgs,1):
        m=simulate(data,eventsets[mode],pd.Timestamp("2021-01-01").date(),pd.Timestamp("2023-12-31").date(),tp,sl,hold,BASE_BPS)
        dev.append({"mode":mode,"tp":tp,"sl":sl,"hold":hold,"dev":m,"score":devscore(m)})
        if n%24==0: print("dev",n,"/",len(cfgs),flush=True)
    finalists=sorted([x for x in dev if x["score"]>-1e8],key=lambda x:x["score"],reverse=True)[:20]
    checked=[]
    for x in finalists:
        a=simulate(data,eventsets[x["mode"]],pd.Timestamp("2024-01-01").date(),pd.Timestamp("2024-12-31").date(),x["tp"],x["sl"],x["hold"],BASE_BPS)
        b=simulate(data,eventsets[x["mode"]],pd.Timestamp("2025-01-01").date(),pd.Timestamp("2025-12-31").date(),x["tp"],x["sl"],x["hold"],BASE_BPS)
        comb=simulate(data,eventsets[x["mode"]],pd.Timestamp("2024-01-01").date(),pd.Timestamp("2025-12-31").date(),x["tp"],x["sl"],x["hold"],BASE_BPS)
        gate=(comb["trades"]>=200 and comb["win_rate"]>=.76 and a["expectancy_bps"]>0 and b["expectancy_bps"]>0 and
              a["profit_factor"]>1.2 and b["profit_factor"]>1.2 and comb["max_drawdown"]<.15 and
              comb["sides"]["long"]["trades"]>=30 and comb["sides"]["short"]["trades"]>=30)
        score=comb["win_rate"]*8 + min(comb["expectancy_bps"],50)/10 + min(a["profit_factor"],b["profit_factor"]) + comb["day_10pct"]*8 + comb["day_5pct"]*3 - comb["max_drawdown"]*4
        checked.append({**x,"y24":a,"y25":b,"val":comb,"gate_core":gate,"vscore":score})
    pool=[x for x in checked if x["gate_core"]] or checked
    best=max(pool,key=lambda x:x["vscore"]) if pool else max(dev,key=lambda x:x["score"])
    mode,tp,sl,hold=best["mode"],best["tp"],best["sl"],best["hold"]
    if "val" not in best:
        best["y24"]=simulate(data,eventsets[mode],pd.Timestamp("2024-01-01").date(),pd.Timestamp("2024-12-31").date(),tp,sl,hold,BASE_BPS)
        best["y25"]=simulate(data,eventsets[mode],pd.Timestamp("2025-01-01").date(),pd.Timestamp("2025-12-31").date(),tp,sl,hold,BASE_BPS)
        best["val"]=simulate(data,eventsets[mode],pd.Timestamp("2024-01-01").date(),pd.Timestamp("2025-12-31").date(),tp,sl,hold,BASE_BPS)
        best["gate_core"]=False
    y26=simulate(data,eventsets[mode],pd.Timestamp("2026-01-01").date(),pd.Timestamp("2026-07-31").date(),tp,sl,hold,BASE_BPS)
    stress={str(b):simulate(data,eventsets[mode],pd.Timestamp("2024-01-01").date(),pd.Timestamp("2025-12-31").date(),tp,sl,hold,b) for b in [2,5,10]}
    gate=bool(best["gate_core"] and stress["10"]["expectancy_bps"]>0 and y26["expectancy_bps"]>0 and y26["profit_factor"]>1.05)
    result={"phase":"4A","objective":"10-15% daily scorecard on $2,000 via many rapid long/short trades; risk controls dominate.",
            "universe":SYMS,"candidate_count":len(cfgs),
            "selected":{"mode":mode,"take_profit_pct":tp,"stop_pct":sl,"hold_minutes":hold,
                        "max_trades_day":MAX_TRADES,"risk_per_trade":RISK,"daily_loss_stop":DAILY_STOP},
            "development":best["dev"],"validation_2024":best["y24"],"validation_2025":best["y25"],
            "validation_2024_2025":best["val"],"check_2026":y26,"friction_stress":stress,
            "gate":"PASS" if gate else "FAIL",
            "limitations":["IEX-only historical feed on Alpaca Basic may differ from consolidated SIP market.",
                           "Ordinary bars do not reconstruct historical borrow availability or borrow fees.",
                           "1-minute bars cannot establish high/low ordering; stop-first is used when both boundaries are touched.",
                           "Backtest is research only; no live orders are submitted."]}
    with open("phase4a_results.json","w") as f: json.dump(result,f,indent=2)
    with open("phase4a_summary.md","w") as f:
        m=result["validation_2024_2025"]
        f.write(f"# MarketPulse — Phase 4A Rapid Long/Short Validation\\n\\n**Gate: {result['gate']}**\\n\\n")
        f.write(f"Selected: **{mode}**, TP **{tp:.2%}**, stop **{sl:.2%}**, time stop **{hold} min**.\\n\\n")
        f.write(f"Validation 2024-25: {m['trades']} trades, {m['win_rate']:.2%} wins, {m['expectancy_bps']:.1f} bps expectancy, PF {m['profit_factor']:.2f}, max DD {m['max_drawdown']:.2%}.\\n\\n")
        f.write(f"Days reaching +2%: {m['day_2pct']:.2%}; +5%: {m['day_5pct']:.2%}; +10%: {m['day_10pct']:.2%}; +15%: {m['day_15pct']:.2%}.\\n\\n")
        f.write("The 10%-15% daily objective is a scorecard, not a guarantee. Hard loss/profit-protection rules dominate.\\n")
    print(json.dumps(result,indent=2),flush=True)

if __name__=="__main__": main()
