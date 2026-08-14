import json
from itertools import product

import numpy as np
import pandas as pd
import yfinance as yf

START_EQ = 2500.0
BASE_BPS = 2.0
STRESS_BPS = 10.0
START = "2007-01-01"
END = "2026-08-01"

RISKY = ["SPY", "QQQ", "IWM", "XLE", "XLP", "XLU", "GLD", "TLT"]
DEFENSIVE = "BIL"
SYMS = RISKY + [DEFENSIVE]

LOOKBACKS = [126, 252]
SMAS = [150, 200]
TOP_NS = [2, 3]
BREADTH_MINS = [2, 4, 6]
RISK_FRACTIONS = [0.4, 0.6, 0.8]
SPY_GATES = [True, False]

DEV_START = pd.Timestamp("2008-01-01").date()
DEV_END = pd.Timestamp("2014-12-31").date()
VAL_START = pd.Timestamp("2015-01-01").date()
VAL_END = pd.Timestamp("2019-12-31").date()
HOLD1_START = pd.Timestamp("2020-01-01").date()
HOLD1_END = pd.Timestamp("2023-12-31").date()
HOLD2_START = pd.Timestamp("2024-01-01").date()
HOLD2_END = pd.Timestamp("2026-07-31").date()


def download_panel():
    d = yf.download(SYMS, start=START, end=END, auto_adjust=True, actions=False,
                    progress=False, group_by="column", threads=False)
    if d.empty:
        raise RuntimeError("No historical data")
    o, c = d["Open"].copy(), d["Close"].copy()
    o.index = pd.to_datetime(o.index).date
    c.index = pd.to_datetime(c.index).date
    coverage = {}
    for s in SYMS:
        v = c[s].dropna()
        coverage[s] = {"start": str(v.index.min()), "end": str(v.index.max()), "bars": int(len(v))}
    common = sorted(c.dropna().index.intersection(o.dropna().index))
    if not common or pd.Timestamp(common[0]).date() > pd.Timestamp("2007-06-30").date():
        raise RuntimeError(f"Insufficient history: {coverage}")
    return o.loc[common].astype(float), c.loc[common].astype(float), coverage


def monthly_rebalance(dates, i):
    if i <= 0:
        return True
    a, b = pd.Timestamp(dates[i-1]), pd.Timestamp(dates[i])
    return a.month != b.month or a.year != b.year


def target_weights(c, i, cfg):
    lb, sma_n = cfg["lookback"], cfg["sma"]
    if i < max(lb, sma_n):
        return {DEFENSIVE: 1.0}
    eligible = []
    for s in RISKY:
        px = float(c.iloc[i][s])
        old = float(c.iloc[i-lb][s])
        sma = float(c[s].iloc[i-sma_n+1:i+1].mean())
        mom = px / old - 1.0 if old > 0 else np.nan
        if np.all(np.isfinite([px, old, sma, mom])) and px > sma and mom > 0:
            eligible.append((s, mom))

    eligible.sort(key=lambda x: x[1], reverse=True)
    breadth = len(eligible)
    risk = cfg["risk_fraction"] if breadth >= cfg["breadth_min"] else 0.0

    if cfg["spy_gate"]:
        spy = float(c.iloc[i]["SPY"])
        spy_sma = float(c["SPY"].iloc[i-sma_n+1:i+1].mean())
        spy_mom = float(c.iloc[i]["SPY"] / c.iloc[i-lb]["SPY"] - 1.0)
        if not (spy > spy_sma and spy_mom > 0):
            risk = 0.0

    chosen = [s for s, _ in eligible[:cfg["top_n"]]]
    if not chosen or risk <= 0:
        return {DEFENSIVE: 1.0}

    w = risk / len(chosen)
    target = {s: w for s in chosen}
    target[DEFENSIVE] = 1.0 - risk
    return target


def normalize(w):
    t = sum(max(x, 0.0) for x in w.values())
    return {s: max(x,0.0)/t for s,x in w.items() if x > 1e-12} if t > 0 else {DEFENSIVE:1.0}


def drift(w, rets):
    return normalize({s:x*(1+rets.get(s,0.0)) for s,x in w.items()})


