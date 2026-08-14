import json
from itertools import product

import numpy as np
import pandas as pd

import phase4e_trend_day_backtest as p4e

BASE_BPS = 2.0
STRESS_BPS = 10.0


def load_phase4e_selection():
    with open("phase4e_results.json", "r") as f:
        result = json.load(f)
    selected = result["selected"]
    return selected["family"], selected["config"], result


def enrich_regime_features(days):
    rows = []
    for rec in days:
        op = float(rec["sig_open"][0])
        hi = float(np.nanmax(rec["sig_high"]))
        lo = float(np.nanmin(rec["sig_low"]))
        cl = float(rec["sig_close"][-1])
        rows.append({
            "date": rec["date"],
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "range_pct": (hi - lo) / op if op > 0 else np.nan,
        })

    d = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    d["ret"] = d["close"].pct_change()
    prev_close = d["close"].shift(1)
    close_10 = d["close"].shift(11)
    d["trend_strength_bps"] = ((prev_close / close_10 - 1.0).abs() * 10000.0)

    abs_path = d["ret"].abs().shift(1).rolling(10, min_periods=10).sum()
    net_move = (prev_close / close_10 - 1.0).abs()
    d["efficiency10"] = (net_move / abs_path.replace(0, np.nan)).clip(0, 1)

    d["vol20"] = d["ret"].shift(1).rolling(20, min_periods=20).std()
    d["vol20_median60"] = d["vol20"].rolling(60, min_periods=20).median()
    d["vol_ratio"] = d["vol20"] / d["vol20_median60"].replace(0, np.nan)

    prior5_range = d["range_pct"].shift(1).rolling(5, min_periods=5).median()
    prior20_range = d["range_pct"].shift(1).rolling(20, min_periods=10).median()
    d["range_regime"] = prior5_range / prior20_range.replace(0, np.nan)

    ema20 = d["close"].shift(1).ewm(span=20, adjust=False).mean()
    d["ema_distance_bps"] = ((prev_close / ema20 - 1.0).abs() * 10000.0)

    feature_map = d.set_index("date")[[
        "efficiency10", "trend_strength_bps", "vol_ratio", "range_regime", "ema_distance_bps"
    ]].to_dict("index")
    for rec in days:
        rec["phase4f_regime"] = feature_map.get(rec["date"], {})
    return d


def rule_name(rule):
    parts = []
    if rule["min_eff"] > 0:
        parts.append(f"eff>={rule['min_eff']:.2f}")
    if rule["min_trend_bps"] > 0:
        parts.append(f"trend>={rule['min_trend_bps']:.0f}bps")
    if rule["vol_low"] > 0:
        parts.append(f"vol>={rule['vol_low']:.2f}x")
    if rule["vol_high"] < 99:
        parts.append(f"vol<={rule['vol_high']:.2f}x")
    return "baseline" if not parts else " + ".join(parts)


def active_rules(rule):
    n = 0
    n += int(rule["min_eff"] > 0)
    n += int(rule["min_trend_bps"] > 0)
    n += int(rule["vol_low"] > 0 or rule["vol_high"] < 99)
    return n


def passes(rec, rule):
    if active_rules(rule) == 0:
        return True
    f = rec.get("phase4f_regime", {})
    if rule["min_eff"] > 0:
        v = f.get("efficiency10", np.nan)
        if not np.isfinite(v) or v < rule["min_eff"]:
            return False
    if rule["min_trend_bps"] > 0:
        v = f.get("trend_strength_bps", np.nan)
        if not np.isfinite(v) or v < rule["min_trend_bps"]:
            return False
    if rule["vol_low"] > 0 or rule["vol_high"] < 99:
        v = f.get("vol_ratio", np.nan)
        if not np.isfinite(v) or v < rule["vol_low"] or v > rule["vol_high"]:
            return False
    return True


def filtered_days(days, rule):
    return [rec for rec in days if passes(rec, rule)]


