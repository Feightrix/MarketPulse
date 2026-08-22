import json
import pandas as pd

import phase5h_sector_neutral_ensemble as base

CONTROL_CAP = 0.50
BASE_CAP = 0.575
CONTROL_TARGET_VOL = 0.08
CANDIDATE_TARGET_VOL = 0.09
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


def eval_curve(o, c, start, end, cap, target_vol):
    original_cap = base.RISK_CAP
    original_target = base.TARGET_VOL
    base.RISK_CAP = cap
    base.TARGET_VOL = target_vol
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
        base.RISK_CAP = original_cap
        base.TARGET_VOL = original_target


def evaluate(o, c, cap, target_vol):
    return {
        name: eval_curve(o, c, start, end, cap, target_vol)
        for name, (start, end) in BLOCKS.items()
    }


def main():
    o, c = base.download_panel()
    control50 = evaluate(o, c, CONTROL_CAP, CONTROL_TARGET_VOL)
    base575 = evaluate(o, c, BASE_CAP, CONTROL_TARGET_VOL)
    candidate = evaluate(o, c, BASE_CAP, CANDIDATE_TARGET_VOL)

    h1c = control50["holdout1_2020_2023"]
    h1b = base575["holdout1_2020_2023"]
    h1x = candidate["holdout1_2020_2023"]
    h2c = control50["holdout2_2024_2026"]
    h2b = base575["holdout2_2024_2026"]
    h2x = candidate["holdout2_2024_2026"]

    checks = {
        "recent_holdout_return_beats_57_5_base": h2x["total_return_pct"] > h2b["total_return_pct"],
        "recent_holdout_avg_daily_pnl_beats_57_5_base": h2x["avg_daily_pnl_dollars"] > h2b["avg_daily_pnl_dollars"],
        "recent_holdout_drawdown_within_original_control_plus_1pp": h2x["max_drawdown_pct"] <= h2c["max_drawdown_pct"] + 1.0,
        "prior_holdout_return_not_worse_than_57_5_by_more_than_0_5pp": h1x["total_return_pct"] >= h1b["total_return_pct"] - 0.5,
        "prior_holdout_drawdown_within_original_control_plus_1pp": h1x["max_drawdown_pct"] <= h1c["max_drawdown_pct"] + 1.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": "S2-E5-TARGET-VOL-9-ON-RISKCAP-57.5",
        "research_only": True,
        "single_changed_variable": "TARGET_VOL",
        "base_risk_cap": BASE_CAP,
        "control_target_vol": CONTROL_TARGET_VOL,
        "candidate_target_vol": CANDIDATE_TARGET_VOL,
        "transaction_cost_stress_bps": COST_BPS,
        "all_other_strategy_parameters": "frozen Phase 5H plus accepted 57.5% risk cap",
        "control_50_targetvol8": control50,
        "base_57_5_targetvol8": base575,
        "candidate_57_5_targetvol9": candidate,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate_target_vol_9": passed,
    }
    with open("strategy2_experiment5_targetvol9_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Strategy 2 — Experiment 5: TARGET_VOL 9%",
        "",
        "**Single variable:** `TARGET_VOL` 8% → 9% with `RISK_CAP = 57.5%` fixed.",
        f"**Gate: {result['gate']}**",
        "",
        "Everything else remains frozen. Results use 10 bps transaction-cost stress.",
        "",
    ]
    for block in BLOCKS:
        c0 = control50[block]
        b = base575[block]
        x = candidate[block]
        lines += [
            f"## {block}",
            f"- Control 50% / 8% vol: return {c0['total_return_pct']:+.3f}% | CAGR {c0['cagr_pct']:+.3f}% | DD {c0['max_drawdown_pct']:.3f}% | avg day ${c0['avg_daily_pnl_dollars']:+.3f}",
            f"- Base 57.5% / 8% vol: return {b['total_return_pct']:+.3f}% | CAGR {b['cagr_pct']:+.3f}% | DD {b['max_drawdown_pct']:.3f}% | avg day ${b['avg_daily_pnl_dollars']:+.3f}",
            f"- Candidate 57.5% / 9% vol: return {x['total_return_pct']:+.3f}% | CAGR {x['cagr_pct']:+.3f}% | DD {x['max_drawdown_pct']:.3f}% | avg day ${x['avg_daily_pnl_dollars']:+.3f}",
            f"- Candidate best/worst day: ${x['best_day_pnl_dollars']:+.2f} / ${x['worst_day_pnl_dollars']:+.2f}",
            "",
        ]
    lines.append("## Predeclared checks")
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += [
        "",
        f"**Decision: {'PASS FOR FURTHER VALIDATION' if passed else 'REJECT 9% TARGET VOL CANDIDATE'}**",
        "",
    ]
    with open("strategy2_experiment5_targetvol9_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
