import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

START_EQ = 2500.0
COST_BPS_PER_FILL = 10.0
RISK_PER_TRADE_PCT = 0.0075
MAX_NOTIONAL_PCT = 0.50
MIN_STOP_PCT = 0.005
MAX_STOP_PCT = 0.020
FORCE_EXIT_ET = dtime(15, 55)
NEWS_START_ET = dtime(10, 0)
NEWS_END_ET = dtime(15, 0)
FEED = "iex"
ET = ZoneInfo("America/New_York")
DATA_BASE = "https://data.alpaca.markets"

UNIVERSE = ["NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "NFLX", "GOOGL"]
BENCHMARK = "SPY"
PERIODS = {
    "development_2024": ("2024-01-01", "2024-12-31"),
    "validation_2025": ("2025-01-01", "2025-12-31"),
}

COMPANY_ALIASES = {
    "NVDA": ["nvidia", "nvda"],
    "TSLA": ["tesla", "tsla"],
    "AAPL": ["apple", "aapl"],
    "AMD": ["advanced micro devices", "amd"],
    "META": ["meta", "facebook", "instagram", "whatsapp"],
    "AMZN": ["amazon", "amzn"],
    "NFLX": ["netflix", "nflx"],
    "GOOGL": ["google", "alphabet", "googl"],
}

CATALYST_GROUPS = {
    "earnings": ["earnings", "eps", "revenue", "quarter", "results", "profit", "sales"],
    "guidance": ["guidance", "outlook", "forecast", "raises", "raised", "boosts", "reaffirms"],
    "regulatory": ["fda", "approval", "approved", "trial", "phase 3", "phase iii", "regulatory"],
    "deal": ["acquisition", "acquire", "merger", "takeover", "buyout", "strategic investment"],
    "commercial": ["contract", "order", "partnership", "partner", "deal", "customer", "award"],
    "capital_return": ["buyback", "repurchase", "dividend"],
    "legal": ["settlement", "court", "lawsuit", "antitrust", "investigation", "probe"],
    "product": ["launch", "product", "chip", "platform", "ai", "artificial intelligence"],
}
HARD_GROUPS = {"earnings", "guidance", "regulatory", "deal", "commercial", "capital_return", "legal"}

# Locked before results.
MIN_MOVE_Z = 1.75
MIN_VOLUME_Z = 1.00
MIN_RS_Z = 1.25
MIN_BASELINE_SAMPLES = 250
BASELINE_TAIL_SAMPLES = 1500
HOLD_MINUTES = 8
MAX_RETRACE_FRACTION = 0.60
MAX_TRADES_PER_DAY = 1


def headers():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca credentials required")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_json(url, params, tries=7):
    for attempt in range(tries):
        r = requests.get(url, headers=headers(), params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(2 + 2 * attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Repeated rate limit: {url}")


def catalyst_groups(text):
    t = re.sub(r"\s+", " ", (text or "").lower())
    return [name for name, words in CATALYST_GROUPS.items() if any(w in t for w in words)]


def headline_names_company(headline, symbol):
    h = re.sub(r"\s+", " ", (headline or "").lower())
    for alias in COMPANY_ALIASES[symbol]:
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", h):
            return True
    return False


def month_chunks(start, end):
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    out = []
    cur = s
    while cur <= e:
        nxt = min(cur + pd.offsets.MonthEnd(0), e)
        out.append((cur.date().isoformat(), nxt.date().isoformat()))
        cur = nxt + pd.Timedelta(days=1)
    return out


def fetch_news_chunk(start, end):
    rows, token = [], None
    while True:
        params = {
            "symbols": ",".join(UNIVERSE), "start": start, "end": end,
            "sort": "asc", "limit": 50, "include_content": "false",
        }
        if token:
            params["page_token"] = token
        p = get_json(f"{DATA_BASE}/v1beta1/news", params)
        rows.extend(p.get("news") or [])
        token = p.get("next_page_token")
        if not token:
            break
    return rows


def collect_events(start, end):
    items = []
    chunks = month_chunks(start, end)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(fetch_news_chunk, a, b) for a, b in chunks]
        for fut in as_completed(futs):
            items.extend(fut.result())

    events = []
    seen = set()
    last_kept = {}
    universe = set(UNIVERSE)
    for item in sorted(items, key=lambda x: str(x.get("created_at") or x.get("updated_at") or "")):
        created_raw = item.get("created_at") or item.get("updated_at")
        if not created_raw:
            continue
        created = pd.Timestamp(created_raw)
        if created.tzinfo is None:
            created = created.tz_localize("UTC")
        created = created.tz_convert(ET)
        if not (NEWS_START_ET <= created.time() <= NEWS_END_ET):
            continue
        headline = str(item.get("headline") or "")
        summary = str(item.get("summary") or "")
        groups = catalyst_groups(headline + " " + summary)
        if not groups:
            continue
        syms = universe.intersection(str(x).upper() for x in (item.get("symbols") or []))
        for sym in sorted(syms):
            if not headline_names_company(headline, sym):
                continue
            key = (sym, str(item.get("id") or ""), created.isoformat())
            if key in seen:
                continue
            prev = last_kept.get(sym)
            if prev is not None and (created - prev).total_seconds() < 3600:
                continue
            seen.add(key)
            events.append({
                "symbol": sym, "created_et": created, "headline": headline,
                "source": str(item.get("source") or ""), "groups": groups,
                "hard_catalyst": bool(set(groups) & HARD_GROUPS),
            })
            last_kept[sym] = created
    return sorted(events, key=lambda x: x["created_et"])


def fetch_symbol_bars(symbol, start, end):
    bars, token = [], None
    params = {
        "timeframe": "1Min", "start": f"{start}T13:00:00Z", "end": f"{end}T21:15:00Z",
        "limit": 10000, "adjustment": "all", "feed": FEED, "sort": "asc",
    }
    while True:
        p = dict(params)
        if token:
            p["page_token"] = token
        payload = get_json(f"{DATA_BASE}/v2/stocks/{symbol}/bars", p)
        bars.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    df = df.set_index("ts").sort_index()
    return df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))].copy()


