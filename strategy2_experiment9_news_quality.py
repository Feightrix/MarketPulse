import json
import math
import os
import re
import time
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
PERIODS = {"development_2024": ("2024-01-01", "2024-12-31"), "validation_2025": ("2025-01-01", "2025-12-31")}

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

PROFILES = {
    "balanced": {
        "min_reaction": 0.0075, "min_rs": 0.0040, "min_relvol": 1.50, "min_score": 60.0,
        "hard_required": True, "mode": "persistence", "max_fade_fraction": 0.50,
    },
    "strong_tape": {
        "min_reaction": 0.0100, "min_rs": 0.0060, "min_relvol": 2.00, "min_score": 68.0,
        "hard_required": False, "mode": "persistence", "max_fade_fraction": 0.35,
    },
    "retest_breakout": {
        "min_reaction": 0.0075, "min_rs": 0.0040, "min_relvol": 1.50, "min_score": 60.0,
        "hard_required": True, "mode": "retest_breakout", "max_fade_fraction": 0.50,
    },
}


def headers():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca credentials required")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_json(url, params, tries=6):
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


def collect_events(start, end):
    out, token = [], None
    while True:
        params = {"symbols": ",".join(UNIVERSE), "start": start, "end": end, "sort": "asc", "limit": 50, "include_content": "false"}
        if token:
            params["page_token"] = token
        p = get_json(f"{DATA_BASE}/v1beta1/news", params)
        out.extend(p.get("news") or [])
        token = p.get("next_page_token")
        if not token:
            break

    events, last_kept = [], {}
    u = set(UNIVERSE)
    for item in out:
        created = pd.Timestamp(item.get("created_at") or item.get("updated_at"))
        if created.tzinfo is None:
            created = created.tz_localize("UTC")
        created = created.tz_convert(ET)
        if not (NEWS_START_ET <= created.time() <= NEWS_END_ET):
            continue
        text = " ".join([str(item.get("headline") or ""), str(item.get("summary") or "")])
        groups = catalyst_groups(text)
        if not groups:
            continue
        syms = u.intersection(str(x).upper() for x in (item.get("symbols") or []))
        for sym in sorted(syms):
            prev = last_kept.get(sym)
            if prev is not None and (created - prev).total_seconds() < 3600:
                continue
            events.append({"symbol": sym, "created_et": created, "headline": str(item.get("headline") or ""), "source": str(item.get("source") or ""), "groups": groups})
            last_kept[sym] = created
    return sorted(events, key=lambda x: x["created_et"])


def fetch_symbol_bars(symbol, start, end):
    bars, token = [], None
    params = {"timeframe": "1Min", "start": f"{start}T13:00:00Z", "end": f"{end}T21:15:00Z", "limit": 10000, "adjustment": "all", "feed": FEED, "sort": "asc"}
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


def quality_score(groups, reaction, relvol, relstrength, vwap_hold):
    hard = len(set(groups) & HARD_GROUPS)
    catalyst = min(35.0, (30.0 if hard else 18.0) + 4.0 * max(0, len(groups) - 1))
    price = min(25.0, max(0.0, reaction / 0.025 * 25.0))
    volume = min(20.0, max(0.0, relvol / 3.5 * 20.0))
    rs = min(15.0, max(0.0, relstrength / 0.018 * 15.0))
    return float(catalyst + price + volume + rs + (5.0 if vwap_hold else 0.0))


def day_slice(df, day):
    if df.empty:
        return df
    return df[df.index.date == pd.Timestamp(day).date()]