def candidate_rules():
    baseline = {"min_eff": 0.0, "min_trend_bps": 0.0, "vol_low": 0.0, "vol_high": 99.0}
    rules = [baseline]

    effs = [0.20, 0.35, 0.50]
    trends = [50.0, 100.0, 150.0]
    vol_bands = [
        (0.0, 1.50),
        (0.0, 1.30),
        (0.75, 1.75),
        (0.85, 1.50),
        (0.90, 1.35),
    ]

    for eff in effs:
        rules.append({"min_eff": eff, "min_trend_bps": 0.0, "vol_low": 0.0, "vol_high": 99.0})
    for trend in trends:
        rules.append({"min_eff": 0.0, "min_trend_bps": trend, "vol_low": 0.0, "vol_high": 99.0})
    for lo, hi in vol_bands:
        rules.append({"min_eff": 0.0, "min_trend_bps": 0.0, "vol_low": lo, "vol_high": hi})

    for eff, trend in product(effs, trends):
        rules.append({"min_eff": eff, "min_trend_bps": trend, "vol_low": 0.0, "vol_high": 99.0})
    for eff, (lo, hi) in product(effs, vol_bands):
        rules.append({"min_eff": eff, "min_trend_bps": 0.0, "vol_low": lo, "vol_high": hi})
    for trend, (lo, hi) in product(trends, vol_bands):
        rules.append({"min_eff": 0.0, "min_trend_bps": trend, "vol_low": lo, "vol_high": hi})

    unique = []
    seen = set()
    for r in rules:
        key = tuple(sorted(r.items()))
        if key not in seen and active_rules(r) <= 2:
            seen.add(key)
            unique.append(r)
    return unique


def dev_valid(base, stress10, baseline10):
    retention = stress10["trades"] / max(baseline10["trades"], 1)
    return (
        stress10["trades"] >= 32
        and retention >= 0.48
        and base["total_return_pct"] > 0
        and stress10["total_return_pct"] > 0
        and stress10["expectancy_bps"] >= max(12.0, baseline10["expectancy_bps"] + 3.0)
        and stress10["profit_factor"] >= 1.15
        and stress10["max_drawdown_pct"] <= 5.0
        and stress10["positive_month_rate_pct"] >= 55.0
    )


def score_candidate(stress10, baseline10):
    retention = min(stress10["trades"] / max(baseline10["trades"], 1), 1.0)
    dd = max(stress10["max_drawdown_pct"], 0.25)
    return (
        stress10["expectancy_bps"] * retention
        + 0.45 * stress10["total_return_pct"]
        + 0.08 * stress10["positive_month_rate_pct"]
        + 0.15 * stress10["total_return_pct"] / dd
    )


def attach_features(trades, days):
    fmap = {str(rec["date"]): rec.get("phase4f_regime", {}) for rec in days}
    out = []
    for t in trades:
        x = dict(t)
        x["regime"] = fmap.get(t["date"], {})
        out.append(x)
    return out


def trade_feature_summary(trades):
    if not trades:
        return {"trades": 0}
    rows = []
    for t in trades:
        f = t.get("regime", {})
        rows.append({
            "win": t["net_pnl"] > 0,
            "efficiency10": f.get("efficiency10", np.nan),
            "trend_strength_bps": f.get("trend_strength_bps", np.nan),
            "vol_ratio": f.get("vol_ratio", np.nan),
            "range_regime": f.get("range_regime", np.nan),
            "ema_distance_bps": f.get("ema_distance_bps", np.nan),
            "net_bps": t["net_bps"],
        })
    df = pd.DataFrame(rows)
    result = {"trades": len(df), "wins": int(df["win"].sum()), "losses": int((~df["win"]).sum())}
    for col in ["efficiency10", "trend_strength_bps", "vol_ratio", "range_regime", "ema_distance_bps"]:
        vals = df[col].replace([np.inf, -np.inf], np.nan).dropna()
        result[col] = {
            "all_mean": float(vals.mean()) if len(vals) else None,
            "winner_mean": float(df.loc[df["win"], col].dropna().mean()) if df.loc[df["win"], col].notna().any() else None,
            "loser_mean": float(df.loc[~df["win"], col].dropna().mean()) if df.loc[~df["win"], col].notna().any() else None,
        }
    return result


