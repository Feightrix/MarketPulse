import json
import math
import re
from collections import defaultdict

import numpy as np
import pandas as pd

import strategy2_experiment10_entity_abnormal_news as base
import strategy2_experiment12_primary_event_news as classifier

EXPERIMENT = "S2-E13-OVERNIGHT-PRIMARY-EVENT-OPENING-CONTINUATION"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_FOLLOWUP = True

# Risk/execution stress retained from Experiment 10.
COST_BPS_PER_FILL = base.COST_BPS_PER_FILL
RISK_PER_TRADE_PCT = base.RISK_PER_TRADE_PCT
MAX_NOTIONAL_PCT = base.MAX_NOTIONAL_PCT
MIN_STOP_PCT = base.MIN_STOP_PCT
MAX_STOP_PCT = base.MAX_STOP_PCT
FORCE_EXIT_ET = base.FORCE_EXIT_ET

# Locked opening-continuation protocol.
EVENT_CUTOFF_ET_MINUTE = 9 * 60 + 25
OPENING_MINUTES = 10
ATR_LOOKBACK_DAYS = 20
VOLUME_LOOKBACK_DAYS = 20
MIN_GAP_ATR = 0.40
MIN_OPENING_RELVOL = 1.50
MIN_GAP_RETENTION = 0.60
MIN_SPY_RELATIVE_STRENGTH_PCT = 0.0
MAX_TRADES_PER_DAY = 1

# Reject previews/derivative summaries that can accidentally match direct-event syntax.
DERIVATIVE_REJECT = [
    r"\bexpected to report\b", r"\bto report earnings\b", r"\bon radar\b", r"\bwhat to expect\b",
    r"\bcatalysts? to watch\b", r"\bmarket summary\b", r"\bopening bell update\b", r"\bclosing bell update\b",
    r"\bbig stocks moving\b", r"\bpre-market session\b", r"\bpremarket session\b", r"\bafter the close\b",
    r"\bearnings preview\b", r"\bseveral catalysts\b",
]


def is_actual_primary_event(headline):
    event_type, reason = classifier.classify_primary_event(headline)
    if event_type is None:
        return None
    h = re.sub(r"\s+", " ", (headline or "").lower())
    if any(re.search(p, h) for p in DERIVATIVE_REJECT):
        return None
    return event_type


def collect_overnight_events(start, end):
    items = []
    for a, b in base.month_chunks(start, end):
        items.extend(base.fetch_news_chunk(a, b))
    universe = set(base.UNIVERSE)
    out = []
    seen = set()
    for item in sorted(items, key=lambda x: str(x.get("created_at") or x.get("updated_at") or "")):
        raw = item.get("created_at") or item.get("updated_at")
        if not raw:
            continue
        ts = pd.Timestamp(raw)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert(base.ET)
        headline = str(item.get("headline") or "")
        event_type = is_actual_primary_event(headline)
        if event_type is None:
            continue
        syms = universe.intersection(str(x).upper() for x in (item.get("symbols") or []))
        for sym in sorted(syms):
            if not base.headline_names_company(headline, sym):
                continue
            key = (sym, str(item.get("id") or ""), event_type)
            if key in seen:
                continue
            seen.add(key)
            out.append({"symbol": sym, "created_et": ts, "headline": headline, "event_type": event_type})
    return out


def next_session_for_event(ts, trading_days):
    d = ts.date()
    minute = ts.hour * 60 + ts.minute
    days = trading_days
    if minute >= 16 * 60:
        eligible = [x for x in days if x > d]
    elif minute <= EVENT_CUTOFF_ET_MINUTE:
        eligible = [x for x in days if x >= d]
    else:
        return None
    return eligible[0] if eligible else None


def session_df(panel, d):
    return panel[panel.index.date == d]


def session_stats(panel):
    rows = []
    for d, g in panel.groupby(panel.index.date):
        if len(g) < 30:
            continue
        first10 = g.head(OPENING_MINUTES)
        rows.append({
            "date": d,
            "open": float(g.iloc[0]["o"]),
            "high": float(g["h"].max()),
            "low": float(g["l"].min()),
            "close": float(g.iloc[-1]["c"]),
            "first10_volume": float(first10["v"].sum()),
        })
    return pd.DataFrame(rows).set_index("date").sort_index() if rows else pd.DataFrame()


def prior_atr(stats, d):
    prior = stats[stats.index < d].tail(ATR_LOOKBACK_DAYS + 1).copy()
    if len(prior) < ATR_LOOKBACK_DAYS:
        return None
    prev_close = prior["close"].shift(1)
    tr = pd.concat([
        prior["high"] - prior["low"],
        (prior["high"] - prev_close).abs(),
        (prior["low"] - prev_close).abs(),
    ], axis=1).max(axis=1).dropna().tail(ATR_LOOKBACK_DAYS)
    if len(tr) < ATR_LOOKBACK_DAYS - 1:
        return None
    atr = float(tr.mean())
    return atr if np.isfinite(atr) and atr > 0 else None


