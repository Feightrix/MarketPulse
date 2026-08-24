import json

import numpy as np
import pandas as pd

import strategy2_experiment18_volatile_leader_trendday as e18
import strategy2_experiment17b_volatile_5m_pattern_scan as base

EXPERIMENT = "S2-E20-EXCEPTIONAL-RUNNER-OUTLIER-GATE"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_FOLLOWUP = True

CANDIDATES = list(e18.CANDIDATES)
BENCHMARK = "QQQ"
BLOCKS = dict(e18.BLOCKS)

# Locked rare-outlier gate. All inputs are known by 09:55 ET and all
# percentile histories use only prior sessions.
LOOKBACK_SESSIONS = 40
MIN_HISTORY = 25
MIN_RETURN_PERCENTILE = 0.90
MIN_VOLUME_PERCENTILE = 0.80
MIN_RANGE_ATR_PERCENTILE = 0.80
MIN_RELATIVE_STRENGTH = 0.010
MIN_CLOSE_LOCATION = 0.80
MIN_LEADER_SEPARATION = 0.005
MIN_QQQ_FIRST30_RETURN = -0.005
TARGET_MIN_FREQUENCY_PCT = 5.0
TARGET_MAX_FREQUENCY_PCT = 15.0


def opening_table(panel, dstats):
    rows = []
    for day, g in panel.groupby(panel.index.date):
        f = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(e18.OPEN_END).time())]
        hist = dstats[dstats.index < day]
        if len(f) < 6 or hist.empty:
            continue
        atr = float(hist.iloc[-1]["atr20"])
        if not np.isfinite(atr) or atr <= 0:
            continue
        op = float(f.iloc[0]["o"])
        cl = float(f.iloc[-1]["c"])
        hi = float(f["h"].max())
        lo = float(f["l"].min())
        rng = hi - lo
        if op <= 0 or rng <= 0:
            continue
        rows.append({
            "date": day,
            "ret": cl / op - 1.0,
            "volume": float(f["v"].sum()),
            "range_atr": rng / atr,
            "close_location": (cl - lo) / rng,
        })
    return pd.DataFrame(rows).set_index("date").sort_index() if rows else pd.DataFrame()


def prior_percentile(table, day, col, value):
    hist = table[table.index < day][col].dropna().tail(LOOKBACK_SESSIONS)
    if len(hist) < MIN_HISTORY:
        return None
    return float((hist <= value).mean())


def first30_return(g):
    f = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(e18.OPEN_END).time())]
    if len(f) < 6:
        return None
    op = float(f.iloc[0]["o"])
    cl = float(f.iloc[-1]["c"])
    return cl / op - 1.0 if op > 0 else None


def exceptional_snapshot(day, panels, dstats, otables):
    names = base.selected_names(day, dstats)
    if len(names) < base.TOP_VOL_NAMES:
        return None
    qret = first30_return(base.day_slice(panels[BENCHMARK], day))
    if qret is None or not np.isfinite(qret):
        return None

    ranked = []
    for sym in names:
        tab = otables[sym]
        if tab.empty or day not in tab.index:
            continue
        row = tab.loc[day]
        rp = prior_percentile(tab, day, "ret", float(row["ret"]))
        vp = prior_percentile(tab, day, "volume", float(row["volume"]))
        xp = prior_percentile(tab, day, "range_atr", float(row["range_atr"]))
        if rp is None or vp is None or xp is None:
            continue
        ranked.append({
            "symbol": sym,
            "ret": float(row["ret"]),
            "return_percentile": rp,
            "volume_percentile": vp,
            "range_atr_percentile": xp,
            "range_atr": float(row["range_atr"]),
            "close_location": float(row["close_location"]),
            "relative_strength": float(row["ret"]) - float(qret),
        })
    if len(ranked) < 2:
        return None
    ranked.sort(key=lambda x: (x["ret"], x["symbol"]), reverse=True)
    leader, second = ranked[0], ranked[1]
    separation = leader["ret"] - second["ret"]
    passes = (
        leader["return_percentile"] >= MIN_RETURN_PERCENTILE
        and leader["volume_percentile"] >= MIN_VOLUME_PERCENTILE
        and leader["range_atr_percentile"] >= MIN_RANGE_ATR_PERCENTILE
        and leader["relative_strength"] >= MIN_RELATIVE_STRENGTH
        and leader["close_location"] >= MIN_CLOSE_LOCATION
        and separation >= MIN_LEADER_SEPARATION
        and float(qret) >= MIN_QQQ_FIRST30_RETURN
    )
    return {
        "names": names,
        "leader": leader,
        "second_symbol": second["symbol"],
        "leader_separation_pct": separation * 100.0,
        "qqq_first30_return_pct": float(qret) * 100.0,
        "passes": bool(passes),
    }


