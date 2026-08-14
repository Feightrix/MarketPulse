import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf

# Phase 6A: independent-source validation of the FROZEN Phase 5H strategy.
START_EQ = 2500.0
STRESS_BPS = 10.0
SHORT_BORROW_BPS_ANNUAL = 50.0
YF_START = "2020-01-01"
ALPACA_START = "2020-07-20T00:00:00Z"
END = "2026-08-01T00:00:00Z"
EVAL_START = pd.Timestamp("2022-01-01").date()
EVAL_END = pd.Timestamp("2026-07-31").date()

TREND_ASSETS = ["SPY", "QQQ", "IWM", "XLE", "XLP", "XLU", "GLD", "TLT"]
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
DEFENSIVE = "BIL"
SYMS = sorted(set(TREND_ASSETS + SECTORS + [DEFENSIVE]))

# Exact frozen Phase 5H rules.
LOOKBACK = 252
TREND_SMA = 150
VOL_WINDOW = 42
TARGET_VOL = 0.08
RISK_CAP = 0.50
BREADTH_MIN = 2
SPY_GATE_SMA = 200
SECTOR_LOOKBACK = 252
SECTOR_SKIP = 0
TOP_N = 3
NEUTRAL_WEIGHT = 0.15
TREND_WEIGHT = 0.85

# Pre-committed independent-validation gates.
MIN_MONTHLY_SIGNAL_AGREEMENT = 0.85
MIN_RISK_ON_OFF_AGREEMENT = 0.90
MIN_SECTOR_SIDE_AGREEMENT = 0.80
MAX_ANNUAL_RETURN_DIFF_PP = 3.0
MAX_DRAWDOWN_DIFF_PP = 2.0