def load_panels(start, end):
    symbols = UNIVERSE + [BENCHMARK]
    panels = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_symbol_bars, s, start, end): s for s in symbols}
        for fut in as_completed(futs):
            panels[futs[fut]] = fut.result()
    return panels


def day_slice(df, day):
    if df.empty:
        return df
    d = pd.Timestamp(day).date()
    return df[df.index.date == d]


def robust_z(value, history):
    x = pd.Series(history, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(x) < MIN_BASELINE_SAMPLES:
        return None
    x = x.tail(BASELINE_TAIL_SAMPLES)
    med = float(x.median())
    mad = float((x - med).abs().median())
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale <= 1e-12:
        scale = float(x.std(ddof=1))
    if not np.isfinite(scale) or scale <= 1e-12:
        return None
    return float((value - med) / scale)


def rolling_5m_features(stock_pre, spy_pre):
    if stock_pre.empty or spy_pre.empty:
        return None
    s = stock_pre[["c", "v"]].copy()
    p = spy_pre[["c"]].copy().rename(columns={"c": "spy_c"})
    aligned = s.join(p, how="inner")
    if len(aligned) < MIN_BASELINE_SAMPLES + 10:
        return None
    ret5 = aligned["c"].pct_change(5)
    spy5 = aligned["spy_c"].pct_change(5)
    rs5 = ret5 - spy5
    vol5 = aligned["v"].rolling(5).sum()
    logvol5 = np.log(vol5.where(vol5 > 0))
    return ret5, rs5, logvol5


def event_features(event, bars, spy):
    t = event["created_et"].ceil("min")
    pre = bars[bars.index < t]
    post = bars[bars.index >= t].head(5)
    spy_pre = spy[spy.index < t]
    spy_post = spy[spy.index >= t].head(5)
    if len(pre) < MIN_BASELINE_SAMPLES + 10 or len(post) < 5 or len(spy_pre) < MIN_BASELINE_SAMPLES + 10 or len(spy_post) < 5:
        return None, "baseline"

    pre_close = float(pre.iloc[-1]["c"])
    post_close = float(post.iloc[-1]["c"])
    reaction = post_close / pre_close - 1.0
    if reaction <= 0:
        return None, "direction"
    spy_reaction = float(spy_post.iloc[-1]["c"] / spy_pre.iloc[-1]["c"] - 1.0)
    rs = reaction - spy_reaction
    post_volume = float(post["v"].sum())
    if post_volume <= 0:
        return None, "volume"

    hist = rolling_5m_features(pre, spy_pre)
    if hist is None:
        return None, "baseline"
    ret5, rs5, logvol5 = hist
    move_z = robust_z(reaction, ret5)
    rs_z = robust_z(rs, rs5)
    volume_z = robust_z(math.log(post_volume), logvol5)
    if move_z is None or rs_z is None or volume_z is None:
        return None, "baseline"

    weights = post["v"].astype(float).to_numpy()
    vw = post["vw"].astype(float).to_numpy() if "vw" in post else post["c"].astype(float).to_numpy()
    post_vwap = float(np.average(vw, weights=weights)) if weights.sum() > 0 else float(post["c"].mean())
    vwap_hold = post_close >= post_vwap
    return {
        "t": t, "post": post, "pre_close": pre_close, "post_close": post_close,
        "reaction": reaction, "rs": rs, "move_z": move_z, "rs_z": rs_z, "volume_z": volume_z,
        "post_vwap": post_vwap, "vwap_hold": vwap_hold,
        "impulse_low": float(post["l"].min()), "impulse_high": float(post["h"].max()),
    }, "pass"


def make_entry(event, f, bars):
    if f["move_z"] < MIN_MOVE_Z:
        return None, "move_z"
    if f["volume_z"] < MIN_VOLUME_Z:
        return None, "volume_z"
    if f["rs_z"] < MIN_RS_Z:
        return None, "rs_z"
    if not f["vwap_hold"]:
        return None, "vwap"

    impulse = f["post_close"] - f["pre_close"]
    if impulse <= 0:
        return None, "direction"
    after = bars[bars.index > f["post"].index[-1]]
    hold = after.head(HOLD_MINUTES)
    if len(hold) < HOLD_MINUTES:
        return None, "structure"
    retrace = max(0.0, (f["post_close"] - float(hold["l"].min())) / impulse)
    midpoint = f["pre_close"] + 0.50 * impulse
    if retrace > MAX_RETRACE_FRACTION:
        return None, "structure"
    if float(hold["l"].min()) < f["pre_close"]:
        return None, "structure"
    if float(hold.iloc[-1]["c"]) < max(f["post_vwap"], midpoint):
        return None, "structure"

    nxt = after[after.index > hold.index[-1]].head(1)
    if nxt.empty:
        return None, "structure"
    entry_ts = nxt.index[0]
    if entry_ts.time() >= FORCE_EXIT_ET:
        return None, "structure"
    entry_open = float(nxt.iloc[0]["o"])
    structure_low = float(pd.concat([f["post"]["l"], hold["l"]]).min())
    raw_stop_pct = (entry_open - structure_low) / entry_open
    if raw_stop_pct <= 0 or raw_stop_pct > MAX_STOP_PCT:
        return None, "stop"
    stop = entry_open * (1 - MIN_STOP_PCT) if raw_stop_pct < MIN_STOP_PCT else structure_low
    confidence = 50.0 + min(15.0, 5.0 * f["move_z"]) + min(12.5, 4.0 * f["volume_z"]) + min(12.5, 4.0 * f["rs_z"]) + (5.0 if event["hard_catalyst"] else 0.0) + 5.0
    return {
        "score": float(min(100.0, confidence)),
        "reaction_pct": f["reaction"] * 100.0,
        "relative_strength_pct": f["rs"] * 100.0,
        "move_z": f["move_z"], "volume_z": f["volume_z"], "rs_z": f["rs_z"],
        "retrace_fraction": retrace, "entry_ts": entry_ts, "entry_open": entry_open, "initial_stop": stop,
    }, "pass"


def adverse_buy(px):
    return float(px) * (1 + COST_BPS_PER_FILL / 10000.0)


def adverse_sell(px):
    return float(px) * (1 - COST_BPS_PER_FILL / 10000.0)


def simulate_trade(event, conf, bars, equity):
    entry_fill = adverse_buy(conf["entry_open"])
    stop = float(conf["initial_stop"])
    risk_per_share = entry_fill - stop
    if risk_per_share <= 0:
        return None
    shares = min(equity * RISK_PER_TRADE_PCT / risk_per_share, equity * MAX_NOTIONAL_PCT / entry_fill)
    if shares <= 0:
        return None
    initial_r = risk_per_share
    highest, active_stop = entry_fill, stop
    path = bars[bars.index >= conf["entry_ts"]]
    exit_px = exit_ts = reason = None
    for ts, row in path.iterrows():
        if ts.time() >= FORCE_EXIT_ET:
            exit_px, exit_ts, reason = float(row["c"]), ts, "FORCE_CLOSE"
            break
        hi, lo = float(row["h"]), float(row["l"])
        prospective_high = max(highest, hi)
        prospective_stop = active_stop
        if prospective_high >= entry_fill + initial_r:
            prospective_stop = max(prospective_stop, entry_fill)
        if prospective_high >= entry_fill + 2 * initial_r:
            prospective_stop = max(prospective_stop, prospective_high - initial_r)
        if lo <= prospective_stop:
            exit_px, exit_ts, reason = prospective_stop, ts, "TRAIL_OR_STOP"
            break
        highest, active_stop = prospective_high, prospective_stop
    if exit_px is None:
        if path.empty:
            return None
        exit_px, exit_ts, reason = float(path.iloc[-1]["c"]), path.index[-1], "LAST_BAR"
    exit_fill = adverse_sell(exit_px)
    pnl = shares * (exit_fill - entry_fill)
    risk_dollars = shares * initial_r
    return {
        "symbol": event["symbol"], "event_time_et": event["created_et"].isoformat(),
        "headline": event["headline"], "groups": event["groups"], "hard_catalyst": event["hard_catalyst"],
        "confidence_score": conf["score"], "reaction_pct": conf["reaction_pct"],
        "relative_strength_pct": conf["relative_strength_pct"], "move_z": conf["move_z"],
        "volume_z": conf["volume_z"], "rs_z": conf["rs_z"], "retrace_fraction": conf["retrace_fraction"],
        "entry_time_et": conf["entry_ts"].isoformat(), "entry_fill": entry_fill, "initial_stop": stop,
        "exit_time_et": exit_ts.isoformat(), "exit_fill": exit_fill, "exit_reason": reason,
        "notional": shares * entry_fill, "pnl_dollars": pnl,
        "r_multiple": pnl / risk_dollars if risk_dollars > 0 else 0.0,
    }


def summarize(trades):
    eq = START_EQ
    curve = [eq]
    wins, losses = [], []
    for t in trades:
        eq += float(t["pnl_dollars"])
        curve.append(eq)
        if t["pnl_dollars"] > 0:
            wins.append(float(t["pnl_dollars"]))
        elif t["pnl_dollars"] < 0:
            losses.append(float(t["pnl_dollars"]))
    s = pd.Series(curve, dtype=float)
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) else 0.0
    gp, gl = float(sum(wins)), float(-sum(losses))
    aw = float(np.mean(wins)) if wins else 0.0
    al = float(abs(np.mean(losses))) if losses else 0.0
    return {
        "ending_equity": eq, "total_return_pct": (eq / START_EQ - 1) * 100,
        "max_drawdown_pct": dd, "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0),
        "avg_trade_pnl_dollars": float(np.mean([t["pnl_dollars"] for t in trades])) if trades else 0.0,
        "avg_winner_dollars": aw, "avg_loser_dollars": al,
        "avg_winner_to_loser": aw / al if al > 0 else 0.0,
        "avg_r_multiple": float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0,
        "best_trade_dollars": max([t["pnl_dollars"] for t in trades], default=0.0),
        "worst_trade_dollars": min([t["pnl_dollars"] for t in trades], default=0.0),
    }


