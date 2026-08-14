import json
from itertools import product

import numpy as np
import pandas as pd
import yfinance as yf

START_EQ = 2500.0
BASE_BPS = 2.0
STRESS_BPS = 10.0
START = "2009-01-01"
END = "2026-08-01"

PAIRS = {
    "SPY": {"long": "SPY", "inverse": "SH"},
    "QQQ": {"long": "QQQ", "inverse": "PSQ"},
    "IWM": {"long": "IWM", "inverse": "RWM"},
    "TLT": {"long": "TLT", "inverse": "TBF"},
}
DEFENSIVE = "BIL"
SIGNALS = list(PAIRS)
EXEC = sorted({x for p in PAIRS.values() for x in p.values()} | {DEFENSIVE})
SYMS = sorted(set(SIGNALS) | set(EXEC))

LOOKBACKS = [63, 126, 252]
SMAS = [100, 150, 200]
TOP_NS = [2, 4]
FREQS = ["weekly", "monthly"]

DEV_START = pd.Timestamp("2010-01-01").date()
DEV_END = pd.Timestamp("2015-12-31").date()
VAL_START = pd.Timestamp("2016-01-01").date()
VAL_END = pd.Timestamp("2019-12-31").date()
HOLD1_START = pd.Timestamp("2020-01-01").date()
HOLD1_END = pd.Timestamp("2023-12-31").date()
HOLD2_START = pd.Timestamp("2024-01-01").date()
HOLD2_END = pd.Timestamp("2026-07-31").date()


def download_panel():
    data = yf.download(SYMS, start=START, end=END, auto_adjust=True, actions=False,
                       progress=False, group_by="column", threads=False)
    if data.empty:
        raise RuntimeError("No historical data")
    opens = data["Open"].copy()
    closes = data["Close"].copy()
    opens.index = pd.to_datetime(opens.index).date
    closes.index = pd.to_datetime(closes.index).date
    coverage = {}
    for sym in SYMS:
        v = closes[sym].dropna()
        if v.empty:
            raise RuntimeError(f"No history for {sym}")
        coverage[sym] = {"start": str(v.index.min()), "end": str(v.index.max()), "bars": int(len(v))}
    common = sorted(closes.dropna().index.intersection(opens.dropna().index))
    if not common or pd.Timestamp(common[0]).date() > pd.Timestamp("2009-12-31").date():
        raise RuntimeError(f"Insufficient common history; coverage={coverage}")
    return opens.loc[common].astype(float), closes.loc[common].astype(float), coverage


def rebalance_day(dates, i, freq):
    if i <= 0:
        return True
    a = pd.Timestamp(dates[i - 1])
    b = pd.Timestamp(dates[i])
    if freq == "weekly":
        return a.isocalendar().week != b.isocalendar().week or a.year != b.year
    return a.month != b.month or a.year != b.year


def target_weights(closes, i_signal, cfg):
    lb, sma_n, top_n = cfg["lookback"], cfg["sma"], cfg["top_n"]
    if i_signal < max(lb, sma_n):
        return {DEFENSIVE: 1.0}
    candidates = []
    for signal, pair in PAIRS.items():
        px = float(closes.iloc[i_signal][signal])
        old = float(closes.iloc[i_signal - lb][signal])
        sma = float(closes[signal].iloc[i_signal - sma_n + 1:i_signal + 1].mean())
        if not np.all(np.isfinite([px, old, sma])) or old <= 0:
            continue
        mom = px / old - 1.0
        if px > sma and mom > 0:
            candidates.append((pair["long"], abs(mom), signal, "long"))
        elif px < sma and mom < 0:
            candidates.append((pair["inverse"], abs(mom), signal, "inverse"))
    candidates.sort(key=lambda x: x[1], reverse=True)
    chosen = candidates[:top_n]
    if not chosen:
        return {DEFENSIVE: 1.0}
    w = 1.0 / len(chosen)
    return {sym: w for sym, _, _, _ in chosen}


def normalize(w):
    total = sum(max(x, 0.0) for x in w.values())
    if total <= 0:
        return {DEFENSIVE: 1.0}
    return {s: max(x, 0.0) / total for s, x in w.items() if x > 1e-12}


