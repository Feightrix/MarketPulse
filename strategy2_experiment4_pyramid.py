import json
import numpy as np
import pandas as pd

import phase5h_sector_neutral_ensemble as base

CONTROL_CAP = 0.50
BASE_CAP = 0.575
CFG = {"lookback": 252, "skip": 0, "top_n": 3}
NEUTRAL_WEIGHT = 0.15
COST_BPS = 10.0
START_EQ = 2500.0

# Locked before results: one add per trend asset per month, funded from BIL only.
PYRAMID_TRIGGER_PCT = 0.03
PYRAMID_ADD_FRACTION_OF_BASE_WEIGHT = 0.25
PYRAMID_ADD_CAP_ABS_WEIGHT = 0.025
MAX_PYRAMIDS_PER_SYMBOL_PER_MONTH = 1

BLOCKS = {
    "development_2008_2014": (base.DEV_START, base.DEV_END),
    "validation_2015_2019": (base.VAL_START, base.VAL_END),
    "holdout1_2020_2023": (base.H1_START, base.H1_END),
    "holdout2_2024_2026": (base.H2_START, base.H2_END),
}


def stats(curve):
    out = base.summarize(curve)
    d = curve.diff().dropna()
    out.update({
        "avg_daily_pnl_dollars": float(d.mean()) if len(d) else 0.0,
        "median_daily_pnl_dollars": float(d.median()) if len(d) else 0.0,
        "positive_day_rate_pct": float((d > 0).mean() * 100.0) if len(d) else 0.0,
        "worst_day_pnl_dollars": float(d.min()) if len(d) else 0.0,
        "best_day_pnl_dollars": float(d.max()) if len(d) else 0.0,
        "trading_days": int(len(curve)),
    })
    return out


def normal_curve(o, c, start, end, cap):
    original = base.RISK_CAP
    base.RISK_CAP = cap
    try:
        t = base.trend_curve(o, c, start, end, COST_BPS)
        n = base.neutral_curve(o, c, start, end, CFG, COST_BPS)
        return base.combine(t, n, NEUTRAL_WEIGHT)
    finally:
        base.RISK_CAP = original


def pyramid_trend_curve(o, c, start, end):
    original = base.RISK_CAP
    base.RISK_CAP = BASE_CAP
    try:
        dates = list(c.index)
        eq = 1.0
        w = {base.DEFENSIVE: 1.0}
        curve = []
        monthly_base = {}
        entry_open = {}
        pyramids = {}
        pyramid_events = 0

        for i in range(1, len(dates)):
            d = dates[i]
            if d < start:
                continue
            if d > end:
                break

            prev = c.iloc[i - 1]
            op = o.iloc[i]
            cl = c.iloc[i]

            # Carry existing holdings through the overnight gap first.
            overnight = {s: float(op[s] / prev[s] - 1.0) for s in w}
            eq *= 1.0 + sum(float(w[s]) * overnight[s] for s in w)
            w = base.drift(w, overnight)

            if base.monthly(dates, i):
                tw = base.trend_target(c, i - 1)
                turn = base.turnover(w, tw)
                eq -= eq * (COST_BPS / 10000.0) * turn
                w = dict(tw)
                monthly_base = dict(tw)
                entry_open = {
                    s: float(op[s]) for s, weight in tw.items()
                    if s != base.DEFENSIVE and float(weight) > 1e-12
                }
                pyramids = {s: 0 for s in entry_open}
            elif monthly_base:
                # Signal/trigger is measured on yesterday's close and executed at today's open.
                valid_now = base.trend_target(c, i - 1)
                for s, base_weight in sorted(monthly_base.items()):
                    if s == base.DEFENSIVE or float(base_weight) <= 1e-12:
                        continue
                    if s not in entry_open:
                        continue
                    if int(pyramids.get(s, 0)) >= MAX_PYRAMIDS_PER_SYMBOL_PER_MONTH:
                        continue
                    gain = float(prev[s] / entry_open[s] - 1.0)
                    if gain < PYRAMID_TRIGGER_PCT:
                        continue
                    if float(valid_now.get(s, 0.0)) <= 1e-12:
                        continue
                    bil_available = max(0.0, float(w.get(base.DEFENSIVE, 0.0)))
                    add = min(
                        PYRAMID_ADD_CAP_ABS_WEIGHT,
                        PYRAMID_ADD_FRACTION_OF_BASE_WEIGHT * float(base_weight),
                        bil_available,
                    )
                    if add <= 1e-12:
                        continue
                    target = dict(w)
                    target[s] = float(target.get(s, 0.0)) + add
                    target[base.DEFENSIVE] = max(0.0, float(target.get(base.DEFENSIVE, 0.0)) - add)
                    turn = base.turnover(w, target)
                    eq -= eq * (COST_BPS / 10000.0) * turn
                    w = target
                    pyramids[s] = int(pyramids.get(s, 0)) + 1
                    pyramid_events += 1

            intraday = {s: float(cl[s] / op[s] - 1.0) for s in w}
            eq *= 1.0 + sum(float(w[s]) * intraday[s] for s in w)
            w = base.drift(w, intraday)
            curve.append((d, eq))

        series = pd.Series(
            [e for _, e in curve],
            index=pd.to_datetime([d for d, _ in curve]),
            dtype=float,
        )
        return series, pyramid_events
    finally:
        base.RISK_CAP = original


