import json
from datetime import datetime, timezone
from pathlib import Path

import options_pattern2_contract_ab as ab

RESULT_JSON = "options_pattern2_contract_gate_results.json"
RESULT_MD = "options_pattern2_contract_gate_results.md"
POLICY_NAME = "itm_4d_delta60"
MIN_FOLD_TRADES = 12
MIN_EXTERNAL_TRADES = 8

# Predeclared single-variable execution gates. Underlying Pattern #2 signals remain frozen.
# The gate may only SKIP a signal; it never changes entry timing, direction, stop, target, or contract policy.
GATES = [
    {"name": "iv_max_0p12", "feature": "iv_proxy", "op": "max", "value": 0.12},
    {"name": "iv_max_0p16", "feature": "iv_proxy", "op": "max", "value": 0.16},
    {"name": "iv_max_0p20", "feature": "iv_proxy", "op": "max", "value": 0.20},
    {"name": "premium_max_350", "feature": "premium_dollars", "op": "max", "value": 350.0},
    {"name": "premium_max_400", "feature": "premium_dollars", "op": "max", "value": 400.0},
    {"name": "premium_max_450", "feature": "premium_dollars", "op": "max", "value": 450.0},
    {"name": "efficiency_max_0p35", "feature": "efficiency", "op": "max", "value": 0.35},
    {"name": "efficiency_max_0p45", "feature": "efficiency", "op": "max", "value": 0.45},
    {"name": "efficiency_max_0p55", "feature": "efficiency", "op": "max", "value": 0.55},
    {"name": "adverse_vwap_slope_max_0p15", "feature": "adverse_vwap_slope_atr", "op": "max", "value": 0.15},
    {"name": "adverse_vwap_slope_max_0p30", "feature": "adverse_vwap_slope_atr", "op": "max", "value": 0.30},
    {"name": "adverse_vwap_slope_max_0p40", "feature": "adverse_vwap_slope_atr", "op": "max", "value": 0.40},
]


def adverse_vwap_slope(sig):
    slope = float(sig.get("vwap_slope_atr", 0.0))
    return max(0.0, -slope) if sig["side"] == "CALL" else max(0.0, slope)


def attach_features(sig, trade):
    out = dict(trade)
    out["efficiency"] = round(float(sig.get("efficiency", 0.0)), 4)
    out["vwap_slope_atr"] = round(float(sig.get("vwap_slope_atr", 0.0)), 4)
    out["adverse_vwap_slope_atr"] = round(adverse_vwap_slope(sig), 4)
    out["rsi_turn"] = round(float(sig.get("rsi_turn", 0.0)), 2)
    return out


def passes_gate(trade, gate):
    val = trade.get(gate["feature"])
    if val is None:
        return False
    if gate["op"] == "max":
        return float(val) <= float(gate["value"])
    raise ValueError(f"Unsupported gate op: {gate['op']}")


def split(trades, fold_bounds):
    development = [t for t in trades if datetime.fromisoformat(t["date"]).date() >= ab.DEVELOPMENT_START]
    external = [t for t in trades if datetime.fromisoformat(t["date"]).date() < ab.DEVELOPMENT_START]
    folds = ab.split_dev_by_bounds(development, fold_bounds)
    return development, external, folds


def gate_result(trades, gate, fold_bounds):
    kept = [t for t in trades if passes_gate(t, gate)]
    dev, external, folds = split(kept, fold_bounds)
    return {
        "gate": gate,
        "development_folds": [ab.summarize(f) for f in folds],
        "development": ab.summarize(dev),
        "external_validation": ab.summarize(external),
        "full": ab.summarize(kept),
        "direction_split": {
            "calls": ab.summarize([t for t in kept if t["side"] == "CALL"]),
            "puts": ab.summarize([t for t in kept if t["side"] == "PUT"]),
        },
        "kept_trades": len(kept),
        "skipped_trades": len(trades) - len(kept),
    }


