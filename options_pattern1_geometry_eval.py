import json
from datetime import datetime, timezone
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern1_geometry as geo

RESULT_JSON = "options_pattern1_geometry_results.json"
RESULT_MD = "options_pattern1_geometry_results.md"


def main():
    raw = base.fetch_bars()
    by_day = base.regular_session_bars(raw)
    days = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        if base.opening_range(bars) is not None:
            days.append((day, bars))

    split = int(len(days) * geo.TRAIN_FRACTION)
    dev = days[:split]
    holdout_days = days[split:]
    fold_split = len(dev) // 2
    fold_a = dev[:fold_split]
    fold_b = dev[fold_split:]

    candidates = []
    both_profitable = []
    for cfg in geo.grid():
        sa = base.summarize(geo.evaluate(fold_a, cfg))
        sb = base.summarize(geo.evaluate(fold_b, cfg))
        if sa["trades"] < geo.MIN_FOLD_TRADES or sb["trades"] < geo.MIN_FOLD_TRADES:
            continue
        row = (geo.robust_score(sa, sb), cfg, sa, sb)
        candidates.append(row)
        if sa["net_r"] > 0 and sb["net_r"] > 0:
            both_profitable.append(row)

    if not candidates:
        raise RuntimeError("No geometry produced enough trades for evaluation")

    # Select without seeing the holdout. Highest score emphasizes the weaker development fold.
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, selected, sa, sb = candidates[0]

    hold_trades = geo.evaluate(holdout_days, selected)
    full_trades = geo.evaluate(days, selected)
    hold = geo.dollarize(base.summarize(hold_trades))
    full = geo.dollarize(base.summarize(full_trades))

    prior = json.loads(Path("options_pattern1_refinement_results.json").read_text())
    calls = geo.dollarize(base.summarize([t for t in full_trades if t["side"] == "CALL"]))
    puts = geo.dollarize(base.summarize([t for t in full_trades if t["side"] == "PUT"]))

    result = {
        "strategy": "options_pattern1_entry_stop_geometry",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "complete_sessions": len(days),
        "target_r": geo.TARGET_R,
        "starting_balance": geo.STARTING_BALANCE,
        "risk_dollars_per_1r": geo.RISK_DOLLARS,
        "geometry_grid_tested": len(list(geo.grid())),
        "eligible_geometries": len(candidates),
        "profitable_in_both_development_folds": len(both_profitable),
        "robust_development_pass": len(both_profitable) > 0,
        "selected_geometry": selected,
        "fold_a": geo.dollarize(sa),
        "fold_b": geo.dollarize(sb),
        "holdout": hold,
        "full_sample": full,
        "direction_split_full": {"calls": calls, "puts": puts},
        "prior_leader": {
            "full_pl_dollars": prior["full_sample"]["net_pl_dollars"],
            "holdout_pl_dollars": prior["holdout"]["net_pl_dollars"],
            "full_win_rate_pct": prior["full_sample"]["win_rate_pct"],
            "holdout_win_rate_pct": prior["holdout"]["win_rate_pct"],
        },
        "replace_prior_leader": (
            len(both_profitable) > 0
            and hold["net_pl_dollars"] > prior["holdout"]["net_pl_dollars"]
            and full["net_pl_dollars"] > prior["full_sample"]["net_pl_dollars"]
        ),
    }
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    cfg = selected
    lines = [
        "# MarketPulse — Options Pattern 1 Entry/Stop Geometry",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        f"- Geometries tested: **{result['geometry_grid_tested']}**",
        f"- Profitable in both development folds: **{result['profitable_in_both_development_folds']}**",
        f"- Robust development pass: **{'YES' if result['robust_development_pass'] else 'NO'}**",
        "",
        "## Selected Least-Bad Geometry (chosen before holdout)",
        f"- Entry mode: **{cfg['entry_mode']}**",
        f"- Trigger buffer: **{cfg['trigger_range_buffer']:.0%} of retest range**",
        f"- Stop padding: **{cfg['stop_range_pad']:.0%} of retest range**",
        f"- Stop base: **{cfg['stop_base']}**",
        "- Target: **1.5R**",
        "",
        "## Development Fold A",
        f"- Trades: **{result['fold_a']['trades']}** | Win rate: **{result['fold_a']['win_rate_pct']:.2f}%** | P/L: **${result['fold_a']['net_pl_dollars']:,.2f}**",
        "## Development Fold B",
        f"- Trades: **{result['fold_b']['trades']}** | Win rate: **{result['fold_b']['win_rate_pct']:.2f}%** | P/L: **${result['fold_b']['net_pl_dollars']:,.2f}**",
        "",
        "## Untouched Holdout",
        f"- Trades: **{hold['trades']}**",
        f"- Win rate: **{hold['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${hold['net_pl_dollars']:,.2f}**",
        f"- Profit factor: **{hold['profit_factor']}**",
        f"- Max drawdown: **${hold['max_drawdown_dollars']:,.2f}**",
        "",
        "## Full 180-Day Sample",
        f"- Trades: **{full['trades']}**",
        f"- Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${full['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%**",
        f"- Profit factor: **{full['profit_factor']}**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        "",
        "## Decision",
        f"- Replace prior +${prior['full_sample']['net_pl_dollars']:,.2f} leader: **{'YES' if result['replace_prior_leader'] else 'NO'}**",
        "",
        "Dollar P/L is risk-normalized underlying-pattern P/L at $25 per 1R, not actual option-premium P/L.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
