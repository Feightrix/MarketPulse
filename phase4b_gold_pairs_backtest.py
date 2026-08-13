import json, os, time, urllib.parse, urllib.request
from itertools import product
import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
SYMS = ["GLD", "GDX", "IAU", "SLV"]
PAIRS = [("GLD","GDX"),("GLD","SLV"),("IAU","GDX"),("IAU","SLV"),("GLD","IAU")]
CONTROL_PAIR = "GLD/IAU"
START = "2021-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
TZ = "America/New_York"
START_EQ = 2000.0
GROSS_CAP = 0.95
RISK_PER_PAIR = 0.0035
DAILY_STOP = 0.015
MAX_TRADES_DAY = 30
COOLDOWN_MIN = 1
BASE_BPS = 2.0

LOOKBACKS = [60,120,240]
ENTRY_ZS = [1.5,2.0,2.5]
EXIT_ZS = [0.25,0.50]
Z_STOPS = [3.5]
HOLDS = [30,60]

DEV_START=pd.Timestamp("2021-01-01").date(); DEV_END=pd.Timestamp("2023-12-31").date()
Y24_START=pd.Timestamp("2024-01-01").date(); Y24_END=pd.Timestamp("2024-12-31").date()
Y25_START=pd.Timestamp("2025-01-01").date(); Y25_END=pd.Timestamp("2025-12-31").date()
V_START=Y24_START; V_END=Y25_END
Y26_START=pd.Timestamp("2026-01-01").date(); Y26_END=pd.Timestamp("2026-07-31").date()


def headers():
    k=os.getenv("ALPACA_API_KEY_ID"); s=os.getenv("ALPACA_API_SECRET_KEY")
    if not k or not s: raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID":k,"APCA-API-SECRET-KEY":s}


def fetch(sym):
    rows=[]; token=None
    while True:
        q={"timeframe":"1Min","start":START,"end":END,"adjustment":"all","feed":"iex","limit":10000,"sort":"asc"}
        if token: q["page_token"]=token
        url=BASE.format(symbol=sym)+"?"+urllib.parse.urlencode(q)
        req=urllib.request.Request(url,headers=headers())
        with urllib.request.urlopen(req,timeout=60) as r:
            z=json.loads(r.read().decode())
        rows.extend(z.get("bars",[])); token=z.get("next_page_token")
        if not token: break
        time.sleep(.28)
    d=pd.DataFrame(rows)
    if d.empty: raise RuntimeError("No bars for "+sym)
    ts=pd.to_datetime(d["t"],utc=True).dt.tz_convert(TZ)
    d["ts"]=ts; d["date"]=ts.dt.date; d["minute"]=ts.dt.hour*60+ts.dt.minute
    d=d[(d["minute"]>=570)&(d["minute"]<=960)].copy()
    d=d.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return d[["ts","date","minute","open","high","low","close","volume"]].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def align(a,b):
    x=a.merge(b,on="ts",suffixes=("_a","_b"),how="inner")
    x["date"]=x["ts"].dt.date
    x["minute"]=x["ts"].dt.hour*60+x["ts"].dt.minute
    return x.reset_index(drop=True)


def features(df,w):
    x=df.copy()
    la=np.log(x["close_a"]); lb=np.log(x["close_b"])
    ra=la.diff(); rb=lb.diff()
    beta=ra.rolling(w,min_periods=w).cov(rb)/rb.rolling(w,min_periods=w).var().replace(0,np.nan)
    beta=beta.clip(0.25,3.0)
    ma=la.rolling(w,min_periods=w).mean(); mb=lb.rolling(w,min_periods=w).mean()
    resid=(la-ma)-beta*(lb-mb)
    rz=resid.rolling(w,min_periods=w).mean(); rs=resid.rolling(w,min_periods=w).std().replace(0,np.nan)
    x["beta"]=beta; x["z"]=(resid-rz)/rs
    # require some actual IEX activity in both legs
    va=x["volume_a"].rolling(20,min_periods=10).median(); vb=x["volume_b"].rolling(20,min_periods=10).median()
    x["active"]=(va>0)&(vb>0)&np.isfinite(x["z"])&np.isfinite(x["beta"])
    return x


