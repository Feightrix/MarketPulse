import json, math, os, time, urllib.parse, urllib.request
from itertools import product
import numpy as np
import pandas as pd

BASE='https://data.alpaca.markets/v2/stocks/{symbol}/bars'
SYMBOLS=['SPY','QQQ','IWM']
START='2021-01-01T00:00:00Z'
END='2026-08-01T00:00:00Z'
START_EQ=100.0
CAPITAL=0.95
MAX_TRADES=3
COOLDOWN_MIN=15
BASE_FRICTION_BPS=2.0
TZ='America/New_York'


def headers():
    key=os.getenv('ALPACA_API_KEY_ID'); sec=os.getenv('ALPACA_API_SECRET_KEY')
    if not key or not sec:
        raise RuntimeError('Missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY')
    return {'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':sec}


def fetch(symbol):
    out=[]; token=None; h=headers()
    while True:
        p={'timeframe':'5Min','start':START,'end':END,'adjustment':'all','feed':'iex','limit':10000,'sort':'asc'}
        if token: p['page_token']=token
        req=urllib.request.Request(BASE.format(symbol=symbol)+'?'+urllib.parse.urlencode(p),headers=h)
        with urllib.request.urlopen(req,timeout=60) as r: js=json.loads(r.read().decode())
        out.extend(js.get('bars',[])); token=js.get('next_page_token')
        if not token: break
        time.sleep(.05)
    if not out: raise RuntimeError(f'No bars for {symbol}')
    d=pd.DataFrame(out)
    d['ts']=pd.to_datetime(d['t'],utc=True).dt.tz_convert(TZ)
    d=d.set_index('ts').sort_index().rename(columns={'o':'open','h':'high','l':'low','c':'close','v':'volume'})
    return d[['open','high','low','close','volume']].astype(float)


def rsi(s,n=7):
    delta=s.diff(); up=delta.clip(lower=0); dn=-delta.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=au/ad.replace(0,np.nan)
    return (100-100/(1+rs)).fillna(50)


def prep(symbol,d):
    d=d.between_time('09:30','16:00',inclusive='left').copy(); d['symbol']=symbol; d['session']=d.index.date
    chunks=[]
    for _,x in d.groupby('session',sort=True):
        x=x.copy(); x['ema9']=x.close.ewm(span=9,adjust=False).mean(); x['ema21']=x.close.ewm(span=21,adjust=False).mean(); x['rsi7']=rsi(x.close)
        typ=(x.high+x.low+x.close)/3; cv=x.volume.cumsum().replace(0,np.nan); x['vwap']=(typ*x.volume).cumsum()/cv
        x['vr']=x.volume/x.volume.rolling(20,min_periods=5).mean().replace(0,np.nan)
        chunks.append(x)
    d=pd.concat(chunks).sort_index()
    tm=d.index.time; m=(tm>=pd.Timestamp('09:45').time())&(tm<=pd.Timestamp('11:30').time()); a=(tm>=pd.Timestamp('14:00').time())&(tm<=pd.Timestamp('15:30').time())
    d['time_ok']=m|a
    return d


def load_data():
    data={}
    for s in SYMBOLS:
        print('Downloading',s); data[s]=prep(s,fetch(s)); print(len(data[s]),'bars')
    return data


def signals(d,rsi_level):
    reclaim=(d.close.shift(1)<=d.ema9.shift(1))&(d.close>d.ema9)
    rr=(d.rsi7.shift(1)<rsi_level)&(d.rsi7>=rsi_level)
    trend=(d.ema9>d.ema21)&(d.close>d.vwap); vol=d.vr.fillna(0)>=0.8
    return (reclaim&rr&trend&vol&d.time_ok).shift(1,fill_value=False)


def score(row):
    return max(0,row.ema9/row.ema21-1)*4+max(0,row.close/row.vwap-1)*2+max(0,(row.rsi7-50)/100)


def metrics(trades,eq,maxdd):
    if not trades:
        return {'trades':0,'final_equity':START_EQ,'total_return':0,'win_rate':0,'expectancy_bps':0,'profit_factor':0,'max_drawdown':0,'daily_sharpe':0,'positive_days':0}
    t=pd.DataFrame(trades); wins=t.loc[t.pnl>0,'pnl'].sum(); losses=-t.loc[t.pnl<0,'pnl'].sum(); t['date']=pd.to_datetime(t.exit_time).dt.date
    daily=t.groupby('date')['ret'].sum(); sd=daily.std(ddof=0); sharpe=float(daily.mean()/sd*math.sqrt(252)) if len(daily)>1 and sd>0 else 0
    return {'trades':int(len(t)),'final_equity':float(eq),'total_return':float(eq/START_EQ-1),'win_rate':float((t.pnl>0).mean()),'expectancy_bps':float(t.ret.mean()*10000),'profit_factor':float(wins/losses) if losses>0 else 99.0,'max_drawdown':float(maxdd),'daily_sharpe':sharpe,'positive_days':float((daily>0).mean())}