def drift(w, rets):
    return normalize({s: x * (1.0 + rets.get(s, 0.0)) for s, x in w.items()})


def turnover(a, b):
    return sum(abs(b.get(s, 0.0) - a.get(s, 0.0)) for s in set(a) | set(b))


def simulate(opens, closes, start, end, cfg, bps):
    dates = list(closes.index)
    eq = START_EQ
    weights = {DEFENSIVE: 1.0}
    curve = []
    events = 0
    turns = 0.0

    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break
        prev = closes.iloc[i - 1]
        op = opens.iloc[i]
        cl = closes.iloc[i]

        overnight = {s: float(op[s] / prev[s] - 1.0) for s in weights}
        eq *= 1.0 + sum(weights[s] * overnight[s] for s in weights)
        weights = drift(weights, overnight)

        if rebalance_day(dates, i, cfg["freq"]):
            target = target_weights(closes, i - 1, cfg)
            t = turnover(weights, target)
            if t > 1e-8:
                eq -= eq * (bps / 10000.0) * t
                events += 1
                turns += t
            weights = target

        intraday = {s: float(cl[s] / op[s] - 1.0) for s in weights}
        eq *= 1.0 + sum(weights[s] * intraday[s] for s in weights)
        weights = drift(weights, intraday)
        curve.append((d, eq))
    return summarize(curve, events, turns)


def summarize(curve, events, turns):
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
    elapsed = max((s.index[-1] - s.index[0]).days / 365.25, 1/365.25)
    cagr = float(((s.iloc[-1] / START_EQ) ** (1.0 / elapsed) - 1.0) * 100.0)
    dd = float((1.0 - s / s.cummax()).max() * 100.0)
    monthly = s.resample("ME").last()
    mr = (monthly / monthly.shift(1) - 1.0).dropna()
    pm = float((mr > 0).mean() * 100.0) if len(mr) else 0.0
    return {"final_equity": float(s.iloc[-1]), "total_return_pct": total, "cagr_pct": cagr,
            "max_drawdown_pct": dd, "positive_month_rate_pct": pm,
            "trade_events": int(events), "turnover_sum": float(turns), "annual_returns_pct": annual}


def years_positive(m, years):
    a = m["annual_returns_pct"]
    return all(str(y) in a and a[str(y)] > 0 for y in years)


def dev_valid(m):
    return (years_positive(m, range(2010, 2016)) and m["cagr_pct"] >= 4.0
            and m["max_drawdown_pct"] <= 15.0 and m["positive_month_rate_pct"] >= 52.0)


def score(m):
    ys = list(m["annual_returns_pct"].values())
    weakest = min(ys) if ys else -999
    return 2.0 * weakest + m["cagr_pct"] - 0.35 * m["max_drawdown_pct"]


def checks_for_block(prefix, m, years):
    return {f"{prefix}_{y}_positive_10bps": m["annual_returns_pct"].get(str(y), -999) > 0 for y in years}


def gate(dev, val, h1, h2):
    checks = {"development_2010_2015_all_positive": years_positive(dev, range(2010, 2016))}
    checks.update(checks_for_block("validation", val, range(2016, 2020)))
    checks["validation_drawdown"] = val["max_drawdown_pct"] <= 15.0
    checks.update(checks_for_block("holdout1", h1, range(2020, 2024)))
    checks["holdout1_drawdown"] = h1["max_drawdown_pct"] <= 15.0
    checks.update(checks_for_block("holdout2", h2, range(2024, 2027)))
    checks["holdout2_drawdown"] = h2["max_drawdown_pct"] <= 15.0
    return checks, all(checks.values())


def fmt(m):
    yrs = ", ".join(f"{y}:{r:+.2f}%" for y, r in m["annual_returns_pct"].items())
    return (f"return {m['total_return_pct']:+.2f}% | CAGR {m['cagr_pct']:+.2f}% | DD {m['max_drawdown_pct']:.2f}% | "
            f"positive months {m['positive_month_rate_pct']:.1f}% | trades {m['trade_events']} | [{yrs}]")


