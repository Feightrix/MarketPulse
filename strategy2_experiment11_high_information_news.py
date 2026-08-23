import json
import re

import strategy2_experiment10_entity_abnormal_news as base

EXPERIMENT = "S2-E11-HIGH-INFORMATION-CATALYST-FILTER"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False

# Experiment 10 execution/risk/tape rules remain unchanged.
# Experiment 11 changes only catalyst-quality filtering.
GENERIC_REJECT_PATTERNS = [
    r"comparative study",
    r"performance comparison",
    r"market analysis",
    r"industry competitors",
    r"compared to competitors",
    r"standing in .* industry",
    r"analyst maintains",
    r"analyst reiterates",
    r"analyst initiates",
    r"analyst upgrades",
    r"analyst downgrades",
    r"raises price target",
    r"cuts price target",
    r"price target to",
    r"top stocks",
    r"stocks to watch",
    r"why .* stock is moving",
]

HIGH_INFORMATION_EVENT_PATTERNS = [
    r"\bearnings\b", r"\beps\b", r"\brevenue\b", r"\bguidance\b", r"\boutlook\b",
    r"\braises guidance\b", r"\bcuts guidance\b", r"\bannounces\b", r"\bannounced\b",
    r"\blaunches\b", r"\blaunched\b", r"\bunveils\b", r"\bunveiled\b",
    r"\bsigns\b", r"\bsigned\b", r"\bwins? (?:a )?(?:major )?(?:contract|order|award)\b",
    r"\bcontract\b", r"\border\b", r"\bpartnership\b", r"\bpartners? with\b",
    r"\bacquires?\b", r"\bacquisition\b", r"\bmerger\b", r"\bbuyout\b",
    r"\bapproved\b", r"\bapproval\b", r"\bfda\b", r"\bregulatory\b",
    r"\bsettlement\b", r"\bsettles\b", r"\blawsuit\b", r"\bantitrust\b",
    r"\binvestigation\b", r"\bprobe\b", r"\bfiles?\b", r"\brecall\b", r"\brecalls\b",
    r"\bbuyback\b", r"\brepurchase\b", r"\bdividend\b",
]

_original_collect_events = base.collect_events


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def is_high_information(event):
    headline = normalize(event.get("headline", ""))
    if any(re.search(p, headline) for p in GENERIC_REJECT_PATTERNS):
        return False
    if bool(event.get("hard_catalyst")):
        return True
    return any(re.search(p, headline) for p in HIGH_INFORMATION_EVENT_PATTERNS)


def collect_high_information_events(start, end):
    events = _original_collect_events(start, end)
    return [e for e in events if is_high_information(e)]


base.collect_events = collect_high_information_events


def dev_checks(stats):
    return {
        "development_trades_at_least_5": stats["trades"] >= 5,
        "development_positive_expectancy": stats["avg_trade_pnl_dollars"] > 0,
        "development_profit_factor_at_least_1_20": stats["profit_factor"] >= 1.20,
        "development_winner_loser_at_least_1_25": stats["avg_winner_to_loser"] >= 1.25,
        "development_drawdown_at_most_5pct": stats["max_drawdown_pct"] <= 5.0,
    }


def validation_checks(dev, val):
    return {
        "validation_trades_at_least_5": val["trades"] >= 5,
        "validation_positive_after_costs": val["total_return_pct"] > 0,
        "validation_positive_expectancy": val["avg_trade_pnl_dollars"] > 0,
        "validation_profit_factor_at_least_1_25": val["profit_factor"] >= 1.25,
        "validation_winner_loser_at_least_1_50": val["avg_winner_to_loser"] >= 1.50,
        "validation_drawdown_at_most_8pct": val["max_drawdown_pct"] <= 8.0,
        "combined_trades_at_least_15": dev["trades"] + val["trades"] >= 15,
    }