def initial_features(event, bars, spy):
    t = event["created_et"].ceil("min")
    pre = bars[bars.index < t].tail(30)
    post = bars[bars.index >= t].head(5)
    spy_pre = spy[spy.index < t].tail(1)
    spy_post = spy[spy.index >= t].head(5)
    if len(pre) < 20 or len(post) < 5 or len(spy_pre) < 1 or len(spy_post) < 5:
        return None
    pre_close = float(pre.iloc[-1]["c"])
    post_close = float(post.iloc[-1]["c"])
    reaction = post_close / pre_close - 1.0
    prior30_volume = float(pre["v"].sum())
    expected5 = prior30_volume / 6.0 if prior30_volume > 0 else np.nan
    relvol = float(post["v"].sum() / expected5) if expected5 and np.isfinite(expected5) else 0.0
    spy_reaction = float(spy_post.iloc[-1]["c"] / spy_pre.iloc[-1]["c"] - 1.0)
    relstrength = reaction - spy_reaction
    weights = post["v"].astype(float).to_numpy()
    v = post["vw"].astype(float).to_numpy() if "vw" in post else post["c"].astype(float).to_numpy()
    post_vwap = float(np.average(v, weights=weights)) if weights.sum() > 0 else float(post["c"].mean())
    vwap_hold = post_close >= post_vwap
    return {
        "t": t, "pre": pre, "post": post, "pre_close": pre_close, "post_close": post_close,
        "reaction": reaction, "relvol": relvol, "relstrength": relstrength, "post_vwap": post_vwap,
        "vwap_hold": vwap_hold, "impulse_high": float(post["h"].max()), "impulse_low": float(post["l"].min()),
        "score": quality_score(event["groups"], reaction, relvol, relstrength, vwap_hold),
    }


def make_entry(event, f, bars, profile):
    p = PROFILES[profile]
    if p["hard_required"] and not (set(event["groups"]) & HARD_GROUPS):
        return None, "hard_catalyst"
    if f["reaction"] < p["min_reaction"]:
        return None, "reaction"
    if f["relstrength"] < p["min_rs"]:
        return None, "relative_strength"
    if f["relvol"] < p["min_relvol"]:
        return None, "relative_volume"
    if not f["vwap_hold"]:
        return None, "vwap"
    if f["score"] < p["min_score"]:
        return None, "score"

    impulse = max(f["post_close"] - f["pre_close"], 1e-9)
    after = bars[bars.index > f["post"].index[-1]]
    if p["mode"] == "persistence":
        hold = after.head(5)
        if len(hold) < 5:
            return None, "structure"
        min_allowed = f["post_close"] - p["max_fade_fraction"] * impulse
        if float(hold["l"].min()) < min_allowed:
            return None, "structure"
        if float(hold.iloc[-1]["c"]) < f["post_vwap"]:
            return None, "structure"
        nxt = after[after.index > hold.index[-1]].head(1)
        if nxt.empty:
            return None, "structure"
        entry_ts = nxt.index[0]
        entry_open = float(nxt.iloc[0]["o"])
        structure_low = float(pd.concat([f["post"]["l"], hold["l"]]).min())
    else:
        pull = after.head(10)
        if len(pull) < 5:
            return None, "structure"
        min_allowed = f["post_close"] - p["max_fade_fraction"] * impulse
        if float(pull["l"].min()) < min_allowed or float(pull["l"].min()) < f["pre_close"]:
            return None, "structure"
        future = after[after.index > pull.index[-1]].head(10)
        hit = future[future["h"] > f["impulse_high"]]
        if hit.empty:
            return None, "structure"
        breakout_ts = hit.index[0]
        nxt = bars[bars.index > breakout_ts].head(1)
        if nxt.empty:
            return None, "structure"
        entry_ts = nxt.index[0]
        entry_open = float(nxt.iloc[0]["o"])
        structure_low = float(pull["l"].min())

    if entry_ts.time() >= FORCE_EXIT_ET:
        return None, "structure"
    raw_stop_pct = (entry_open - structure_low) / entry_open
    if raw_stop_pct <= 0 or raw_stop_pct > MAX_STOP_PCT:
        return None, "stop"
    stop = entry_open * (1 - MIN_STOP_PCT) if raw_stop_pct < MIN_STOP_PCT else structure_low
    return {
        "score": f["score"], "reaction_pct": f["reaction"] * 100, "relative_strength_pct": f["relstrength"] * 100,
        "relative_volume": f["relvol"], "entry_ts": entry_ts, "entry_open": entry_open, "initial_stop": stop,
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
        "symbol": event["symbol"], "event_time_et": event["created_et"].isoformat(), "headline": event["headline"],
        "groups": event["groups"], "confidence_score": conf["score"], "reaction_pct": conf["reaction_pct"],
        "relative_strength_pct": conf["relative_strength_pct"], "relative_volume": conf["relative_volume"],
        "entry_time_et": conf["entry_ts"].isoformat(), "entry_fill": entry_fill, "initial_stop": stop,
        "exit_time_et": exit_ts.isoformat(), "exit_fill": exit_fill, "exit_reason": reason,
        "notional": shares * entry_fill, "pnl_dollars": pnl, "r_multiple": pnl / risk_dollars if risk_dollars > 0 else 0.0,
    }


