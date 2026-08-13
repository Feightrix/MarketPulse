import json, math, os, time, urllib.parse, urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
    "AMD", "AVGO", "NFLX", "JPM", "XOM", "BA", "CAT", "COST", "HD", "WMT",
]
START = "2020-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
START_EQ = 100.0
CAPITAL = 0.95
BASE_FRICTION_BPS = 5.0


def headers():
    key = os.getenv("ALPACA_API_KEY_ID")
    sec = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch(symbol):
    params = {
        "timeframe": "1Day", "start": START, "end": END, "adjustment": "all",
        "feed": "iex", "limit": 10000, "sort": "asc", "asof": "2026-08-01",
    }
    url = BASE.format(symbol=symbol) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode())
    rows = payload.get("bars", [])
    if not rows:
        raise RuntimeError(f"No daily bars returned for {symbol}")
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["t"], utc=True).dt.date
    d = d.set_index("date").sort_index().rename(
        columns={"o":"open", "h":"high", "l":"low", "c":"close", "v":"volume"}
    )
    return d[["open","high","low","close","volume"]].astype(float)


def prep(d):
    x = d.copy()
    prev = x.close.shift(1)
    tr = pd.concat([
        x.high - x.low,
        (x.high - prev).abs(),
        (x.low - prev).abs(),
    ], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14, min_periods=14).mean()
    x["atr_pct"] = x.atr14 / x.close
    x["ret20"] = x.close / x.close.shift(20) - 1
    x["ret60"] = x.close / x.close.shift(60) - 1
    x["sma50"] = x.close.rolling(50, min_periods=50).mean()
    x["sma100"] = x.close.rolling(100, min_periods=100).mean()
    x["hi20"] = x.high.shift(1).rolling(20, min_periods=20).max()
    x["hi50"] = x.high.shift(1).rolling(50, min_periods=50).max()
    x["dvol20"] = (x.close * x.volume).rolling(20, min_periods=20).mean()
    return x


def load_data():
    out = {}
    for s in UNIVERSE:
        print("Downloading", s, flush=True)
        out[s] = prep(fetch(s))
        print(s, len(out[s]), "daily bars", flush=True)
        time.sleep(0.02)
    return out


def all_dates(data, start, end):
    dates = set()
    for d in data.values():
        dates.update(x for x in d.index if start <= x <= end)
    return sorted(dates)


def monthly_stats(equity_points):
    if not equity_points:
        return {
            "months_total":0, "months_doubled":0, "monthly_positive_rate":0.0,
            "best_month_return":0.0, "median_month_return":0.0,
        }
    e = pd.Series({pd.Timestamp(k): v for k, v in equity_points.items()}).sort_index()
    month_end = e.groupby(e.index.to_period("M")).last()
    month_start = e.groupby(e.index.to_period("M")).first()
    rets = month_end / month_start - 1.0
    if len(rets) == 0:
        return {
            "months_total":0, "months_doubled":0, "monthly_positive_rate":0.0,
            "best_month_return":0.0, "median_month_return":0.0,
        }
    return {
        "months_total": int(len(rets)),
        "months_doubled": int((rets >= 1.0).sum()),
        "monthly_positive_rate": float((rets > 0).mean()),
        "best_month_return": float(rets.max()),
        "median_month_return": float(rets.median()),
    }


def metrics(trades, equity_points):
    e = pd.Series({pd.Timestamp(k): v for k, v in equity_points.items()}).sort_index()
    final_eq = float(e.iloc[-1]) if len(e) else START_EQ
    peak = e.cummax() if len(e) else pd.Series(dtype=float)
    dd = (e / peak - 1.0) if len(e) else pd.Series(dtype=float)
    maxdd = float(-dd.min()) if len(dd) else 0.0
    m = monthly_stats(equity_points)
    if not trades:
        return {
            "trades":0, "final_equity":final_eq, "total_return":final_eq/START_EQ-1,
            "win_rate":0.0, "expectancy_bps":0.0, "profit_factor":0.0,
            "max_drawdown":maxdd, **m,
        }
    t = pd.DataFrame(trades)
    wins = t.loc[t.pnl > 0, "pnl"].sum()
    losses = -t.loc[t.pnl < 0, "pnl"].sum()
    return {
        "trades": int(len(t)),
        "final_equity": final_eq,
        "total_return": float(final_eq / START_EQ - 1),
        "win_rate": float((t.pnl > 0).mean()),
        "expectancy_bps": float(t.account_ret.mean() * 10000),
        "profit_factor": float(wins / losses) if losses > 0 else 99.0,
        "max_drawdown": maxdd,
        **m,
    }


