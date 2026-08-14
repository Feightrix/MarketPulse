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
MR_ASSETS = ["SPY", "QQQ"]
SYMS = sorted(set(RISKY + [DEFENSIVE] + MR_ASSETS))

PROTECTIVE_CFG = {"lookback": 252, "sma": 200, "top_n": 3, "breadth_min": 2,
                  "risk_fraction": 0.60, "spy_gate": True}
RSI_ENTRIES = [5.0, 10.0, 15.0]
RSI_EXITS = [60.0, 70.0, 80.0]
MAX_HOLDS = [2, 3, 5]
PROTECTIVE_WEIGHTS = [0.50, 0.65, 0.80]

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
    common = sorted(c.dropna().index.intersection(o.dropna().index))
    if not common or pd.Timestamp(common[0]).date() > pd.Timestamp("2007-06-30").date():
        raise RuntimeError("Insufficient common history")
    return o.loc[common].astype(float), c.loc[common].astype(float)


def normalize(w):
    t = sum(max(x, 0.0) for x in w.values())
    return {s:max(x,0.0)/t for s,x in w.items() if x>1e-12} if t>0 else {DEFENSIVE:1.0}


def drift(w, rets):
    return normalize({s:x*(1+rets.get(s,0.0)) for s,x in w.items()})


def turnover(a,b):
    return sum(abs(b.get(s,0)-a.get(s,0)) for s in set(a)|set(b))


def monthly_rebalance(dates,i):
    if i<=0: return True
    a,b=pd.Timestamp(dates[i-1]),pd.Timestamp(dates[i])
    return a.month!=b.month or a.year!=b.year


def protective_target(c,i):
    cfg=PROTECTIVE_CFG; lb=cfg["lookback"]; sma_n=cfg["sma"]
    if i<max(lb,sma_n): return {DEFENSIVE:1.0}
    eligible=[]
    for s in RISKY:
        px=float(c.iloc[i][s]); old=float(c.iloc[i-lb][s]); sma=float(c[s].iloc[i-sma_n+1:i+1].mean())
        mom=px/old-1 if old>0 else np.nan
        if np.all(np.isfinite([px,old,sma,mom])) and px>sma and mom>0: eligible.append((s,mom))
    eligible.sort(key=lambda x:x[1],reverse=True)
    risk=cfg["risk_fraction"] if len(eligible)>=cfg["breadth_min"] else 0.0
    spy=float(c.iloc[i]["SPY"]); spy_sma=float(c["SPY"].iloc[i-sma_n+1:i+1].mean()); spy_mom=float(c.iloc[i]["SPY"]/c.iloc[i-lb]["SPY"]-1)
    if cfg["spy_gate"] and not (spy>spy_sma and spy_mom>0): risk=0.0
    chosen=[s for s,_ in eligible[:cfg["top_n"]]]
    if not chosen or risk<=0: return {DEFENSIVE:1.0}
    w=risk/len(chosen); out={s:w for s in chosen}; out[DEFENSIVE]=1-risk; return out


def protective_curve(o,c,start,end,bps):
    dates=list(c.index); eq=1.0; w={DEFENSIVE:1.0}; curve=[]
    for i in range(1,len(dates)):
        d=dates[i]
        if d<start: continue
        if d>end: break
        prev,op,cl=c.iloc[i-1],o.iloc[i],c.iloc[i]
        overnight={s:float(op[s]/prev[s]-1) for s in w}; eq*=1+sum(w[s]*overnight[s] for s in w); w=drift(w,overnight)
        if monthly_rebalance(dates,i):
            target=protective_target(c,i-1); t=turnover(w,target); eq-=eq*(bps/10000.0)*t; w=target
        intra={s:float(cl[s]/op[s]-1) for s in w}; eq*=1+sum(w[s]*intra[s] for s in w); w=drift(w,intra)
        curve.append((d,eq))
    return pd.Series([x for _,x in curve],index=pd.to_datetime([d for d,_ in curve]),dtype=float)


def rsi2_series(s):
    delta=s.diff(); gain=delta.clip(lower=0); loss=(-delta.clip(upper=0))
    avg_gain=gain.rolling(2).mean(); avg_loss=loss.rolling(2).mean(); rs=avg_gain/avg_loss.replace(0,np.nan)
    rsi=100-(100/(1+rs)); rsi= rsi.where(avg_loss>0,100.0); rsi=rsi.where(avg_gain>0,0.0)
    return rsi