def summarize(trades):
    eq = START_EQ
    curve = [eq]
    wins, losses = [], []
    for t in trades:
        eq += float(t["pnl_dollars"])
        curve.append(eq)
        (wins if t["pnl_dollars"] > 0 else losses).append(float(t["pnl_dollars"]))
    s = pd.Series(curve, dtype=float)
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) else 0.0
    gp, gl = float(sum(wins)), float(-sum(x for x in losses if x < 0))
    aw = float(np.mean(wins)) if wins else 0.0
    al = float(abs(np.mean([x for x in losses if x < 0]))) if any(x < 0 for x in losses) else 0.0
    return {
        "ending_equity": eq, "total_return_pct": (eq / START_EQ - 1) * 100, "max_drawdown_pct": dd,
        "trades": len(trades), "wins": len(wins), "losses": len([x for x in losses if x < 0]),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0),
        "avg_trade_pnl_dollars": float(np.mean([t["pnl_dollars"] for t in trades])) if trades else 0.0,
        "avg_winner_dollars": aw, "avg_loser_dollars": al, "avg_winner_to_loser": aw / al if al > 0 else 0.0,
        "avg_r_multiple": float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0,
        "best_trade_dollars": max([t["pnl_dollars"] for t in trades], default=0.0),
        "worst_trade_dollars": min([t["pnl_dollars"] for t in trades], default=0.0),
    }


def evaluate_period(start, end):
    events = collect_events(start, end)
    panels = {s: fetch_symbol_bars(s, start, end) for s in UNIVERSE + [BENCHMARK]}
    results = {}
    for profile in PROFILES:
        trades, used_days = [], set()
        funnel = {"eligible_news": len(events), "hard_catalyst_reject": 0, "reaction_reject": 0, "relative_strength_reject": 0,
                  "relative_volume_reject": 0, "vwap_reject": 0, "score_reject": 0, "structure_reject": 0, "stop_reject": 0, "qualified": 0}
        for event in events:
            day = event["created_et"].date().isoformat()
            if day in used_days:
                continue
            bars = day_slice(panels[event["symbol"]], day)
            spy = day_slice(panels[BENCHMARK], day)
            f = initial_features(event, bars, spy)
            if f is None:
                continue
            conf, reason = make_entry(event, f, bars, profile)
            if conf is None:
                key = {"hard_catalyst":"hard_catalyst_reject", "reaction":"reaction_reject", "relative_strength":"relative_strength_reject",
                       "relative_volume":"relative_volume_reject", "vwap":"vwap_reject", "score":"score_reject", "structure":"structure_reject", "stop":"stop_reject"}.get(reason)
                if key:
                    funnel[key] += 1
                continue
            funnel["qualified"] += 1
            equity = START_EQ + sum(t["pnl_dollars"] for t in trades)
            tr = simulate_trade(event, conf, bars, equity)
            if tr:
                trades.append(tr)
                used_days.add(day)
        stats = summarize(trades)
        stats["funnel"] = funnel
        stats["top_trades"] = sorted(trades, key=lambda x: x["pnl_dollars"], reverse=True)[:10]
        stats["bottom_trades"] = sorted(trades, key=lambda x: x["pnl_dollars"])[:10]
        results[profile] = stats
    return results