def gate(dev_ok, y24_10, y25_10, val2, val10, y26_2, y26_10):
    checks = {
        "development_filter_valid": bool(dev_ok),
        "2024_10bps_min_trades": y24_10["trades"] >= 5,
        "2024_10bps_profitable": y24_10["total_return_pct"] > 0 and y24_10["profit_factor"] > 1.0,
        "2025_10bps_min_trades": y25_10["trades"] >= 5,
        "2025_10bps_profitable": y25_10["total_return_pct"] > 0 and y25_10["profit_factor"] > 1.0,
        "validation_min_trades": val10["trades"] >= 18,
        "validation_base_profitable": val2["total_return_pct"] > 0 and val2["profit_factor"] > 1.05,
        "validation_10bps_profitable": val10["total_return_pct"] > 0 and val10["expectancy_bps"] > 0 and val10["profit_factor"] > 1.05,
        "validation_10bps_drawdown": val10["max_drawdown_pct"] <= 6.0,
        "2026_min_trades": y26_10["trades"] >= 5,
        "2026_base_profitable": y26_2["total_return_pct"] > 0 and y26_2["profit_factor"] > 1.0,
        "2026_10bps_profitable": y26_10["total_return_pct"] > 0 and y26_10["expectancy_bps"] > 0 and y26_10["profit_factor"] > 1.0,
    }
    return checks, all(checks.values())


def fmt(m):
    return (
        f'{m["trades"]} trades | return {m["total_return_pct"]:+.4f}% | '
        f'expectancy {m["expectancy_bps"]:+.2f} bps/trade | PF {m["profit_factor"]:.3f} | '
        f'max DD {m["max_drawdown_pct"]:.3f}% | positive months {m["positive_month_rate_pct"]:.2f}%'
    )