def evaluate_period(start, end):
    events = collect_events(start, end)
    panels = load_panels(start, end)
    trades, used_days = [], set()
    funnel = {
        "entity_relevant_news": len(events), "baseline_reject": 0, "direction_reject": 0,
        "move_z_reject": 0, "volume_z_reject": 0, "rs_z_reject": 0, "vwap_reject": 0,
        "structure_reject": 0, "stop_reject": 0, "qualified": 0,
    }
    for event in events:
        day = event["created_et"].date().isoformat()
        if day in used_days:
            continue
        bars = day_slice(panels.get(event["symbol"], pd.DataFrame()), day)
        spy = day_slice(panels.get(BENCHMARK, pd.DataFrame()), day)
        # Baselines must use all historical bars strictly before the event, not just the event day.
        stock_pre = panels.get(event["symbol"], pd.DataFrame())
        spy_pre = panels.get(BENCHMARK, pd.DataFrame())
        if stock_pre.empty or spy_pre.empty:
            funnel["baseline_reject"] += 1
            continue
        f, reason = event_features(event, stock_pre[stock_pre.index.date <= event["created_et"].date()], spy_pre[spy_pre.index.date <= event["created_et"].date()])
        if f is None:
            funnel[{"baseline":"baseline_reject", "direction":"direction_reject", "volume":"volume_z_reject"}.get(reason, "baseline_reject")] += 1
            continue
        conf, reason = make_entry(event, f, bars)
        if conf is None:
            key = {"move_z":"move_z_reject", "volume_z":"volume_z_reject", "rs_z":"rs_z_reject", "vwap":"vwap_reject", "structure":"structure_reject", "stop":"stop_reject", "direction":"direction_reject"}.get(reason)
            if key:
                funnel[key] += 1
            continue
        funnel["qualified"] += 1
        equity = START_EQ + sum(float(t["pnl_dollars"]) for t in trades)
        tr = simulate_trade(event, conf, bars, equity)
        if tr:
            trades.append(tr)
            used_days.add(day)
    stats = summarize(trades)
    stats["funnel"] = funnel
    stats["top_trades"] = sorted(trades, key=lambda x: x["pnl_dollars"], reverse=True)[:10]
    stats["bottom_trades"] = sorted(trades, key=lambda x: x["pnl_dollars"])[:10]
    return stats


