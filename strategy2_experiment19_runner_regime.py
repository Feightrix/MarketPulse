import json

import numpy as np
import pandas as pd

import strategy2_experiment18_volatile_leader_trendday as e18
import strategy2_experiment17b_volatile_5m_pattern_scan as base

EXPERIMENT = "S2-E19-RUNNER-DAY-REGIME"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_FOLLOWUP = True

CANDIDATES = list(e18.CANDIDATES)
BENCHMARK = "QQQ"
BLOCKS = dict(e18.BLOCKS)

# Locked before seeing Experiment 19 results.
# All values are known by 09:55 ET. This is a market-environment overlay only;
# the individual trade setup and execution remain Experiment 18 unchanged.
MIN_POSITIVE_BREADTH = 3
MIN_MEDIAN_FIRST30_RETURN = 0.0025
MIN_TOP2_AVG_FIRST30_RETURN = 0.0100
MIN_QQQ_FIRST30_RETURN = -0.0035


def first30_return(g):
    f = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(e18.OPEN_END).time())]
    if len(f) < 6:
        return None
    op = float(f.iloc[0]["o"])
    cl = float(f.iloc[-1]["c"])
    if op <= 0:
        return None
    return cl / op - 1.0


def regime_snapshot(day, panels, dstats):
    names = base.selected_names(day, dstats)
    if len(names) < base.TOP_VOL_NAMES:
        return None

    returns = []
    by_symbol = {}
    for sym in names:
        r = first30_return(base.day_slice(panels[sym], day))
        if r is None or not np.isfinite(r):
            return None
        returns.append(float(r))
        by_symbol[sym] = float(r)

    qret = first30_return(base.day_slice(panels[BENCHMARK], day))
    if qret is None or not np.isfinite(qret):
        return None

    ordered = sorted(returns, reverse=True)
    positive_breadth = sum(r > 0 for r in returns)
    median_ret = float(np.median(returns))
    top2_avg = float(np.mean(ordered[:2]))

    passes = (
        positive_breadth >= MIN_POSITIVE_BREADTH
        and median_ret >= MIN_MEDIAN_FIRST30_RETURN
        and top2_avg >= MIN_TOP2_AVG_FIRST30_RETURN
        and float(qret) >= MIN_QQQ_FIRST30_RETURN
    )

    return {
        "names": names,
        "returns": by_symbol,
        "positive_breadth": positive_breadth,
        "median_first30_return_pct": median_ret * 100.0,
        "top2_avg_first30_return_pct": top2_avg * 100.0,
        "qqq_first30_return_pct": float(qret) * 100.0,
        "passes": bool(passes),
    }


def evaluate(start, end, panels, dstats, ostats):
    trades = []
    regime_days = []
    all_days = [d for d in dstats["SPY"].index if pd.Timestamp(start).date() <= d <= pd.Timestamp(end).date()]

    for day in all_days:
        snap = regime_snapshot(day, panels, dstats)
        if snap is None or not snap["passes"]:
            continue
        regime_days.append({"day": str(day), **snap})

        signals = []
        for sym in snap["names"]:
            sig = e18.candidate_signal(sym, day, panels, dstats, ostats)
            if sig is not None:
                signals.append(sig)
        if not signals:
            continue

        sig = max(signals, key=lambda x: (x["score"], x["symbol"]))
        g = base.day_slice(panels[sig["symbol"]], day)
        equity = e18.START_EQ + sum(float(t["pnl"]) for t in trades)
        tr = e18.simulate(sig, g, equity)
        if tr:
            tr["regime"] = {k: v for k, v in snap.items() if k not in {"returns", "names", "passes"}}
            trades.append(tr)

    stats = e18.summarize(trades, start, end)
    stats["regime_days"] = len(regime_days)
    stats["business_days"] = len(all_days)
    stats["regime_frequency_pct"] = len(regime_days) / len(all_days) * 100.0 if all_days else 0.0
    return trades, regime_days, stats


def main():
    panels = base.load_panels()
    missing = [s for s in base.CANDIDATES + base.BENCHMARKS if s not in panels or panels[s].empty]
    if missing:
        raise RuntimeError(f"Missing SIP data: {missing}")

    dstats = {s: base.daily_stats(panels[s]) for s in base.CANDIDATES + base.BENCHMARKS}
    ostats = {s: e18.opening_stats(panels[s]) for s in CANDIDATES}

    blocks = {}
    for name, (a, b) in BLOCKS.items():
        trades, regime_days, stats = evaluate(a, b, panels, dstats, ostats)
        blocks[name] = {"stats": stats, "trades": trades, "regime_days": regime_days}

    result = {
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "nonblind_followup": True,
        "candidate_pool": CANDIDATES,
        "dynamic_top_atr_names": base.TOP_VOL_NAMES,
        "locked_regime": {
            "decision_time_et": "09:55",
            "min_positive_breadth_of_top5": MIN_POSITIVE_BREADTH,
            "min_median_first30_return_pct": MIN_MEDIAN_FIRST30_RETURN * 100.0,
            "min_top2_avg_first30_return_pct": MIN_TOP2_AVG_FIRST30_RETURN * 100.0,
            "min_qqq_first30_return_pct": MIN_QQQ_FIRST30_RETURN * 100.0,
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

    with open("strategy2_experiment19_runner_regime_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 19 — Runner-Day Regime Overlay",
        "",
        "Nonblind research-only follow-up. By 09:55 ET, the overlay asks whether the five prior-ATR-selected volatile names are moving together strongly enough to permit the unchanged Experiment 18 leader trade.",
        "",
        "Runner-day rule: at least 3/5 positive; median first-30m return >= +0.25%; top-2 average >= +1.00%; QQQ first-30m return >= -0.35%.",
        "",
    ]
    for name in BLOCKS:
        s = blocks[name]["stats"]
        lines.append(
            f"- {name}: regime days {s['regime_days']}/{s['business_days']} ({s['regime_frequency_pct']:.1f}%) | "
            f"trades {s['trades']} | win {s['win_rate_pct']:.1f}% | PF {s['profit_factor']:.2f} | "
            f"return {s['return_pct']:+.2f}% | DD {s['max_drawdown_pct']:.2f}% | "
            f"avg trade-day {s['avg_return_on_trade_days_pct']:+.2f}% | >=1% days {s['days_ge_1pct']} | >=3% days {s['days_ge_3pct']}"
        )
    lines += ["", "Activation OFF. Historical 2026 data is nonblind; any promising result must be confirmed in forward shadow trading before aggressive sizing."]
    with open("strategy2_experiment19_runner_regime_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
