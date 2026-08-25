import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2
import options_pattern2_trend_refinement as p2t
import options_pattern2_contract_sim as cs
import options_pattern2_contract_ab as ab
import options_pattern3_fast_research as p3

RESULT_JSON = "options_pattern2_transfer_universe_results.json"
RESULT_MD = "options_pattern2_transfer_universe_results.md"
STARTING_BALANCE = 2500.0
MONTHLY_TARGET = 1000.0
LOOKBACK_DAYS = 540
ORDER_SUBMISSION_ENABLED = False

UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "GLD", "SLV", "TLT",
    "AAPL", "NVDA", "AMD", "TSLA", "AMZN", "META", "MSFT", "GOOGL", "PLTR", "BAC",
]
MIN_SYMBOLS_PER_DAY = 14
DEV_FRACTION = 0.70
MIN_TRADES_PER_DEV_FOLD = 5
MIN_DEV_COMBINED_TRADES = 12
MIN_DEV_COMBINED_PF = 1.15

FROZEN_P2_CFG = {"max_vwap_slope_atr": 0.50, "max_efficiency": 0.65, "min_rsi_turn": 3.0}
CONTRACT_POLICY = {"target_dte": 4, "side_moneyness": -0.005, "target_abs_delta": 0.60}
DTE_MIN, DTE_MAX = 1, 10
MAX_SHORTLIST = 8
MIN_PREMIUM_DOLLARS = 30.0
MAX_PREMIUM_DOLLARS = 500.0
MAX_PREMIUM_FRACTION = 0.20
MAX_CONCURRENT = 2
MAX_DAILY_ENTRIES = 5
MAX_OPEN_PREMIUM_FRACTION = 0.40
DAILY_LOSS_LOCK = 75.0
EST_ROUNDTRIP_FEES = 0.11
FALLBACK_HALF_SPREAD_PCT = 0.005
FALLBACK_MIN_HALF_SPREAD = 0.01


def aggregate_5m(raw):
    out = []
    for i in range(0, len(raw) - 4, 5):
        g = raw[i:i + 5]
        if len(g) < 5:
            continue
        out.append({
            "ts": g[0]["ts"], "o": g[0]["o"], "h": max(x["h"] for x in g),
            "l": min(x["l"] for x in g), "c": g[-1]["c"], "v": sum(x["v"] for x in g),
        })
    return out


def fetch_sessions():
    old = p3.LOOKBACK_DAYS
    p3.LOOKBACK_DAYS = LOOKBACK_DAYS
    out = {}
    try:
        for symbol in UNIVERSE:
            print("fetch", symbol, flush=True)
            out[symbol] = p3.parse_sessions(p3.fetch_bars(symbol))
    finally:
        p3.LOOKBACK_DAYS = old
    return out


def build_signals(symbol, sessions, eval_set):
    day_items = []
    lookup = {}
    for day, raw in sorted(sessions.items()):
        if day not in eval_set:
            continue
        bars = aggregate_5m(raw)
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        p2.add_atr_rsi(bars)
        day_items.append((day, bars))
        for b in bars:
            lookup[b["ts"].isoformat()] = b
    trades = p2t.evaluate(day_items, FROZEN_P2_CFG)
    out = []
    for t in trades:
        b = lookup.get(t["entry_ts"])
        if not b:
            continue
        item = dict(t)
        item["symbol"] = symbol
        item["underlying_entry_spot"] = round(float(b["c"]), 4)
        out.append(item)
    return out


def r_summary(trades):
    return base.summarize(trades)


def select_symbols(signals_by_symbol, dev1, dev2):
    selected = []
    diagnostics = {}
    for symbol in UNIVERSE:
        trades = signals_by_symbol.get(symbol, [])
        f1 = [t for t in trades if datetime.fromisoformat(t["date"]).date() in dev1]
        f2 = [t for t in trades if datetime.fromisoformat(t["date"]).date() in dev2]
        combined = f1 + f2
        s1, s2, sc = r_summary(f1), r_summary(f2), r_summary(combined)
        passed = (
            s1["trades"] >= MIN_TRADES_PER_DEV_FOLD and s2["trades"] >= MIN_TRADES_PER_DEV_FOLD
            and s1["net_r"] > 0 and s2["net_r"] > 0
            and (s1["profit_factor"] or 0) > 1.0 and (s2["profit_factor"] or 0) > 1.0
            and sc["trades"] >= MIN_DEV_COMBINED_TRADES and sc["net_r"] > 0
            and (sc["profit_factor"] or 0) >= MIN_DEV_COMBINED_PF
        )
        diagnostics[symbol] = {"dev_fold_1": s1, "dev_fold_2": s2, "development": sc, "selected_before_holdout": passed}
        if passed:
            selected.append(symbol)
    return selected, diagnostics


