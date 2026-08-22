import json

import strategy2_experiment8_news_momentum_long as base
import strategy2_experiment8_news_momentum_runner as fast

base.UNIVERSE = ["NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "NFLX", "GOOGL"]
base.collect_events = fast.collect_events_fast


def main():
    stats = base.run_block("pilot_2026_ytd", "2026-01-01", "2026-07-31")
    checks = {
        "at_least_5_trades": stats["trades"] >= 5,
        "positive_after_costs": stats["total_return_pct"] > 0.0,
        "positive_expectancy": stats["avg_trade_pnl_dollars"] > 0.0,
        "profit_factor_at_least_1_25": stats["profit_factor"] >= 1.25,
        "winner_loser_ratio_at_least_1_5": stats["avg_winner_to_loser"] >= 1.5,
        "max_drawdown_at_most_8pct": stats["max_drawdown_pct"] <= 8.0,
    }
    passed = all(checks.values())
    compact = {k: v for k, v in stats.items() if k != "trade_details"}
    compact["top_10_trades"] = sorted(stats["trade_details"], key=lambda x: x["pnl_dollars"], reverse=True)[:10]
    compact["bottom_10_trades"] = sorted(stats["trade_details"], key=lambda x: x["pnl_dollars"])[:10]
    result = {
        "experiment": "S2-E8-NEWS-MOMENTUM-2026-YTD-PILOT",
        "research_only": True,
        "full_validation": False,
        "universe": base.UNIVERSE,
        "period": ["2026-01-01", "2026-07-31"],
        "strategy_rules": "identical to locked Experiment 8",
        "stats": compact,
        "checks": checks,
        "pilot_gate": "PASS" if passed else "FAIL",
        "activate": False,
    }
    with open("strategy2_experiment8_news_momentum_quick_results.json", "w") as f:
        json.dump(result, f, indent=2)
    lines = [
        "# Experiment 8 — 2026 YTD News Momentum Pilot",
        "",
        f"**Pilot gate: {result['pilot_gate']}**",
        "",
        "Same locked trading rules as Experiment 8. Scope-only reduction to eight liquid news-sensitive names and Jan–Jul 2026.",
        "",
        f"- Eligible news events: {stats['eligible_news_events']}",
        f"- Confirmed events: {stats['confirmed_events']}",
        f"- Trades: {stats['trades']}",
        f"- Ending equity: ${stats['ending_equity']:.2f}",
        f"- Total return: {stats['total_return_pct']:+.3f}%",
        f"- Max drawdown: {stats['max_drawdown_pct']:.3f}%",
        f"- Win rate: {stats['win_rate_pct']:.2f}%",
        f"- Profit factor: {stats['profit_factor']:.3f}",
        f"- Avg trade P&L: ${stats['avg_trade_pnl_dollars']:+.2f}",
        f"- Avg winner / loser: ${stats['avg_winner_dollars']:.2f} / ${stats['avg_loser_dollars']:.2f}",
        f"- Winner/loser ratio: {stats['avg_winner_to_loser']:.2f}",
        f"- Best / worst trade: ${stats['best_trade_dollars']:+.2f} / ${stats['worst_trade_dollars']:+.2f}",
        f"- Avg R multiple: {stats['avg_r_multiple']:+.3f}",
        "",
        "## Pilot checks",
    ]
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "Activation remains OFF. This pilot is not a substitute for multi-period validation.", ""]
    with open("strategy2_experiment8_news_momentum_quick_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
