import json
from itertools import product

import numpy as np
import pandas as pd
import yfinance as yf

START_EQ=2500.0
BASE_BPS=2.0
STRESS_BPS=10.0
START="2007-01-01"
END="2026-08-01"
RISKY=["SPY","QQQ","IWM","XLE","XLP","XLU","GLD","TLT"]
DEFENSIVE="BIL"
SYMS=RISKY+[DEFENSIVE]

# Locked from Phase 5F. Phase 5G only tests the portfolio safety gate.
LOOKBACK=252
TREND_SMA=150
VOL_WINDOW=42
TARGET_VOL=0.08
RISK_CAP=0.50
BREADTH_MINS=[2,3,4,5,6]
SPY_GATE_SMAS=[150,200]

DEV_START=pd.Timestamp("2008-01-01").date(); DEV_END=pd.Timestamp("2014-12-31").date()
VAL_START=pd.Timestamp("2015-01-01").date(); VAL_END=pd.Timestamp("2019-12-31").date()
H1_START=pd.Timestamp("2020-01-01").date(); H1_END=pd.Timestamp("2023-12-31").date()
H2_START=pd.Timestamp("2024-01-01").date(); H2_END=pd.Timestamp("2026-07-31").date()


def download_panel():
    d=yf.download(SYMS,start=START,end=END,auto_adjust=True,actions=False,progress=False,group_by="column",threads=False)
    if d.empty: raise RuntimeError("No history")
    o,c=d["Open"].copy(),d["Close"].copy(); o.index=pd.to_datetime(o.index).date; c.index=pd.to_datetime(c.index).date
    common=sorted(c.dropna().index.intersection(o.dropna().index))
    if not common or pd.Timestamp(common[0]).date()>pd.Timestamp("2007-06-30").date(): raise RuntimeError("Insufficient common history")
    return o.loc[common].astype(float),c.loc[common].astype(float)


def monthly(dates,i):
    if i<=0:return True
    a,b=pd.Timestamp(dates[i-1]),pd.Timestamp(dates[i]); return a.month!=b.month or a.year!=b.year


def target(c,i,cfg):
    need=max(LOOKBACK,TREND_SMA,VOL_WINDOW+1,cfg["spy_sma"])
    if i<need:return {DEFENSIVE:1.0}
    eligible=[]
    for s in RISKY:
        px=float(c.iloc[i][s]); old=float(c.iloc[i-LOOKBACK][s]); sma=float(c[s].iloc[i-TREND_SMA+1:i+1].mean())
        mom=px/old-1.0 if old>0 else np.nan
        if np.all(np.isfinite([px,old,sma,mom])) and px>sma and mom>0: eligible.append(s)
    if len(eligible)<cfg["breadth_min"]: return {DEFENSIVE:1.0}
    spy=float(c.iloc[i]["SPY"]); spy_old=float(c.iloc[i-LOOKBACK]["SPY"]); spy_sma=float(c["SPY"].iloc[i-cfg["spy_sma"]+1:i+1].mean())
    if not (spy>spy_sma and spy>spy_old): return {DEFENSIVE:1.0}
    r=c[eligible].pct_change().iloc[i-VOL_WINDOW+1:i+1].dropna()
    vols=(r.std(ddof=1)*np.sqrt(252)).replace([np.inf,-np.inf],np.nan).dropna(); vols=vols[vols>0]
    eligible=[s for s in eligible if s in vols.index]
    if not eligible:return {DEFENSIVE:1.0}
    inv=1.0/vols[eligible]; base=inv/inv.sum(); cov=r[eligible].cov().to_numpy()*252.0; w=base.to_numpy(float)
    pvol=float(np.sqrt(max(float(w@cov@w),0.0)))
    if not np.isfinite(pvol) or pvol<=0:return {DEFENSIVE:1.0}
    scale=max(0.0,min(RISK_CAP,TARGET_VOL/pvol)); out={s:float(base[s]*scale) for s in eligible}; out[DEFENSIVE]=1.0-scale; return out