def meanrev_curve(o,c,start,end,cfg,bps):
    dates=list(c.index); eq=1.0; holding=None; held=0; curve=[]
    rsi={s:rsi2_series(c[s]) for s in MR_ASSETS}
    sma200={s:c[s].rolling(200).mean() for s in MR_ASSETS}
    w={DEFENSIVE:1.0}
    for i in range(1,len(dates)):
        d=dates[i]
        if d<start: continue
        if d>end: break
        prev,op,cl=c.iloc[i-1],o.iloc[i],c.iloc[i]
        overnight={s:float(op[s]/prev[s]-1) for s in w}; eq*=1+sum(w[s]*overnight[s] for s in w); w=drift(w,overnight)

        signal_i=i-1
        target=w
        if holding is None:
            candidates=[]
            for s in MR_ASSETS:
                rv=float(rsi[s].iloc[signal_i]); px=float(c.iloc[signal_i][s]); sm=float(sma200[s].iloc[signal_i])
                if np.all(np.isfinite([rv,px,sm])) and px>sm and rv<=cfg["entry_rsi"]:
                    candidates.append((s,rv))
            if candidates:
                candidates.sort(key=lambda x:x[1]); holding=candidates[0][0]; held=0; target={holding:1.0}
        else:
            rv=float(rsi[holding].iloc[signal_i]); held+=1
            if (np.isfinite(rv) and rv>=cfg["exit_rsi"]) or held>=cfg["max_hold"]:
                holding=None; held=0; target={DEFENSIVE:1.0}
            else:
                target={holding:1.0}
        t=turnover(w,target)
        if t>1e-8: eq-=eq*(bps/10000.0)*t
        w=target
        intra={s:float(cl[s]/op[s]-1) for s in w}; eq*=1+sum(w[s]*intra[s] for s in w); w=drift(w,intra)
        curve.append((d,eq))
    return pd.Series([x for _,x in curve],index=pd.to_datetime([d for d,_ in curve]),dtype=float)


def combine(p,m,wp):
    idx=p.index.intersection(m.index); p=p.loc[idx]; m=m.loc[idx]
    return START_EQ*(wp*(p/p.iloc[0])+(1-wp)*(m/m.iloc[0]))


def summarize(s):
    annual={}
    for y in sorted(s.index.year.unique()):
        ys=s[s.index.year==y]; prior=s[s.index<ys.index[0]]; y0=float(prior.iloc[-1]) if len(prior) else START_EQ
        annual[str(y)]=float((ys.iloc[-1]/y0-1)*100)
    total=float((s.iloc[-1]/START_EQ-1)*100); elapsed=max((s.index[-1]-s.index[0]).days/365.25,1/365.25)
    cagr=float(((s.iloc[-1]/START_EQ)**(1/elapsed)-1)*100); dd=float((1-s/s.cummax()).max()*100)
    mon=s.resample("ME").last(); mr=(mon/mon.shift(1)-1).dropna(); pm=float((mr>0).mean()*100) if len(mr) else 0.0
    return {"final_equity":float(s.iloc[-1]),"total_return_pct":total,"cagr_pct":cagr,"max_drawdown_pct":dd,
            "positive_month_rate_pct":pm,"annual_returns_pct":annual}


def years_positive(m,years):
    return all(m["annual_returns_pct"].get(str(y),-999)>0 for y in years)


def dev_valid(m):
    return years_positive(m,range(2008,2015)) and m["max_drawdown_pct"]<=10 and m["cagr_pct"]>=3


def score(m):
    ys=list(m["annual_returns_pct"].values()); weakest=min(ys) if ys else -999
    return 3*weakest+m["cagr_pct"]-0.5*m["max_drawdown_pct"]


def eval_block(o,c,start,end,mr_cfg,wp,bps):
    p=protective_curve(o,c,start,end,bps); m=meanrev_curve(o,c,start,end,mr_cfg,bps); return summarize(combine(p,m,wp))


def gate(dev,val,h1,h2):
    checks={"development_2008_2014_all_positive":years_positive(dev,range(2008,2015))}
    for y in range(2015,2020): checks[f"validation_{y}_positive_10bps"]=val["annual_returns_pct"].get(str(y),-999)>0
    checks["validation_drawdown"]=val["max_drawdown_pct"]<=10
    for y in range(2020,2024): checks[f"holdout1_{y}_positive_10bps"]=h1["annual_returns_pct"].get(str(y),-999)>0
    checks["holdout1_drawdown"]=h1["max_drawdown_pct"]<=10
    for y in range(2024,2027): checks[f"holdout2_{y}_positive_10bps"]=h2["annual_returns_pct"].get(str(y),-999)>0
    checks["holdout2_drawdown"]=h2["max_drawdown_pct"]<=10
    return checks,all(checks.values())


