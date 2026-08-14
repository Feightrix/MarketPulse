import json
from itertools import product

import numpy as np
import pandas as pd
import yfinance as yf

START_EQ = 2500.0
BASE_BPS = 2.0
STRESS_BPS = 10.0
START = "2007-01-01"
END = "2026-08-01"

RISKY = ["SPY", "QQQ", "IWM", "XLE", "XLP", "XLU", "GLD", "TLT"]
DEFENSIVE = "BIL"
SYMS = RISKY + [DEFENSIVE]

LOOKBACKS = [63, 126, 252]
SMAS = [100, 150, 200]
TOP_NS = [1, 2, 3]
FREQS = ["weekly", "monthly"]

DEV_START = pd.Timestamp("2008-01-01").date()
DEV_END = pd.Timestamp("2014-12-31").date()
VAL_START = pd.Timestamp("2015-01-01").date()
VAL_END = pd.Timestamp("2019-12-31").date()
HOLD1_START = pd.Timestamp("2020-01-01").date()
HOLD1_END = pd.Timestamp("2023-12-31").date()
HOLD2_START = pd.Timestamp("2024-01-01").date()
HOLD2_END = pd.Timestamp("2026-07-31").date()


def download_panel():
    data = yf.download(
        SYMS,
        start=START,
        end=END,
        auto_adjust=True,
        actions=False,
        progress=False,
        group_by="column",
        threads=False,
    )
    if data.empty:
        raise RuntimeError("Historical data download returned no rows")

    opens = data["Open"].copy()
    closes = data["Close"].copy()
    opens.index = pd.to_datetime(opens.index).date
    closes.index = pd.to_datetime(closes.index).date

    coverage = {}
    for sym in SYMS:
        valid = closes[sym].dropna()
        if valid.empty:
            raise RuntimeError(f"No historical data for {sym}")
        coverage[sym] = {
            "start": str(valid.index.min()),
            "end": str(valid.index.max()),
            "bars": int(len(valid)),
        }

    common = closes.dropna().index.intersection(opens.dropna().index)
    common = sorted(common)
    if not common:
        raise RuntimeError("No common history across tournament universe")
    if pd.Timestamp(common[0]).date() > pd.Timestamp("2007-06-30").date():
        raise RuntimeError(f"Insufficient long history: starts {common[0]}; coverage={coverage}")

    return opens.loc[common].astype(float), closes.loc[common].astype(float), coverage


def rebalance_day(dates, i, freq):
    if i <= 0:
        return True
    now = pd.Timestamp(dates[i])
    prev = pd.Timestamp(dates[i - 1])
    if freq == "weekly":
        return (now.isocalendar().week != prev.isocalendar().week) or (now.year != prev.year)
    return (now.month != prev.month) or (now.year != prev.year)


def target_weights(closes, i_signal, cfg):
    lb = cfg["lookback"]
    sma_n = cfg["sma"]
    n = cfg["top_n"]
    if i_signal < max(lb, sma_n):
        return {DEFENSIVE: 1.0}

    ranked = []
    for sym in RISKY:
        px = float(closes.iloc[i_signal][sym])
        base = float(closes.iloc[i_signal - lb][sym])
        sma = float(closes[sym].iloc[i_signal - sma_n + 1:i_signal + 1].mean())
        if not np.all(np.isfinite([px, base, sma])) or base <= 0:
            continue
        mom = px / base - 1.0
        if px > sma and mom > 0:
            ranked.append((sym, mom))

    ranked.sort(key=lambda x: x[1], reverse=True)
    chosen = [s for s, _ in ranked[:n]]
    if not chosen:
        return {DEFENSIVE: 1.0}
    w = 1.0 / len(chosen)
    return {s: w for s in chosen}


def normalize(weights):
    total = sum(max(v, 0.0) for v in weights.values())
    if total <= 0:
        return {DEFENSIVE: 1.0}
    return {s: max(v, 0.0) / total for s, v in weights.items() if v > 1e-12}


def drift(weights, rets):
    return normalize({s: w * (1.0 + rets.get(s, 0.0)) for s, w in weights.items()})


def turnover(a, b):
    return sum(abs(b.get(s, 0.0) - a.get(s, 0.0)) for s in set(a) | set(b))