def opening_features(symbol, d, panels, stats_map):
    bars = session_df(panels[symbol], d)
    spy = session_df(panels[base.BENCHMARK], d)
    if len(bars) < OPENING_MINUTES + 1 or len(spy) < OPENING_MINUTES:
        return None
    sstats = stats_map[symbol]
    spystats = stats_map[base.BENCHMARK]
    prior_dates = sstats.index[sstats.index < d]
    spy_prior_dates = spystats.index[spystats.index < d]
    if len(prior_dates) < ATR_LOOKBACK_DAYS or len(spy_prior_dates) < 1:
        return None
    prev_d = prior_dates[-1]
    spy_prev_d = spy_prior_dates[-1]
    prev_close = float(sstats.loc[prev_d, "close"])
    spy_prev_close = float(spystats.loc[spy_prev_d, "close"])
    atr = prior_atr(sstats, d)
    if atr is None:
        return None

    first10 = bars.head(OPENING_MINUTES)
    spy10 = spy.head(OPENING_MINUTES)
    op = float(first10.iloc[0]["o"])
    close10 = float(first10.iloc[-1]["c"])
    low10 = float(first10["l"].min())
    gap = op - prev_close
    if gap <= 0:
        return None
    gap_atr = gap / atr
    retention = (close10 - prev_close) / gap
    vol10 = float(first10["v"].sum())
    hist_vol = sstats[sstats.index < d]["first10_volume"].tail(VOLUME_LOOKBACK_DAYS)
    if len(hist_vol) < VOLUME_LOOKBACK_DAYS:
        return None
    med_vol = float(hist_vol.median())
    relvol = vol10 / med_vol if med_vol > 0 else 0.0

    weights = first10["v"].astype(float).to_numpy()
    vw = first10["vw"].astype(float).to_numpy() if "vw" in first10 else first10["c"].astype(float).to_numpy()
    vwap10 = float(np.average(vw, weights=weights)) if weights.sum() > 0 else float(first10["c"].mean())
    stock_move = close10 / prev_close - 1.0
    spy_move = float(spy10.iloc[-1]["c"]) / spy_prev_close - 1.0
    rs = stock_move - spy_move

    if gap_atr < MIN_GAP_ATR:
        return None
    if relvol < MIN_OPENING_RELVOL:
        return None
    if retention < MIN_GAP_RETENTION:
        return None
    if close10 < vwap10:
        return None
    if rs < MIN_SPY_RELATIVE_STRENGTH_PCT:
        return None
    if low10 < prev_close:
        return None

    nxt = bars.iloc[OPENING_MINUTES:OPENING_MINUTES+1]
    if nxt.empty:
        return None
    entry_ts = nxt.index[0]
    entry_open = float(nxt.iloc[0]["o"])
    raw_stop_pct = (entry_open - low10) / entry_open
    if raw_stop_pct <= 0 or raw_stop_pct > MAX_STOP_PCT:
        return None
    stop = entry_open * (1.0 - MIN_STOP_PCT) if raw_stop_pct < MIN_STOP_PCT else low10
    score = gap_atr + math.log(max(relvol, 1e-9)) + max(0.0, rs * 100.0)
    return {
        "entry_ts": entry_ts, "entry_open": entry_open, "initial_stop": stop,
        "gap_atr": gap_atr, "opening_relvol": relvol, "gap_retention": retention,
        "relative_strength_pct": rs * 100.0, "score": score,
    }


def simulate(event, f, bars, equity):
    conf = {
        "entry_ts": f["entry_ts"], "entry_open": f["entry_open"], "initial_stop": f["initial_stop"],
        "score": f["score"], "reaction_pct": f["gap_atr"],
        "relative_strength_pct": f["relative_strength_pct"], "move_z": f["gap_atr"],
        "volume_z": f["opening_relvol"], "rs_z": f["relative_strength_pct"],
        "retrace_fraction": 1.0 - f["gap_retention"],
    }
    tr = base.simulate_trade(event, conf, bars, equity)
    if tr:
        tr["event_type"] = event["event_type"]
        tr["gap_atr"] = f["gap_atr"]
        tr["opening_relvol"] = f["opening_relvol"]
        tr["gap_retention"] = f["gap_retention"]
    return tr