def main():
    dev = evaluate_period(*PERIODS["development_2024"])
    val = evaluate_period(*PERIODS["validation_2025"])

    dev_eligible = {}
    for name, s in dev.items():
        checks = {
            "trades_at_least_5": s["trades"] >= 5,
            "positive_expectancy": s["avg_trade_pnl_dollars"] > 0,
            "profit_factor_at_least_1_20": s["profit_factor"] >= 1.20,
            "winner_loser_at_least_1_25": s["avg_winner_to_loser"] >= 1.25,
            "drawdown_at_most_5pct": s["max_drawdown_pct"] <= 5.0,
        }
        dev_eligible[name] = {"checks": checks, "eligible": all(checks.values()), "selection_score": s["avg_r_multiple"] * math.sqrt(max(s["trades"], 1))}

    candidates = [n for n in PROFILES if dev_eligible[n]["eligible"]]
    selected = max(candidates, key=lambda n: dev_eligible[n]["selection_score"]) if candidates else None

    validation_checks = {}
    if selected:
        s = val[selected]
        validation_checks = {
            "validation_trades_at_least_5": s["trades"] >= 5,
            "validation_positive_after_costs": s["total_return_pct"] > 0,
            "validation_positive_expectancy": s["avg_trade_pnl_dollars"] > 0,
            "validation_profit_factor_at_least_1_25": s["profit_factor"] >= 1.25,
            "validation_winner_loser_at_least_1_5": s["avg_winner_to_loser"] >= 1.5,
            "validation_drawdown_at_most_8pct": s["max_drawdown_pct"] <= 8.0,
            "combined_trades_at_least_15": dev[selected]["trades"] + s["trades"] >= 15,
        }
    passed = bool(selected) and all(validation_checks.values())

    result = {
        "experiment": "S2-E9-NEWS-MOMENTUM-QUALITY-FILTER",
        "research_only": True, "broker_orders": False, "long_only": True, "leverage": False,
        "feed": FEED, "universe": UNIVERSE, "periods": PERIODS, "profiles_locked_before_results": PROFILES,
        "risk_rules": {"risk_per_trade_pct": RISK_PER_TRADE_PCT * 100, "max_notional_pct": MAX_NOTIONAL_PCT * 100,
                       "cost_bps_per_fill": COST_BPS_PER_FILL, "break_even_after_r": 1.0, "trail_after_r": 2.0, "force_exit_et": "15:55"},
        "development_2024": dev, "development_eligibility": dev_eligible, "selected_profile_from_2024": selected,
        "validation_2025": val, "validation_checks_for_selected": validation_checks,
        "gate": "PASS" if passed else "FAIL", "activate": False,
    }
    with open("strategy2_experiment9_news_quality_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = ["# Experiment 9 — News Momentum Quality Filter", "", f"**Gate: {result['gate']}**", "",
             "2024 selects among three predeclared profiles; 2025 validates the selected profile unchanged. Research only; no broker orders.", ""]
    for period_name, block in [("2024 development", dev), ("2025 validation", val)]:
        lines.append(f"## {period_name}")
        for n, s in block.items():
            lines.append(f"- {n}: trades {s['trades']} | return {s['total_return_pct']:+.3f}% | PF {s['profit_factor']:.2f} | avg trade ${s['avg_trade_pnl_dollars']:+.2f} | win/loss {s['avg_winner_to_loser']:.2f} | DD {s['max_drawdown_pct']:.2f}% | avg R {s['avg_r_multiple']:+.2f}")
        lines.append("")
    lines += [f"Selected from 2024: **{selected or 'NONE'}**", ""]
    if selected:
        lines.append("## Validation checks")
        for k, v in validation_checks.items():
            lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "Activation remains OFF regardless of result; a PASS only justifies a separate forward shadow test.", ""]
    with open("strategy2_experiment9_news_quality_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
