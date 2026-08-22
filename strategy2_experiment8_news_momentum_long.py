import json
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
MIN_REACTION_PCT = 0.015
MIN_RELATIVE_STRENGTH_PCT = 0.010
MIN_RELATIVE_VOLUME = 2.0
MIN_CONFIDENCE_SCORE = 70.0
MIN_STOP_PCT = 0.005
MAX_STOP_PCT = 0.020
MAX_TRADES_PER_DAY = 1
NEWS_START_ET = dtime(10, 0)
NEWS_END_ET = dtime(15, 0)
FORCE_EXIT_ET = dtime(15, 55)
FEED = "iex"

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "XOM", "CVX", "LLY", "UNH", "WMT", "COST", "ORCL", "CRM",
]
BENCHMARK = "SPY"

BLOCKS = {
    "development_2024": ("2024-01-01", "2024-12-31"),
    "validation_2025": ("2025-01-01", "2025-12-31"),
    "holdout_2026_ytd": ("2026-01-01", "2026-07-31"),
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

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DATA_BASE = "https://data.alpaca.markets"


def headers():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca credentials are required for historical news/data research")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_json(url, params, tries=5):
    for attempt in range(tries):
        r = requests.get(url, headers=headers(), params=params, timeout=45)
        if r.status_code == 429:
            time.sleep(2.0 + attempt * 2.0)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Repeated rate limit: {url}")


def fetch_news(symbol, start, end):
    out = []
    token = None
    while True:
        params = {
            "symbols": symbol,
            "start": start,
            "end": end,
            "sort": "asc",
            "limit": 50,
            "include_content": "false",
        }
        if token:
            params["page_token"] = token
        payload = get_json(f"{DATA_BASE}/v1beta1/news", params)
        out.extend(payload.get("news") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    return out


def fetch_day_bars(symbol, day):
    start = f"{day}T13:00:00Z"
    end = f"{day}T21:15:00Z"
    params = {
        "timeframe": "1Min",
        "start": start,
        "end": end,
        "limit": 10000,
        "adjustment": "all",
        "feed": FEED,
        "sort": "asc",
    }
    payload = get_json(f"{DATA_BASE}/v2/stocks/{symbol}/bars", params)
    bars = payload.get("bars") or []
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    df = df.set_index("ts").sort_index()
    regular = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))].copy()
    return regular


def catalyst_groups(text):
    t = re.sub(r"\s+", " ", (text or "").lower())
    hit = []
    for name, words in CATALYST_GROUPS.items():
        if any(w in t for w in words):
            hit.append(name)
    return hit


def normalize_news_item(item, requested_symbol):
    created = pd.Timestamp(item.get("created_at") or item.get("updated_at"))
    if created.tzinfo is None:
        created = created.tz_localize("UTC")
    created = created.tz_convert(ET)
    text = " ".join([str(item.get("headline") or ""), str(item.get("summary") or "")])
    groups = catalyst_groups(text)
    symbols = [str(x).upper() for x in (item.get("symbols") or [])]
    if requested_symbol not in symbols:
        return None
    if not groups:
        return None
    if not (NEWS_START_ET <= created.time() <= NEWS_END_ET):
        return None
    return {
        "symbol": requested_symbol,
        "created_et": created,
        "headline": str(item.get("headline") or ""),
        "source": str(item.get("source") or ""),
        "groups": groups,
        "article_id": item.get("id"),
    }


def collect_events(start, end):
    events = []
    for symbol in UNIVERSE:
        items = fetch_news(symbol, start, end)
        last_kept = None
        for item in items:
            ev = normalize_news_item(item, symbol)
            if not ev:
                continue
            if last_kept is not None and (ev["created_et"] - last_kept).total_seconds() < 60 * 60:
                continue
            events.append(ev)
            last_kept = ev["created_et"]
    events.sort(key=lambda x: x["created_et"])
    return events


def confidence_score(groups, reaction, relvol, relstrength, vwap_hold):
    catalyst = min(35.0, 25.0 + 5.0 * max(0, len(groups) - 1))
    price = min(25.0, max(0.0, reaction / 0.03 * 25.0))
    volume = min(20.0, max(0.0, relvol / 4.0 * 20.0))
    rs = min(15.0, max(0.0, relstrength / 0.02 * 15.0))
    vwap = 5.0 if vwap_hold else 0.0
    return float(catalyst + price + volume + rs + vwap)