def evaluate(start, end):
    target_start = pd.Timestamp(start).date()
    target_end = pd.Timestamp(end).date()
    warm_start = (pd.Timestamp(start) - pd.Timedelta(days=120)).date().isoformat()
    panels = base.load_panels(warm_start, end)
    stats_map = {s: session_stats(panels[s]) for s in base.UNIVERSE + [base.BENCHMARK]}
    trading_days = list(stats_map[base.BENCHMARK].index)
    news_start = (pd.Timestamp(start) - pd.Timedelta(days=2)).date().isoformat()
    events = collect_overnight_events(news_start, end)

    mapped = defaultdict(list)
    for ev in events:
        sess = next_session_for_event(ev["created_et"], trading_days)
        if sess is None or sess < target_start or sess > target_end:
            continue
        mapped[sess].append(ev)

    trades = []
    candidate_sessions = 0
    qualified_setups = 0
    for d in sorted(mapped):
        candidate_sessions += 1
        candidates = []
        for ev in mapped[d]:
            f = opening_features(ev["symbol"], d, panels, stats_map)
            if f:
                candidates.append((f["score"], ev, f))
        if not candidates:
            continue
        qualified_setups += len(candidates)
        _, ev, f = max(candidates, key=lambda x: x[0])
        bars = session_df(panels[ev["symbol"]], d)
        equity = base.START_EQ + sum(float(t["pnl_dollars"]) for t in trades)
        tr = simulate(ev, f, bars, equity)
        if tr:
            trades.append(tr)

    stats = base.summarize(trades)
    stats["primary_event_sessions"] = candidate_sessions
    stats["qualified_setups"] = qualified_setups
    stats["top_trades"] = sorted(trades, key=lambda x: x["pnl_dollars"], reverse=True)[:10]
    stats["bottom_trades"] = sorted(trades, key=lambda x: x["pnl_dollars"])[:10]
    return stats


def gate_checks(s, prefix, pf):
    return {
        f"{prefix}_trades_at_least_8": s["trades"] >= 8,
        f"{prefix}_positive_after_costs": s["total_return_pct"] > 0,
        f"{prefix}_positive_expectancy": s["avg_trade_pnl_dollars"] > 0,
        f"{prefix}_profit_factor_at_least_{str(pf).replace('.', '_')}": s["profit_factor"] >= pf,
        f"{prefix}_winner_loser_at_least_1_50": s["avg_winner_to_loser"] >= 1.50,
        f"{prefix}_drawdown_within_limit": s["max_drawdown_pct"] <= (5.0 if prefix == "development" else 8.0),
    }


def main():
    dev = evaluate("2024-01-01", "2024-12-31")
    dev_checks = gate_checks(dev, "development", 1.20)
    dev_pass = all(dev_checks.values())
    robust = evaluate("2025-01-01", "2025-12-31") if dev_pass else None
    robust_checks = gate_checks(robust, "robustness", 1.25) if robust is not None else {}
    passed = dev_pass and all(robust_checks.values())

    result = {
        "experiment": EXPERIMENT, "research_only": True, "broker_orders": False,
        "long_only": True, "leverage": False, "nonblind_followup": True, "independent_oos": False,
        "architecture": "after-hours/premarket primary event -> 10-minute opening confirmation -> next-bar long entry",
        "locked_rules": {
            "min_gap_atr": MIN_GAP_ATR, "min_opening_relvol": MIN_OPENING_RELVOL,
            "min_gap_retention": MIN_GAP_RETENTION, "min_spy_relative_strength_pct": MIN_SPY_RELATIVE_STRENGTH_PCT,
            "opening_minutes": OPENING_MINUTES, "risk_per_trade_pct": RISK_PER_TRADE_PCT * 100,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100, "cost_bps_per_fill": COST_BPS_PER_FILL,
            "break_even_after_r": 1.0, "trail_after_r": 2.0, "force_exit_et": "15:55",
        },
        "development_2024": dev, "development_checks": dev_checks,
        "robustness_2025": robust, "robustness_checks": robust_checks,
        "gate": "PASS" if passed else "FAIL", "activate": False,
    }
    with open("strategy2_experiment13_opening_news_continuation_results.json", "w") as f:
        json.dump(result, f, indent=2)
    lines = ["# Experiment 13 — Overnight Primary-Event Opening Continuation", "", f"**Gate: {result['gate']}**", "",
             "Research only; long-only; no leverage or broker orders. This is a new opening-continuation branch, not an independent OOS test.", "",
             "## 2024 development",
             f"- Primary-event sessions {dev['primary_event_sessions']} | qualified setups {dev['qualified_setups']} | trades {dev['trades']}",
             f"- Ending equity ${dev['ending_equity']:.2f} | return {dev['total_return_pct']:+.3f}% | DD {dev['max_drawdown_pct']:.3f}%",
             f"- Win rate {dev['win_rate_pct']:.2f}% | PF {dev['profit_factor']:.3f} | avg trade ${dev['avg_trade_pnl_dollars']:+.2f}",
             f"- Avg winner/loser ${dev['avg_winner_dollars']:.2f}/${dev['avg_loser_dollars']:.2f} | ratio {dev['avg_winner_to_loser']:.2f}", "",
             "## Development checks"]
    for k,v in dev_checks.items(): lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    if robust is not None:
        lines += ["", "## 2025 robustness (not independent OOS)",
                  f"- Trades {robust['trades']} | return {robust['total_return_pct']:+.3f}% | PF {robust['profit_factor']:.3f} | avg trade ${robust['avg_trade_pnl_dollars']:+.2f} | ratio {robust['avg_winner_to_loser']:.2f} | DD {robust['max_drawdown_pct']:.3f}%", "", "## Robustness checks"]
        for k,v in robust_checks.items(): lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    else:
        lines += ["", "2025 robustness was not opened because 2024 failed."]
    lines += ["", "Activation remains OFF. Historical PASS would only justify forward shadow testing.", ""]
    with open("strategy2_experiment13_opening_news_continuation_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