def evaluate(start, end, panels, dstats, ostats, otables):
    trades, gate_days = [], []
    all_days = [d for d in dstats["SPY"].index if pd.Timestamp(start).date() <= d <= pd.Timestamp(end).date()]
    for day in all_days:
        snap = exceptional_snapshot(day, panels, dstats, otables)
        if snap is None or not snap["passes"]:
            continue
        gate_days.append({"day": str(day), **snap})
        sym = snap["leader"]["symbol"]
        # Trade mechanics remain Experiment 18 unchanged. The exceptional leader
        # still has to pass the original trend-day candidate signal.
        sig = e18.candidate_signal(sym, day, panels, dstats, ostats)
        if sig is None:
            continue
        g = base.day_slice(panels[sym], day)
        equity = e18.START_EQ + sum(float(t["pnl"]) for t in trades)
        tr = e18.simulate(sig, g, equity)
        if tr:
            tr["exceptional_gate"] = {
                "return_percentile": snap["leader"]["return_percentile"],
                "volume_percentile": snap["leader"]["volume_percentile"],
                "range_atr_percentile": snap["leader"]["range_atr_percentile"],
                "leader_separation_pct": snap["leader_separation_pct"],
                "relative_strength_pct": snap["leader"]["relative_strength"] * 100.0,
            }
            trades.append(tr)
    stats = e18.summarize(trades, start, end)
    stats["gate_days"] = len(gate_days)
    stats["business_days"] = len(all_days)
    stats["gate_frequency_pct"] = len(gate_days) / len(all_days) * 100.0 if all_days else 0.0
    return trades, gate_days, stats


def main():
    panels = base.load_panels()
    missing = [s for s in base.CANDIDATES + base.BENCHMARKS if s not in panels or panels[s].empty]
    if missing:
        raise RuntimeError(f"Missing SIP data: {missing}")
    dstats = {s: base.daily_stats(panels[s]) for s in base.CANDIDATES + base.BENCHMARKS}
    ostats = {s: e18.opening_stats(panels[s]) for s in CANDIDATES}
    otables = {s: opening_table(panels[s], dstats[s]) for s in CANDIDATES}

    blocks = {}
    for name, (a, b) in BLOCKS.items():
        trades, gate_days, stats = evaluate(a, b, panels, dstats, ostats, otables)
        blocks[name] = {"stats": stats, "trades": trades, "gate_days": gate_days}

    result = {
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "nonblind_followup": True,
        "candidate_pool": CANDIDATES,
        "dynamic_top_atr_names": base.TOP_VOL_NAMES,
        "locked_gate": {
            "decision_time_et": "09:55",
            "lookback_sessions": LOOKBACK_SESSIONS,
            "min_history": MIN_HISTORY,
            "min_return_percentile": MIN_RETURN_PERCENTILE,
            "min_volume_percentile": MIN_VOLUME_PERCENTILE,
            "min_range_atr_percentile": MIN_RANGE_ATR_PERCENTILE,
            "min_relative_strength_pct": MIN_RELATIVE_STRENGTH * 100.0,
            "min_close_location": MIN_CLOSE_LOCATION,
            "min_leader_separation_pct": MIN_LEADER_SEPARATION * 100.0,
            "min_qqq_first30_return_pct": MIN_QQQ_FIRST30_RETURN * 100.0,
            "target_frequency_pct": [TARGET_MIN_FREQUENCY_PCT, TARGET_MAX_FREQUENCY_PCT],
        },
        "trade_signal": "Experiment 18 volatile leader trend-day unchanged",
        "execution": {
            "risk_per_trade_pct": e18.RISK_PER_TRADE * 100.0,
            "max_notional_pct": e18.MAX_NOTIONAL_PCT * 100.0,
            "cost_bps_per_fill": e18.COST_BPS_PER_FILL,
            "leverage": False,
        },
        "blocks": blocks,
        "activate": False,
    }
    with open("strategy2_experiment20_exceptional_runner_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 20 — Exceptional Runner Outlier Gate",
        "",
        "Nonblind research-only follow-up. The trade mechanics are unchanged; only a rare, prior-history-based outlier gate is added at 09:55 ET.",
        "",
        "Gate: leader first-30m return >= own 90th percentile; opening volume and range/ATR >= own 80th percentile; relative strength >= +1.0%; close location >= 0.80; lead over #2 >= 0.50%; QQQ >= -0.50%.",
        "",
    ]
    for name in BLOCKS:
        s = blocks[name]["stats"]
        lines.append(
            f"- {name}: gate days {s['gate_days']}/{s['business_days']} ({s['gate_frequency_pct']:.1f}%) | "
            f"trades {s['trades']} | win {s['win_rate_pct']:.1f}% | PF {s['profit_factor']:.2f} | "
            f"return {s['return_pct']:+.2f}% | DD {s['max_drawdown_pct']:.2f}% | avg trade-day {s['avg_return_on_trade_days_pct']:+.2f}% | "
            f">=1% days {s['days_ge_1pct']} | >=3% days {s['days_ge_3pct']} | >=5% days {s['days_ge_5pct']}"
        )
    lines += ["", "Activation OFF. 2026 is nonblind development evidence; any favorable result requires forward shadow confirmation before aggressive sizing."]
    with open("strategy2_experiment20_exceptional_runner_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
