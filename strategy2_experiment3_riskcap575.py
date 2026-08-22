# Trigger marker: workflow is now installed on main; no research parameter change.
import json
import numpy as np
import pandas as pd
import phase5h_sector_neutral_ensemble as base

CONTROL_CAP = 0.50
CANDIDATE_CAP = 0.575
CFG = {"lookback": 252, "skip": 0, "top_n": 3}
NEUTRAL_WEIGHT = 0.15
COST_BPS = 10.0
START_EQ = 2500.0

BLOCKS = {
    "development_2008_2014": (base.DEV_START, base.DEV_END),
    "validation_2015_2019": (base.VAL_START, base.VAL_END),
    "holdout1_2020_2023": (base.H1_START, base.H1_END),
    "holdout2_2024_2026": (base.H2_START, base.H2_END),
}


def eval_curve(o, c, start, end, cap):
    original = base.RISK_CAP
    base.RISK_CAP = cap
    try:
        trend = base.trend_curve(o, c, start, end, COST_BPS)
        neutral = base.neutral_curve(o, c, start, end, CFG, COST_BPS)
        curve = base.combine(trend, neutral, NEUTRAL_WEIGHT)
        stats = base.summarize(curve)
        d = curve.diff().dropna()
        stats.update({
            "avg_daily_pnl_dollars": float(d.mean()) if len(d) else 0.0,
            "median_daily_pnl_dollars": float(d.median()) if len(d) else 0.0,
            "positive_day_rate_pct": float((d > 0).mean() * 100.0) if len(d) else 0.0,
            "worst_day_pnl_dollars": float(d.min()) if len(d) else 0.0,
            "best_day_pnl_dollars": float(d.max()) if len(d) else 0.0,
            "trading_days": int(len(curve)),
        })
        return stats
    finally:
        base.RISK_CAP = original


def evaluate(o, c, cap):
    return {name: eval_curve(o, c, start, end, cap) for name, (start, end) in BLOCKS.items()}


def main():
    o, c = base.download_panel()
    control = evaluate(o, c, CONTROL_CAP)
    candidate = evaluate(o, c, CANDIDATE_CAP)

    h1c, h1x = control["holdout1_2020_2023"], candidate["holdout1_2020_2023"]
    h2c, h2x = control["holdout2_2024_2026"], candidate["holdout2_2024_2026"]
    checks = {
        "recent_holdout_return_improves": h2x["total_return_pct"] > h2c["total_return_pct"],
        "recent_holdout_drawdown_not_over_control_plus_1pp": h2x["max_drawdown_pct"] <= h2c["max_drawdown_pct"] + 1.0,
        "prior_holdout_return_not_worse_by_more_than_1pp": h1x["total_return_pct"] >= h1c["total_return_pct"] - 1.0,
        "prior_holdout_drawdown_not_over_control_plus_1pp": h1x["max_drawdown_pct"] <= h1c["max_drawdown_pct"] + 1.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": "S2-E3-RISK-CAP-57.5",
        "single_changed_variable": "RISK_CAP",
        "control_value": CONTROL_CAP,
        "candidate_value": CANDIDATE_CAP,
        "all_other_strategy_parameters": "frozen Phase 5H",
        "transaction_cost_stress_bps": COST_BPS,
        "control": control,
        "candidate": candidate,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate_candidate": passed,
        "research_only": True,
    }
    with open("strategy2_experiment3_riskcap575_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Strategy 2 — Experiment 3: RISK_CAP 57.5%",
        "",
        "**Single variable:** trend `RISK_CAP` 50% → 57.5%",
        f"**Gate: {result['gate']}**",
        "",
        "Everything else remains frozen to Strategy 1. Results use 10 bps transaction-cost stress.",
        "",
    ]
    for block in BLOCKS:
        a, b = control[block], candidate[block]
        lines += [
            f"## {block}",
            f"- Control: return {a['total_return_pct']:+.3f}% | CAGR {a['cagr_pct']:+.3f}% | DD {a['max_drawdown_pct']:.3f}% | avg day ${a['avg_daily_pnl_dollars']:+.3f}",
            f"- Candidate: return {b['total_return_pct']:+.3f}% | CAGR {b['cagr_pct']:+.3f}% | DD {b['max_drawdown_pct']:.3f}% | avg day ${b['avg_daily_pnl_dollars']:+.3f}",
            f"- Candidate best/worst day: ${b['best_day_pnl_dollars']:+.2f} / ${b['worst_day_pnl_dollars']:+.2f}",
            "",
        ]
    lines.append("## Predeclared activation checks")
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", f"**Activation decision: {'PASS FOR FURTHER VALIDATION' if passed else 'REJECT 57.5% CANDIDATE'}**", ""]
    with open("strategy2_experiment3_riskcap575_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
