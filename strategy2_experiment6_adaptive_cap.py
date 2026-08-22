# Trigger marker: workflow installed; no research parameter change.
import json
import numpy as np
import pandas as pd

import phase5h_sector_neutral_ensemble as base

CONTROL_CAP = 0.50
BASE_CAP = 0.575
OFFENSIVE_CAP = 0.60
DEFENSIVE_CAP = 0.50
TARGET_VOL = 0.08
OFFENSIVE_MIN_BREADTH = 5
OFFENSIVE_MAX_PORTFOLIO_VOL = 0.15
DEFENSIVE_MAX_BREADTH = 3
DEFENSIVE_MIN_PORTFOLIO_VOL = 0.20
CFG = {"lookback": 252, "skip": 0, "top_n": 3}
NEUTRAL_WEIGHT = 0.15
COST_BPS = 10.0
START_EQ = 2500.0

BLOCKS = {
    "development_2008_2014": (base.DEV_START, base.DEV_END),
    "validation_2015_2019": (base.VAL_START, base.VAL_END),
    "holdout1_2020_2023": (base.H1_START, base.H1_END),
    "holdout2_2024_2026": (base.H2_START, base.H2_END),
}


def adaptive_trend_target(c, i, regime_counts=None):
    need = max(base.LOOKBACK, base.TREND_SMA, base.VOL_WINDOW + 1, base.SPY_GATE_SMA)
    if i < need:
        return {base.DEFENSIVE: 1.0}

    eligible = []
    for s in base.TREND_ASSETS:
        px = float(c.iloc[i][s])
        old = float(c.iloc[i - base.LOOKBACK][s])
        sma = float(c[s].iloc[i - base.TREND_SMA + 1:i + 1].mean())
        mom = px / old - 1 if old > 0 else np.nan
        if np.all(np.isfinite([px, old, sma, mom])) and px > sma and mom > 0:
            eligible.append(s)

    breadth = len(eligible)
    if breadth < base.BREADTH_MIN:
        return {base.DEFENSIVE: 1.0}

    spy = float(c.iloc[i]["SPY"])
    spy_old = float(c.iloc[i - base.LOOKBACK]["SPY"])
    spy_sma = float(c["SPY"].iloc[i - base.SPY_GATE_SMA + 1:i + 1].mean())
    if not (spy > spy_sma and spy > spy_old):
        return {base.DEFENSIVE: 1.0}

    r = c[eligible].pct_change().iloc[i - base.VOL_WINDOW + 1:i + 1].dropna()
    vols = (r.std(ddof=1) * np.sqrt(252)).replace([np.inf, -np.inf], np.nan).dropna()
    vols = vols[vols > 0]
    eligible = [s for s in eligible if s in vols.index]
    if not eligible:
        return {base.DEFENSIVE: 1.0}

    inv = 1 / vols[eligible]
    raw = inv / inv.sum()
    cov = r[eligible].cov().to_numpy() * 252
    wv = raw.to_numpy(float)
    portfolio_vol = float(np.sqrt(max(float(wv @ cov @ wv), 0)))
    if not np.isfinite(portfolio_vol) or portfolio_vol <= 0:
        return {base.DEFENSIVE: 1.0}

    if breadth >= OFFENSIVE_MIN_BREADTH and portfolio_vol <= OFFENSIVE_MAX_PORTFOLIO_VOL:
        cap = OFFENSIVE_CAP
        regime = "offensive_60"
    elif breadth <= DEFENSIVE_MAX_BREADTH or portfolio_vol >= DEFENSIVE_MIN_PORTFOLIO_VOL:
        cap = DEFENSIVE_CAP
        regime = "defensive_50"
    else:
        cap = BASE_CAP
        regime = "normal_57_5"

    if regime_counts is not None:
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

    scale = max(0.0, min(cap, TARGET_VOL / portfolio_vol))
    out = {s: float(raw[s] * scale) for s in eligible}
    out[base.DEFENSIVE] = 1.0 - scale
    return out


