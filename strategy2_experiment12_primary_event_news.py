import json
import re

import strategy2_experiment10_entity_abnormal_news as base

EXPERIMENT = "S2-E12-PRIMARY-EVENT-NEWS-MOMENTUM"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_FOLLOWUP = True

# Experiment 10 tape, execution, stop, sizing, and cost rules remain unchanged.
# Experiment 12 changes only event classification / novelty filtering.

REJECT_PATTERNS = [
    r"\banalyst\b", r"\bprice target\b", r"\bmaintains?\b", r"\breiterates?\b",
    r"\bupgrades?\b", r"\bdowngrades?\b", r"\binitiates?\b", r"\bsurvey\b", r"\bpoll\b",
    r"\bcomparative study\b", r"\bperformance comparison\b", r"\bmarket analysis\b",
    r"\bindustry competitors?\b", r"\bstocks? to watch\b", r"\btop stocks?\b",
    r"\bwhy .* stock (?:is )?moving\b", r"\bwhat investors need to know\b", r"\bpreview\b",
    r"\bahead of earnings\b", r"\bsize up\b", r"\bready to cancel\b",
    r"\bcould\b", r"\bmight\b", r"\bmay\b", r"\bconsiders?\b", r"\bconsidering\b",
    r"\breportedly\b", r"\brumou?r\b", r"\bexplores?\b", r"\bweighs?\b", r"\bin talks\b",
    r"\brecap\b", r"\bpreviously reported\b", r"\bearlier report\b", r"\brivalry\b",
]

EVENT_PATTERNS = {
    "earnings_result": [
        r"\breports?\b.*\b(?:earnings|eps|revenue|results)\b",
        r"\b(?:earnings|eps|revenue)\b.*\b(?:beats?|misses?|tops?|falls short)\b",
        r"\b(?:beats?|misses?)\b.*\b(?:earnings|eps|revenue|estimate)\b",
        r"\bquarterly results\b",
    ],
    "guidance_change": [
        r"\b(?:raises?|boosts?|increases?|lifts?)\b.*\b(?:guidance|outlook|forecast)\b",
        r"\b(?:cuts?|lowers?|reduces?)\b.*\b(?:guidance|outlook|forecast)\b",
        r"\b(?:guidance|outlook|forecast)\b.*\b(?:raised|boosted|increased|lifted|cut|lowered|reduced)\b",
    ],
    "definitive_deal": [
        r"\bagrees? to acquire\b", r"\bto acquire\b", r"\bacquires?\b", r"\bacquisition agreement\b",
        r"\bmerger agreement\b", r"\bdefinitive agreement\b.*\b(?:acquire|merger)\b", r"\bbuyout\b",
    ],
    "contract_award": [
        r"\bawarded\b.*\b(?:contract|order|deal)\b", r"\bwins?\b.*\b(?:contract|order|award)\b",
        r"\bsigns?\b.*\b(?:contract|agreement|deal)\b", r"\b(?:contract|order)\b.*\bawarded\b",
    ],
    "regulatory_legal_decision": [
        r"\bfda\b.*\bapprov", r"\bregulator\b.*\bapprov", r"\bapproved by\b.*\b(?:fda|regulator)",
        r"\bcourt rules?\b", r"\bjudge rules?\b", r"\bsettles?\b.*\b(?:case|lawsuit|probe|investigation)",
        r"\bsettlement\b.*\b(?:case|lawsuit|probe|investigation)",
    ],
    "capital_return": [
        r"\b(?:authorizes?|approves?|announces?)\b.*\b(?:buyback|repurchase)\b",
        r"\b(?:raises?|increases?|boosts?)\b.*\bdividend\b",
    ],
    "company_launch": [
        r"\b(?:launches?|unveils?|introduces?|releases?)\b.*\b(?:product|platform|service|chip|model|device|ai)\b",
    ],
}


def classify_primary_event(headline):
    h = re.sub(r"\s+", " ", (headline or "").lower()).strip()
    if any(re.search(p, h) for p in REJECT_PATTERNS):
        return None, "low_information_or_speculative"
    for event_type, patterns in EVENT_PATTERNS.items():
        if any(re.search(p, h) for p in patterns):
            return event_type, "accepted_primary_event"
    return None, "no_primary_event_pattern"


_original_collect_events = base.collect_events
_original_simulate_trade = base.simulate_trade


def collect_primary_events(start, end):
    raw = _original_collect_events(start, end)
    kept = []
    for ev in raw:
        event_type, reason = classify_primary_event(ev.get("headline", ""))
        if event_type is None:
            continue
        e = dict(ev)
        e["event_type"] = event_type
        e["event_quality_reason"] = reason
        kept.append(e)
    return kept