def day_index(df,start,end):
    return [d for d in pd.unique(df["date"]) if start<=d<=end]


def trade_pnl_at(df,k,q,side_a,beta,eq,bps):
    pa=float(df["open_a"].iloc[k]); pb=float(df["open_b"].iloc[k])
    gross=eq*GROSS_CAP; wb=abs(beta)/(1+abs(beta)); wa=1-wb
    na=gross*wa; nb=gross*wb
    qa=na/pa; qb=nb/pb; side_b=-side_a
    ea=pa; eb=pb; xa=float(df["close_a"].iloc[q]); xb=float(df["close_b"].iloc[q])
    gross_pnl=side_a*qa*(xa-ea)+side_b*qb*(xb-eb)
    entry_notional=abs(qa*ea)+abs(qb*eb); exit_notional=abs(qa*xa)+abs(qb*xb)
    cost=(bps/10000.0)*(entry_notional+exit_notional)
    return gross_pnl-cost, gross_pnl, entry_notional+exit_notional, qa,qb,ea,eb,xa,xb


def simulate(df,start,end,entry_z,exit_z,zstop,hold,bps=BASE_BPS,return_trades=False):
    dates=day_index(df,start,end); dmap={d:0.0 for d in dates}
    arr_date=df["date"].to_numpy(); arr_min=df["minute"].to_numpy(); z=df["z"].to_numpy(float); beta=df["beta"].to_numpy(float); active=df["active"].to_numpy(bool)
    eq=START_EQ; i=1; trades=[]; daily_n={d:0 for d in dates}; daily_losses={d:0 for d in dates}; stopped={d:False for d in dates}; day0={d:None for d in dates}; day_high={d:None for d in dates}; protect={d:False for d in dates}; extra={d:0 for d in dates}; cooldown={d:-1 for d in dates}
    n=len(df)
    while i<n-1:
        d=arr_date[i]
        if d not in dmap or not active[i] or arr_min[i]>=940:
            i+=1; continue
        if day0[d] is None: day0[d]=eq; day_high[d]=eq
        if stopped[d] or daily_n[d]>=MAX_TRADES_DAY or i<=cooldown[d]:
            i+=1; continue
        zz=z[i]
        if not np.isfinite(zz) or abs(zz)<entry_z:
            i+=1; continue
        k=i+1
        if k>=n or arr_date[k]!=d:
            i+=1; continue
        side_a=-1 if zz>0 else 1
        b=float(beta[i])
        if not np.isfinite(b):
            i+=1; continue
        risk_frac=RISK_PER_PAIR/2 if protect[d] else RISK_PER_PAIR
        max_loss=eq*risk_frac
        last=min(k+hold-1,n-1); exit_i=last; reason="TIME"
        for q in range(k,last+1):
            if arr_date[q]!=d:
                exit_i=q-1; reason="EOD"; break
            pnl,_,_,*_=trade_pnl_at(df,k,q,side_a,b,eq,bps)
            if pnl<=-max_loss:
                exit_i=q; reason="PAIR_STOP"; break
            qz=z[q]
            if np.isfinite(qz):
                if abs(qz)>=zstop:
                    exit_i=q; reason="Z_STOP"; break
                if abs(qz)<=exit_z:
                    exit_i=q; reason="CONVERGENCE"; break
            if arr_min[q]>=950:
                exit_i=q; reason="EOD"; break
        pnl,gross_pnl,turnover,qa,qb,ea,eb,xa,xb=trade_pnl_at(df,k,exit_i,side_a,b,eq,bps)
        eq_before=eq; eq+=pnl; dmap[d]+=pnl; daily_n[d]+=1
        daily_losses[d]=daily_losses[d]+1 if pnl<0 else 0
        day_high[d]=max(day_high[d],eq); dg=eq/day0[d]-1
        if dg>=0.05 and (day_high[d]-eq)/day0[d]>=0.02: stopped[d]=True
        if dg>=0.10 and not protect[d]: protect[d]=True; extra[d]=5
        elif protect[d]:
            extra[d]-=1
            if extra[d]<=0: stopped[d]=True
        if dg>=0.15 or dg<=-DAILY_STOP or daily_losses[d]>=3: stopped[d]=True
        cooldown[d]=exit_i+COOLDOWN_MIN
        trades.append({"date":str(d),"entry_i":int(k),"exit_i":int(exit_i),"pnl":float(pnl),"gross_pnl":float(gross_pnl),"turnover":float(turnover),"eq_before":float(eq_before),"ret":float(pnl/eq_before),"side_a":int(side_a),"beta":b,"reason":reason})
        i=exit_i+1
    return summarize(trades,dates,return_trades)


