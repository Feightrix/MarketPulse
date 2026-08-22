import json
from pathlib import Path

import strategy2_micro_blind_backtest as base

# Follow-up test after the original blind result. This is NOT a blind test.
# Only the profit-lock arm threshold changes from 0.35% to 0.60%.
# Trigger marker: workflow available on main.
base.ARM_PROFIT_PCT = 0.0060
base.RESULT_FILE = "strategy2_micro_arm60_results.json"
base.SUMMARY_FILE = "strategy2_micro_arm60_summary.md"


def main():
    base.main()

    result_path = Path(base.RESULT_FILE)
    result = json.loads(result_path.read_text())
    result["experiment"] = "CONTROL_CLONE_MICRO_PROFIT_LOCK_ARM60_FOLLOWUP"
    result["followup_nonblind"] = True
    result["blind_protocol_locked_before_results"] = False
    result["no_post_result_tuning"] = False
    result["parameter_change_from_prior_test"] = {
        "field": "arm_profit_pct",
        "old": 0.0035,
        "new": 0.0060,
        "all_other_parameters_frozen": True,
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    c = result["control"]
    m = result["micro"]
    lines = [
        "# Strategy 2 — 0.60% Arm Follow-Up Backtest",
        "",
        "This is a **follow-up, non-blind** test. The 0.60% arm threshold was selected after reviewing the prior 0.35% test.",
        "",
        "## Frozen setup",
        f"- Test: **{result['test_start']} through {result['test_end']}**",
        "- Only changed variable: **profit-lock arm 0.35% → 0.60%**",
        "- All other execution/risk settings: **unchanged**",
        "- Historical data: **Alpaca IEX 1-minute bars**",
        "- Cost model: **10 bps per fill**",
        "",
        "## Results",
        f"- Control ending equity: **${c['final_equity']:,.2f}**",
        f"- Control total return: **{c['total_return_pct']:+.3f}%**",
        f"- Control max drawdown: **{c['max_drawdown_pct']:.3f}%**",
        f"- 0.60% micro ending equity: **${m['final_equity']:,.2f}**",
        f"- 0.60% micro total return: **{m['total_return_pct']:+.3f}%**",
        f"- 0.60% micro max drawdown: **{m['max_drawdown_pct']:.3f}%**",
        f"- Avg daily P&L: **${m['avg_daily_pnl']:+.2f}**",
        f"- Round trips: **{m['total_round_trips']}**",
        f"- Avg round trips/day: **{m['avg_round_trips_per_day']:.2f}**",
        f"- Modeled execution costs: **${m['total_modeled_execution_costs']:,.2f}**",
        "",
        f"## Gate: **{result['gate']}**",
    ]
    for k, v in result["gate_checks"].items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    Path(base.SUMMARY_FILE).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