def norm(w):
    t=sum(max(x,0) for x in w.values()); return {s:max(x,0)/t for s,x in w.items() if x>1e-12} if t>0 else {DEFENSIVE:1.0}

def drift(w,rets): return norm({s:x*(1+rets.get(s,0)) for s,x in w.items()})
def turnover(a,b): return sum(abs(b.get(s,0)-a.get(s,0)) for s in set(a)|set(b))


def simulate(o,c,start,end,cfg,bps):
    dates=list(c.index);eq=START_EQ;w={DEFENSIVE:1.0};curve=[];events=0
    for i in range(1,len(dates)):
        d=dates[i]
        if d<start:continue
        if d>end:break
        prev,op,cl=c.iloc[i-1],o.iloc[i],c.iloc[i]
        ov={s:float(op[s]/prev[s]-1) for s in w};eq*=1+sum(w[s]*ov[s] for s in w);w=drift(w,ov)
        if monthly(dates,i):
            tw=target(c,i-1,cfg);t=turnover(w,tw)
            if t>1e-8:eq-=eq*(bps/10000.0)*t;events+=1
            w=tw
        intr={s:float(cl[s]/op[s]-1) for s in w};eq*=1+sum(w[s]*intr[s] for s in w);w=drift(w,intr);curve.append((d,eq))
    return summarize(curve,events)


def summarize(curve,events):
    s=pd.Series([e for _,e in curve],index=pd.to_datetime([d for d,_ in curve]),dtype=float);annual={}
    for y in sorted(s.index.year.unique()):
        ys=s[s.index.year==y];prior=s[s.index<ys.index[0]];y0=float(prior.iloc[-1]) if len(prior) else START_EQ;annual[str(y)]=float((ys.iloc[-1]/y0-1)*100)
    total=float((s.iloc[-1]/START_EQ-1)*100);elapsed=max((s.index[-1]-s.index[0]).days/365.25,1/365.25);cagr=float(((s.iloc[-1]/START_EQ)**(1/elapsed)-1)*100)
    dd=float((1-s/s.cummax()).max()*100);m=s.resample("ME").last();mr=(m/m.shift(1)-1).dropna();pm=float((mr>0).mean()*100) if len(mr) else 0
    return {"final_equity":float(s.iloc[-1]),"total_return_pct":total,"cagr_pct":cagr,"max_drawdown_pct":dd,"positive_month_rate_pct":pm,"trade_events":events,"annual_returns_pct":annual}


def pos(m,yrs):return all(m["annual_returns_pct"].get(str(y),-999)>0 for y in yrs)
def dev_valid(m):return pos(m,range(2008,2015)) and m["max_drawdown_pct"]<=8 and m["cagr_pct"]>=2.5

def score(m):
    ys=list(m["annual_returns_pct"].values());return 5*min(ys)+m["cagr_pct"]-0.75*m["max_drawdown_pct"] if ys else -9999

def gate(dev,val,h1,h2):
    ch={"development_2008_2014_all_positive":pos(dev,range(2008,2015))}
    for y in range(2015,2020):ch[f"validation_{y}_positive_10bps"]=val["annual_returns_pct"].get(str(y),-999)>0
    ch["validation_drawdown"]=val["max_drawdown_pct"]<=8
    for y in range(2020,2024):ch[f"holdout1_{y}_positive_10bps"]=h1["annual_returns_pct"].get(str(y),-999)>0
    ch["holdout1_drawdown"]=h1["max_drawdown_pct"]<=8
    for y in range(2024,2027):ch[f"holdout2_{y}_positive_10bps"]=h2["annual_returns_pct"].get(str(y),-999)>0
    ch["holdout2_drawdown"]=h2["max_drawdown_pct"]<=8
    return ch,all(ch.values())