def summarize(trades,dates,include=False,stress_bps=None):
    eq=START_EQ; daily={d:0.0 for d in dates}; pnls=[]; rets=[]
    for t in trades:
        if stress_bps is None:
            p=t["pnl"]
        else:
            p=t["gross_pnl"]-(stress_bps/10000.0)*t["turnover"]
        before=eq; eq+=p; pnls.append(p); rets.append(p/before)
        d=pd.Timestamp(t["date"]).date();
        if d in daily: daily[d]+=p
    p=np.array(pnls,float); r=np.array(rets,float)
    wins=p[p>0].sum() if len(p) else 0; losses=-p[p<0].sum() if len(p) else 0
    pf=wins/losses if losses>0 else (99.0 if wins>0 else 0.0)
    curve=np.r_[START_EQ,START_EQ+np.cumsum(p)] if len(p) else np.array([START_EQ])
    peak=np.maximum.accumulate(curve); maxdd=float(np.max(1-curve/peak))
    dr=[]
    for d in dates:
        # daily return relative to approximate start-of-day equity
        prior=START_EQ+sum(v for dd,v in daily.items() if dd<d)
        dr.append(daily[d]/prior if prior else 0.0)
    dr=np.array(dr,float)
    out={"trades":int(len(p)),"final_equity":round(float(eq),2),"total_return":float(eq/START_EQ-1),"win_rate":float(np.mean(p>0)) if len(p) else 0.0,"expectancy_bps":float(np.mean(r)*10000) if len(r) else 0.0,"profit_factor":float(pf),"max_drawdown":maxdd,"days":int(len(dr)),"positive_day_rate":float(np.mean(dr>0)) if len(dr) else 0.0,"median_day":float(np.median(dr)) if len(dr) else 0.0,"best_day":float(np.max(dr)) if len(dr) else 0.0,"worst_day":float(np.min(dr)) if len(dr) else 0.0,"day_1pct":float(np.mean(dr>=.01)) if len(dr) else 0.0,"day_2pct":float(np.mean(dr>=.02)) if len(dr) else 0.0,"day_5pct":float(np.mean(dr>=.05)) if len(dr) else 0.0,"day_10pct":float(np.mean(dr>=.10)) if len(dr) else 0.0,"day_15pct":float(np.mean(dr>=.15)) if len(dr) else 0.0,"median_trades_day":float(np.median([sum(1 for t in trades if t["date"]==str(d)) for d in dates])) if dates else 0.0}
    out["return_to_drawdown"]=float(out["total_return"]/maxdd) if maxdd>0 else (99.0 if out["total_return"]>0 else 0.0)
    if include: out["trade_records"]=trades
    return out


def score(m):
    if m["trades"]<150: return -1e9
    return (m["total_return"]*5 + m["positive_day_rate"]*2 + min(m["profit_factor"],3.0) + m["expectancy_bps"]/20 + m["median_day"]*50 - m["max_drawdown"]*4 + min(m["return_to_drawdown"],5)*0.5)