def sim(data,start,end,tp,sl,hold,rsi_level,friction_bps):
    st=pd.Timestamp(start,tz=TZ); en=pd.Timestamp(end,tz=TZ)+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1)
    frames={s:d[(d.index>=st)&(d.index<=en)].copy() for s,d in data.items()}; sig={s:signals(d,rsi_level) for s,d in frames.items()}; timeline=sorted(set().union(*[set(d.index) for d in frames.values()]))
    eq=START_EQ; peak=eq; maxdd=0; pos=None; trades=[]; perday={}; cooldown=None; fr=friction_bps/10000
    for ts in timeline:
        day=ts.date(); perday.setdefault(day,0)
        if pos:
            s=pos['s']; d=frames[s]
            if ts in d.index:
                b=d.loc[ts]; pos['bars']+=1; stop=pos['entry']*(1-sl); target=pos['entry']*(1+tp); raw=None; why=None
                if b.low<=stop: raw=stop; why='STOP'
                elif b.high>=target: raw=target; why='TARGET'
                elif pos['bars']>=hold: raw=b.close; why='TIME'
                elif ts.time()>=pd.Timestamp('15:50').time(): raw=b.close; why='EOD'
                if raw is not None:
                    xp=float(raw)*(1-fr); pnl=pos['qty']*(xp-pos['entry']); ret=pnl/pos['eq0']; eq+=pnl; peak=max(peak,eq); maxdd=max(maxdd,(peak-eq)/peak if peak else 0)
                    trades.append({'s':s,'entry_time':pos['time'],'exit_time':ts,'pnl':pnl,'ret':ret,'reason':why}); perday[day]+=1; cooldown=ts+pd.Timedelta(minutes=COOLDOWN_MIN); pos=None
            continue
        if cooldown is not None and ts<=cooldown: continue
        if perday[day]>=MAX_TRADES: continue
        cand=[]
        for s,d in frames.items():
            if ts not in d.index: continue
            i=d.index.get_loc(ts)
            if not isinstance(i,(int,np.integer)) or i<=0 or not bool(sig[s].iloc[i]): continue
            signal_bar=d.iloc[i-1]; entry_bar=d.iloc[i]
            if not bool(signal_bar.time_ok): continue
            cand.append((score(signal_bar),s,entry_bar))
        if cand:
            _,s,b=max(cand,key=lambda z:z[0]); ep=float(b.open)*(1+fr); qty=(eq*CAPITAL)/ep; pos={'s':s,'time':ts,'entry':ep,'qty':qty,'eq0':eq,'bars':0}
    if pos:
        d=frames[pos['s']]; tail=d[d.index>=pos['time']]
        if len(tail):
            ts=tail.index[-1]; xp=float(tail.iloc[-1].close)*(1-fr); pnl=pos['qty']*(xp-pos['entry']); ret=pnl/pos['eq0']; eq+=pnl; peak=max(peak,eq); maxdd=max(maxdd,(peak-eq)/peak if peak else 0); trades.append({'s':pos['s'],'entry_time':pos['time'],'exit_time':ts,'pnl':pnl,'ret':ret,'reason':'FINAL'})
    return metrics(trades,eq,maxdd)


def sel_score(m):
    if m['trades']<150 or m['expectancy_bps']<=0: return -1e9
    return m['daily_sharpe']+1.5*m['win_rate']+.25*min(m['profit_factor'],3)+.05*min(m['expectancy_bps'],10)-max(0,m['max_drawdown']-.12)*10


def rounded(m):
    r=dict(m)
    for k in ['final_equity','total_return','win_rate','expectancy_bps','profit_factor','max_drawdown','daily_sharpe','positive_days']:
        r[k]=round(float(r[k]),4 if k not in ['total_return','win_rate','max_drawdown','positive_days'] else 6)
    return r