def fmt(m):
    yrs=", ".join(f"{y}:{r:+.2f}%" for y,r in m["annual_returns_pct"].items())
    return f"return {m['total_return_pct']:+.2f}% | CAGR {m['cagr_pct']:+.2f}% | DD {m['max_drawdown_pct']:.2f}% | positive months {m['positive_month_rate_pct']:.1f}% | [{yrs}]"


def main():
    print("Downloading Phase 5E history..."); o,c=download_panel(); candidates=[]
    configs=[({"entry_rsi":e,"exit_rsi":x,"max_hold":h},wp) for e,x,h,wp in product(RSI_ENTRIES,RSI_EXITS,MAX_HOLDS,PROTECTIVE_WEIGHTS)]
    for mr,wp in configs:
        d=eval_block(o,c,DEV_START,DEV_END,mr,wp,STRESS_BPS); candidates.append({"mean_reversion":mr,"protective_weight":wp,"development_10bps":d,"dev_valid":dev_valid(d),"score":score(d)})
    valid=[x for x in candidates if x["dev_valid"]]; selected=max(valid if valid else candidates,key=lambda x:x["score"]); mr=selected["mean_reversion"]; wp=selected["protective_weight"]
    d2=eval_block(o,c,DEV_START,DEV_END,mr,wp,BASE_BPS); v2=eval_block(o,c,VAL_START,VAL_END,mr,wp,BASE_BPS); v10=eval_block(o,c,VAL_START,VAL_END,mr,wp,STRESS_BPS)
    h12=eval_block(o,c,HOLD1_START,HOLD1_END,mr,wp,BASE_BPS); h110=eval_block(o,c,HOLD1_START,HOLD1_END,mr,wp,STRESS_BPS)
    h22=eval_block(o,c,HOLD2_START,HOLD2_END,mr,wp,BASE_BPS); h210=eval_block(o,c,HOLD2_START,HOLD2_END,mr,wp,STRESS_BPS)
    checks,passed=gate(selected["development_10bps"],v10,h110,h210)
    result={"phase":"5E","strategy":"protective momentum + short-term reversal ensemble","starting_equity":START_EQ,
            "protective_config":PROTECTIVE_CFG,"selected_mean_reversion":mr,"protective_weight":wp,"reversal_weight":1-wp,
            "candidate_count":len(configs),"valid_development_candidates":len(valid),"development_2bps":d2,"development_10bps":selected["development_10bps"],
            "validation_2bps":v2,"validation_10bps":v10,"holdout1_2bps":h12,"holdout1_10bps":h110,"holdout2_2bps":h22,"holdout2_10bps":h210,
            "gate_checks":checks,"gate":"PASS" if passed else "FAIL","research_only":True}
    with open("phase5e_results.json","w") as f: json.dump(result,f,indent=2)
    failures=[k for k,v in checks.items() if not v]
    summary=f"""# MarketPulse Phase 5E — Momentum/Reversal Ensemble\n\n**Gate: {result['gate']}**\n\n## Architecture\n- Protective momentum sleeve: **{wp*100:.0f}%**\n- Short-term reversal sleeve: **{(1-wp)*100:.0f}%**\n- Protective rules: locked from Phase 5D\n- Reversal entry RSI(2): **<= {mr['entry_rsi']}**\n- Reversal exit RSI(2): **>= {mr['exit_rsi']}** or **{mr['max_hold']} days**\n- Reversal assets: **SPY / QQQ**, only above 200-day SMA\n\n## Development 2008–2014\n- Valid candidates: **{len(valid)} / {len(configs)}**\n- 2 bps: {fmt(d2)}\n- 10 bps: {fmt(selected['development_10bps'])}\n\n## Validation 2015–2019\n- 2 bps: {fmt(v2)}\n- 10 bps: {fmt(v10)}\n\n## Holdout 2020–2023\n- 2 bps: {fmt(h12)}\n- 10 bps: {fmt(h110)}\n\n## Final holdout 2024–2026 YTD\n- 2 bps: {fmt(h22)}\n- 10 bps: {fmt(h210)}\n\n## Gate checks\n"""
    for k,v in checks.items(): summary+=f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary+="\n## Failure reasons\n"+("- None\n" if not failures else "".join(f"- {x}\n" for x in failures))
    summary+="\n## Research status\nResearch only. PASS means the historical protocol was satisfied, not guaranteed future profit. Independent data validation and paper trading remain mandatory.\n"
    with open("phase5e_summary.md","w") as f: f.write(summary)
    print(summary)

if __name__=="__main__": main()