def confirmation(event, bars, spy):
    if bars.empty or spy.empty:
        return None
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
    vw_values = post["vw"].astype(float).to_numpy() if "vw" in post else post["c"].astype(float).to_numpy()
    post_vwap = float(np.average(vw_values, weights=weights)) if weights.sum() > 0 else float(post["c"].mean())
    vwap_hold = post_close >= post_vwap

    score = confidence_score(event["groups"], reaction, relvol, relstrength, vwap_hold)
    passed = (
        reaction >= MIN_REACTION_PCT
        and relstrength >= MIN_RELATIVE_STRENGTH_PCT
        and relvol >= MIN_RELATIVE_VOLUME
        and vwap_hold
        and score >= MIN_CONFIDENCE_SCORE
    )
    if not passed:
        return None

    entry_candidates = bars[bars.index > post.index[-1]]
    if entry_candidates.empty:
        return None
    entry_ts = entry_candidates.index[0]
    if entry_ts.time() >= FORCE_EXIT_ET:
        return None
    entry_open = float(entry_candidates.iloc[0]["o"])
    structure_low = float(post["l"].min())
    raw_stop_pct = (entry_open - structure_low) / entry_open
    if raw_stop_pct <= 0:
        return None
    if raw_stop_pct < MIN_STOP_PCT:
        stop = entry_open * (1.0 - MIN_STOP_PCT)
    elif raw_stop_pct > MAX_STOP_PCT:
        return None
    else:
        stop = structure_low

    return {
        "score": score,
        "reaction_pct": reaction * 100.0,
        "relative_strength_pct": relstrength * 100.0,
        "relative_volume": relvol,
        "post_vwap": post_vwap,
        "entry_ts": entry_ts,
        "entry_open": entry_open,
        "initial_stop": stop,
        "confirmation_low": structure_low,
    }


def adverse_buy(px):
    return float(px) * (1.0 + COST_BPS_PER_FILL / 10000.0)


def adverse_sell(px):
    return float(px) * (1.0 - COST_BPS_PER_FILL / 10000.0)


def simulate_trade(event, conf, bars, equity):
    entry_fill = adverse_buy(conf["entry_open"])
    stop = float(conf["initial_stop"])
    risk_per_share = entry_fill - stop
    if risk_per_share <= 0:
        return None
    risk_budget = equity * RISK_PER_TRADE_PCT
    shares_by_risk = risk_budget / risk_per_share
    shares_by_notional = (equity * MAX_NOTIONAL_PCT) / entry_fill
    shares = min(shares_by_risk, shares_by_notional)
    if shares <= 0:
        return None

    initial_r = risk_per_share
    highest = entry_fill
    active_stop = stop
    exit_px = None
    exit_ts = None
    reason = None

    path = bars[bars.index >= conf["entry_ts"]]
    for ts, row in path.iterrows():
        if ts.time() >= FORCE_EXIT_ET:
            exit_px = float(row["c"])
            exit_ts = ts
            reason = "FORCE_CLOSE"
            break

        bar_high = float(row["h"])
        bar_low = float(row["l"])
        prospective_high = max(highest, bar_high)
        prospective_stop = active_stop
        if prospective_high >= entry_fill + initial_r:
            prospective_stop = max(prospective_stop, entry_fill)
        if prospective_high >= entry_fill + 2.0 * initial_r:
            prospective_stop = max(prospective_stop, prospective_high - initial_r)

        # Conservative intrabar assumption: if the bar spans the newly eligible stop, count the stop.
        if bar_low <= prospective_stop:
            exit_px = prospective_stop
            exit_ts = ts
            reason = "TRAIL_OR_STOP"
            active_stop = prospective_stop
            highest = prospective_high
            break

        highest = prospective_high
        active_stop = prospective_stop

    if exit_px is None:
        last = path.iloc[-1]
        exit_px = float(last["c"])
        exit_ts = path.index[-1]
        reason = "LAST_BAR"

    exit_fill = adverse_sell(exit_px)
    pnl = shares * (exit_fill - entry_fill)
    r_multiple = pnl / (shares * initial_r) if shares * initial_r > 0 else 0.0
    return {
        "symbol": event["symbol"],
        "event_time_et": event["created_et"].isoformat(),
        "headline": event["headline"],
        "source": event["source"],
        "groups": event["groups"],
        "confidence_score": conf["score"],
        "reaction_pct": conf["reaction_pct"],
        "relative_strength_pct": conf["relative_strength_pct"],
        "relative_volume": conf["relative_volume"],
        "entry_time_et": conf["entry_ts"].isoformat(),
        "entry_fill": entry_fill,
        "initial_stop": stop,
        "shares": shares,
        "notional": shares * entry_fill,
        "exit_time_et": exit_ts.isoformat(),
        "exit_fill": exit_fill,
        "exit_reason": reason,
        "pnl_dollars": pnl,
        "return_on_equity_pct": pnl / equity * 100.0,
        "r_multiple": r_multiple,
    }