def turnover(a,b):
    return sum(abs(b.get(s,0)-a.get(s,0)) for s in set(a)|set(b))


def simulate(o,c,start,end,cfg,bps):
    dates=list(c.index); eq=START_EQ; w={DEFENSIVE:1.0}; curve=[]; events=0; turns=0.0
    for i in range(1,len(dates)):
        d=dates[i]
        if d<start: continue
        if d>end: break
        prev,op,cl=c.iloc[i-1],o.iloc[i],c.iloc[i]
        overnight={s:float(op[s]/prev[s]-1) for s in w}
        eq*=1+sum(w[s]*overnight[s] for s in w); w=drift(w,overnight)
        if monthly_rebalance(dates,i):
            target=target_weights(c,i-1,cfg); t=turnover(w,target)
            if t>1e-8:
                eq-=eq*(bps/10000.0)*t; events+=1; turns+=t
            w=target
        intra={s:float(cl[s]/op[s]-1) for s in w}
        eq*=1+sum(w[s]*intra[s] for s in w); w=drift(w,intra)
        curve.append((d,eq))
    return summarize(curve,events,turns)


def summarize(curve,events,turns):
    s=pd.Series([e for _,e in curve],index=pd.to_datetime([d for d,_ in curve]),dtype=float)
    annual={}
    for y in sorted(s.index.year.unique()):
        ys=s[s.index.year==y]; prior=s[s.index<ys.index[0]]; y0=float(prior.iloc[-1]) if len(prior) else START_EQ
        annual[str(y)]=float((ys.iloc[-1]/y0-1)*100)
    total=float((s.iloc[-1]/START_EQ-1)*100); elapsed=max((s.index[-1]-s.index[0]).days/365.25,1/365.25)
    cagr=float(((s.iloc[-1]/START_EQ)**(1/elapsed)-1)*100); dd=float((1-s/s.cummax()).max()*100)
    m=s.resample("ME").last(); mr=(m/m.shift(1)-1).dropna(); pm=float((mr>0).mean()*100) if len(mr) else 0.0
    return {"final_equity":float(s.iloc[-1]),"total_return_pct":total,"cagr_pct":cagr,
            "max_drawdown_pct":dd,"positive_month_rate_pct":pm,"trade_events":events,
            "turnover_sum":turns,"annual_returns_pct":annual}


def years_positive(m, years):
    a=m["annual_returns_pct"]; return all(str(y) in a and a[str(y)]>0 for y in years)


def dev_valid(m):
    return years_positive(m,range(2008,2015)) and m["cagr_pct"]>=3.0 and m["max_drawdown_pct"]<=10.0


def score(m):
    ys=list(m["annual_returns_pct"].values()); weakest=min(ys) if ys else -999
    return 3.0*weakest + m["cagr_pct"] - 0.5*m["max_drawdown_pct"]


def gate(dev,val,h1,h2):
    checks={"development_2008_2014_all_positive":years_positive(dev,range(2008,2015))}
    for y in range(2015,2020): checks[f"validation_{y}_positive_10bps"]=val["annual_returns_pct"].get(str(y),-999)>0
    checks["validation_drawdown"]=val["max_drawdown_pct"]<=10.0
    for y in range(2020,2024): checks[f"holdout1_{y}_positive_10bps"]=h1["annual_returns_pct"].get(str(y),-999)>0
    checks["holdout1_drawdown"]=h1["max_drawdown_pct"]<=10.0
    for y in range(2024,2027): checks[f"holdout2_{y}_positive_10bps"]=h2["annual_returns_pct"].get(str(y),-999)>0
    checks["holdout2_drawdown"]=h2["max_drawdown_pct"]<=10.0
    return checks,all(checks.values())


def fmt(m):
    yrs=", ".join(f"{y}:{r:+.2f}%" for y,r in m["annual_returns_pct"].items())
    return f"return {m['total_return_pct']:+.2f}% | CAGR {m['cagr_pct']:+.2f}% | DD {m['max_drawdown_pct']:.2f}% | positive months {m['positive_month_rate_pct']:.1f}% | [{yrs}]"