def simulate(opens, closes, start, end, cfg, bps):
    dates = list(closes.index)
    eq = START_EQ
    weights = {DEFENSIVE: 1.0}
    curve = []
    events = 0
    turnover_total = 0.0

    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break

        prev_close = closes.iloc[i - 1]
        op = opens.iloc[i]
        cl = closes.iloc[i]

        overnight = {s: float(op[s] / prev_close[s] - 1.0) for s in weights}
        eq *= 1.0 + sum(weights[s] * overnight[s] for s in weights)
        weights = drift(weights, overnight)

        if rebalance_day(dates, i, cfg["freq"]):
            target = target_weights(closes, i - 1, cfg)
            t = turnover(weights, target)
            if t > 1e-8:
                eq -= eq * (bps / 10000.0) * t
                turnover_total += t
                events += 1
            weights = target

        intraday = {s: float(cl[s] / op[s] - 1.0) for s in weights}
        eq *= 1.0 + sum(weights[s] * intraday[s] for s in weights)
        weights = drift(weights, intraday)
        curve.append((d, eq))

    return summarize(curve, events, turnover_total)


def summarize(curve, events, turnover_total):
    s = pd.Series([e for _, e in curve], index=pd.to_datetime([d for d, _ in curve]), dtype=float)
    if s.empty:
        return {"final_equity": START_EQ, "total_return_pct": 0.0, "cagr_pct": 0.0,
                "max_drawdown_pct": 0.0, "positive_month_rate_pct": 0.0,
                "trade_events": 0, "turnover_sum": 0.0, "annual_returns_pct": {}}

    annual = {}
    for y in sorted(s.index.year.unique()):
        ys = s[s.index.year == y]
        prior = s[s.index < ys.index[0]]
        y0 = float(prior.iloc[-1]) if len(prior) else START_EQ
        annual[str(y)] = float((ys.iloc[-1] / y0 - 1.0) * 100.0)

    total = float((s.iloc[-1] / START_EQ - 1.0) * 100.0)
    elapsed = max((s.index[-1] - s.index[0]).days / 365.25, 1 / 365.25)
    cagr = float(((s.iloc[-1] / START_EQ) ** (1.0 / elapsed) - 1.0) * 100.0)
    max_dd = float((1.0 - s / s.cummax()).max() * 100.0)
    monthly = s.resample("ME").last()
    mr = (monthly / monthly.shift(1) - 1.0).dropna()
    pm = float((mr > 0).mean() * 100.0) if len(mr) else 0.0

    return {
        "final_equity": float(s.iloc[-1]),
        "total_return_pct": total,
        "cagr_pct": cagr,
        "max_drawdown_pct": max_dd,
        "positive_month_rate_pct": pm,
        "trade_events": int(events),
        "turnover_sum": float(turnover_total),
        "annual_returns_pct": annual,
    }


def years_positive(m, years):
    a = m["annual_returns_pct"]
    return all(str(y) in a and a[str(y)] > 0 for y in years)


def development_valid(m):
    return (
        years_positive(m, range(2008, 2015))
        and m["cagr_pct"] >= 4.0
        and m["max_drawdown_pct"] <= 15.0
        and m["positive_month_rate_pct"] >= 52.0
        and m["trade_events"] >= 12
    )


def score(m):
    yr = list(m["annual_returns_pct"].values())
    weakest = min(yr) if yr else -999.0
    median_year = float(np.median(yr)) if yr else -999.0
    return 2.0 * weakest + 0.5 * median_year + m["cagr_pct"] - 0.35 * m["max_drawdown_pct"]


def block_positive(m, years):
    return {str(y): m["annual_returns_pct"].get(str(y), -999.0) > 0 for y in years}


def final_gate(dev, val, hold1, hold2):
    checks = {"development_2008_2014_all_positive": years_positive(dev, range(2008, 2015))}
    for y, ok in block_positive(val, range(2015, 2020)).items():
        checks[f"validation_{y}_positive_10bps"] = ok
    checks["validation_drawdown"] = val["max_drawdown_pct"] <= 15.0
    for y, ok in block_positive(hold1, range(2020, 2024)).items():
        checks[f"holdout1_{y}_positive_10bps"] = ok
    checks["holdout1_drawdown"] = hold1["max_drawdown_pct"] <= 15.0
    for y, ok in block_positive(hold2, range(2024, 2027)).items():
        checks[f"holdout2_{y}_positive_10bps"] = ok
    checks["holdout2_drawdown"] = hold2["max_drawdown_pct"] <= 15.0
    return checks, all(checks.values())


def fmt(m):
    years = ", ".join(f"{y}:{r:+.2f}%" for y, r in m["annual_returns_pct"].items())
    return (f"return {m['total_return_pct']:+.2f}% | CAGR {m['cagr_pct']:+.2f}% | "
            f"DD {m['max_drawdown_pct']:.2f}% | positive months {m['positive_month_rate_pct']:.1f}% | "
            f"rebalance trades {m['trade_events']} | [{years}]")