def fmt(m):
    yrs=", ".join(f"{y}:{r:+.2f}%" for y,r in m["annual_returns_pct"].items());return f"return {m['total_return_pct']:+.2f}% | CAGR {m['cagr_pct']:+.2f}% | DD {m['max_drawdown_pct']:.2f}% | [{yrs}]"


def main():
    o,c=download_panel();configs=[{"breadth_min":b,"spy_sma":s} for b,s in product(BREADTH_MINS,SPY_GATE_SMAS)];cand=[]
    for cfg in configs:
        d=simulate(o,c,DEV_START,DEV_END,cfg,STRESS_BPS);cand.append({"config":cfg,"development_10bps":d,"dev_valid":dev_valid(d),"score":score(d)})
    valid=[x for x in cand if x["dev_valid"]];sel=max(valid if valid else cand,key=lambda x:x["score"]);cfg=sel["config"]
    d2=simulate(o,c,DEV_START,DEV_END,cfg,BASE_BPS);v2=simulate(o,c,VAL_START,VAL_END,cfg,BASE_BPS);v10=simulate(o,c,VAL_START,VAL_END,cfg,STRESS_BPS)
    h12=simulate(o,c,H1_START,H1_END,cfg,BASE_BPS);h110=simulate(o,c,H1_START,H1_END,cfg,STRESS_BPS);h22=simulate(o,c,H2_START,H2_END,cfg,BASE_BPS);h210=simulate(o,c,H2_START,H2_END,cfg,STRESS_BPS)
    checks,passed=gate(sel["development_10bps"],v10,h110,h210)
    result={"phase":"5G","strategy":"Phase 5F vol-target trend plus breadth/SPY safety gate","locked_5f":{"lookback":LOOKBACK,"trend_sma":TREND_SMA,"vol_window":VOL_WINDOW,"target_vol":TARGET_VOL,"risk_cap":RISK_CAP},"selected_gate":cfg,"candidate_count":len(configs),"valid_development_candidates":len(valid),"development_2bps":d2,"development_10bps":sel["development_10bps"],"validation_2bps":v2,"validation_10bps":v10,"holdout1_2bps":h12,"holdout1_10bps":h110,"holdout2_2bps":h22,"holdout2_10bps":h210,"gate_checks":checks,"gate":"PASS" if passed else "FAIL","research_only":True}
    with open("phase5g_results.json","w") as f:json.dump(result,f,indent=2)
    failures=[k for k,v in checks.items() if not v]
    summary=f"""# MarketPulse Phase 5G — Gated Volatility Trend\n\n**Gate: {result['gate']}**\n\n## Locked Phase 5F engine\n- 252-day momentum / 150-day trend SMA\n- 42-day volatility estimator\n- 8% target volatility / 50% max risky allocation\n- Monthly rebalance\n\n## Selected safety gate\n- Minimum eligible breadth: **{cfg['breadth_min']} of {len(RISKY)}**\n- SPY must be above its **{cfg['spy_sma']}-day SMA** and above its 252-day-ago close\n\n## Development 2008–2014\n- Valid gates: **{len(valid)} / {len(configs)}**\n- 2 bps: {fmt(d2)}\n- 10 bps: {fmt(sel['development_10bps'])}\n\n## Validation 2015–2019\n- 2 bps: {fmt(v2)}\n- 10 bps: {fmt(v10)}\n\n## Holdout 2020–2023\n- 2 bps: {fmt(h12)}\n- 10 bps: {fmt(h110)}\n\n## Final holdout 2024–2026 YTD\n- 2 bps: {fmt(h22)}\n- 10 bps: {fmt(h210)}\n\n## Gate checks\n"""
    for k,v in checks.items():summary+=f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary+="\n## Failure reasons\n"+("- None\n" if not failures else "".join(f"- {x}\n" for x in failures));summary+="\n## Research status\nResearch only. PASS would mean historical consistency under this protocol, not guaranteed future profit.\n"
    with open("phase5g_summary.md","w") as f:f.write(summary)
    print(summary)

if __name__=="__main__":main()