def dev_valid(m):
    return m["trades"]>=150 and m["expectancy_bps"]>0 and m["profit_factor"]>1.05 and m["max_drawdown"]<0.25


def feasibility_from_drawdown(m):
    dd=max(0.0,min(0.99,m["max_drawdown"]))
    buffer=2000/(1-dd) if dd<1 else None
    return {"starting_at_2000_is_fragile":True,"estimated_start_equity_to_remain_above_2000_through_observed_max_drawdown":round(buffer,2) if buffer else None,"note":"Approximation only; actual short access also depends on current asset borrow status and broker requirements."}


def main():
    raw={}
    for s in SYMS:
        print("Downloading",s,flush=True); raw[s]=fetch(s); print(s,len(raw[s]),flush=True)
    packs={}
    for a,b in PAIRS:
        key=f"{a}/{b}"; base=align(raw[a],raw[b]); packs[key]={w:features(base,w) for w in LOOKBACKS}; print("Prepared",key,len(base),flush=True)

    cfgs=[]; n=0
    for (a,b),w,ez,xz,zs,h in product(PAIRS,LOOKBACKS,ENTRY_ZS,EXIT_ZS,Z_STOPS,HOLDS):
        n+=1; pair=f"{a}/{b}"; d=packs[pair][w]
        m=simulate(d,DEV_START,DEV_END,ez,xz,zs,h)
        cfgs.append({"pair":pair,"lookback":w,"entry_z":ez,"exit_z":xz,"z_stop":zs,"hold":h,"development":m,"development_valid":dev_valid(m),"score":score(m)})
        if n%30==0: print("development",n,"/",180,flush=True)

    ranked=sorted(cfgs,key=lambda x:x["score"],reverse=True)
    finalists=[x for x in ranked if x["development_valid"]][:20]
    if not finalists: finalists=ranked[:20]
    checked=[]
    for x in finalists:
        d=packs[x["pair"]][x["lookback"]]; args=(x["entry_z"],x["exit_z"],x["z_stop"],x["hold"])
        y24=simulate(d,Y24_START,Y24_END,*args); y25=simulate(d,Y25_START,Y25_END,*args); val=simulate(d,V_START,V_END,*args,return_trades=True)
        core=(val["trades"]>=200 and y24["expectancy_bps"]>0 and y25["expectancy_bps"]>0 and y24["profit_factor"]>1.2 and y25["profit_factor"]>1.2 and val["max_drawdown"]<.15 and val["positive_day_rate"]>=.55)
        vscore=score(val)+min(y24["profit_factor"],y25["profit_factor"])
        checked.append({**x,"validation_2024":y24,"validation_2025":y25,"validation":val,"core_gate":core,"validation_score":vscore})
    pool=[x for x in checked if x["core_gate"]] or checked
    best=max(pool,key=lambda x:x["validation_score"])
    d=packs[best["pair"]][best["lookback"]]; args=(best["entry_z"],best["exit_z"],best["z_stop"],best["hold"])
    y26=simulate(d,Y26_START,Y26_END,*args)
    base_val=best["validation"]; trade_records=base_val.pop("trade_records")
    val_dates=day_index(d,V_START,V_END)
    stress={str(bps):summarize(trade_records,val_dates,stress_bps=bps) for bps in [2,5,10]}
    high_friction_ok=stress["10"]["expectancy_bps"]>0 and stress["10"]["total_return"]>0
    gate=bool(best["core_gate"] and high_friction_ok and y26["expectancy_bps"]>0 and y26["profit_factor"]>1.0 and best["pair"]!=CONTROL_PAIR)
    top20=[]
    for x in checked:
        top20.append({"pair":x["pair"],"lookback":x["lookback"],"entry_z":x["entry_z"],"exit_z":x["exit_z"],"z_stop":x["z_stop"],"hold":x["hold"],"development":x["development"],"validation_2024":x["validation_2024"],"validation_2025":x["validation_2025"],"validation_2024_2025":x["validation"],"core_gate":x["core_gate"],"validation_score":x["validation_score"]})
    top20=sorted(top20,key=lambda x:x["validation_score"],reverse=True)

    result={"phase":"4B","mission":"Hedged gold relative-value research; profit consistency outranks win rate.","starting_equity":START_EQ,"symbols":SYMS,"pairs":[f"{a}/{b}" for a,b in PAIRS],"candidate_count":len(cfgs),"development_valid_count":sum(1 for x in cfgs if x["development_valid"]),"selected":{"pair":best["pair"],"lookback":best["lookback"],"entry_z":best["entry_z"],"exit_z":best["exit_z"],"z_stop":best["z_stop"],"hold_minutes":best["hold"]},"development":best["development"],"validation_2024":best["validation_2024"],"validation_2025":best["validation_2025"],"validation_2024_2025":base_val,"check_2026":y26,"same_trade_friction_stress":stress,"short_access_feasibility":feasibility_from_drawdown(base_val),"top20_checked":top20,"gate":"PASS" if gate else "FAIL","limitations":["IEX-only historical feed on Alpaca Basic may differ from consolidated SIP market.","One-minute bars do not model exact bid/ask spread, queue position, or two-leg fill/legging risk.","Historical borrow availability and borrow fees are not reconstructed from ordinary bars.","Pair-level hard dollar stop is evaluated on minute closes, so intraminute losses may exceed the modeled stop.","Research results do not guarantee future profit."]}
    with open("phase4b_results.json","w") as f: json.dump(result,f,indent=2)
    m=base_val
    with open("phase4b_summary.md","w") as f:
        f.write(f"# MarketPulse — Phase 4B Hedged Gold Relative-Value Research\n\n**Gate: {result['gate']}**\n\n")
        f.write(f"Selected pair: **{best['pair']}** · lookback **{best['lookback']} min** · entry **|z| ≥ {best['entry_z']}** · exit **|z| ≤ {best['exit_z']}** · z-stop **{best['z_stop']}** · time stop **{best['hold']} min**.\n\n")
        f.write(f"Development-valid candidates: **{result['development_valid_count']} / {result['candidate_count']}**.\n\n")
        f.write(f"2024–2025 validation: **{m['total_return']:.2%}** return, **{m['trades']}** pair trades, **{m['expectancy_bps']:.2f} bps/trade** expectancy, **PF {m['profit_factor']:.2f}**, **{m['positive_day_rate']:.2%} positive days**, median day **{m['median_day']:.3%}**, max DD **{m['max_drawdown']:.2%}**. Win rate: {m['win_rate']:.2%} (diagnostic only).\n\n")
        f.write(f"Daily thresholds: +1% **{m['day_1pct']:.2%}**, +2% **{m['day_2pct']:.2%}**, +5% **{m['day_5pct']:.2%}**, +10% **{m['day_10pct']:.2%}**, +15% **{m['day_15pct']:.2%}**. Best day **{m['best_day']:.2%}**, worst day **{m['worst_day']:.2%}**.\n\n")
        f.write(f"Same-trade 10 bps friction: **{stress['10']['total_return']:.2%}** return, **{stress['10']['expectancy_bps']:.2f} bps/trade**, PF **{stress['10']['profit_factor']:.2f}**.\n\n")
        f.write(f"Approximate starting equity needed to remain above Alpaca's $2,000 short threshold through the observed validation max drawdown: **${result['short_access_feasibility']['estimated_start_equity_to_remain_above_2000_through_observed_max_drawdown']:,.2f}**. This is only a buffer estimate, not a broker guarantee.\n\n")
        f.write("The +10% to +15% daily objective remains a stretch scorecard and never overrides hard risk controls.\n")
    print(json.dumps(result,indent=2),flush=True)

if __name__=="__main__": main()