def main():
    print("Downloading long adjusted daily history for Phase 5B...")
    opens, closes, coverage = download_panel()
    configs = [
        {"lookback": lb, "sma": sma, "top_n": n, "freq": freq}
        for lb, sma, n, freq in product(LOOKBACKS, SMAS, TOP_NS, FREQS)
    ]
    print(f"Testing {len(configs)} pre-defined configurations...")

    candidates = []
    for cfg in configs:
        dev10 = simulate(opens, closes, DEV_START, DEV_END, cfg, STRESS_BPS)
        candidates.append({"config": cfg, "development_10bps": dev10,
                           "dev_valid": development_valid(dev10), "score": score(dev10)})

    valid = [c for c in candidates if c["dev_valid"]]
    selected = max(valid if valid else candidates, key=lambda c: c["score"])
    cfg = selected["config"]

    dev2 = simulate(opens, closes, DEV_START, DEV_END, cfg, BASE_BPS)
    val2 = simulate(opens, closes, VAL_START, VAL_END, cfg, BASE_BPS)
    val10 = simulate(opens, closes, VAL_START, VAL_END, cfg, STRESS_BPS)
    h12 = simulate(opens, closes, HOLD1_START, HOLD1_END, cfg, BASE_BPS)
    h110 = simulate(opens, closes, HOLD1_START, HOLD1_END, cfg, STRESS_BPS)
    h22 = simulate(opens, closes, HOLD2_START, HOLD2_END, cfg, BASE_BPS)
    h210 = simulate(opens, closes, HOLD2_START, HOLD2_END, cfg, STRESS_BPS)

    checks, passed = final_gate(selected["development_10bps"], val10, h110, h210)
    result = {
        "phase": "5B",
        "strategy": "consistency tournament: multi-asset and sector absolute momentum trend rotation",
        "starting_equity": START_EQ,
        "universe": RISKY,
        "defensive_asset": DEFENSIVE,
        "historical_source": "yfinance adjusted daily OHLC",
        "coverage": coverage,
        "candidate_count": len(configs),
        "valid_development_candidates": len(valid),
        "selected_config": cfg,
        "development_2008_2014_2bps": dev2,
        "development_2008_2014_10bps": selected["development_10bps"],
        "validation_2015_2019_2bps": val2,
        "validation_2015_2019_10bps": val10,
        "holdout_2020_2023_2bps": h12,
        "holdout_2020_2023_10bps": h110,
        "final_holdout_2024_2026_2bps": h22,
        "final_holdout_2024_2026_10bps": h210,
        "gate_checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "research_only": True,
        "note": "Historical consistency is not a guarantee. A PASS still requires data cross-check and paper trading before live deployment.",
    }
    with open("phase5b_results.json", "w") as f:
        json.dump(result, f, indent=2)

    failures = [k for k, v in checks.items() if not v]
    summary = f"""# MarketPulse Phase 5B — Consistency Tournament\n\n**Gate: {result['gate']}**\n\n## Standard\nA strategy is rejected if any required calendar year is negative at 10 bps per side. Parameters are chosen only from 2008–2014. Later periods are evaluation blocks.\n\n## Universe\n- Risk/rotation assets: **{', '.join(RISKY)}**\n- Defensive fallback: **{DEFENSIVE}**\n- Starting equity: **${START_EQ:,.0f}**\n- Historical data: adjusted daily OHLC\n\n## Selected configuration\n- Momentum: **{cfg['lookback']} trading days**\n- Trend filter: **{cfg['sma']}-day SMA**\n- Hold top: **{cfg['top_n']}**\n- Rebalance: **{cfg['freq']}**\n\n## Development 2008–2014\n- Valid candidates: **{len(valid)} / {len(configs)}**\n- 2 bps: {fmt(dev2)}\n- 10 bps: {fmt(selected['development_10bps'])}\n\n## Validation 2015–2019\n- 2 bps: {fmt(val2)}\n- 10 bps: {fmt(val10)}\n\n## Holdout 2020–2023\n- 2 bps: {fmt(h12)}\n- 10 bps: {fmt(h110)}\n\n## Final holdout 2024–2026 YTD\n- 2 bps: {fmt(h22)}\n- 10 bps: {fmt(h210)}\n\n## Gate checks\n"""
    for k, v in checks.items():
        summary += f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary += "\n## Failure reasons\n"
    summary += "- None\n" if not failures else "".join(f"- {x}\n" for x in failures)
    summary += "\n## Research status\nResearch only. Even a PASS does not guarantee future profit; it would move only to independent data validation and paper trading.\n"
    with open("phase5b_summary.md", "w") as f:
        f.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