def side_moneyness(side, strike, spot):
    return strike / spot - 1.0 if side == "CALL" else 1.0 - strike / spot


def fetch_contracts(underlying, signal_date, side, spot):
    day = datetime.fromisoformat(signal_date).date()
    option_type = "call" if side == "CALL" else "put"
    contracts = []
    for status in ("inactive", "active"):
        data = cs.request_json(
            f"{cs.PAPER_BASE}/v2/options/contracts",
            params={
                "underlying_symbols": underlying,
                "status": status,
                "expiration_date_gte": (day + timedelta(days=DTE_MIN)).isoformat(),
                "expiration_date_lte": (day + timedelta(days=DTE_MAX)).isoformat(),
                "type": option_type,
                "strike_price_gte": f"{max(0.5, spot * 0.88):.2f}",
                "strike_price_lte": f"{spot * 1.12:.2f}",
                "limit": 1000,
            },
        )
        if data:
            contracts.extend(data.get("option_contracts", []))
    return list({c.get("symbol"): c for c in contracts if c.get("symbol")}.values())


def metas(contracts, signal_date, side, spot):
    day = datetime.fromisoformat(signal_date).date()
    out = []
    for c in contracts:
        try:
            exp = datetime.fromisoformat(c["expiration_date"]).date()
            strike = float(c["strike_price"])
        except Exception:
            continue
        dte = (exp - day).days
        if not DTE_MIN <= dte <= DTE_MAX:
            continue
        out.append({
            "symbol": c["symbol"], "expiration_date": c["expiration_date"], "strike": strike, "dte": dte,
            "side_moneyness": side_moneyness(side, strike, spot), "open_interest": int(c.get("open_interest") or 0),
        })
    out.sort(key=lambda m: (abs(m["dte"] - CONTRACT_POLICY["target_dte"]), abs(m["side_moneyness"] - CONTRACT_POLICY["side_moneyness"]), -m["open_interest"]))
    return out[:MAX_SHORTLIST]


def conservative_fill(bar, entry):
    px = float(bar.get("o") if entry else bar.get("c"))
    haircut = max(FALLBACK_MIN_HALF_SPREAD, px * FALLBACK_HALF_SPREAD_PCT)
    return px + haircut if entry else max(0.01, px - haircut)


def candidate_score(c):
    return (
        0.30 * abs(c["dte"] - CONTRACT_POLICY["target_dte"]) / 3.0
        + 0.30 * abs(c["side_moneyness"] - CONTRACT_POLICY["side_moneyness"]) / 0.005
        + 0.40 * abs(c["abs_delta"] - CONTRACT_POLICY["target_abs_delta"]) / 0.15
    )