def alpaca_headers():
    key = os.getenv("ALPACA_API_KEY_ID")
    secret = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch_alpaca_symbol(sym):
    rows = []
    token = None
    while True:
        q = {
            "timeframe": "1Day",
            "start": ALPACA_START,
            "end": END,
            "adjustment": "all",
            "feed": "iex",
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            q["page_token"] = token
        url = f"https://data.alpaca.markets/v2/stocks/{sym}/bars?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers=alpaca_headers())
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
        rows.extend(payload.get("bars", []))
        token = payload.get("next_page_token")
        if not token:
            break
        time.sleep(0.10)
    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError(f"No Alpaca bars returned for {sym}")
    dt = pd.to_datetime(d["t"], utc=True).dt.tz_convert("America/New_York").dt.date
    d = pd.DataFrame({"date": dt, "Open": d["o"].astype(float), "Close": d["c"].astype(float)})
    return d.drop_duplicates("date").set_index("date").sort_index()


def load_alpaca_panel():
    raw = {s: fetch_alpaca_symbol(s) for s in SYMS}
    common = None
    for s in SYMS:
        idx = set(raw[s].index)
        common = idx if common is None else common & idx
    common = sorted(common)
    if not common:
        raise RuntimeError("No common Alpaca history")
    o = pd.DataFrame({s: raw[s].loc[common, "Open"] for s in SYMS}, index=common).astype(float)
    c = pd.DataFrame({s: raw[s].loc[common, "Close"] for s in SYMS}, index=common).astype(float)
    return o, c


def load_yahoo_panel():
    d = yf.download(SYMS, start=YF_START, end="2026-08-01", auto_adjust=True, actions=False,
                    progress=False, group_by="column", threads=False)
    if d.empty:
        raise RuntimeError("No Yahoo history")
    o = d["Open"].copy()
    c = d["Close"].copy()
    o.index = pd.to_datetime(o.index).date
    c.index = pd.to_datetime(c.index).date
    return o.astype(float), c.astype(float)


def align_sources(yo, yc, ao, ac):
    common = sorted(set(yo.dropna().index) & set(yc.dropna().index) & set(ao.dropna().index) & set(ac.dropna().index))
    if len(common) < 500:
        raise RuntimeError(f"Insufficient common cross-source history: {len(common)} bars")
    if common[0] > pd.Timestamp("2020-08-15").date():
        raise RuntimeError(f"Cross-source history begins too late: {common[0]}")
    return yo.loc[common], yc.loc[common], ao.loc[common], ac.loc[common]


def monthly(dates, i):
    if i <= 0:
        return True
    a, b = pd.Timestamp(dates[i - 1]), pd.Timestamp(dates[i])
    return a.month != b.month or a.year != b.year


def norm_long(w):
    total = sum(max(x, 0.0) for x in w.values())
    return {s: max(x, 0.0) / total for s, x in w.items() if x > 1e-12} if total > 0 else {DEFENSIVE: 1.0}


def drift(w, rets):
    return norm_long({s: x * (1.0 + rets.get(s, 0.0)) for s, x in w.items()})


def turnover(a, b):
    return sum(abs(b.get(s, 0.0) - a.get(s, 0.0)) for s in set(a) | set(b))


def trend_target(c, i):
    need = max(LOOKBACK, TREND_SMA, VOL_WINDOW + 1, SPY_GATE_SMA)
    if i < need:
        return {DEFENSIVE: 1.0}
    eligible = []
    for s in TREND_ASSETS:
        px = float(c.iloc[i][s])
        old = float(c.iloc[i - LOOKBACK][s])
        sma = float(c[s].iloc[i - TREND_SMA + 1:i + 1].mean())
        mom = px / old - 1.0 if old > 0 else np.nan
        if np.all(np.isfinite([px, old, sma, mom])) and px > sma and mom > 0:
            eligible.append(s)
    if len(eligible) < BREADTH_MIN:
        return {DEFENSIVE: 1.0}
    spy = float(c.iloc[i]["SPY"])
    spy_old = float(c.iloc[i - LOOKBACK]["SPY"])
    spy_sma = float(c["SPY"].iloc[i - SPY_GATE_SMA + 1:i + 1].mean())
    if not (spy > spy_sma and spy > spy_old):
        return {DEFENSIVE: 1.0}
    r = c[eligible].pct_change().iloc[i - VOL_WINDOW + 1:i + 1].dropna()
    vols = (r.std(ddof=1) * np.sqrt(252)).replace([np.inf, -np.inf], np.nan).dropna()
    vols = vols[vols > 0]
    eligible = [s for s in eligible if s in vols.index]
    if not eligible:
        return {DEFENSIVE: 1.0}
    inv = 1.0 / vols[eligible]
    base = inv / inv.sum()
    cov = r[eligible].cov().to_numpy() * 252.0
    wv = base.to_numpy(float)
    pvol = float(np.sqrt(max(float(wv @ cov @ wv), 0.0)))
    if not np.isfinite(pvol) or pvol <= 0:
        return {DEFENSIVE: 1.0}
    scale = max(0.0, min(RISK_CAP, TARGET_VOL / pvol))
    out = {s: float(base[s] * scale) for s in eligible}
    out[DEFENSIVE] = 1.0 - scale
    return out


def neutral_target(c, i):
    signal_i = i - SECTOR_SKIP
    if signal_i < SECTOR_LOOKBACK:
        return {}
    scores = []
    for s in SECTORS:
        now = float(c.iloc[signal_i][s])
        old = float(c.iloc[signal_i - SECTOR_LOOKBACK][s])
        if np.isfinite(now) and np.isfinite(old) and old > 0:
            scores.append((s, now / old - 1.0))
    scores.sort(key=lambda x: x[1], reverse=True)
    if len(scores) < 2 * TOP_N:
        return {}
    longs = [s for s, _ in scores[:TOP_N]]
    shorts = [s for s, _ in scores[-TOP_N:]]
    out = {s: 0.5 / TOP_N for s in longs}
    for s in shorts:
        out[s] = out.get(s, 0.0) - 0.5 / TOP_N
    return out


def trend_curve(o, c, start, end, bps):
    dates = list(c.index)
    eq = 1.0
    w = {DEFENSIVE: 1.0}
    curve = []
    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break
        prev, op, cl = c.iloc[i - 1], o.iloc[i], c.iloc[i]
        ov = {s: float(op[s] / prev[s] - 1.0) for s in w}
        eq *= 1.0 + sum(w[s] * ov[s] for s in w)
        w = drift(w, ov)
        if monthly(dates, i):
            tw = trend_target(c, i - 1)
            t = turnover(w, tw)
            eq -= eq * (bps / 10000.0) * t
            w = tw
        intra = {s: float(cl[s] / op[s] - 1.0) for s in w}
        eq *= 1.0 + sum(w[s] * intra[s] for s in w)
        w = drift(w, intra)
        curve.append((d, eq))
    return pd.Series([e for _, e in curve], index=pd.to_datetime([d for d, _ in curve]), dtype=float)


def neutral_curve(o, c, start, end, bps):
    dates = list(c.index)
    eq = 1.0
    w = {}
    curve = []
    daily_borrow = SHORT_BORROW_BPS_ANNUAL / 10000.0 / 252.0
    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break
        if monthly(dates, i):
            tw = neutral_target(c, i - 1)
            t = turnover(w, tw)
            eq -= eq * (bps / 10000.0) * t
            w = tw
        prev, cl = c.iloc[i - 1], c.iloc[i]
        r = sum(weight * float(cl[s] / prev[s] - 1.0) for s, weight in w.items())
        short_notional = sum(abs(x) for x in w.values() if x < 0)
        r -= daily_borrow * short_notional
        eq *= 1.0 + r
        curve.append((d, eq))
    return pd.Series([e for _, e in curve], index=pd.to_datetime([d for d, _ in curve]), dtype=float)


def combine(p, n):
    idx = p.index.intersection(n.index)
    p, n = p.loc[idx], n.loc[idx]
    return START_EQ * (TREND_WEIGHT * (p / p.iloc[0]) + NEUTRAL_WEIGHT * (n / n.iloc[0]))


def strategy_curve(o, c):
    p = trend_curve(o, c, EVAL_START, EVAL_END, STRESS_BPS)
    n = neutral_curve(o, c, EVAL_START, EVAL_END, STRESS_BPS)
    return combine(p, n)


def summarize(s):
    annual = {}
    for y in sorted(s.index.year.unique()):
        ys = s[s.index.year == y]
        prior = s[s.index < ys.index[0]]
        y0 = float(prior.iloc[-1]) if len(prior) else START_EQ
        annual[str(y)] = float((ys.iloc[-1] / y0 - 1.0) * 100.0)
    total = float((s.iloc[-1] / START_EQ - 1.0) * 100.0)
    dd = float((1.0 - s / s.cummax()).max() * 100.0)
    return {"final_equity": float(s.iloc[-1]), "total_return_pct": total,
            "max_drawdown_pct": dd, "annual_returns_pct": annual}


def signal_agreement(yc, ac):
    dates = list(yc.index)
    rows = []
    for i in range(1, len(dates)):
        d = dates[i]
        if d < EVAL_START or d > EVAL_END or not monthly(dates, i):
            continue
        yt, at = trend_target(yc, i - 1), trend_target(ac, i - 1)
        yn, an = neutral_target(yc, i - 1), neutral_target(ac, i - 1)
        yrisk = 1.0 - yt.get(DEFENSIVE, 0.0)
        arisk = 1.0 - at.get(DEFENSIVE, 0.0)
        risk_on_same = (yrisk > 1e-9) == (arisk > 1e-9)
        l1 = sum(abs(yt.get(s, 0.0) - at.get(s, 0.0)) for s in set(yt) | set(at))
        trend_close = l1 <= 0.15
        ylong = {s for s, w in yn.items() if w > 0}
        yshort = {s for s, w in yn.items() if w < 0}
        along = {s for s, w in an.items() if w > 0}
        ashort = {s for s, w in an.items() if w < 0}
        sector_side_same = ylong == along and yshort == ashort
        overall = trend_close and sector_side_same
        rows.append({"date": str(d), "risk_on_same": risk_on_same, "trend_close": trend_close,
                     "sector_side_same": sector_side_same, "overall": overall,
                     "trend_l1": float(l1), "yahoo_risk": float(yrisk), "alpaca_risk": float(arisk)})
    if not rows:
        raise RuntimeError("No monthly signals available for comparison")
    n = len(rows)
    return {
        "months": n,
        "overall_agreement": sum(r["overall"] for r in rows) / n,
        "risk_on_off_agreement": sum(r["risk_on_same"] for r in rows) / n,
        "trend_close_agreement": sum(r["trend_close"] for r in rows) / n,
        "sector_side_agreement": sum(r["sector_side_same"] for r in rows) / n,
        "mean_trend_l1": float(np.mean([r["trend_l1"] for r in rows])),
        "details": rows,
    }


def main():
    print("Downloading Yahoo adjusted history...")
    yo, yc = load_yahoo_panel()
    print("Downloading Alpaca adjusted IEX history...")
    ao, ac = load_alpaca_panel()
    yo, yc, ao, ac = align_sources(yo, yc, ao, ac)
    print(f"Common bars: {len(yc)} | {yc.index[0]} to {yc.index[-1]}")

    ys = summarize(strategy_curve(yo, yc))
    aps = summarize(strategy_curve(ao, ac))
    sig = signal_agreement(yc, ac)

    years = sorted(set(ys["annual_returns_pct"]) & set(aps["annual_returns_pct"]))
    annual_diff = {y: abs(ys["annual_returns_pct"][y] - aps["annual_returns_pct"][y]) for y in years}
    sign_match = {y: (ys["annual_returns_pct"][y] > 0) == (aps["annual_returns_pct"][y] > 0) for y in years}

    checks = {
        "all_annual_signs_match": all(sign_match.values()),
        "both_cumulative_positive": ys["total_return_pct"] > 0 and aps["total_return_pct"] > 0,
        "annual_return_difference_within_3pp": max(annual_diff.values()) <= MAX_ANNUAL_RETURN_DIFF_PP,
        "drawdown_difference_within_2pp": abs(ys["max_drawdown_pct"] - aps["max_drawdown_pct"]) <= MAX_DRAWDOWN_DIFF_PP,
        "monthly_signal_agreement_at_least_85pct": sig["overall_agreement"] >= MIN_MONTHLY_SIGNAL_AGREEMENT,
        "risk_on_off_agreement_at_least_90pct": sig["risk_on_off_agreement"] >= MIN_RISK_ON_OFF_AGREEMENT,
        "sector_side_agreement_at_least_80pct": sig["sector_side_agreement"] >= MIN_SECTOR_SIDE_AGREEMENT,
    }
    passed = all(checks.values())
    result = {
        "phase": "6A",
        "strategy": "frozen Phase 5H",
        "source_a": "Yahoo Finance via yfinance, auto-adjusted",
        "source_b": "Alpaca IEX daily bars, adjustment=all",
        "evaluation": {"start": str(EVAL_START), "end": str(EVAL_END)},
        "yahoo": ys,
        "alpaca": aps,
        "annual_abs_diff_pp": annual_diff,
        "annual_sign_match": sign_match,
        "signal_agreement": sig,
        "gate_checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "paper_trading_authorized": bool(passed),
    }
    with open("phase6a_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Phase 6A — Independent Data Validation",
        "",
        f"**Gate: {result['gate']}**",
        "",
        "## Frozen strategy",
        "Phase 5H is unchanged. No parameter optimization is performed in Phase 6A.",
        "",
        "## Data sources",
        "- Source A: Yahoo Finance adjusted daily OHLC via yfinance",
        "- Source B: Alpaca IEX adjusted daily OHLC (`adjustment=all`)",
        f"- Common evaluation window: **{EVAL_START} through {EVAL_END}**",
        "",
        "## 10 bps results",
        f"- Yahoo: cumulative {ys['total_return_pct']:+.2f}% | max DD {ys['max_drawdown_pct']:.2f}%",
        f"- Alpaca: cumulative {aps['total_return_pct']:+.2f}% | max DD {aps['max_drawdown_pct']:.2f}%",
        "",
        "### Annual returns",
    ]
    for y in years:
        lines.append(f"- {y}: Yahoo {ys['annual_returns_pct'][y]:+.2f}% | Alpaca {aps['annual_returns_pct'][y]:+.2f}% | abs diff {annual_diff[y]:.2f} pp | sign {'MATCH' if sign_match[y] else 'MISMATCH'}")
    lines += [
        "",
        "## Monthly signal agreement",
        f"- Compared rebalance months: **{sig['months']}**",
        f"- Overall portfolio-signal agreement: **{sig['overall_agreement']*100:.1f}%**",
        f"- Risk-on/off agreement: **{sig['risk_on_off_agreement']*100:.1f}%**",
        f"- Trend-weight closeness: **{sig['trend_close_agreement']*100:.1f}%**",
        f"- Sector long/short side agreement: **{sig['sector_side_agreement']*100:.1f}%**",
        "",
        "## Gate checks",
    ]
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += [
        "",
        "## Decision",
        "Paper trading is authorized only if every independent-data gate above passes.",
        "",
        "Historical agreement across two data sources does not guarantee future profit.",
    ]
    summary = "\n".join(lines) + "\n"
    with open("phase6a_summary.md", "w") as f:
        f.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