def eligible_row(row, breakout_lb, breakout_band, trend_mode):
    if row is None or row.isna().any():
        return False
    if row.close <= 5 or not (0.008 <= row.atr_pct <= 0.10):
        return False
    hi = row.hi20 if breakout_lb == 20 else row.hi50
    if row.close < hi * (1.0 - breakout_band):
        return False
    if row.close <= row.sma50:
        return False
    if trend_mode == "strict" and row.sma50 <= row.sma100:
        return False
    return True


def sim(data, start, end, rank_lb, breakout_lb, breakout_band, trend_mode, hold_days, stop_pct, target_pct, friction_bps):
    dates = all_dates(data, start, end)
    fr = friction_bps / 10000.0
    cash = START_EQ
    pos = None
    pending = None
    trades = []
    equity = {}

    for dt in dates:
        # Enter at today's open based only on yesterday's close signal.
        if pos is None and pending is not None:
            s = pending
            row = data[s].loc[dt] if dt in data[s].index else None
            if row is not None and np.isfinite(row.open):
                entry = float(row.open) * (1 + fr)
                invest = cash * CAPITAL
                qty = invest / entry
                cash -= invest
                pos = {
                    "symbol": s, "entry": entry, "qty": qty, "eq0": cash + qty * entry,
                    "bars": 0, "cash_base": cash,
                }
            pending = None

        # Manage any open swing using daily OHLC. If target and stop both touch, assume stop first.
        if pos is not None:
            s = pos["symbol"]
            row = data[s].loc[dt] if dt in data[s].index else None
            if row is not None:
                pos["bars"] += 1
                stop_px = pos["entry"] * (1 - stop_pct)
                target_px = pos["entry"] * (1 + target_pct)
                raw = why = None
                if row.low <= stop_px:
                    raw, why = stop_px, "STOP"
                elif row.high >= target_px:
                    raw, why = target_px, "TARGET"
                elif pos["bars"] >= hold_days:
                    raw, why = float(row.close), "TIME"
                if raw is not None:
                    exit_px = float(raw) * (1 - fr)
                    before = pos["eq0"]
                    cash = cash + pos["qty"] * exit_px
                    pnl = cash - before
                    trades.append({
                        "date": str(dt), "symbol": s, "pnl": pnl,
                        "account_ret": pnl / before, "reason": why,
                    })
                    pos = None

        # Mark account at end of day.
        if pos is None:
            eq = cash
        else:
            s = pos["symbol"]
            row = data[s].loc[dt] if dt in data[s].index else None
            mark = float(row.close) if row is not None else pos["entry"]
            eq = cash + pos["qty"] * mark
        equity[dt] = float(eq)

        # At close, if flat, rank eligible instruments for tomorrow's open.
        if pos is None:
            candidates = []
            for s, d in data.items():
                if dt not in d.index:
                    continue
                row = d.loc[dt]
                cols = ["open","high","low","close","atr_pct","ret20","ret60","sma50","sma100","hi20","hi50"]
                if any(pd.isna(row.get(c, np.nan)) for c in cols):
                    continue
                if not eligible_row(row, breakout_lb, breakout_band, trend_mode):
                    continue
                score = float(row.ret20 if rank_lb == 20 else row.ret60)
                candidates.append((score, s))
            if candidates:
                candidates.sort(reverse=True)
                pending = candidates[0][1]

    # Liquidate at final available close for a clean period metric.
    if pos is not None and dates:
        dt = dates[-1]
        s = pos["symbol"]
        row = data[s].loc[dt] if dt in data[s].index else None
        raw = float(row.close) if row is not None else pos["entry"]
        exit_px = raw * (1 - fr)
        before = pos["eq0"]
        cash = cash + pos["qty"] * exit_px
        pnl = cash - before
        trades.append({"date":str(dt), "symbol":s, "pnl":pnl, "account_ret":pnl/before, "reason":"FINAL"})
        equity[dt] = float(cash)

    return metrics(trades, equity)