def main():
    data=load_data(); grid=list(product([.002,.003,.004],[.0015,.002,.0025],[4,6],[48,50])); train=[]
    for n,(tp,sl,hold,rr) in enumerate(grid,1):
        m=sim(data,'2021-01-01','2023-12-31',tp,sl,hold,rr,BASE_FRICTION_BPS); train.append({'tp':tp,'sl':sl,'hold':hold,'rsi':rr,'m':m,'score':sel_score(m)}); print('train',n,'/',len(grid),sel_score(m))
    finalists=sorted(train,key=lambda x:x['score'],reverse=True)[:8]; vals=[]
    for c in finalists:
        m=sim(data,'2024-01-01','2024-12-31',c['tp'],c['sl'],c['hold'],c['rsi'],BASE_FRICTION_BPS); vals.append({**c,'val':m,'vscore':sel_score(m)})
    best=max(vals,key=lambda x:x['vscore']); tp,sl,hold,rr=best['tp'],best['sl'],best['hold'],best['rsi']
    holdout=sim(data,'2025-01-01','2026-07-31',tp,sl,hold,rr,BASE_FRICTION_BPS)
    stress={str(b):sim(data,'2025-01-01','2026-07-31',tp,sl,hold,rr,b) for b in [2.,4.,6.,10.]}
    neigh=[]
    for tp2 in sorted(set([max(.0015,tp-.0005),tp,tp+.0005])):
        for sl2 in sorted(set([max(.001,sl-.0005),sl,sl+.0005])):
            neigh.append(sim(data,'2025-01-01','2026-07-31',tp2,sl2,hold,rr,BASE_FRICTION_BPS))
    npos=sum(m['expectancy_bps']>0 for m in neigh)
    gate=holdout['trades']>=100 and holdout['expectancy_bps']>0 and holdout['profit_factor']>1.05 and holdout['max_drawdown']<.15 and stress['6.0']['expectancy_bps']>0 and npos>=math.ceil(len(neigh)*.65)
    result={'data':{'source':'Alpaca Market Data API / IEX','symbols':SYMBOLS,'timeframe':'5Min','range':'2021-01-01 through 2026-07-31','train':'2021-2023','validation':'2024','holdout':'2025-2026-07-31'},'selected':{'take_profit_pct':tp,'stop_loss_pct':sl,'max_hold_minutes':hold*5,'rsi_reclaim':rr,'capital_fraction':CAPITAL,'max_trades_per_day':MAX_TRADES,'cooldown_minutes':COOLDOWN_MIN},'train':rounded(best['m']),'validation':rounded(best['val']),'holdout':rounded(holdout),'friction_stress':{k:rounded(v) for k,v in stress.items()},'neighbor_positive':npos,'neighbor_total':len(neigh),'gate':'PASS' if gate else 'FAIL','warning':'Backtests cannot guarantee future profit. Paper trading is required before live money.'}
    with open('micro_backtest_results.json','w') as f: json.dump(result,f,indent=2)
    lines=['# MarketPulse Micro — Phase 3 Intraday Validation','',f"**Data:** Alpaca IEX 5-minute bars, 2021-01-01 through 2026-07-31  ",f"**Universe:** {', '.join(SYMBOLS)}  ",'**Starting capital model:** $100, 1x buying power, long-only  ',f'**Base friction:** {BASE_FRICTION_BPS:.1f} bps one-way  ','','## Selected micro setup','',f'- Take profit: **{tp:.2%}**',f'- Stop loss: **{sl:.2%}**',f'- Maximum hold: **{hold*5} minutes**',f'- RSI reclaim: **{rr}**',f'- Max trades/day: **{MAX_TRADES}**',f'- Cooldown: **{COOLDOWN_MIN} minutes**','','## Results','','| Period | Trades | Return | Win rate | Expectancy | Profit factor | Daily Sharpe | Max DD | Positive days |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for label,m in [('Train 2021-2023',result['train']),('Validation 2024',result['validation']),('Untouched holdout 2025-2026-07',result['holdout'])]: lines.append(f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | {m['expectancy_bps']:.2f} bps/trade | {m['profit_factor']:.2f} | {m['daily_sharpe']:.2f} | {m['max_drawdown']:.2%} | {m['positive_days']:.2%} |")
    lines+=['','## Friction stress — untouched holdout','','| One-way friction | Expectancy | Return | Profit factor |','|---:|---:|---:|---:|']
    for b,m in result['friction_stress'].items(): lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.2f} bps/trade | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines+=['',f"**Nearby parameter combinations profitable on holdout:** {npos}/{len(neigh)}  ",f"**Phase 3 historical gate:** {result['gate']}",'','## Important','','This is a historical simulation, not a guarantee or promise of profit. Micro strategies are especially sensitive to spread, slippage, fills, data-feed differences, taxes, and market regime changes. The exact locked strategy must pass live paper trading before any real-money automation is considered.']
    with open('micro_backtest_summary.md','w') as f: f.write('\n'.join(lines)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