def compact_rules():
    return {
        "only_changed_variable": "catalyst_quality_filter",
        "generic_headline_patterns_rejected": GENERIC_REJECT_PATTERNS,
        "hard_catalyst_accepted": True,
        "explicit_event_language_accepted": HIGH_INFORMATION_EVENT_PATTERNS,
        "experiment10_rules_unchanged": {
            "min_move_robust_z": base.MIN_MOVE_Z,
            "min_volume_robust_z": base.MIN_VOLUME_Z,
            "min_spy_relative_strength_robust_z": base.MIN_RS_Z,
            "hold_minutes": base.HOLD_MINUTES,
            "max_retrace_fraction": base.MAX_RETRACE_FRACTION,
            "cost_bps_per_fill": base.COST_BPS_PER_FILL,
            "risk_per_trade_pct": base.RISK_PER_TRADE_PCT * 100,
            "max_notional_pct": base.MAX_NOTIONAL_PCT * 100,
            "break_even_after_r": 1.0,
            "trail_after_r": 2.0,
            "force_exit_et": "15:55",
        },
    }


def main():
    dev = base.evaluate_period("2024-01-01", "2024-12-31")
    dchecks = dev_checks(dev)
    dpass = all(dchecks.values())

    val = None
    vchecks = {}
    if dpass:
        val = base.evaluate_period("2025-01-01", "2025-12-31")
        vchecks = validation_checks(dev, val)

    passed = dpass and bool(val) and all(vchecks.values())
    result = {
        "experiment": EXPERIMENT,
        "research_only": RESEARCH_ONLY,
        "broker_orders": BROKER_ORDERS,
        "long_only": LONG_ONLY,
        "leverage": LEVERAGE,
        "feed": base.FEED,
        "universe": base.UNIVERSE,
        "rules_locked_before_results": compact_rules(),
        "development_2024": dev,
        "development_checks": dchecks,
        "development_gate": "PASS" if dpass else "FAIL",
        "validation_2025": val,
        "validation_checks": vchecks,
        "gate": "PASS" if passed else "FAIL",
        "activate": False,
    }
    with open("strategy2_experiment11_high_information_news_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 11 — High-Information Catalyst Filter",
        "",
        f"**Development gate: {result['development_gate']}**",
        f"**Overall gate: {result['gate']}**",
        "",
        "Only catalyst-quality filtering changed from Experiment 10. Tape, entry, stop, sizing and cost rules are identical.",
        "",
        "## 2024 development",
        f"- High-information entity-relevant events: {dev['funnel']['entity_relevant_news']}",
        f"- Trades: {dev['trades']} | return {dev['total_return_pct']:+.3f}% | ending equity ${dev['ending_equity']:.2f}",
        f"- Win rate {dev['win_rate_pct']:.2f}% | PF {dev['profit_factor']:.3f} | avg trade ${dev['avg_trade_pnl_dollars']:+.2f}",
        f"- Avg winner/loser ${dev['avg_winner_dollars']:.2f} / ${dev['avg_loser_dollars']:.2f} | ratio {dev['avg_winner_to_loser']:.2f}",
        f"- Max DD {dev['max_drawdown_pct']:.3f}% | best/worst ${dev['best_trade_dollars']:+.2f} / ${dev['worst_trade_dollars']:+.2f}",
        "",
        "## Development checks",
    ]
    for k, v in dchecks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    if val is not None:
        lines += [
            "",
            "## 2025 validation",
            f"- Trades: {val['trades']} | return {val['total_return_pct']:+.3f}% | ending equity ${val['ending_equity']:.2f}",
            f"- Win rate {val['win_rate_pct']:.2f}% | PF {val['profit_factor']:.3f} | avg trade ${val['avg_trade_pnl_dollars']:+.2f}",
            f"- Avg winner/loser ${val['avg_winner_dollars']:.2f} / ${val['avg_loser_dollars']:.2f} | ratio {val['avg_winner_to_loser']:.2f}",
            f"- Max DD {val['max_drawdown_pct']:.3f}% | best/worst ${val['best_trade_dollars']:+.2f} / ${val['worst_trade_dollars']:+.2f}",
            "",
            "## Validation checks",
        ]
        for k, v in vchecks.items():
            lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    else:
        lines += ["", "2025 validation was not opened because the predeclared 2024 development gate failed."]
    lines += ["", "Activation remains OFF; this is research only.", ""]
    with open("strategy2_experiment11_high_information_news_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