def development_score(item):
    folds = item["development_folds"]
    return (
        min(f["net_pl_dollars"] for f in folds),
        min((f["profit_factor"] or 0.0) for f in folds),
        item["development"]["net_pl_dollars"],
        -item["development"]["max_drawdown_dollars"],
        item["development"]["trades"],
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    baseline = result["baseline"]
    chosen = result.get("selected_development_gate")
    ext_pass = result.get("external_confirmation_pass", False)
    lines = [
        "# MarketPulse — Pattern 2 0.60Δ Contract Execution Gate",
        "",
        "**Research only. Pattern #2 signals and the 4-DTE slightly-ITM ~0.60Δ contract policy remain frozen.**",
        "",
        "## Baseline 4-DTE ~0.60Δ",
        f"- Development Fold 1 P/L: **${baseline['development_folds'][0]['net_pl_dollars']:,.2f}**",
        f"- Development P/L: **${baseline['development']['net_pl_dollars']:,.2f}**",
        f"- External validation P/L: **${baseline['external_validation']['net_pl_dollars']:,.2f}**",
        f"- Full P/L: **${baseline['full']['net_pl_dollars']:,.2f}**",
        "",
    ]
    if chosen:
        lines += [
            "## Selected Development Gate",
            f"- Gate: **{chosen['gate']['name']}**",
            f"- Development Fold 1 P/L: **${chosen['development_folds'][0]['net_pl_dollars']:,.2f}**",
            f"- Development Fold 2 P/L: **${chosen['development_folds'][1]['net_pl_dollars']:,.2f}**",
            f"- Development Fold 3 P/L: **${chosen['development_folds'][2]['net_pl_dollars']:,.2f}**",
            f"- Development P/L: **${chosen['development']['net_pl_dollars']:,.2f}**",
            f"- External validation P/L: **${chosen['external_validation']['net_pl_dollars']:,.2f}**",
            f"- Full P/L: **${chosen['full']['net_pl_dollars']:,.2f}**",
            f"- Full win rate: **{chosen['full']['win_rate_pct']:.2f}%**",
            f"- Full profit factor: **{chosen['full']['profit_factor']}**",
            f"- Full max drawdown: **${chosen['full']['max_drawdown_dollars']:,.2f}**",
            f"- Kept / skipped trades: **{chosen['kept_trades']} / {chosen['skipped_trades']}**",
            "",
            f"- External confirmation improved versus baseline: **{'YES' if ext_pass else 'NO'}**",
            f"- Gate promoted: **{'YES' if result['promoted_gate'] else 'NO'}**",
        ]
    else:
        lines += [
            "## Result",
            "No single-variable gate was profitable in all three development folds with sufficient trade count.",
            "- Gate promoted: **NO**",
        ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    signals, sessions = ab.build_frozen_signals()
    fold_bounds = ab.development_fold_bounds(signals)
    trades = []

    for idx, sig in enumerate(signals, 1):
        results, _ = ab.evaluate_signal(sig)
        trade = results.get(POLICY_NAME)
        if trade:
            trades.append(attach_features(sig, trade))
        if idx % 20 == 0:
            print(f"processed {idx}/{len(signals)} frozen signals")

    base_dev, base_external, base_folds = split(trades, fold_bounds)
    baseline = {
        "development_folds": [ab.summarize(f) for f in base_folds],
        "development": ab.summarize(base_dev),
        "external_validation": ab.summarize(base_external),
        "full": ab.summarize(trades),
        "direction_split": {
            "calls": ab.summarize([t for t in trades if t["side"] == "CALL"]),
            "puts": ab.summarize([t for t in trades if t["side"] == "PUT"]),
        },
    }

    results = [gate_result(trades, gate, fold_bounds) for gate in GATES]
    eligible = []
    for item in results:
        folds = item["development_folds"]
        if all(f["trades"] >= MIN_FOLD_TRADES and f["net_pl_dollars"] > 0 for f in folds):
            eligible.append(item)

    selected = max(eligible, key=development_score) if eligible else None
    external_pass = False
    promoted = None
    if selected:
        ext = selected["external_validation"]
        base_ext = baseline["external_validation"]
        external_pass = (
            ext["trades"] >= MIN_EXTERNAL_TRADES
            and ext["net_pl_dollars"] > base_ext["net_pl_dollars"]
            and ext["profit_factor"] is not None
            and ext["profit_factor"] >= (base_ext["profit_factor"] or 0.0)
        )
        if external_pass:
            promoted = selected["gate"]

    result = {
        "strategy": "options_pattern2_contract_execution_gate",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "complete_sessions": sessions,
        "signals_generated": len(signals),
        "contracts_simulated": len(trades),
        "frozen_contract_policy": {
            "name": POLICY_NAME,
            "target_dte": 4,
            "side_moneyness": -0.005,
            "target_abs_delta": 0.60,
        },
        "gates_predeclared": GATES,
        "baseline": baseline,
        "gate_results": {x["gate"]["name"]: x for x in results},
        "eligible_on_development_only": len(eligible),
        "selected_development_gate": selected,
        "external_confirmation_pass": external_pass,
        "promoted_gate": promoted,
    }
    write_results(result)


if __name__ == "__main__":
    main()
