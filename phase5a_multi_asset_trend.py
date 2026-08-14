import json
import math
import os
import time
import urllib.parse
import urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
START = "2015-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
TIMEFRAME = "1Day"
START_EQ = 2500.0
BASE_BPS = 2.0
STRESS_BPS = 10.0

RISKY = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
CASH = "BIL"
SYMS = RISKY + [CASH]

LOOKBACKS = [63, 126, 252]
SMAS = [100, 150, 200]
TOP_NS = [1, 2]
FREQS = ["weekly", "monthly"]

DEV_START = pd.Timestamp("2017-01-01").date()
DEV_END = pd.Timestamp("2020-12-31").date()
VAL_START = pd.Timestamp("2021-01-01").date()
VAL_END = pd.Timestamp("2023-12-31").date()
HOLD_START = pd.Timestamp("2024-01-01").date()
HOLD_END = pd.Timestamp("2026-07-31").date()


def headers():
    key = os.getenv("ALPACA_API_KEY_ID")
    secret = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch(sym):
    rows = []
    token = None
    while True:
        q = {
            "timeframe": TIMEFRAME,
            "start": START,
            "end": END,
            "adjustment": "all",
            "feed": "iex",
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            q["page_token"] = token
        url = BASE.format(symbol=sym) + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers=headers())
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
        rows.extend(payload.get("bars", []))
        token = payload.get("next_page_token")
        if not token:
            break
        time.sleep(0.15)

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError("No bars returned for " + sym)
    d["date"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert("America/New_York").dt.date
    d = d.rename(columns={"o": "open", "c": "close"})
    return d[["date", "open", "close"]].drop_duplicates("date").set_index("date").sort_index()


def aligned_panel(raw):
    common = None
    for sym in SYMS:
        idx = raw[sym].index
        common = idx if common is None else common.intersection(idx)
    common = common.sort_values()
    opens = pd.DataFrame(index=common)
    closes = pd.DataFrame(index=common)
    for sym in SYMS:
        opens[sym] = raw[sym].loc[common, "open"].astype(float)
        closes[sym] = raw[sym].loc[common, "close"].astype(float)
    return opens, closes


def is_rebalance_day(dates, i, freq):
    if i <= 0:
        return True
    now = pd.Timestamp(dates[i])
    prev = pd.Timestamp(dates[i - 1])
    if freq == "weekly":
        return now.isocalendar().week != prev.isocalendar().week or now.year != prev.year
    return now.month != prev.month or now.year != prev.year


def target_weights(closes, i_signal, config):
    lb = config["lookback"]
    sma_n = config["sma"]
    top_n = config["top_n"]
    if i_signal < max(lb, sma_n):
        return {CASH: 1.0}

    scores = []
    for sym in RISKY:
        px = float(closes.iloc[i_signal][sym])
        px_lb = float(closes.iloc[i_signal - lb][sym])
        sma = float(closes[sym].iloc[i_signal - sma_n + 1:i_signal + 1].mean())
        if not np.isfinite(px) or not np.isfinite(px_lb) or not np.isfinite(sma) or px_lb <= 0:
            continue
        mom = px / px_lb - 1.0
        if px > sma and mom > 0:
            scores.append((sym, mom))

    scores.sort(key=lambda x: x[1], reverse=True)
    chosen = [s for s, _ in scores[:top_n]]
    if not chosen:
        return {CASH: 1.0}

    w = 1.0 / len(chosen)
    return {s: w for s in chosen}


def normalize(weights):
    total = sum(max(v, 0.0) for v in weights.values())
    if total <= 0:
        return {CASH: 1.0}
    return {k: max(v, 0.0) / total for k, v in weights.items() if v > 1e-12}


def drift(weights, returns):
    grown = {}
    for sym, w in weights.items():
        r = returns.get(sym, 0.0)
        grown[sym] = w * (1.0 + r)
    return normalize(grown)


def turnover(current, target):
    syms = set(current) | set(target)
    return sum(abs(target.get(s, 0.0) - current.get(s, 0.0)) for s in syms)


def simulate(opens, closes, start, end, config, bps):
    dates = list(closes.index)
    eq = START_EQ
    weights = {CASH: 1.0}
    curve = []
    trade_events = 0
    turnover_sum = 0.0

    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break

        prev_close = closes.iloc[i - 1]
        day_open = opens.iloc[i]
        day_close = closes.iloc[i]

        overnight = {}
        for sym in weights:
            pc = float(prev_close[sym])
            op = float(day_open[sym])
            overnight[sym] = op / pc - 1.0 if pc > 0 else 0.0
        eq *= 1.0 + sum(weights[s] * overnight[s] for s in weights)
        weights = drift(weights, overnight)

        if is_rebalance_day(dates, i, config["freq"]):
            target = target_weights(closes, i - 1, config)
            t = turnover(weights, target)
            if t > 1e-8:
                cost = eq * (bps / 10000.0) * t
                eq -= cost
                turnover_sum += t
                trade_events += 1
            weights = target

        intraday = {}
        for sym in weights:
            op = float(day_open[sym])
            cl = float(day_close[sym])
            intraday[sym] = cl / op - 1.0 if op > 0 else 0.0
        eq *= 1.0 + sum(weights[s] * intraday[s] for s in weights)
        weights = drift(weights, intraday)
        curve.append((d, eq))

    return summarize(curve, trade_events, turnover_sum)


def summarize(curve, trade_events, turnover_sum):
    if not curve:
        return {
            "final_equity": START_EQ,
            "total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "positive_month_rate_pct": 0.0,
            "trade_events": 0,
            "turnover_sum": 0.0,
            "annual_returns_pct": {},
        }

    s = pd.Series([x[1] for x in curve], index=pd.to_datetime([x[0] for x in curve]), dtype=float)
    running_max = s.cummax()
    dd = 1.0 - s / running_max
    max_dd = float(dd.max() * 100.0)

    monthly = s.resample("ME").last()
    monthly_prev = monthly.shift(1)
    monthly_ret = monthly / monthly_prev - 1.0
    monthly_ret = monthly_ret.dropna()
    pos_month = float((monthly_ret > 0).mean() * 100.0) if len(monthly_ret) else 0.0

    annual = {}
    years = sorted(s.index.year.unique())
    for y in years:
        ys = s[s.index.year == y]
        if ys.empty:
            continue
        prior = s[s.index < ys.index[0]]
        start_eq = float(prior.iloc[-1]) if len(prior) else START_EQ
        annual[str(y)] = float((ys.iloc[-1] / start_eq - 1.0) * 100.0)

    total_return = float((s.iloc[-1] / START_EQ - 1.0) * 100.0)
    days = max((s.index[-1] - s.index[0]).days, 1)
    years_elapsed = days / 365.25
    cagr = float(((s.iloc[-1] / START_EQ) ** (1.0 / years_elapsed) - 1.0) * 100.0) if s.iloc[-1] > 0 else -100.0

    return {
        "final_equity": float(s.iloc[-1]),
        "total_return_pct": total_return,
        "cagr_pct": cagr,
        "max_drawdown_pct": max_dd,
        "positive_month_rate_pct": pos_month,
        "trade_events": int(trade_events),
        "turnover_sum": float(turnover_sum),
        "annual_returns_pct": annual,
    }


def all_years_positive(metrics, years):
    annual = metrics["annual_returns_pct"]
    return all(str(y) in annual and annual[str(y)] > 0 for y in years)


def dev_valid(m):
    return (
        all_years_positive(m, [2017, 2018, 2019, 2020])
        and m["cagr_pct"] >= 4.0
        and m["max_drawdown_pct"] <= 12.0
        and m["positive_month_rate_pct"] >= 52.0
        and m["trade_events"] >= 8
    )


def score(m):
    weakest = min(m["annual_returns_pct"].values()) if m["annual_returns_pct"] else -999.0
    return 1.5 * weakest + m["cagr_pct"] + 0.20 * m["positive_month_rate_pct"] - 0.35 * m["max_drawdown_pct"]


def gate(selected_dev, val10, hold10):
    checks = {
        "development_all_years_positive_10bps": all_years_positive(selected_dev, [2017, 2018, 2019, 2020]),
        "validation_2021_positive_10bps": val10["annual_returns_pct"].get("2021", -999) > 0,
        "validation_2022_positive_10bps": val10["annual_returns_pct"].get("2022", -999) > 0,
        "validation_2023_positive_10bps": val10["annual_returns_pct"].get("2023", -999) > 0,
        "validation_drawdown": val10["max_drawdown_pct"] <= 12.0,
        "holdout_2024_positive_10bps": hold10["annual_returns_pct"].get("2024", -999) > 0,
        "holdout_2025_positive_10bps": hold10["annual_returns_pct"].get("2025", -999) > 0,
        "holdout_2026_ytd_positive_10bps": hold10["annual_returns_pct"].get("2026", -999) > 0,
        "holdout_drawdown": hold10["max_drawdown_pct"] <= 12.0,
    }
    return checks, all(checks.values())


def fmt(m):
    yrs = ", ".join(f"{k}:{v:+.2f}%" for k, v in m["annual_returns_pct"].items())
    return (
        f"return {m['total_return_pct']:+.2f}% | CAGR {m['cagr_pct']:+.2f}% | "
        f"max DD {m['max_drawdown_pct']:.2f}% | positive months {m['positive_month_rate_pct']:.1f}% | "
        f"rebalance trades {m['trade_events']} | years [{yrs}]"
    )


def main():
    print("Fetching daily Alpaca data for Phase 5A...")
    raw = {s: fetch(s) for s in SYMS}
    opens, closes = aligned_panel(raw)

    candidates = []
    configs = [
        {"lookback": lb, "sma": sma, "top_n": n, "freq": freq}
        for lb, sma, n, freq in product(LOOKBACKS, SMAS, TOP_NS, FREQS)
    ]
    print(f"Testing {len(configs)} low-turnover multi-asset configurations...")

    for config in configs:
        dev10 = simulate(opens, closes, DEV_START, DEV_END, config, STRESS_BPS)
        ok = dev_valid(dev10)
        candidates.append({"config": config, "development_10bps": dev10, "dev_valid": ok, "score": score(dev10)})

    valid = [c for c in candidates if c["dev_valid"]]
    pool = valid if valid else candidates
    selected = max(pool, key=lambda x: x["score"])
    config = selected["config"]

    dev2 = simulate(opens, closes, DEV_START, DEV_END, config, BASE_BPS)
    val2 = simulate(opens, closes, VAL_START, VAL_END, config, BASE_BPS)
    val10 = simulate(opens, closes, VAL_START, VAL_END, config, STRESS_BPS)
    hold2 = simulate(opens, closes, HOLD_START, HOLD_END, config, BASE_BPS)
    hold10 = simulate(opens, closes, HOLD_START, HOLD_END, config, STRESS_BPS)

    checks, passed = gate(selected["development_10bps"], val10, hold10)
    result = {
        "phase": "5A",
        "strategy": "long-only multi-asset absolute momentum + trend rotation",
        "starting_equity": START_EQ,
        "universe": RISKY,
        "cash_proxy": CASH,
        "candidate_count": len(configs),
        "valid_development_candidates": len(valid),
        "selection_policy": "Select using 2017-2020 only at 10 bps per side. Validate 2021-2023, then final holdout 2024-2026 YTD.",
        "selected_config": config,
        "development_2bps": dev2,
        "development_10bps": selected["development_10bps"],
        "validation_2021_2023_2bps": val2,
        "validation_2021_2023_10bps": val10,
        "holdout_2024_2026_2bps": hold2,
        "holdout_2024_2026_10bps": hold10,
        "gate_checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "research_only": True,
        "note": "A PASS means historical robustness under this test protocol, not guaranteed future profit. Paper trading is still required before live deployment.",
    }
    with open("phase5a_results.json", "w") as f:
        json.dump(result, f, indent=2)

    failures = [k for k, v in checks.items() if not v]
    summary = f"""# MarketPulse Phase 5A — Multi-Asset Trend Rotation\n\n**Gate: {result['gate']}**\n\n## Objective\nFind a materially more consistent strategy by abandoning minute-level leveraged-ETF trading and testing low-turnover, long-only trend/momentum rotation across diversified liquid ETFs.\n\n## Universe\n- Risk assets: **{', '.join(RISKY)}**\n- Defensive/cash proxy: **{CASH}**\n- Starting equity: **${START_EQ:,.0f}**\n- Fractional allocation assumed for research\n\n## Selected configuration\n- Momentum lookback: **{config['lookback']} trading days**\n- Trend filter: **close above {config['sma']}-day SMA**\n- Hold top: **{config['top_n']}** eligible asset(s)\n- Rebalance: **{config['freq']}**\n- If nothing qualifies: **100% {CASH}**\n\n## Development 2017–2020\n- Valid candidates: **{len(valid)} / {len(configs)}**\n- 2 bps: {fmt(dev2)}\n- 10 bps: {fmt(selected['development_10bps'])}\n\n## Validation 2021–2023\n- 2 bps: {fmt(val2)}\n- 10 bps: {fmt(val10)}\n\n## Final holdout 2024–2026 YTD\n- 2 bps: {fmt(hold2)}\n- 10 bps: {fmt(hold10)}\n\n## Gate checks\n"""
    for k, v in checks.items():
        summary += f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary += "\n## Failure reasons\n"
    summary += "- None\n" if not failures else "".join(f"- {x}\n" for x in failures)
    summary += "\n## Research status\nResearch only. Historical consistency does not guarantee future returns. A PASS still requires paper trading before live deployment.\n"
    with open("phase5a_summary.md", "w") as f:
        f.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