def main():
    print("Downloading Phase 5D history..."); o,c,coverage=download_panel()
    configs=[{"lookback":lb,"sma":sma,"top_n":n,"breadth_min":b,"risk_fraction":rf,"spy_gate":sg}
             for lb,sma,n,b,rf,sg in product(LOOKBACKS,SMAS,TOP_NS,BREADTH_MINS,RISK_FRACTIONS,SPY_GATES)]
    candidates=[]
    for cfg in configs:
        d10=simulate(o,c,DEV_START,DEV_END,cfg,STRESS_BPS)
        candidates.append({"config":cfg,"development_10bps":d10,"dev_valid":dev_valid(d10),"score":score(d10)})
    valid=[x for x in candidates if x["dev_valid"]]; selected=max(valid if valid else candidates,key=lambda x:x["score"]); cfg=selected["config"]
    d2=simulate(o,c,DEV_START,DEV_END,cfg,BASE_BPS); v2=simulate(o,c,VAL_START,VAL_END,cfg,BASE_BPS); v10=simulate(o,c,VAL_START,VAL_END,cfg,STRESS_BPS)
    h12=simulate(o,c,HOLD1_START,HOLD1_END,cfg,BASE_BPS); h110=simulate(o,c,HOLD1_START,HOLD1_END,cfg,STRESS_BPS)
    h22=simulate(o,c,HOLD2_START,HOLD2_END,cfg,BASE_BPS); h210=simulate(o,c,HOLD2_START,HOLD2_END,cfg,STRESS_BPS)
    checks,passed=gate(selected["development_10bps"],v10,h110,h210)
    result={"phase":"5D","strategy":"protective momentum with breadth and trend risk scaling","starting_equity":START_EQ,
            "universe":RISKY,"defensive_asset":DEFENSIVE,"candidate_count":len(configs),"valid_development_candidates":len(valid),
            "selected_config":cfg,"coverage":coverage,"development_2bps":d2,"development_10bps":selected["development_10bps"],
            "validation_2bps":v2,"validation_10bps":v10,"holdout1_2bps":h12,"holdout1_10bps":h110,
            "holdout2_2bps":h22,"holdout2_10bps":h210,"gate_checks":checks,"gate":"PASS" if passed else "FAIL","research_only":True}
    with open("phase5d_results.json","w") as f: json.dump(result,f,indent=2)
    failures=[k for k,v in checks.items() if not v]
    summary=f"""# MarketPulse Phase 5D — Protective Momentum\n\n**Gate: {result['gate']}**\n\n## Selected configuration\n- Lookback: **{cfg['lookback']} days**\n- SMA: **{cfg['sma']} days**\n- Top assets: **{cfg['top_n']}**\n- Minimum breadth: **{cfg['breadth_min']} of {len(RISKY)}**\n- Risk allocation when qualified: **{cfg['risk_fraction']*100:.0f}%**\n- SPY confirmation gate: **{cfg['spy_gate']}**\n- Rebalance: **monthly**\n\n## Development 2008–2014\n- Valid candidates: **{len(valid)} / {len(configs)}**\n- 2 bps: {fmt(d2)}\n- 10 bps: {fmt(selected['development_10bps'])}\n\n## Validation 2015–2019\n- 2 bps: {fmt(v2)}\n- 10 bps: {fmt(v10)}\n\n## Holdout 2020–2023\n- 2 bps: {fmt(h12)}\n- 10 bps: {fmt(h110)}\n\n## Final holdout 2024–2026 YTD\n- 2 bps: {fmt(h22)}\n- 10 bps: {fmt(h210)}\n\n## Gate checks\n"""
    for k,v in checks.items(): summary+=f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary+="\n## Failure reasons\n"+("- None\n" if not failures else "".join(f"- {x}\n" for x in failures))
    summary+="\n## Research status\nResearch only. PASS would mean historical consistency under this protocol, not guaranteed future profit. Independent data validation and paper trading remain mandatory.\n"
    with open("phase5d_summary.md","w") as f: f.write(summary)
    print(summary)

if __name__=="__main__": main()