def simulate_trade_with_event_type(event, conf, bars, equity):
    out = _original_simulate_trade(event, conf, bars, equity)
    if out is not None:
        out["event_type"] = event.get("event_type")
    return out


base.collect_events = collect_primary_events
base.simulate_trade = simulate_trade_with_event_type


def checks_for(stats, prefix, pf_min):
    return {
        f"{prefix}_trades_at_least_5": stats["trades"] >= 5,
        f"{prefix}_positive_after_costs": stats["total_return_pct"] > 0.0,
        f"{prefix}_positive_expectancy": stats["avg_trade_pnl_dollars"] > 0.0,
        f"{prefix}_profit_factor_at_least_{str(pf_min).replace('.', '_')}": stats["profit_factor"] >= pf_min,
        f"{prefix}_winner_loser_at_least_1_50": stats["avg_winner_to_loser"] >= 1.50,
        f"{prefix}_drawdown_at_most_{5 if prefix == 'development' else 8}pct": stats["max_drawdown_pct"] <= (5.0 if prefix == "development" else 8.0),
    }


def main():
    dev = base.evaluate_period("2024-01-01", "2024-12-31")
    dev_checks = checks_for(dev, "development", 1.20)
    dev_pass = all(dev_checks.values())

    # 2025 has already been inspected in prior follow-up experiments, so this is robustness evidence,
    # not a fresh independent out-of-sample validation block.
    robust = base.evaluate_period("2025-01-01", "2025-12-31") if dev_pass else None
    robust_checks = checks_for(robust, "robustness", 1.25) if robust is not None else {}
    passed = dev_pass and all(robust_checks.values())

    result = {
        "experiment": EXPERIMENT,
        "research_only": RESEARCH_ONLY,
        "broker_orders": BROKER_ORDERS,
        "long_only": LONG_ONLY,
        "leverage": LEVERAGE,
        "nonblind_followup": NONBLIND_FOLLOWUP,
        "independent_oos": False,
        "only_changed_variable": "event_type_and_novelty_classifier",
        "experiment10_rules_unchanged": {
            "min_move_robust_z": base.MIN_MOVE_Z,
            "min_volume_robust_z": base.MIN_VOLUME_Z,
            "min_spy_relative_strength_robust_z": base.MIN_RS_Z,
            "hold_minutes": base.HOLD_MINUTES,
            "max_retrace_fraction": base.MAX_RETRACE_FRACTION,
            "cost_bps_per_fill": base.COST_BPS_PER_FILL,
            "risk_per_trade_pct": base.RISK_PER_TRADE_PCT * 100.0,
            "max_notional_pct": base.MAX_NOTIONAL_PCT * 100.0,
            "break_even_after_r": 1.0,
            "trail_after_r": 2.0,
        },
        "event_types_accepted": list(EVENT_PATTERNS.keys()),
        "development_2024": dev,
        "development_checks": dev_checks,
        "robustness_2025": robust,
        "robustness_checks": robust_checks,
        "gate": "PASS" if passed else "FAIL",
        "activate": False,
    }
    with open("strategy2_experiment12_primary_event_news_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 12 — Primary-Event News Momentum", "",
        f"**Gate: {result['gate']}**", "",
        "Follow-up/nonblind historical research. Only event classification changed from Experiment 10; tape, entry, stops, sizing and costs are unchanged.", "",
        "## 2024 development",
        f"- Primary-event candidates: {dev['funnel']['entity_relevant_news']} | trades: {dev['trades']}",
        f"- Ending equity ${dev['ending_equity']:.2f} | return {dev['total_return_pct']:+.3f}% | DD {dev['max_drawdown_pct']:.3f}%",
        f"- Win rate {dev['win_rate_pct']:.2f}% | PF {dev['profit_factor']:.3f} | avg trade ${dev['avg_trade_pnl_dollars']:+.2f}",
        f"- Avg winner/loser ${dev['avg_winner_dollars']:.2f}/${dev['avg_loser_dollars']:.2f} | ratio {dev['avg_winner_to_loser']:.2f}", "",
        "## Development checks",
    ]
    for k, v in dev_checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    if robust is not None:
        lines += ["", "## 2025 robustness (not independent OOS)",
                  f"- Trades {robust['trades']} | return {robust['total_return_pct']:+.3f}% | PF {robust['profit_factor']:.3f} | avg trade ${robust['avg_trade_pnl_dollars']:+.2f} | ratio {robust['avg_winner_to_loser']:.2f} | DD {robust['max_drawdown_pct']:.3f}%",
                  "", "## Robustness checks"]
        for k, v in robust_checks.items():
            lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    else:
        lines += ["", "2025 robustness was not opened because the 2024 development gate failed."]
    lines += ["", "Activation remains OFF. Any historical PASS is tuning evidence; clean forward shadow data is required before promotion.", ""]
    with open("strategy2_experiment12_primary_event_news_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
