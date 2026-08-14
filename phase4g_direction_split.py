import json

import numpy as np

import phase4e_trend_day_backtest as p4e


OUT_JSON = "phase4g_results.json"
OUT_MD = "phase4g_summary.md"
DIRECTIONS = ("bull", "bear")


def load_locked_setup():
    with open("phase4e_results.json", "r") as f:
        result = json.load(f)
    family = result["selected"]["family"]
    config = result["selected"]["config"]
    return family, config


def simulate_direction(days, start, end, config, direction_only, bps=p4e.BASE_BPS, return_trades=False):
    eq = p4e.START_EQ
    peak = eq
    max_dd = 0.0
    trades = []

    for rec in days:
        if not (start <= rec["date"] <= end):
            continue

        setup = p4e.setup_for_day(rec, config)
        if not setup:
            continue

        direction, entry_i, impulse, range_ratio, volume_ratio = setup
        if direction != direction_only:
            continue

        prefix = "bull" if direction == "bull" else "bear"
        entry = float(rec[prefix + "_open"][entry_i])
        if not np.isfinite(entry) or entry <= 0:
            continue

        stop_dist = entry * config["stop_bps"] / 10000.0
        target_dist = entry * config["target_bps"] / 10000.0
        risk_dollars = eq * p4e.RISK_PER_TRADE
        qty_risk = int(risk_dollars // stop_dist) if stop_dist > 0 else 0
        qty_cap = int((eq * p4e.GROSS_CAP) // entry)
        qty = min(qty_risk, qty_cap)
        if qty < 1:
            continue

        stop = entry - stop_dist
        target = entry + target_dist
        exit_price = None
        exit_i = None
        exit_reason = None
        last_i = len(rec["minute"]) - 1

        for k in range(entry_i, last_i + 1):
            low = float(rec[prefix + "_low"][k])
            high = float(rec[prefix + "_high"][k])
            if not np.isfinite(low) or not np.isfinite(high):
                continue
            stop_hit = low <= stop
            target_hit = high >= target
            if stop_hit and target_hit:
                exit_price, exit_i, exit_reason = stop, k, "stop_same_bar"
                break
            if stop_hit:
                exit_price, exit_i, exit_reason = stop, k, "stop"
                break
            if target_hit:
                exit_price, exit_i, exit_reason = target, k, "target"
                break

        if exit_price is None:
            exit_i = last_i
            exit_price = float(rec[prefix + "_close"][exit_i])
            if not np.isfinite(exit_price):
                continue
            exit_reason = "close"

        gross_pnl = qty * (exit_price - entry)
        cost = p4e.trade_cost(entry, exit_price, qty, bps)
        net_pnl = gross_pnl - cost
        notional = qty * entry
        net_bps = net_pnl / notional * 10000.0 if notional > 0 else 0.0
        eq_before = eq
        eq += net_pnl
        peak = max(peak, eq)
        dd = 1.0 - eq / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

        trades.append({
            "date": str(rec["date"]),
            "direction": direction,
            "entry_minute": int(rec["minute"][entry_i]),
            "exit_minute": int(rec["minute"][exit_i]),
            "entry": entry,
            "exit": exit_price,
            "qty": qty,
            "gross_pnl": gross_pnl,
            "cost": cost,
            "net_pnl": net_pnl,
            "net_bps": net_bps,
            "exit_reason": exit_reason,
            "impulse_bps": impulse,
            "range_ratio": range_ratio,
            "volume_ratio": volume_ratio,
            "equity_before": eq_before,
            "equity_after": eq,
        })

    metrics = p4e.summarize(eq, max_dd, trades)
    return (metrics, trades) if return_trades else metrics


def direction_gate(dev10, y24_10, y25_10, val10, y26_10):
    checks = {
        "development_min_trades": dev10["trades"] >= 20,
        "development_10bps_profitable": dev10["total_return_pct"] > 0 and dev10["expectancy_bps"] > 0 and dev10["profit_factor"] > 1.05,
        "2024_min_trades": y24_10["trades"] >= 4,
        "2024_10bps_profitable": y24_10["total_return_pct"] > 0 and y24_10["profit_factor"] > 1.0,
        "2025_min_trades": y25_10["trades"] >= 4,
        "2025_10bps_profitable": y25_10["total_return_pct"] > 0 and y25_10["profit_factor"] > 1.0,
        "validation_min_trades": val10["trades"] >= 10,
        "validation_10bps_profitable": val10["total_return_pct"] > 0 and val10["expectancy_bps"] > 0 and val10["profit_factor"] > 1.05,
        "validation_drawdown": val10["max_drawdown_pct"] <= 8.0,
        "2026_min_trades": y26_10["trades"] >= 4,
        "2026_10bps_profitable": y26_10["total_return_pct"] > 0 and y26_10["expectancy_bps"] > 0 and y26_10["profit_factor"] > 1.0,
    }
    return checks, all(checks.values())


def fmt(m):
    return (
        f'{m["trades"]} trades | return {m["total_return_pct"]:+.4f}% | '
        f'expectancy {m["expectancy_bps"]:+.2f} bps/trade | PF {m["profit_factor"]:.3f} | '
        f'max DD {m["max_drawdown_pct"]:.3f}% | win rate {m["win_rate_pct"]:.2f}%'
    )


def evaluate_direction(days, config, direction):
    out = {}
    periods = {
        "development": (p4e.DEV_START, p4e.DEV_END),
        "2024": (p4e.Y24_START, p4e.Y24_END),
        "2025": (p4e.Y25_START, p4e.Y25_END),
        "validation_2024_2025": (p4e.V_START, p4e.V_END),
        "holdout_2026": (p4e.Y26_START, p4e.Y26_END),
    }
    for label, (start, end) in periods.items():
        out[label] = {
            "2bps": simulate_direction(days, start, end, config, direction, 2.0),
            "5bps": simulate_direction(days, start, end, config, direction, 5.0),
            "10bps": simulate_direction(days, start, end, config, direction, 10.0),
        }

    checks, passed = direction_gate(
        out["development"]["10bps"],
        out["2024"]["10bps"],
        out["2025"]["10bps"],
        out["validation_2024_2025"]["10bps"],
        out["holdout_2026"]["10bps"],
    )
    out["gate_checks"] = checks
    out["gate"] = "PASS" if passed else "FAIL"
    return out


def main():
    family, config = load_locked_setup()
    spec = p4e.FAMILIES[family]
    symbols = [spec["signal"], spec["bull"], spec["bear"]]

    print(f"Phase 4G locked family: {family} | config: {config}")
    print("Fetching locked-family 5-minute data...")
    raw = {symbol: p4e.fetch(symbol) for symbol in symbols}
    days = p4e.make_day_records(p4e.prepare_family(raw, spec))

    results = {direction: evaluate_direction(days, config, direction) for direction in DIRECTIONS}
    passing = [direction for direction in DIRECTIONS if results[direction]["gate"] == "PASS"]

    # Direction split research passes only if at least one independently robust side exists.
    overall_gate = "PASS" if passing else "FAIL"
    preferred = None
    if passing:
        preferred = max(
            passing,
            key=lambda d: results[d]["holdout_2026"]["10bps"]["expectancy_bps"],
        )

    output = {
        "phase": "4G",
        "strategy": "Phase 4E direction decomposition",
        "starting_equity": p4e.START_EQ,
        "phase4e_family_locked": family,
        "phase4e_config_locked": config,
        "selection_policy": "No parameter search. Bull-only and bear-only are evaluated independently using the locked Phase 4E setup.",
        "directions": results,
        "passing_directions": passing,
        "preferred_direction": preferred,
        "gate": overall_gate,
        "research_only": True,
        "note": "A PASS does not guarantee future profit and requires a separate paper-trading gate before live use.",
    }

    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2)

    summary = f"""# MarketPulse Phase 4G — Direction Split\n\n**Gate: {overall_gate}**\n\n## Objective\nDetermine whether the locked Phase 4E edge is direction-dependent by evaluating bull-only and bear-only trades as separate strategies. No parameter search or regime filter is used.\n\n## Locked Phase 4E setup\n- Family: **{family}** ({spec['signal']} → {spec['bull']}/{spec['bear']})\n- Impulse: **{config['impulse_min']} min / {config['impulse_bps']} bps**\n- Stop / target: **{config['stop_bps']} / {config['target_bps']} bps**\n- Entry, exit, sizing and friction model: **unchanged from Phase 4E**\n\n"""

    for direction in DIRECTIONS:
        r = results[direction]
        summary += f"## {direction.upper()} ONLY — {r['gate']}\n"
        summary += f"- Development 2 bps: {fmt(r['development']['2bps'])}\n"
        summary += f"- Development 10 bps: {fmt(r['development']['10bps'])}\n"
        summary += f"- 2024 10 bps: {fmt(r['2024']['10bps'])}\n"
        summary += f"- 2025 10 bps: {fmt(r['2025']['10bps'])}\n"
        summary += f"- 2024–2025 combined 10 bps: {fmt(r['validation_2024_2025']['10bps'])}\n"
        summary += f"- 2026 2 bps: {fmt(r['holdout_2026']['2bps'])}\n"
        summary += f"- 2026 5 bps: {fmt(r['holdout_2026']['5bps'])}\n"
        summary += f"- 2026 10 bps: {fmt(r['holdout_2026']['10bps'])}\n"
        summary += "- Gate checks:\n"
        for key, ok in r["gate_checks"].items():
            summary += f"  - {'PASS' if ok else 'FAIL'} — {key}\n"
        summary += "\n"

    summary += "## Conclusion\n"
    if passing:
        summary += f"- Passing direction(s): **{', '.join(d.upper() for d in passing)}**\n"
        summary += f"- Preferred direction by 2026 10-bps expectancy: **{preferred.upper()}**\n"
    else:
        summary += "- Neither direction independently passed the full robustness gate.\n"
    summary += "\n## Research status\nResearch only. A PASS does not guarantee future profits and does not authorize live trading.\n"

    with open(OUT_MD, "w") as f:
        f.write(summary)

    print(summary)


if __name__ == "__main__":
    main()