def summarize(trades, start_eq=START_EQ):
    eq = start_eq
    curve = [eq]
    wins = []
    losses = []
    for t in trades:
        eq += float(t["pnl_dollars"])
        curve.append(eq)
        if t["pnl_dollars"] > 0:
            wins.append(float(t["pnl_dollars"]))
        elif t["pnl_dollars"] < 0:
            losses.append(float(t["pnl_dollars"]))
    s = pd.Series(curve, dtype=float)
    dd = float((1.0 - s / s.cummax()).max() * 100.0) if len(s) else 0.0
    gross_profit = float(sum(wins))
    gross_loss = float(-sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(abs(np.mean(losses))) if losses else 0.0
    return {
        "starting_equity": start_eq,
        "ending_equity": eq,
        "total_return_pct": (eq / start_eq - 1.0) * 100.0,
        "max_drawdown_pct": dd,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100.0 if trades else 0.0,
        "gross_profit_dollars": gross_profit,
        "gross_loss_dollars": gross_loss,
        "profit_factor": pf,
        "avg_trade_pnl_dollars": float(np.mean([t["pnl_dollars"] for t in trades])) if trades else 0.0,
        "avg_winner_dollars": avg_win,
        "avg_loser_dollars": avg_loss,
        "avg_winner_to_loser": avg_win / avg_loss if avg_loss > 0 else 0.0,
        "best_trade_dollars": max([t["pnl_dollars"] for t in trades], default=0.0),
        "worst_trade_dollars": min([t["pnl_dollars"] for t in trades], default=0.0),
        "avg_r_multiple": float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0,
        "max_confidence_score": max([t["confidence_score"] for t in trades], default=0.0),
    }


def run_block(name, start, end):
    events = collect_events(start, end)
    cache = {}
    spy_cache = {}
    trades = []
    used_days = set()
    confirmed = 0

    for event in events:
        day = event["created_et"].date().isoformat()
        if day in used_days:
            continue
        key = (event["symbol"], day)
        if key not in cache:
            cache[key] = fetch_day_bars(event["symbol"], day)
        if day not in spy_cache:
            spy_cache[day] = fetch_day_bars(BENCHMARK, day)
        conf = confirmation(event, cache[key], spy_cache[day])
        if not conf:
            continue
        confirmed += 1
        equity = START_EQ + sum(float(t["pnl_dollars"]) for t in trades)
        trade = simulate_trade(event, conf, cache[key], equity)
        if not trade:
            continue
        trades.append(trade)
        used_days.add(day)

    stats = summarize(trades)
    stats["eligible_news_events"] = len(events)
    stats["confirmed_events"] = confirmed
    stats["trade_details"] = trades
    return stats


def main():
    results = {name: run_block(name, start, end) for name, (start, end) in BLOCKS.items()}
    dev = results["development_2024"]
    val = results["validation_2025"]
    hold = results["holdout_2026_ytd"]

    checks = {
        "enough_pre_holdout_trades_at_least_20": dev["trades"] + val["trades"] >= 20,
        "validation_positive_after_costs": val["total_return_pct"] > 0.0,
        "validation_profit_factor_at_least_1_10": val["profit_factor"] >= 1.10,
        "holdout_positive_after_costs": hold["total_return_pct"] > 0.0,
        "holdout_positive_expectancy": hold["avg_trade_pnl_dollars"] > 0.0,
        "holdout_profit_factor_at_least_1_25": hold["profit_factor"] >= 1.25,
        "holdout_winner_loser_ratio_at_least_1_5": hold["avg_winner_to_loser"] >= 1.5,
        "holdout_drawdown_at_most_8pct": hold["max_drawdown_pct"] <= 8.0,
    }
    passed = all(checks.values())

    compact = {}
    for name, stats in results.items():
        compact[name] = {k: v for k, v in stats.items() if k != "trade_details"}
        compact[name]["top_10_trades"] = sorted(
            stats["trade_details"], key=lambda x: x["pnl_dollars"], reverse=True
        )[:10]
        compact[name]["bottom_10_trades"] = sorted(
            stats["trade_details"], key=lambda x: x["pnl_dollars"]
        )[:10]

    result = {
        "experiment": "S2-E8-HIGH-CONFIDENCE-NEWS-MOMENTUM-LONG",
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "feed": FEED,
        "universe": UNIVERSE,
        "rules_locked_before_results": {
            "eligible_intraday_news_window_et": "10:00-15:00",
            "material_catalyst_keyword_required": True,
            "confirmation_minutes": 5,
            "min_price_reaction_pct": MIN_REACTION_PCT * 100.0,
            "min_relative_strength_vs_spy_pct": MIN_RELATIVE_STRENGTH_PCT * 100.0,
            "min_relative_volume": MIN_RELATIVE_VOLUME,
            "vwap_hold_required": True,
            "min_confidence_score": MIN_CONFIDENCE_SCORE,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT * 100.0,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100.0,
            "stop_distance_bounds_pct": [MIN_STOP_PCT * 100.0, MAX_STOP_PCT * 100.0],
            "break_even_after_r": 1.0,
            "trail_after_r": 2.0,
            "trail_distance_r": 1.0,
            "force_exit_et": "15:55",
            "cost_bps_per_fill": COST_BPS_PER_FILL,
            "execution": "news timestamp -> five full 1-minute confirmation bars -> enter next bar open; conservative intrabar stop assumption",
        },
        "blocks": compact,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate": False,
    }

    with open("strategy2_experiment8_news_momentum_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Strategy 2 — Experiment 8: High-Confidence News Momentum Long",
        "",
        f"**Gate: {result['gate']}**",
        "",
        "Research only. Historical Alpaca news + IEX 1-minute bars. Long-only, no leverage, no broker orders.",
        "",
        "Locked before results: one trade max/day; material catalyst + 5-minute price/volume/VWAP/relative-strength confirmation; 0.75% equity risk; 50% notional cap; break-even after +1R; trail after +2R; flat by 15:55 ET; 10 bps per fill.",
        "",
    ]
    for name in BLOCKS:
        s = results[name]
        lines += [
            f"## {name}",
            f"- Eligible news events: {s['eligible_news_events']} | confirmed: {s['confirmed_events']} | trades: {s['trades']}",
            f"- Ending equity: ${s['ending_equity']:.2f} | return {s['total_return_pct']:+.3f}% | max DD {s['max_drawdown_pct']:.3f}%",
            f"- Win rate {s['win_rate_pct']:.2f}% | profit factor {s['profit_factor']:.3f} | avg trade ${s['avg_trade_pnl_dollars']:+.2f}",
            f"- Avg winner/loser ${s['avg_winner_dollars']:.2f} / ${s['avg_loser_dollars']:.2f} | ratio {s['avg_winner_to_loser']:.2f}",
            f"- Best/worst trade ${s['best_trade_dollars']:+.2f} / ${s['worst_trade_dollars']:+.2f} | avg R {s['avg_r_multiple']:+.3f}",
            "",
        ]
    lines.append("## Predeclared checks")
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "**Activation remains OFF. A PASS would justify a separate forward shadow test, not live trading.**", ""]
    with open("strategy2_experiment8_news_momentum_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