def price_signal(sig):
    entry_ts = cs.parse_ts(sig["entry_ts"]); exit_ts = cs.parse_ts(sig["exit_ts"])
    date = entry_ts.date().isoformat(); spot = float(sig["underlying_entry_spot"]); underlying = sig["symbol"]
    ms = metas(fetch_contracts(underlying, date, sig["side"], spot), date, sig["side"], spot)
    if not ms:
        return None, "no_contracts"
    entry_ref = entry_ts + timedelta(minutes=ab.ENTRY_DELAY_MINUTES)
    exit_ref = exit_ts + timedelta(minutes=ab.EXIT_BAR_OFFSET_MINUTES)
    if exit_ref <= entry_ref: exit_ref = entry_ref + timedelta(minutes=1)
    bar_map = ab.fetch_option_bars_multi([m["symbol"] for m in ms], entry_ts, exit_ref + timedelta(minutes=2))
    priced = []
    for m in ms:
        bars = bar_map.get(m["symbol"])
        if not bars: continue
        eb = ab.first_bar_at_or_after(bars, entry_ref); xb = ab.first_bar_at_or_after(bars, exit_ref)
        if eb is None or xb is None: continue
        entry = conservative_fill(eb, True); exit_px = conservative_fill(xb, False); premium = entry * 100.0
        if premium < MIN_PREMIUM_DOLLARS or premium > MAX_PREMIUM_DOLLARS: continue
        iv, delta = cs.implied_vol_and_delta(spot, m["strike"], entry, m["dte"], sig["side"])
        if delta is None: continue
        c = {**m, "entry": entry, "exit": exit_px, "premium": premium, "iv_proxy": iv, "delta_proxy": delta, "abs_delta": abs(delta)}
        priced.append(c)
    if not priced: return None, "no_affordable_priced_contract"
    c = min(priced, key=candidate_score)
    net = (c["exit"] - c["entry"]) * 100.0 - EST_ROUNDTRIP_FEES
    return {
        "symbol": underlying, "date": date, "side": sig["side"], "entry_ts": sig["entry_ts"], "exit_ts": sig["exit_ts"],
        "contract": c["symbol"], "expiration_date": c["expiration_date"], "dte": c["dte"], "strike": c["strike"],
        "delta_proxy": round(c["delta_proxy"], 4), "iv_proxy": c["iv_proxy"],
        "premium_dollars": round(c["premium"], 2), "entry_fill": round(c["entry"], 4), "exit_fill": round(c["exit"], 4),
        "net_pl_dollars": round(net, 2), "underlying_r": sig["r"], "fill_mode": "trade_bar_conservative",
    }, None


def option_summary(trades, starting=STARTING_BALANCE):
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "net_pl_dollars": 0.0, "ending_balance_dollars": starting, "return_pct": 0.0, "profit_factor": None, "expectancy_dollars": 0.0, "max_drawdown_dollars": 0.0, "avg_premium_dollars": 0.0}
    ordered = sorted(trades, key=lambda t: t["entry_ts"]); pls = [float(t["net_pl_dollars"]) for t in ordered]
    wins=[p for p in pls if p>0]; losses=[p for p in pls if p<=0]; gp=sum(wins); gl=-sum(losses)
    eq=starting; peak=eq; dd=0.0
    for p in pls: eq+=p; peak=max(peak,eq); dd=max(dd,peak-eq)
    net=sum(pls)
    return {"trades":len(pls),"wins":len(wins),"losses":len(losses),"win_rate_pct":round(100*len(wins)/len(pls),2),"net_pl_dollars":round(net,2),"ending_balance_dollars":round(starting+net,2),"return_pct":round(100*net/starting,2),"profit_factor":round(gp/gl,3) if gl>0 else None,"expectancy_dollars":round(net/len(pls),2),"max_drawdown_dollars":round(dd,2),"avg_premium_dollars":round(statistics.mean(t["premium_dollars"] for t in ordered),2)}


def account_sim(trades):
    cash=STARTING_BALANCE; open_pos=[]; accepted=[]; daily_entries=defaultdict(int); daily_realized=defaultdict(float); skips=Counter()
    def close_due(now):
        nonlocal cash, open_pos
        remaining=[]
        for p in open_pos:
            if cs.parse_ts(p["exit_ts"]) <= now:
                pl=float(p["net_pl_dollars"]); cash += float(p["premium_dollars"]) + pl; daily_realized[p["date"]] += pl
            else: remaining.append(p)
        open_pos=remaining
    for t in sorted(trades,key=lambda x:x["entry_ts"]):
        et=cs.parse_ts(t["entry_ts"]); close_due(et); day=t["date"]
        if daily_entries[day]>=MAX_DAILY_ENTRIES: skips["daily_cap"]+=1; continue
        if daily_realized[day] <= -DAILY_LOSS_LOCK: skips["daily_loss_lock"]+=1; continue
        if len(open_pos)>=MAX_CONCURRENT: skips["concurrency"]+=1; continue
        if any(p["symbol"]==t["symbol"] for p in open_pos): skips["same_symbol_open"]+=1; continue
        locked=sum(float(p["premium_dollars"]) for p in open_pos); equity=cash+locked; prem=float(t["premium_dollars"])
        if prem > min(MAX_PREMIUM_DOLLARS,equity*MAX_PREMIUM_FRACTION) or prem>cash: skips["premium_cap"]+=1; continue
        if locked+prem > equity*MAX_OPEN_PREMIUM_FRACTION: skips["open_premium_cap"]+=1; continue
        cash-=prem; open_pos.append(t); accepted.append(t); daily_entries[day]+=1
    close_due(datetime.max.replace(tzinfo=timezone.utc))
    s=option_summary(accepted); s["ending_cash_dollars"]=round(cash,2); s["skips"]=dict(skips); return accepted,s