def adaptive_trend_curve(o, c, start, end, bps):
    dates = list(c.index)
    eq = 1.0
    w = {base.DEFENSIVE: 1.0}
    curve = []
    regime_counts = {"defensive_50": 0, "normal_57_5": 0, "offensive_60": 0}

    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break
        prev, op, cl = c.iloc[i - 1], o.iloc[i], c.iloc[i]
        ov = {s: float(op[s] / prev[s] - 1) for s in w}
        eq *= 1 + sum(w[s] * ov[s] for s in w)
        w = base.drift(w, ov)
        if base.monthly(dates, i):
            tw = adaptive_trend_target(c, i - 1, regime_counts)
            t = base.turnover(w, tw)
            eq -= eq * (bps / 10000.0) * t
            w = tw
        intra = {s: float(cl[s] / op[s] - 1) for s in w}
        eq *= 1 + sum(w[s] * intra[s] for s in w)
        w = base.drift(w, intra)
        curve.append((d, eq))

    series = pd.Series([e for _, e in curve], index=pd.to_datetime([d for d, _ in curve]), dtype=float)
    return series, regime_counts


def summarize_curve(curve):
    stats = base.summarize(curve)
    d = curve.diff().dropna()
    stats.update({
        "avg_daily_pnl_dollars": float(d.mean()) if len(d) else 0.0,
        "median_daily_pnl_dollars": float(d.median()) if len(d) else 0.0,
        "positive_day_rate_pct": float((d > 0).mean() * 100.0) if len(d) else 0.0,
        "worst_day_pnl_dollars": float(d.min()) if len(d) else 0.0,
        "best_day_pnl_dollars": float(d.max()) if len(d) else 0.0,
        "trading_days": int(len(curve)),
    })
    return stats


def fixed_eval(o, c, start, end, cap):
    original_cap = base.RISK_CAP
    original_target = base.TARGET_VOL
    base.RISK_CAP = cap
    base.TARGET_VOL = TARGET_VOL
    try:
        trend = base.trend_curve(o, c, start, end, COST_BPS)
        neutral = base.neutral_curve(o, c, start, end, CFG, COST_BPS)
        return summarize_curve(base.combine(trend, neutral, NEUTRAL_WEIGHT))
    finally:
        base.RISK_CAP = original_cap
        base.TARGET_VOL = original_target


def adaptive_eval(o, c, start, end):
    trend, counts = adaptive_trend_curve(o, c, start, end, COST_BPS)
    neutral = base.neutral_curve(o, c, start, end, CFG, COST_BPS)
    stats = summarize_curve(base.combine(trend, neutral, NEUTRAL_WEIGHT))
    stats["monthly_regime_counts"] = counts
    stats["monthly_regime_total"] = int(sum(counts.values()))
    return stats


def evaluate_fixed(o, c, cap):
    return {name: fixed_eval(o, c, start, end, cap) for name, (start, end) in BLOCKS.items()}


def evaluate_adaptive(o, c):
    return {name: adaptive_eval(o, c, start, end) for name, (start, end) in BLOCKS.items()}