def main():
    family, config, phase4e = load_phase4e_selection()
    spec = p4e.FAMILIES[family]
    syms = sorted({spec["signal"], spec["bull"], spec["bear"]})

    print("Fetching Phase 4F market data...")
    raw = {sym: p4e.fetch(sym) for sym in syms}
    days = p4e.make_day_records(p4e.prepare_family(raw, spec))
    enrich_regime_features(days)

    baseline_dev2 = p4e.simulate(days, p4e.DEV_START, p4e.DEV_END, config, BASE_BPS)
    baseline_dev10 = p4e.simulate(days, p4e.DEV_START, p4e.DEV_END, config, STRESS_BPS)

    candidates = []
    for rule in candidate_rules():
        fd = filtered_days(days, rule)
        dev2 = p4e.simulate(fd, p4e.DEV_START, p4e.DEV_END, config, BASE_BPS)
        dev10 = p4e.simulate(fd, p4e.DEV_START, p4e.DEV_END, config, STRESS_BPS)
        ok = dev_valid(dev2, dev10, baseline_dev10)
        candidates.append({
            "rule": rule,
            "name": rule_name(rule),
            "active_rules": active_rules(rule),
            "development_2bps": dev2,
            "development_10bps": dev10,
            "dev_valid": ok,
            "score": score_candidate(dev10, baseline_dev10),
        })

    valid = [c for c in candidates if c["dev_valid"] and c["active_rules"] > 0]
    pool = valid if valid else [c for c in candidates if c["active_rules"] > 0]
    selected = max(pool, key=lambda c: c["score"])
    rule = selected["rule"]
    fd = filtered_days(days, rule)

    y24_10 = p4e.simulate(fd, p4e.Y24_START, p4e.Y24_END, config, STRESS_BPS)
    y25_10 = p4e.simulate(fd, p4e.Y25_START, p4e.Y25_END, config, STRESS_BPS)
    val2 = p4e.simulate(fd, p4e.V_START, p4e.V_END, config, BASE_BPS)
    val5 = p4e.simulate(fd, p4e.V_START, p4e.V_END, config, 5.0)
    val10, val_trades = p4e.simulate(fd, p4e.V_START, p4e.V_END, config, STRESS_BPS, True)
    y26_2 = p4e.simulate(fd, p4e.Y26_START, p4e.Y26_END, config, BASE_BPS)
    y26_5 = p4e.simulate(fd, p4e.Y26_START, p4e.Y26_END, config, 5.0)
    y26_10, y26_trades = p4e.simulate(fd, p4e.Y26_START, p4e.Y26_END, config, STRESS_BPS, True)

    baseline_val10, baseline_val_trades = p4e.simulate(days, p4e.V_START, p4e.V_END, config, STRESS_BPS, True)
    baseline_26_10, baseline_26_trades = p4e.simulate(days, p4e.Y26_START, p4e.Y26_END, config, STRESS_BPS, True)

    baseline_val_trades = attach_features(baseline_val_trades, days)
    baseline_26_trades = attach_features(baseline_26_trades, days)
    val_trades = attach_features(val_trades, days)
    y26_trades = attach_features(y26_trades, days)

    checks, passed = gate(selected["dev_valid"], y24_10, y25_10, val2, val10, y26_2, y26_10)

    diagnostics = {
        "baseline_validation_10bps": trade_feature_summary(baseline_val_trades),
        "baseline_2026_10bps": trade_feature_summary(baseline_26_trades),
        "filtered_validation_10bps": trade_feature_summary(val_trades),
        "filtered_2026_10bps": trade_feature_summary(y26_trades),
        "baseline_validation_metrics_10bps": baseline_val10,
        "baseline_2026_metrics_10bps": baseline_26_10,
    }

    result = {
        "phase": "4F",
        "strategy": "Phase 4E trend-day continuation plus pre-trade regime filter",
        "starting_equity": p4e.START_EQ,
        "phase4e_family_locked": family,
        "phase4e_config_locked": config,
        "selection_policy": "Regime filter selected on 2021-2023 only. 2024-2026 are untouched evaluation periods.",
        "candidate_filter_count": len(candidates),
        "valid_filter_count": len(valid),
        "baseline_development_2bps": baseline_dev2,
        "baseline_development_10bps": baseline_dev10,
        "selected_filter": selected,
        "validation_2024_10bps": y24_10,
        "validation_2025_10bps": y25_10,
        "validation_2024_2025_2bps": val2,
        "validation_2024_2025_5bps": val5,
        "validation_2024_2025_10bps": val10,
        "holdout_2026_2bps": y26_2,
        "holdout_2026_5bps": y26_5,
        "holdout_2026_10bps": y26_10,
        "validation_10bps_trades": val_trades,
        "holdout_2026_10bps_trades": y26_trades,
        "diagnostics": diagnostics,
        "gate_checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "research_only": True,
        "note": "A PASS is not a guarantee of future profit and still requires a separate paper-trading gate before live use.",
    }

    with open("phase4f_results.json", "w") as f:
        json.dump(result, f, indent=2)

    fail_reasons = [k for k, v in checks.items() if not v]
    summary = f"""# MarketPulse Phase 4F — Regime Filter\n\n**Gate: {result['gate']}**\n\n## Objective\nKeep the Phase 4E trade logic completely unchanged and test whether a simple pre-trade market-regime filter can preserve the 2024–2025 friction-resistant edge while repairing the 2026 holdout failure. Filter selection uses 2021–2023 only.\n\n## Locked Phase 4E setup\n- Family: **{family}** ({spec['signal']} → {spec['bull']}/{spec['bear']})\n- Impulse: **{config['impulse_min']} min / {config['impulse_bps']} bps**\n- Stop / target: **{config['stop_bps']} / {config['target_bps']} bps**\n- Entry, exit, sizing and friction model: **unchanged from Phase 4E**\n\n## Selected regime filter\n- Rule: **{selected['name']}**\n- Active constraints: **{selected['active_rules']}**\n- Valid development filters: **{len(valid)} / {len(candidates)-1} non-baseline candidates**\n\n## Development 2021–2023\n- Phase 4E baseline at 10 bps: {fmt(baseline_dev10)}\n- Phase 4F filtered at 2 bps: {fmt(selected['development_2bps'])}\n- Phase 4F filtered at 10 bps: {fmt(selected['development_10bps'])}\n\n## 2024–2025 untouched validation\n- 2024 at 10 bps: {fmt(y24_10)}\n- 2025 at 10 bps: {fmt(y25_10)}\n- Combined 2 bps: {fmt(val2)}\n- Combined 5 bps: {fmt(val5)}\n- Combined 10 bps: {fmt(val10)}\n- Phase 4E baseline combined 10 bps: {fmt(baseline_val10)}\n\n## 2026 untouched holdout (Jan–Jul)\n- 2 bps: {fmt(y26_2)}\n- 5 bps: {fmt(y26_5)}\n- 10 bps: {fmt(y26_10)}\n- Phase 4E baseline 10 bps: {fmt(baseline_26_10)}\n\n## Gate checks\n"""
    for k, v in checks.items():
        summary += f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary += "\n## Failure reasons\n"
    summary += "- None\n" if not fail_reasons else "".join(f"- {x}\n" for x in fail_reasons)
    summary += "\n## Research status\nResearch only. A PASS does not guarantee future profits and does not authorize live trading.\n"

    with open("phase4f_summary.md", "w") as f:
        f.write(summary)

    print(summary)


if __name__ == "__main__":
    main()