def main():
    dev = evaluate_period(*PERIODS["development_2024"])
    dev_checks = {
        "development_trades_at_least_5": dev["trades"] >= 5,
        "development_positive_expectancy": dev["avg_trade_pnl_dollars"] > 0,
        "development_profit_factor_at_least_1_20": dev["profit_factor"] >= 1.20,
        "development_winner_loser_at_least_1_25": dev["avg_winner_to_loser"] >= 1.25,
        "development_drawdown_at_most_5pct": dev["max_drawdown_pct"] <= 5.0,
    }
    dev_pass = all(dev_checks.values())

    # Always compute 2025 for transparency, but it is only a formal validation if 2024 passes.
    val = evaluate_period(*PERIODS["validation_2025"])
    val_checks = {
        "validation_trades_at_least_5": val["trades"] >= 5,
        "validation_positive_after_costs": val["total_return_pct"] > 0,
        "validation_positive_expectancy": val["avg_trade_pnl_dollars"] > 0,
        "validation_profit_factor_at_least_1_25": val["profit_factor"] >= 1.25,
        "validation_winner_loser_at_least_1_50": val["avg_winner_to_loser"] >= 1.50,
        "validation_drawdown_at_most_8pct": val["max_drawdown_pct"] <= 8.0,
        "combined_trades_at_least_15": dev["trades"] + val["trades"] >= 15,
    }
    passed = dev_pass and all(val_checks.values())

    result = {
        "experiment": "S2-E10-ENTITY-RELEVANT-ABNORMAL-NEWS-MOMENTUM",
        "research_only": True, "broker_orders": False, "long_only": True, "leverage": False,
        "feed": FEED, "universe": UNIVERSE, "periods": PERIODS,
        "rules_locked_before_results": {
            "headline_must_name_company_or_ticker": True,
            "material_catalyst_keyword_required": True,
            "confirmation_minutes": 5,
            "min_move_robust_z": MIN_MOVE_Z, "min_volume_robust_z": MIN_VOLUME_Z,
            "min_spy_relative_strength_robust_z": MIN_RS_Z,
            "baseline_min_samples": MIN_BASELINE_SAMPLES,
            "hold_minutes": HOLD_MINUTES, "max_retrace_fraction": MAX_RETRACE_FRACTION,
            "vwap_hold_required": True, "max_trades_per_day": MAX_TRADES_PER_DAY,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT * 100,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100,
            "stop_distance_bounds_pct": [MIN_STOP_PCT * 100, MAX_STOP_PCT * 100],
            "break_even_after_r": 1.0, "trail_after_r": 2.0,
            "force_exit_et": "15:55", "cost_bps_per_fill": COST_BPS_PER_FILL,
        },
        "development_2024": dev, "development_checks": dev_checks,
        "validation_2025": val, "validation_checks": val_checks,
        "gate": "PASS" if passed else "FAIL", "activate": False,
    }
    with open("strategy2_experiment10_entity_abnormal_news_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 10 — Entity-Relevant Abnormal News Momentum", "",
        f"**Gate: {result['gate']}**", "",
        "Long-only research. Direct headline entity relevance + stock-specific abnormal move/volume/relative-strength + hold/retest. No broker orders or leverage.", "",
    ]
    for label, s in [("2024 development", dev), ("2025 validation", val)]:
        lines += [
            f"## {label}",
            f"- Entity-relevant events: {s['funnel']['entity_relevant_news']} | qualified/trades: {s['funnel']['qualified']}/{s['trades']}",
            f"- Ending equity: ${s['ending_equity']:.2f} | return {s['total_return_pct']:+.3f}% | max DD {s['max_drawdown_pct']:.3f}%",
            f"- Win rate {s['win_rate_pct']:.2f}% | PF {s['profit_factor']:.3f} | avg trade ${s['avg_trade_pnl_dollars']:+.2f}",
            f"- Avg winner/loser ${s['avg_winner_dollars']:.2f}/${s['avg_loser_dollars']:.2f} | ratio {s['avg_winner_to_loser']:.2f} | avg R {s['avg_r_multiple']:+.3f}",
            f"- Best/worst ${s['best_trade_dollars']:+.2f}/${s['worst_trade_dollars']:+.2f}", "",
        ]
    lines.append("## 2024 development checks")
    for k, v in dev_checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines.append("")
    lines.append("## 2025 validation checks")
    for k, v in val_checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "Activation remains OFF regardless of result; a PASS only justifies separate forward shadow validation.", ""]
    with open("strategy2_experiment10_entity_abnormal_news_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