def pyramid_curve(o, c, start, end):
    t, events = pyramid_trend_curve(o, c, start, end)
    n = base.neutral_curve(o, c, start, end, CFG, COST_BPS)
    return base.combine(t, n, NEUTRAL_WEIGHT), events


def main():
    o, c = base.download_panel()
    result_blocks = {}
    for name, (start, end) in BLOCKS.items():
        control_curve = normal_curve(o, c, start, end, CONTROL_CAP)
        base575_curve = normal_curve(o, c, start, end, BASE_CAP)
        pyr_curve, events = pyramid_curve(o, c, start, end)
        result_blocks[name] = {
            "control_50": stats(control_curve),
            "base_57_5": stats(base575_curve),
            "pyramid_57_5": stats(pyr_curve),
            "pyramid_events": int(events),
        }

    h1 = result_blocks["holdout1_2020_2023"]
    h2 = result_blocks["holdout2_2024_2026"]
    checks = {
        "recent_holdout_return_beats_57_5_base": h2["pyramid_57_5"]["total_return_pct"] > h2["base_57_5"]["total_return_pct"],
        "recent_holdout_avg_daily_pnl_beats_57_5_base": h2["pyramid_57_5"]["avg_daily_pnl_dollars"] > h2["base_57_5"]["avg_daily_pnl_dollars"],
        "recent_holdout_drawdown_within_original_control_plus_1pp": h2["pyramid_57_5"]["max_drawdown_pct"] <= h2["control_50"]["max_drawdown_pct"] + 1.0,
        "prior_holdout_return_not_worse_than_57_5_by_more_than_0_5pp": h1["pyramid_57_5"]["total_return_pct"] >= h1["base_57_5"]["total_return_pct"] - 0.5,
        "prior_holdout_drawdown_within_original_control_plus_1pp": h1["pyramid_57_5"]["max_drawdown_pct"] <= h1["control_50"]["max_drawdown_pct"] + 1.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": "S2-E4-CONTROLLED-PYRAMID-ON-RISKCAP-57.5",
        "research_only": True,
        "base_risk_cap": BASE_CAP,
        "control_risk_cap": CONTROL_CAP,
        "transaction_cost_stress_bps": COST_BPS,
        "pyramid_rule": {
            "trigger_gain_from_monthly_entry_pct": PYRAMID_TRIGGER_PCT * 100.0,
            "add_fraction_of_base_position_weight": PYRAMID_ADD_FRACTION_OF_BASE_WEIGHT,
            "add_cap_absolute_portfolio_weight_pct": PYRAMID_ADD_CAP_ABS_WEIGHT * 100.0,
            "max_adds_per_symbol_per_month": MAX_PYRAMIDS_PER_SYMBOL_PER_MONTH,
            "funding_source": "BIL only",
            "leverage_added": False,
            "signal_must_still_be_valid": True,
            "trigger_uses_prior_close_execute_next_open": True,
        },
        "blocks": result_blocks,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate_pyramiding": passed,
    }
    with open("strategy2_experiment4_pyramid_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Strategy 2 Experiment 4 — Controlled Pyramiding",
        "",
        f"**Gate: {result['gate']}**",
        "",
        "Base candidate is Strategy 1 with RISK_CAP 57.5%. Pyramiding is research-only unless every gate passes.",
        "",
        "## Locked pyramid rule",
        f"- Trigger: prior close at least **+{PYRAMID_TRIGGER_PCT*100:.1f}%** from monthly entry",
        f"- Add: **{PYRAMID_ADD_FRACTION_OF_BASE_WEIGHT*100:.0f}% of base position weight**, capped at **{PYRAMID_ADD_CAP_ABS_WEIGHT*100:.1f} portfolio points**",
        "- Funding: **reduce BIL by the same amount**",
        "- Maximum: **one add per symbol per month**",
        "- Original trend signal must still be valid",
        "- No leverage / no increase in total portfolio weight",
        f"- Transaction-cost stress: **{COST_BPS:.0f} bps**",
        "",
    ]
    for name, block in result_blocks.items():
        b = block["base_57_5"]
        p = block["pyramid_57_5"]
        lines += [
            f"## {name}",
            f"- 57.5 base: return {b['total_return_pct']:+.3f}% | DD {b['max_drawdown_pct']:.3f}% | avg day ${b['avg_daily_pnl_dollars']:+.3f}",
            f"- Pyramid: return {p['total_return_pct']:+.3f}% | DD {p['max_drawdown_pct']:.3f}% | avg day ${p['avg_daily_pnl_dollars']:+.3f}",
            f"- Pyramid events: **{block['pyramid_events']}**",
            "",
        ]
    lines.append("## Predeclared checks")
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", f"**Decision: {'ELIGIBLE FOR FORWARD TEST' if passed else 'DO NOT ACTIVATE PYRAMIDING'}**", ""]
    with open("strategy2_experiment4_pyramid_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
