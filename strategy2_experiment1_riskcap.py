# Strategy 2 Experiment 1: one-variable risk-cap A/B test.
import json
import phase5h_sector_neutral_ensemble as base

CONTROL_CAP = 0.50
CANDIDATE_CAP = 0.60
CFG = {"lookback": 252, "skip": 0, "top_n": 3}
NEUTRAL_WEIGHT = 0.15
COST_BPS = 10.0

BLOCKS = {
    "development_2008_2014": (base.DEV_START, base.DEV_END),
    "validation_2015_2019": (base.VAL_START, base.VAL_END),
    "holdout1_2020_2023": (base.H1_START, base.H1_END),
    "holdout2_2024_2026": (base.H2_START, base.H2_END),
}


def evaluate(o, c, cap):
    original = base.RISK_CAP
    base.RISK_CAP = cap
    try:
        return {
            name: base.eval_block(o, c, start, end, CFG, NEUTRAL_WEIGHT, COST_BPS)
            for name, (start, end) in BLOCKS.items()
        }
    finally:
        base.RISK_CAP = original


def main():
    o, c = base.download_panel()
    control = evaluate(o, c, CONTROL_CAP)
    candidate = evaluate(o, c, CANDIDATE_CAP)

    h1c = control["holdout1_2020_2023"]
    h1x = candidate["holdout1_2020_2023"]
    h2c = control["holdout2_2024_2026"]
    h2x = candidate["holdout2_2024_2026"]

    checks = {
        "recent_holdout_return_improves": h2x["total_return_pct"] > h2c["total_return_pct"],
        "recent_holdout_drawdown_not_over_control_plus_1pp": h2x["max_drawdown_pct"] <= h2c["max_drawdown_pct"] + 1.0,
        "prior_holdout_return_not_worse_by_more_than_1pp": h1x["total_return_pct"] >= h1c["total_return_pct"] - 1.0,
        "prior_holdout_drawdown_not_over_control_plus_1pp": h1x["max_drawdown_pct"] <= h1c["max_drawdown_pct"] + 1.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": "S2-E1-RISK-CAP",
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
    }
    with open("strategy2_experiment1_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Strategy 2 — Experiment 1",
        "",
        "**Single variable:** trend `RISK_CAP` 50% → 60%",
        f"**Gate: {result['gate']}**",
        "",
        "Everything else remains frozen to the control strategy. Results below use 10 bps transaction-cost stress.",
        "",
    ]
    for block in BLOCKS:
        a = control[block]
        b = candidate[block]
        lines += [
            f"## {block}",
            f"- Control: return {a['total_return_pct']:+.2f}% | CAGR {a['cagr_pct']:+.2f}% | DD {a['max_drawdown_pct']:.2f}%",
            f"- Candidate: return {b['total_return_pct']:+.2f}% | CAGR {b['cagr_pct']:+.2f}% | DD {b['max_drawdown_pct']:.2f}%",
            "",
        ]
    lines += ["## Predeclared activation checks"]
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", f"**Activation decision: {'ENABLE Strategy 2 candidate' if passed else 'KEEP Strategy 2 at control baseline'}**", ""]
    with open("strategy2_experiment1_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
