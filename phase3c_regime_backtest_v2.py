import json, math
from itertools import product

import phase3c_regime_backtest as b


def soft_score(m):
    # Ranking only. This does NOT change the Phase 3C pass gate.
    if m["trades"] <= 0:
        return -1e12
    return (
        m["expectancy_bps"]
        + 2.0 * m["daily_sharpe"]
        + 2.0 * min(m["profit_factor"], 3.0)
        + 2.0 * m["monthly_positive_rate"]
        + min(m["trades"], 60) / 60.0
        - 8.0 * m["max_drawdown"]
    )


def main():
    data = b.load_data()
    regimes, selections = b.daily_regimes(data)
    print("Packing periods", flush=True)
    dev = b.pack_period(data, regimes, selections, "2021-01-01", "2023-12-31")
    y24 = b.pack_period(data, regimes, selections, "2024-01-01", "2024-12-31")
    y25 = b.pack_period(data, regimes, selections, "2025-01-01", "2025-12-31")
    y26 = b.pack_period(data, regimes, selections, "2026-01-01", "2026-07-31")
    v2425 = b.pack_period(data, regimes, selections, "2024-01-01", "2025-12-31")

    configs = list(product(
        list(regimes), [5, 20], dev["entry_names"],
        [1.5, 2.0], [0.8, 1.0], [6, 12]
    ))
    candidates = []
    for n, (regime, lb, entry, tm, sm, hold) in enumerate(configs, 1):
        m = b.sim(dev, regime, lb, entry, tm, sm, hold, b.BASE_FRICTION_BPS)
        hard = b.dev_score(m)
        candidates.append({
            "regime": regime, "selection_lookback": lb, "entry": entry,
            "target_mult": tm, "stop_mult": sm, "hold": hold,
            "development": m, "score": hard, "soft_score": soft_score(m),
            "development_pass": hard > -1e8,
        })
        if n % 32 == 0 or n == len(configs):
            print("development", n, "/", len(configs), flush=True)

    dev_ok = [c for c in candidates if c["development_pass"]]
    pool = dev_ok if dev_ok else [c for c in candidates if c["development"]["trades"] > 0]
    finalists = sorted(pool, key=lambda c: (c["score"] if c["development_pass"] else c["soft_score"]), reverse=True)[:40]

    diag = {
        "candidates_with_trades": sum(c["development"]["trades"] > 0 for c in candidates),
        "positive_expectancy_candidates": sum(c["development"]["expectancy_bps"] > 0 for c in candidates),
        "max_development_trades": max((c["development"]["trades"] for c in candidates), default=0),
        "max_development_expectancy_bps": max((c["development"]["expectancy_bps"] for c in candidates), default=0.0),
        "max_development_return": max((c["development"]["total_return"] for c in candidates), default=0.0),
    }
    print("diagnostics", json.dumps(diag), flush=True)

    checked = []
    for i, c in enumerate(finalists, 1):
        args = (c["regime"], c["selection_lookback"], c["entry"], c["target_mult"], c["stop_mult"], c["hold"])
        m24 = b.sim(y24, *args, b.BASE_FRICTION_BPS)
        m25 = b.sim(y25, *args, b.BASE_FRICTION_BPS)
        robust = (
            m24["trades"] >= 8 and m25["trades"] >= 8
            and m24["expectancy_bps"] > 2.0 and m25["expectancy_bps"] > 2.0
            and m24["profit_factor"] > 1.10 and m25["profit_factor"] > 1.10
            and m24["max_drawdown"] < 0.08 and m25["max_drawdown"] < 0.08
        )
        vscore = (
            min(m24["expectancy_bps"], m25["expectancy_bps"])
            + 2 * min(m24["daily_sharpe"], m25["daily_sharpe"])
            + min(m24["profit_factor"], m25["profit_factor"])
            + min(m24["monthly_positive_rate"], m25["monthly_positive_rate"])
        ) if robust else (
            min(m24["expectancy_bps"], m25["expectancy_bps"])
            + min(m24["profit_factor"], m25["profit_factor"])
        )
        checked.append({**c, "validation_2024": m24, "validation_2025": m25, "robust": robust, "vscore": vscore})
        print("validation", i, "/", len(finalists), robust, flush=True)

    robust_candidates = [c for c in checked if c["robust"] and c["development_pass"]]
    if robust_candidates:
        best = max(robust_candidates, key=lambda c: c["vscore"])
    elif checked:
        best = max(checked, key=lambda c: (c["vscore"], c["soft_score"]))
    else:
        best = max(candidates, key=lambda c: c["soft_score"])
        best = {
            **best,
            "validation_2024": b.calc_metrics([], b.START_EQ, 0.0, y24["months_all"]),
            "validation_2025": b.calc_metrics([], b.START_EQ, 0.0, y25["months_all"]),
            "robust": False,
            "vscore": -1e9,
        }

    args = (best["regime"], best["selection_lookback"], best["entry"], best["target_mult"], best["stop_mult"], best["hold"])
    combined = b.sim(v2425, *args, b.BASE_FRICTION_BPS)
    friction = {str(x): b.sim(v2425, *args, x) for x in [2.0, 4.0, 6.0, 10.0]}
    check26 = b.sim(y26, *args, b.BASE_FRICTION_BPS)

    gate = (
        best.get("development_pass", False)
        and best.get("robust", False)
        and combined["expectancy_bps"] > 2.0
        and combined["profit_factor"] > 1.10
        and friction["4.0"]["expectancy_bps"] > 0
        and friction["4.0"]["profit_factor"] > 1.0
        and check26["trades"] >= 5
        and check26["expectancy_bps"] > 0
        and check26["profit_factor"] > 1.05
    )

    result = {
        "phase": "3C",
        "goal": "Track a 2x first-of-month balance target without allowing the target to change risk or force trades.",
        "method": "Prior-day regime filter + prior-day ETF relative-strength selection + one selective intraday entry/day maximum.",
        "candidate_count": len(configs),
        "development_valid_candidates": len(dev_ok),
        "robust_2024_2025_candidates": len(robust_candidates),
        "diagnostics": diag,
        "selected_is_development_pass": bool(best.get("development_pass", False)),
        "selected": {
            "regime": best["regime"],
            "selection_lookback_days": best["selection_lookback"],
            "entry": best["entry"],
            "target_atr_multiple": best["target_mult"],
            "stop_atr_multiple": best["stop_mult"],
            "target_range_pct": [b.TARGET_FLOOR, b.TARGET_CAP],
            "stop_range_pct": [b.STOP_FLOOR, b.STOP_CAP],
            "max_hold_minutes": best["hold"] * 5,
            "max_trades_per_day": b.MAX_TRADES_PER_DAY,
            "capital_fraction": b.CAPITAL,
        },
        "development": b.rounded(best["development"]),
        "validation_2024": b.rounded(best["validation_2024"]),
        "validation_2025": b.rounded(best["validation_2025"]),
        "validation_2024_2025": b.rounded(combined),
        "check_2026": b.rounded(check26),
        "friction_validation_2024_2025": {k: b.rounded(v) for k, v in friction.items()},
        "gate": "PASS" if gate else "FAIL",
        "warning": "A 100% monthly target is an aspiration, not a guaranteed or expected return. The strategy gate is based on robustness, not on forcing the target.",
    }
    with open("phase3c_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse — Phase 3C Regime-Aware Validation", "",
        "**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)",
        f"**Candidates tested:** {len(configs)}",
        f"**Candidates with at least one development trade:** {diag['candidates_with_trades']}",
        f"**Positive-expectancy development candidates:** {diag['positive_expectancy_candidates']}",
        f"**Development-valid candidates:** {len(dev_ok)}",
        f"**Candidates passing development + both 2024 and 2025 validation:** {len(robust_candidates)}", "",
        "## Selected strongest candidate (not necessarily a PASS)", "",
        f"- Development gate passed: **{'YES' if best.get('development_pass', False) else 'NO'}**",
        f"- Regime filter: **{best['regime']}**",
        f"- ETF selection: strongest prior **{best['selection_lookback']} trading-day** return",
        f"- Entry: **{best['entry']}**",
        f"- Dynamic target: **{best['target_mult']:.2f} × ATR**, bounded to {b.TARGET_FLOOR:.2%}–{b.TARGET_CAP:.2%}",
        f"- Dynamic stop: **{best['stop_mult']:.2f} × ATR**, bounded to {b.STOP_FLOOR:.2%}–{b.STOP_CAP:.2%}",
        f"- Max hold: **{best['hold'] * 5} minutes**",
        f"- Max trades/day: **{b.MAX_TRADES_PER_DAY}**", "",
        "## Results", "",
        "| Period | Trades | Return | Expectancy | PF | Max DD | Positive months | Best month | Doubled months |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, m in [
        ("Development 2021-2023", result["development"]),
        ("Validation 2024", result["validation_2024"]),
        ("Validation 2025", result["validation_2025"]),
        ("Validation 2024-2025", result["validation_2024_2025"]),
        ("2026 check through Jul", result["check_2026"]),
    ]:
        lines.append(
            f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['expectancy_bps']:.2f} bps | "
            f"{m['profit_factor']:.2f} | {m['max_drawdown']:.2%} | {m['monthly_positive_rate']:.2%} | "
            f"{m['best_month_return']:.2%} | {m['months_doubled']}/{m['months_total']} |"
        )
    lines += ["", "## Development diagnostics", "",
              f"- Maximum trades among any candidate: **{diag['max_development_trades']}**",
              f"- Highest candidate expectancy: **{diag['max_development_expectancy_bps']:.2f} bps/trade**",
              f"- Highest candidate total return: **{diag['max_development_return']:.2%}**", "",
              "## Validation friction stress", "",
              "| One-way friction | Expectancy | Return | PF |",
              "|---:|---:|---:|---:|"]
    for x, m in result["friction_validation_2024_2025"].items():
        lines.append(f"| {float(x):.0f} bps | {m['expectancy_bps']:.2f} bps | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += ["", f"**Phase 3C gate: {result['gate']}**", "",
              "## Important", "",
              "The monthly doubling target is an objective only. It never raises leverage, position size, trade frequency, or loss tolerance. This corrected report ranks failed candidates honestly when none clears the development gate; it does not lower the gate to create a PASS."]
    with open("phase3c_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