def rounded(m):
    out = dict(m)
    for k, v in list(out.items()):
        if isinstance(v, (float, np.floating)):
            out[k] = round(float(v), 6)
    return out


def dev_score(m):
    if m["trades"] < 20 or m["total_return"] <= 0 or m["expectancy_bps"] <= 0 or m["profit_factor"] <= 1.05:
        return -1e9
    return (
        3.0 * m["total_return"] + 0.03 * min(m["expectancy_bps"], 100)
        + 0.75 * min(m["profit_factor"], 3.0) + 0.5 * m["monthly_positive_rate"]
        - 3.0 * max(0.0, m["max_drawdown"] - 0.20)
    )


def main():
    data = load_data()
    configs = list(product(
        [20, 60],             # cross-sectional ranking horizon
        [20, 50],             # breakout lookback
        [0.0, 0.01],          # exact breakout or within 1% of prior high
        ["fast", "strict"],  # trend gate
        [3, 5, 10],           # max holding days
        [0.03, 0.05],         # stop
        [0.06, 0.10, 0.15],   # target
    ))
    candidates = []
    for i, cfg in enumerate(configs, 1):
        m = sim(data, pd.Timestamp("2021-01-01").date(), pd.Timestamp("2023-12-31").date(), *cfg, BASE_FRICTION_BPS)
        candidates.append({"cfg":cfg, "development":m, "score":dev_score(m)})
        if i % 48 == 0 or i == len(configs):
            print("development", i, "/", len(configs), flush=True)

    dev_ok = [c for c in candidates if c["score"] > -1e8]
    finalists = sorted(dev_ok, key=lambda x: x["score"], reverse=True)[:30]
    checked = []
    for i, c in enumerate(finalists, 1):
        m24 = sim(data, pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-12-31").date(), *c["cfg"], BASE_FRICTION_BPS)
        m25 = sim(data, pd.Timestamp("2025-01-01").date(), pd.Timestamp("2025-12-31").date(), *c["cfg"], BASE_FRICTION_BPS)
        robust = (
            m24["trades"] >= 5 and m25["trades"] >= 5
            and m24["total_return"] > 0 and m25["total_return"] > 0
            and m24["expectancy_bps"] > 0 and m25["expectancy_bps"] > 0
            and m24["profit_factor"] > 1.05 and m25["profit_factor"] > 1.05
            and m24["max_drawdown"] < 0.25 and m25["max_drawdown"] < 0.25
        )
        vscore = (
            min(m24["total_return"], m25["total_return"])*5
            + 0.02*min(m24["expectancy_bps"], m25["expectancy_bps"])
            + min(m24["profit_factor"], m25["profit_factor"])
        ) if robust else -1e9
        checked.append({**c, "validation_2024":m24, "validation_2025":m25, "robust":robust, "vscore":vscore})
        print("validation", i, "/", len(finalists), robust, flush=True)

    robusts = [x for x in checked if x["robust"]]
    if robusts:
        best = max(robusts, key=lambda x: x["vscore"])
    elif checked:
        best = max(checked, key=lambda x: (
            min(x["validation_2024"]["total_return"], x["validation_2025"]["total_return"]),
            min(x["validation_2024"]["expectancy_bps"], x["validation_2025"]["expectancy_bps"]),
        ))
    else:
        best = max(candidates, key=lambda x: (x["development"]["total_return"], x["development"]["expectancy_bps"]))
        zero = metrics([], {})
        best = {**best, "validation_2024":zero, "validation_2025":zero, "robust":False, "vscore":-1e9}

    combined = sim(data, pd.Timestamp("2024-01-01").date(), pd.Timestamp("2025-12-31").date(), *best["cfg"], BASE_FRICTION_BPS)
    check26 = sim(data, pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-07-31").date(), *best["cfg"], BASE_FRICTION_BPS)
    stress = {
        str(b): sim(data, pd.Timestamp("2024-01-01").date(), pd.Timestamp("2025-12-31").date(), *best["cfg"], b)
        for b in [5.0, 10.0, 20.0, 30.0]
    }
    gate = (
        best.get("robust", False)
        and combined["total_return"] > 0
        and combined["profit_factor"] > 1.10
        and stress["20.0"]["expectancy_bps"] > 0
        and check26["trades"] >= 3
        and check26["total_return"] > 0
        and check26["profit_factor"] > 1.05
    )

    rank_lb, breakout_lb, band, trend, hold, stop, target = best["cfg"]
    result = {
        "phase":"3D",
        "goal":"Track a 2x first-of-month balance objective without changing risk to chase it.",
        "method":"Cross-sectional swing momentum over a fixed liquid large-cap/ETF universe; close signal, next-open entry; multi-day holds.",
        "universe":UNIVERSE,
        "candidate_count":len(configs),
        "development_valid_candidates":len(dev_ok),
        "robust_2024_2025_candidates":len(robusts),
        "selected":{
            "ranking_lookback_days":rank_lb,
            "breakout_lookback_days":breakout_lb,
            "breakout_band_pct":band,
            "trend_mode":trend,
            "max_hold_days":hold,
            "stop_loss_pct":stop,
            "take_profit_pct":target,
            "capital_fraction":CAPITAL,
        },
        "development":rounded(best["development"]),
        "validation_2024":rounded(best["validation_2024"]),
        "validation_2025":rounded(best["validation_2025"]),
        "validation_2024_2025":rounded(combined),
        "check_2026":rounded(check26),
        "friction_stress_2024_2025":{k:rounded(v) for k,v in stress.items()},
        "gate":"PASS" if gate else "FAIL",
        "warning":"This fixed current universe can introduce survivorship bias. Even a PASS is research evidence only and does not authorize real-money trading.",
    }
    with open("phase3d_results.json","w") as f:
        json.dump(result,f,indent=2)

    lines = [
        "# MarketPulse — Phase 3D Cross-Sectional Swing Momentum", "",
        "**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)",
        f"**Universe:** {len(UNIVERSE)} liquid ETFs / large-cap stocks",
        f"**Candidates tested:** {len(configs)}",
        f"**Development-valid candidates:** {len(dev_ok)}",
        f"**Candidates positive in both 2024 and 2025:** {len(robusts)}", "",
        "## Selected setup", "",
        f"- Rank by prior **{rank_lb}-day** return",
        f"- Require price within **{band:.1%}** of a prior **{breakout_lb}-day high**",
        f"- Trend gate: **{trend}**",
        f"- Hold up to **{hold} trading days**",
        f"- Stop: **{stop:.1%}**",
        f"- Target: **{target:.1%}**",
        f"- Capital deployed: **{CAPITAL:.0%}**, long-only, no leverage", "",
        "## Results", "",
        "| Period | Trades | Return | Win rate | Expectancy | PF | Max DD | Positive months | Best month | Median month | Doubled months |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label,m in [
        ("Development 2021-2023",result["development"]),
        ("Validation 2024",result["validation_2024"]),
        ("Validation 2025",result["validation_2025"]),
        ("Validation 2024-2025",result["validation_2024_2025"]),
        ("2026 check through Jul",result["check_2026"]),
    ]:
        lines.append(
            f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | {m['expectancy_bps']:.1f} bps | "
            f"{m['profit_factor']:.2f} | {m['max_drawdown']:.2%} | {m['monthly_positive_rate']:.2%} | "
            f"{m['best_month_return']:.2%} | {m['median_month_return']:.2%} | {m['months_doubled']}/{m['months_total']} |"
        )
    lines += ["", "## Validation friction stress", "", "| One-way friction | Expectancy | Return | PF |", "|---:|---:|---:|---:|"]
    for b,m in result["friction_stress_2024_2025"].items():
        lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.1f} bps | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += ["", f"**Phase 3D gate: {result['gate']}**", "", "## Important", "",
              "This is a genuinely different strategy class from the earlier intraday micro tests. It reduces trading frequency and seeks larger multi-day moves. The fixed present-day universe may create survivorship bias, so even a PASS would require a separate universe-robustness test and paper execution before any real-money use. The 2× monthly objective is a scorecard, not an expected or guaranteed return."]
    with open("phase3d_summary.md","w") as f:
        f.write("\n".join(lines)+"\n")
    print(json.dumps(result,indent=2),flush=True)


if __name__ == "__main__":
    main()