def main():
    print("Downloading Phase 5C history...")
    opens, closes, coverage = download_panel()
    configs = [{"lookback": lb, "sma": sma, "top_n": n, "freq": freq}
               for lb, sma, n, freq in product(LOOKBACKS, SMAS, TOP_NS, FREQS)]
    candidates = []
    for cfg in configs:
        d10 = simulate(opens, closes, DEV_START, DEV_END, cfg, STRESS_BPS)
        candidates.append({"config": cfg, "development_10bps": d10,
                           "dev_valid": dev_valid(d10), "score": score(d10)})
    valid = [c for c in candidates if c["dev_valid"]]
    selected = max(valid if valid else candidates, key=lambda c: c["score"])
    cfg = selected["config"]

    d2 = simulate(opens, closes, DEV_START, DEV_END, cfg, BASE_BPS)
    v2 = simulate(opens, closes, VAL_START, VAL_END, cfg, BASE_BPS)
    v10 = simulate(opens, closes, VAL_START, VAL_END, cfg, STRESS_BPS)
    h12 = simulate(opens, closes, HOLD1_START, HOLD1_END, cfg, BASE_BPS)
    h110 = simulate(opens, closes, HOLD1_START, HOLD1_END, cfg, STRESS_BPS)
    h22 = simulate(opens, closes, HOLD2_START, HOLD2_END, cfg, BASE_BPS)
    h210 = simulate(opens, closes, HOLD2_START, HOLD2_END, cfg, STRESS_BPS)

    checks, passed = gate(selected["development_10bps"], v10, h110, h210)
    result = {"phase": "5C", "strategy": "long/inverse multi-market time-series momentum",
              "starting_equity": START_EQ, "pairs": PAIRS, "defensive_asset": DEFENSIVE,
              "candidate_count": len(configs), "valid_development_candidates": len(valid),
              "selected_config": cfg, "coverage": coverage,
              "development_2bps": d2, "development_10bps": selected["development_10bps"],
              "validation_2bps": v2, "validation_10bps": v10,
              "holdout1_2bps": h12, "holdout1_10bps": h110,
              "holdout2_2bps": h22, "holdout2_10bps": h210,
              "gate_checks": checks, "gate": "PASS" if passed else "FAIL", "research_only": True,
              "note": "Inverse ETFs can have path-dependent decay; PASS would require independent data checks and paper trading."}
    with open("phase5c_results.json", "w") as f:
        json.dump(result, f, indent=2)

    failures = [k for k, v in checks.items() if not v]
    summary = f"""# MarketPulse Phase 5C — Long/Inverse Trend\n\n**Gate: {result['gate']}**\n\n## Strategy\nTrade medium-horizon trends in SPY, QQQ, IWM and TLT. Positive confirmed trends use the ordinary ETF; negative confirmed trends use a corresponding inverse ETF (SH, PSQ, RWM, TBF). BIL is the defensive fallback.\n\n## Selected configuration\n- Lookback: **{cfg['lookback']} days**\n- Trend filter: **{cfg['sma']}-day SMA**\n- Hold strongest: **{cfg['top_n']}** confirmed trends\n- Rebalance: **{cfg['freq']}**\n\n## Development 2010–2015\n- Valid candidates: **{len(valid)} / {len(configs)}**\n- 2 bps: {fmt(d2)}\n- 10 bps: {fmt(selected['development_10bps'])}\n\n## Validation 2016–2019\n- 2 bps: {fmt(v2)}\n- 10 bps: {fmt(v10)}\n\n## Holdout 2020–2023\n- 2 bps: {fmt(h12)}\n- 10 bps: {fmt(h110)}\n\n## Final holdout 2024–2026 YTD\n- 2 bps: {fmt(h22)}\n- 10 bps: {fmt(h210)}\n\n## Gate checks\n"""
    for k, v in checks.items():
        summary += f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary += "\n## Failure reasons\n"
    summary += "- None\n" if not failures else "".join(f"- {x}\n" for x in failures)
    summary += "\n## Research status\nResearch only. A PASS would not guarantee future profit and would move only to independent validation plus paper trading.\n"
    with open("phase5c_summary.md", "w") as f:
        f.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