def monthly(trades, eval_dates):
    by=defaultdict(float); count=defaultdict(int)
    for t in trades: by[t["date"][:7]]+=float(t["net_pl_dollars"]); count[t["date"][:7]]+=1
    months=[]; cur=eval_dates[0].replace(day=1); last=eval_dates[-1].replace(day=1)
    while cur<=last:
        months.append(cur.strftime("%Y-%m")); cur=cur.replace(year=cur.year+(1 if cur.month==12 else 0),month=1 if cur.month==12 else cur.month+1)
    vals=[by[m] for m in months]
    return {"months":len(months),"average_monthly_pl_dollars":round(statistics.mean(vals),2) if vals else 0.0,"median_monthly_pl_dollars":round(statistics.median(vals),2) if vals else 0.0,"best_month_dollars":round(max(vals),2) if vals else 0.0,"worst_month_dollars":round(min(vals),2) if vals else 0.0,"positive_months_pct":round(100*sum(v>0 for v in vals)/len(vals),2) if vals else 0.0,"months_at_or_above_1000":sum(v>=MONTHLY_TARGET for v in vals),"details":[{"month":m,"pl_dollars":round(by[m],2),"trades":count[m]} for m in months]}


def main():
    sessions=fetch_sessions(); coverage=Counter()
    for ds in sessions.values():
        for d in ds: coverage[d]+=1
    eval_dates=sorted(d for d,n in coverage.items() if n>=MIN_SYMBOLS_PER_DAY)
    if len(eval_dates)<180: raise RuntimeError(f"Insufficient eval sessions: {len(eval_dates)}")
    eval_set=set(eval_dates); cut=int(len(eval_dates)*DEV_FRACTION); dev_dates=eval_dates[:cut]; holdout_dates=eval_dates[cut:]; mid=len(dev_dates)//2
    dev1=set(dev_dates[:mid]); dev2=set(dev_dates[mid:]); holdout=set(holdout_dates)

    signals_by_symbol={}
    for symbol in UNIVERSE:
        signals_by_symbol[symbol]=build_signals(symbol,sessions.get(symbol,{}),eval_set)
    selected,diagnostics=select_symbols(signals_by_symbol,dev1,dev2)
    print("selected before holdout",selected,flush=True)
    for symbol in UNIVERSE:
        ht=[t for t in signals_by_symbol[symbol] if datetime.fromisoformat(t["date"]).date() in holdout]
        diagnostics[symbol]["holdout_underlying"] = r_summary(ht)

    priced=[]; pricing_skips=Counter(); per_symbol_option={}
    for symbol in selected:
        symbol_priced=[]
        for idx,sig in enumerate(signals_by_symbol[symbol],1):
            trade,reason=price_signal(sig)
            if trade: symbol_priced.append(trade); priced.append(trade)
            else: pricing_skips[reason or "unknown"]+=1
        dev_opt=[t for t in symbol_priced if datetime.fromisoformat(t["date"]).date() in set(dev_dates)]
        hold_opt=[t for t in symbol_priced if datetime.fromisoformat(t["date"]).date() in holdout]
        per_symbol_option[symbol]={"development":option_summary(dev_opt),"holdout":option_summary(hold_opt),"full":option_summary(symbol_priced)}

    dev_priced=[t for t in priced if datetime.fromisoformat(t["date"]).date() in set(dev_dates)]
    hold_priced=[t for t in priced if datetime.fromisoformat(t["date"]).date() in holdout]
    full_acc,full_summary=account_sim(priced); dev_acc,dev_summary=account_sim(dev_priced); hold_acc,hold_summary=account_sim(hold_priced)
    result={
        "strategy":"frozen_pattern2_universe_transfer","generated_utc":datetime.now(timezone.utc).isoformat(),"order_submission_enabled":ORDER_SUBMISSION_ENABLED,
        "starting_balance":STARTING_BALANCE,"monthly_target":MONTHLY_TARGET,"lookback_days":LOOKBACK_DAYS,"evaluation_sessions":len(eval_dates),
        "development_sessions":len(dev_dates),"holdout_sessions":len(holdout_dates),"holdout_start":holdout_dates[0].isoformat(),"universe":UNIVERSE,
        "frozen_signal_cfg":FROZEN_P2_CFG,"frozen_contract_policy":CONTRACT_POLICY,
        "symbol_selection_rule":{"uses_holdout":False,"min_trades_each_dev_fold":MIN_TRADES_PER_DEV_FOLD,"min_combined_dev_trades":MIN_DEV_COMBINED_TRADES,"min_combined_dev_pf":MIN_DEV_COMBINED_PF,"requires_positive_both_dev_folds":True},
        "selected_symbols_before_holdout":selected,"underlying_diagnostics":diagnostics,"actual_option_by_selected_symbol":per_symbol_option,
        "actual_option_portfolio":{"development":dev_summary,"holdout":hold_summary,"full":full_summary},"monthly_full":monthly(full_acc,eval_dates),"monthly_holdout":monthly(hold_acc,holdout_dates),
        "signals_selected_symbols":sum(len(signals_by_symbol[s]) for s in selected),"priced_option_trades":len(priced),"pricing_skips":dict(pricing_skips),
        "target_met":False,
        "method_note":"Exact frozen Pattern #2 signal and exact frozen 4-DTE ~0.60 delta option mapping are transferred without per-symbol tuning. Symbol selection uses only the first 70% of dates; newest 30% is untouched holdout. Historical fills use actual 1-minute option trade bars plus conservative execution haircut; historical BBO/Greeks are not assumed.",
    }
    result["target_met"] = bool(result["monthly_full"]["average_monthly_pl_dollars"]>=MONTHLY_TARGET and hold_summary["net_pl_dollars"]>0)
    Path(RESULT_JSON).write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    lines=["# MarketPulse — Frozen Pattern #2 Universe Transfer","","**Research only. No orders. No per-symbol parameter tuning.**","",f"Evaluation sessions: **{len(eval_dates)}** | Holdout starts: **{holdout_dates[0]}**",f"Selected using development only: **{', '.join(selected) if selected else 'NONE'}**","","## Actual Options Portfolio",f"- Full: **{full_summary['trades']} trades | ${full_summary['net_pl_dollars']:,.2f} | PF {full_summary['profit_factor']} | DD ${full_summary['max_drawdown_dollars']:,.2f}**",f"- Holdout: **{hold_summary['trades']} trades | ${hold_summary['net_pl_dollars']:,.2f} | PF {hold_summary['profit_factor']} | DD ${hold_summary['max_drawdown_dollars']:,.2f}**",f"- Average month: **${result['monthly_full']['average_monthly_pl_dollars']:,.2f}** | Holdout average month: **${result['monthly_holdout']['average_monthly_pl_dollars']:,.2f}**","","## Selected Symbols"]
    for s in selected:
        o=per_symbol_option[s]; u=diagnostics[s]
        lines.append(f"- {s}: dev underlying **{u['development']['net_r']:.2f}R / PF {u['development']['profit_factor']}**; option dev **${o['development']['net_pl_dollars']:,.2f} / PF {o['development']['profit_factor']}**; option holdout **${o['holdout']['net_pl_dollars']:,.2f} / PF {o['holdout']['profit_factor']}**")
    lines += ["",f"**$1,000/month target met: {'YES' if result['target_met'] else 'NO'}**","","Selection was frozen before viewing the newest 30% holdout. Positive historical performance does not guarantee future returns."]
    Path(RESULT_MD).write_text("\n".join(lines)+"\n")
    print(json.dumps({"selected":selected,"full":full_summary,"holdout":hold_summary,"monthly":result['monthly_full'],"holdout_monthly":result['monthly_holdout']},indent=2),flush=True)


if __name__=="__main__": main()