def main():
    o, c = base.download_panel()
    control50 = evaluate_fixed(o, c, CONTROL_CAP)
    base575 = evaluate_fixed(o, c, BASE_CAP)
    adaptive = evaluate_adaptive(o, c)

    h1c = control50["holdout1_2020_2023"]
    h1b = base575["holdout1_2020_2023"]
    h1x = adaptive["holdout1_2020_2023"]
    h2c = control50["holdout2_2024_2026"]
    h2b = base575["holdout2_2024_2026"]
    h2x = adaptive["holdout2_2024_2026"]
    valb = base575["validation_2015_2019"]
    valx = adaptive["validation_2015_2019"]

    checks = {
        "recent_holdout_return_beats_57_5_base": h2x["total_return_pct"] > h2b["total_return_pct"],
        "recent_holdout_avg_daily_pnl_beats_57_5_base": h2x["avg_daily_pnl_dollars"] > h2b["avg_daily_pnl_dollars"],
        "recent_holdout_drawdown_within_original_control_plus_1pp": h2x["max_drawdown_pct"] <= h2c["max_drawdown_pct"] + 1.0,
        "prior_holdout_return_not_worse_than_57_5_by_more_than_0_5pp": h1x["total_return_pct"] >= h1b["total_return_pct"] - 0.5,
        "prior_holdout_drawdown_within_original_control_plus_1pp": h1x["max_drawdown_pct"] <= h1c["max_drawdown_pct"] + 1.0,
        "validation_return_not_worse_than_57_5_by_more_than_0_5pp": valx["total_return_pct"] >= valb["total_return_pct"] - 0.5,
        "validation_drawdown_at_most_8pct": valx["max_drawdown_pct"] <= 8.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": "S2-E6-ADAPTIVE-RISK-CAP",
        "research_only": True,
        "single_changed_mechanism": "adaptive RISK_CAP regime",
        "target_vol": TARGET_VOL,
        "base_cap": BASE_CAP,
        "adaptive_rule": {
            "defensive_cap": DEFENSIVE_CAP,
            "normal_cap": BASE_CAP,
            "offensive_cap": OFFENSIVE_CAP,
            "offensive_if": f"breadth >= {OFFENSIVE_MIN_BREADTH} and estimated trend portfolio vol <= {OFFENSIVE_MAX_PORTFOLIO_VOL:.0%}",
            "defensive_if": f"breadth <= {DEFENSIVE_MAX_BREADTH} or estimated trend portfolio vol >= {DEFENSIVE_MIN_PORTFOLIO_VOL:.0%}",
            "otherwise": "normal cap",
            "signal_timing": "prior close, monthly rebalance; no lookahead",
        },
        "transaction_cost_stress_bps": COST_BPS,
        "control_50": control50,
        "base_57_5": base575,
        "adaptive": adaptive,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate_adaptive_cap": passed,
    }

    with open("strategy2_experiment6_adaptive_cap_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Strategy 2 — Experiment 6: Adaptive Risk Cap",
        "",
        f"**Gate: {result['gate']}**",
        "",
        "Locked rule before results:",
        f"- Defensive cap: **{DEFENSIVE_CAP:.1%}** when breadth <= {DEFENSIVE_MAX_BREADTH} or estimated portfolio vol >= {DEFENSIVE_MIN_PORTFOLIO_VOL:.0%}",
        f"- Normal cap: **{BASE_CAP:.1%}**",
        f"- Offensive cap: **{OFFENSIVE_CAP:.1%}** when breadth >= {OFFENSIVE_MIN_BREADTH} and estimated portfolio vol <= {OFFENSIVE_MAX_PORTFOLIO_VOL:.0%}",
        f"- Target volatility remains **{TARGET_VOL:.1%}**",
        "- Signals use prior-close data; rebalance remains monthly; 10 bps transaction-cost stress.",
        "",
    ]
    for block in BLOCKS:
        c0, b, x = control50[block], base575[block], adaptive[block]
        lines += [
            f"## {block}",
            f"- Control 50%: return {c0['total_return_pct']:+.3f}% | CAGR {c0['cagr_pct']:+.3f}% | DD {c0['max_drawdown_pct']:.3f}% | avg day ${c0['avg_daily_pnl_dollars']:+.3f}",
            f"- Base 57.5%: return {b['total_return_pct']:+.3f}% | CAGR {b['cagr_pct']:+.3f}% | DD {b['max_drawdown_pct']:.3f}% | avg day ${b['avg_daily_pnl_dollars']:+.3f}",
            f"- Adaptive: return {x['total_return_pct']:+.3f}% | CAGR {x['cagr_pct']:+.3f}% | DD {x['max_drawdown_pct']:.3f}% | avg day ${x['avg_daily_pnl_dollars']:+.3f}",
            f"- Adaptive best/worst day: ${x['best_day_pnl_dollars']:+.2f} / ${x['worst_day_pnl_dollars']:+.2f}",
            f"- Monthly regimes: {x['monthly_regime_counts']}",
            "",
        ]
    lines.append("## Predeclared checks")
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", f"**Decision: {'PASS FOR FURTHER VALIDATION' if passed else 'REJECT ADAPTIVE CAP CANDIDATE'}**", ""]

    with open("strategy2_experiment6_adaptive_cap_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
