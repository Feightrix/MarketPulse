import json, math, os, time, urllib.parse, urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
SYMBOLS = ["SPY", "QQQ", "IWM"]
START = "2021-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
TZ = "America/New_York"
START_EQ = 100.0
CAPITAL = 0.95
MAX_TRADES_PER_DAY = 1
BASE_FRICTION_BPS = 2.0
TARGET_FLOOR = 0.0040
TARGET_CAP = 0.0080
STOP_FLOOR = 0.0025
STOP_CAP = 0.0050


def headers():
    key = os.getenv("ALPACA_API_KEY_ID")
    sec = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch(symbol):
    rows, token, h = [], None, headers()
    while True:
        params = {
            "timeframe": "5Min", "start": START, "end": END,
            "adjustment": "all", "feed": "iex", "limit": 10000, "sort": "asc",
        }
        if token:
            params["page_token"] = token
        url = BASE.format(symbol=symbol) + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
        rows.extend(payload.get("bars", []))
        token = payload.get("next_page_token")
        if not token:
            break
        time.sleep(0.02)
    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError(f"No bars returned for {symbol}")
    d["ts"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert(TZ)
    d = d.set_index("ts").sort_index().rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    return d[["open", "high", "low", "close", "volume"]].astype(float)


def rsi(s, n=7):
    delta = s.diff()
    up, dn = delta.clip(lower=0), -delta.clip(upper=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def prep(symbol, d):
    d = d.between_time("09:30", "16:00", inclusive="left").copy()
    d["session"] = d.index.normalize()
    parts = []
    for _, x in d.groupby("session", sort=True):
        x = x.copy()
        x["ema9"] = x.close.ewm(span=9, adjust=False).mean()
        x["ema21"] = x.close.ewm(span=21, adjust=False).mean()
        x["rsi7"] = rsi(x.close)
        typical = (x.high + x.low + x.close) / 3
        vc = x.volume.cumsum().replace(0, np.nan)
        x["vwap"] = (typical * x.volume).cumsum() / vc
        x["vr"] = x.volume / x.volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
        prev_close = x.close.shift(1)
        tr = pd.concat([
            x.high - x.low,
            (x.high - prev_close).abs(),
            (x.low - prev_close).abs(),
        ], axis=1).max(axis=1)
        x["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=7).mean()
        x["atr_pct"] = x.atr14 / x.close
        x["hi6"] = x.high.shift(1).rolling(6, min_periods=6).max()
        x["lo6"] = x.low.shift(1).rolling(6, min_periods=6).min()
        parts.append(x)
    d = pd.concat(parts).sort_index()
    mins = d.index.hour * 60 + d.index.minute
    d["morning"] = (mins >= 9 * 60 + 50) & (mins <= 11 * 60 + 30)
    d["afternoon"] = (mins >= 14 * 60) & (mins <= 15 * 60 + 10)
    d["time_ok"] = d.morning | d.afternoon
    return d


def load_data():
    out = {}
    for s in SYMBOLS:
        print("Downloading", s, flush=True)
        out[s] = prep(s, fetch(s))
        print(s, len(out[s]), "bars", flush=True)
    return out


def daily_regimes(data):
    daily_close = {}
    for s, d in data.items():
        daily_close[s] = d.groupby("session").close.last()
    close = pd.DataFrame(daily_close).sort_index().dropna(how="all")
    prev_close = close.shift(1)
    r5 = close.pct_change(5).shift(1)
    r20 = close.pct_change(20).shift(1)
    ema20 = close.ewm(span=20, adjust=False).mean().shift(1)
    ema50 = close.ewm(span=50, adjust=False).mean().shift(1)
    vol20 = close.pct_change().rolling(20).std().shift(1)
    breadth = (r20 > 0).sum(axis=1)

    regimes = {
        "strict_trend": (
            (prev_close["SPY"] > ema20["SPY"])
            & (ema20["SPY"] > ema50["SPY"])
            & (r20["SPY"] > 0)
            & (breadth >= 2)
            & (vol20["SPY"] < 0.018)
        ),
        "trend_breadth": (
            (prev_close["SPY"] > ema20["SPY"])
            & (r20["SPY"] > 0)
            & (breadth >= 2)
        ),
        "momentum_breadth": (
            (r20["SPY"] > 0)
            & (r20["QQQ"] > 0)
            & (breadth >= 2)
            & (r5["SPY"] > -0.015)
        ),
        "low_vol_trend": (
            (prev_close["SPY"] > ema20["SPY"])
            & (ema20["SPY"] > ema50["SPY"])
            & (r20["SPY"] > 0)
            & (vol20["SPY"] < 0.012)
        ),
    }

    selections = {}
    for lookback, frame in [(5, r5), (20, r20)]:
        chosen = {}
        for dt, row in frame.iterrows():
            valid = row.dropna()
            if valid.empty or valid.max() <= 0:
                chosen[dt] = None
            else:
                chosen[dt] = str(valid.idxmax())
        selections[lookback] = chosen

    return regimes, selections


def entry_signals(d):
    common = (
        (d.ema9 > d.ema21)
        & (d.close > d.vwap)
        & (d.atr_pct.fillna(0) >= 0.0008)
    )
    cross6 = (d.close > d.hi6) & (d.close.shift(1) <= d.hi6.shift(1))
    breakout = cross6 & common & (d.vr.fillna(0) >= 1.0) & d.time_ok

    vwap_cross = (d.close.shift(1) <= d.vwap.shift(1)) & (d.close > d.vwap)
    vwap = vwap_cross & common & (d.rsi7 >= 50) & (d.vr.fillna(0) >= 0.8) & d.time_ok

    reclaim9 = (d.close.shift(1) <= d.ema9.shift(1)) & (d.close > d.ema9)
    pullback = (
        reclaim9
        & (d.low.shift(1) <= d.ema21.shift(1) * 1.001)
        & (d.rsi7.shift(1) < 55)
        & (d.rsi7 >= 50)
        & common
        & (d.vr.fillna(0) >= 0.8)
        & d.time_ok
    )

    return {
        "breakout6": breakout.shift(1, fill_value=False),
        "vwap_reclaim": vwap.shift(1, fill_value=False),
        "pullback9": pullback.shift(1, fill_value=False),
    }


def quality_score(d):
    trend = np.maximum(0.0, d.ema9 / d.ema21 - 1) * 900
    vwap = np.maximum(0.0, d.close / d.vwap - 1) * 350
    vol = np.minimum(d.vr.fillna(0), 3.0) * 0.20
    atr = np.minimum(d.atr_pct.fillna(0), 0.01) * 100
    return (trend + vwap + vol + atr).shift(1)


def pack_period(data, regimes, selections, start, end):
    st = pd.Timestamp(start, tz=TZ)
    en = pd.Timestamp(end, tz=TZ) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    frames = {s: d[(d.index >= st) & (d.index <= en)].copy() for s, d in data.items()}
    idx = frames[SYMBOLS[0]].index
    for s in SYMBOLS[1:]:
        idx = idx.union(frames[s].index)
    idx = idx.sort_values()
    dates = idx.normalize()
    months = (idx.year * 100 + idx.month).to_numpy(dtype=np.int32)
    pack = {
        "ts_ns": idx.asi8,
        "day_ns": dates.asi8,
        "month": months,
        "minute": (idx.hour * 60 + idx.minute).to_numpy(dtype=np.int16),
        "symbols": {},
        "regimes": {},
        "selections": {},
    }
    for name, series in regimes.items():
        mapping = {int(pd.Timestamp(k).value): bool(v) for k, v in series.items()}
        pack["regimes"][name] = np.array([mapping.get(int(x), False) for x in dates.asi8], dtype=bool)
    for lb, mapping0 in selections.items():
        mapping = {int(pd.Timestamp(k).value): v for k, v in mapping0.items()}
        pack["selections"][lb] = np.array([mapping.get(int(x), None) for x in dates.asi8], dtype=object)

    for s in SYMBOLS:
        d = frames[s]
        a = d.reindex(idx)
        sig = entry_signals(d)
        pack["symbols"][s] = {
            "valid": a.open.notna().to_numpy(),
            "open": a.open.to_numpy(float),
            "high": a.high.to_numpy(float),
            "low": a.low.to_numpy(float),
            "close": a.close.to_numpy(float),
            "atr_pct": a.atr_pct.to_numpy(float),
            "quality": quality_score(d).reindex(idx).to_numpy(float),
            "signals": {k: v.reindex(idx, fill_value=False).to_numpy(bool) for k, v in sig.items()},
        }
    pack["entry_names"] = ["breakout6", "vwap_reclaim", "pullback9"]
    pack["months_all"] = sorted(set(int(x) for x in months))
    return pack


def calc_metrics(trades, eq, maxdd, months_all):
    empty = {
        "trades": 0, "final_equity": START_EQ, "total_return": 0.0, "win_rate": 0.0,
        "expectancy_bps": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
        "daily_sharpe": 0.0, "positive_days": 0.0,
        "monthly_positive_rate": 0.0, "months_doubled": 0,
        "months_total": len(months_all), "best_month_return": 0.0,
        "median_month_return": 0.0, "months_ge_10pct": 0, "months_ge_25pct": 0,
    }
    if not trades:
        return empty
    t = pd.DataFrame(trades)
    wins = t.loc[t.pnl > 0, "pnl"].sum()
    losses = -t.loc[t.pnl < 0, "pnl"].sum()
    daily = t.groupby("day")["ret"].sum()
    sd = daily.std(ddof=0)
    sharpe = float(daily.mean() / sd * math.sqrt(252)) if len(daily) > 1 and sd > 0 else 0.0
    month_returns = {m: 0.0 for m in months_all}
    for m, g in t.groupby("month"):
        month_returns[int(m)] = float(np.prod(1.0 + g["ret"].to_numpy()) - 1.0)
    marr = np.array(list(month_returns.values()), dtype=float)
    return {
        "trades": int(len(t)),
        "final_equity": float(eq),
        "total_return": float(eq / START_EQ - 1),
        "win_rate": float((t.pnl > 0).mean()),
        "expectancy_bps": float(t.ret.mean() * 10000),
        "profit_factor": float(wins / losses) if losses > 0 else 99.0,
        "max_drawdown": float(maxdd),
        "daily_sharpe": sharpe,
        "positive_days": float((daily > 0).mean()),
        "monthly_positive_rate": float((marr > 0).mean()) if len(marr) else 0.0,
        "months_doubled": int((marr >= 1.0).sum()),
        "months_total": int(len(marr)),
        "best_month_return": float(marr.max()) if len(marr) else 0.0,
        "median_month_return": float(np.median(marr)) if len(marr) else 0.0,
        "months_ge_10pct": int((marr >= 0.10).sum()),
        "months_ge_25pct": int((marr >= 0.25).sum()),
    }


def sim(pack, regime_name, selection_lb, entry_name, target_mult, stop_mult, hold_bars, friction_bps):
    ts_ns, day_ns, minute, months = pack["ts_ns"], pack["day_ns"], pack["minute"], pack["month"]
    syms = pack["symbols"]
    regime = pack["regimes"][regime_name]
    selected = pack["selections"][selection_lb]
    fr = friction_bps / 10000.0
    eq, peak, maxdd = START_EQ, START_EQ, 0.0
    pos, trades, perday = None, [], {}

    for i in range(len(ts_ns)):
        day = int(day_ns[i])
        perday.setdefault(day, 0)

        if pos is not None:
            arr = syms[pos["symbol"]]
            if arr["valid"][i]:
                pos["bars"] += 1
                stop_px = pos["entry"] * (1 - pos["stop_pct"])
                target_px = pos["entry"] * (1 + pos["target_pct"])
                raw, why = None, None
                if arr["low"][i] <= stop_px:
                    raw, why = stop_px, "STOP"
                elif arr["high"][i] >= target_px:
                    raw, why = target_px, "TARGET"
                elif pos["bars"] >= hold_bars:
                    raw, why = arr["close"][i], "TIME"
                elif minute[i] >= 15 * 60 + 40:
                    raw, why = arr["close"][i], "EOD"
                if raw is not None:
                    exit_px = float(raw) * (1 - fr)
                    pnl = pos["qty"] * (exit_px - pos["entry"])
                    ret = pnl / pos["eq0"]
                    eq += pnl
                    peak = max(peak, eq)
                    maxdd = max(maxdd, (peak - eq) / peak if peak else 0.0)
                    trades.append({
                        "day": day, "month": int(months[i]), "symbol": pos["symbol"],
                        "pnl": pnl, "ret": ret, "reason": why,
                    })
                    perday[day] += 1
                    pos = None
            continue

        if perday[day] >= MAX_TRADES_PER_DAY or not regime[i]:
            continue
        s = selected[i]
        if s not in SYMBOLS:
            continue
        arr = syms[s]
        if not arr["valid"][i] or not arr["signals"][entry_name][i]:
            continue
        op, atrp, q = arr["open"][i], arr["atr_pct"][i], arr["quality"][i]
        if np.isnan(op) or np.isnan(atrp) or np.isnan(q) or atrp <= 0:
            continue
        target_pct = float(np.clip(atrp * target_mult, TARGET_FLOOR, TARGET_CAP))
        stop_pct = float(np.clip(atrp * stop_mult, STOP_FLOOR, STOP_CAP))
        if target_pct / stop_pct < 1.25:
            continue
        # Require the gross target to remain at least 4x modeled round-trip friction.
        if target_pct < (2 * fr * 4):
            continue
        entry_px = float(op) * (1 + fr)
        qty = eq * CAPITAL / entry_px
        pos = {
            "symbol": s, "entry": entry_px, "qty": qty, "eq0": eq, "bars": 0,
            "target_pct": target_pct, "stop_pct": stop_pct, "start_i": i,
        }

    if pos is not None:
        arr = syms[pos["symbol"]]
        valid = np.flatnonzero(arr["valid"][pos["start_i"]:])
        if valid.size:
            j = pos["start_i"] + int(valid[-1])
            exit_px = float(arr["close"][j]) * (1 - fr)
            pnl = pos["qty"] * (exit_px - pos["entry"])
            ret = pnl / pos["eq0"]
            eq += pnl
            peak = max(peak, eq)
            maxdd = max(maxdd, (peak - eq) / peak if peak else 0.0)
            trades.append({
                "day": int(day_ns[j]), "month": int(months[j]), "symbol": pos["symbol"],
                "pnl": pnl, "ret": ret, "reason": "FINAL",
            })
    return calc_metrics(trades, eq, maxdd, pack["months_all"])


def rounded(m):
    out = dict(m)
    for k, v in list(out.items()):
        if isinstance(v, (float, np.floating)):
            out[k] = round(float(v), 6 if k in {"total_return", "win_rate", "max_drawdown", "positive_days", "monthly_positive_rate", "best_month_return", "median_month_return"} else 4)
    return out


def dev_score(m):
    if m["trades"] < 35 or m["expectancy_bps"] <= 1.0 or m["profit_factor"] <= 1.05:
        return -1e9
    return (
        m["daily_sharpe"]
        + 0.12 * min(m["expectancy_bps"], 25)
        + 0.8 * min(m["profit_factor"], 2.5)
        + m["monthly_positive_rate"]
        - 10 * max(0.0, m["max_drawdown"] - 0.08)
    )


def main():
    data = load_data()
    regimes, selections = daily_regimes(data)
    print("Packing periods", flush=True)
    dev = pack_period(data, regimes, selections, "2021-01-01", "2023-12-31")
    y24 = pack_period(data, regimes, selections, "2024-01-01", "2024-12-31")
    y25 = pack_period(data, regimes, selections, "2025-01-01", "2025-12-31")
    y26 = pack_period(data, regimes, selections, "2026-01-01", "2026-07-31")
    v2425 = pack_period(data, regimes, selections, "2024-01-01", "2025-12-31")

    regime_names = list(regimes)
    entry_names = dev["entry_names"]
    configs = list(product(
        regime_names, [5, 20], entry_names,
        [1.5, 2.0], [0.8, 1.0], [6, 12]
    ))
    candidates = []
    for n, (regime, lb, entry, tm, sm, hold) in enumerate(configs, 1):
        m = sim(dev, regime, lb, entry, tm, sm, hold, BASE_FRICTION_BPS)
        candidates.append({
            "regime": regime, "selection_lookback": lb, "entry": entry,
            "target_mult": tm, "stop_mult": sm, "hold": hold,
            "development": m, "score": dev_score(m),
        })
        if n % 32 == 0 or n == len(configs):
            print("development", n, "/", len(configs), flush=True)

    dev_ok = [c for c in candidates if c["score"] > -1e8]
    finalists = sorted(dev_ok, key=lambda c: c["score"], reverse=True)[:40]
    checked = []
    for i, c in enumerate(finalists, 1):
        args = (c["regime"], c["selection_lookback"], c["entry"], c["target_mult"], c["stop_mult"], c["hold"])
        m24 = sim(y24, *args, BASE_FRICTION_BPS)
        m25 = sim(y25, *args, BASE_FRICTION_BPS)
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
        ) if robust else -1e9
        checked.append({**c, "validation_2024": m24, "validation_2025": m25, "robust": robust, "vscore": vscore})
        print("validation", i, "/", len(finalists), robust, flush=True)

    robust_candidates = [c for c in checked if c["robust"]]
    if robust_candidates:
        best = max(robust_candidates, key=lambda c: c["vscore"])
    elif checked:
        best = max(checked, key=lambda c: (
            min(c["validation_2024"]["expectancy_bps"], c["validation_2025"]["expectancy_bps"]), c["score"]
        ))
    else:
        best = max(candidates, key=lambda c: c["score"])
        zero24 = calc_metrics([], START_EQ, 0.0, y24["months_all"])
        zero25 = calc_metrics([], START_EQ, 0.0, y25["months_all"])
        best = {**best, "validation_2024": zero24, "validation_2025": zero25, "robust": False, "vscore": -1e9}

    args = (best["regime"], best["selection_lookback"], best["entry"], best["target_mult"], best["stop_mult"], best["hold"])
    combined = sim(v2425, *args, BASE_FRICTION_BPS)
    friction = {str(b): sim(v2425, *args, b) for b in [2.0, 4.0, 6.0, 10.0]}
    check26 = sim(y26, *args, BASE_FRICTION_BPS)

    gate = (
        best.get("robust", False)
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
        "selected": {
            "regime": best["regime"],
            "selection_lookback_days": best["selection_lookback"],
            "entry": best["entry"],
            "target_atr_multiple": best["target_mult"],
            "stop_atr_multiple": best["stop_mult"],
            "target_range_pct": [TARGET_FLOOR, TARGET_CAP],
            "stop_range_pct": [STOP_FLOOR, STOP_CAP],
            "max_hold_minutes": best["hold"] * 5,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "capital_fraction": CAPITAL,
        },
        "development": rounded(best["development"]),
        "validation_2024": rounded(best["validation_2024"]),
        "validation_2025": rounded(best["validation_2025"]),
        "validation_2024_2025": rounded(combined),
        "check_2026": rounded(check26),
        "friction_validation_2024_2025": {k: rounded(v) for k, v in friction.items()},
        "gate": "PASS" if gate else "FAIL",
        "warning": "A 100% monthly target is an aspiration, not a guaranteed or expected return. The strategy gate is based on robustness, not on forcing the target.",
    }
    with open("phase3c_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse — Phase 3C Regime-Aware Validation", "",
        "**Monthly objective:** 2× the balance recorded at the start of each month (tracked, never forced)",
        f"**Candidates tested:** {len(configs)}",
        f"**Development-valid candidates:** {len(dev_ok)}",
        f"**Candidates passing both 2024 and 2025 validation:** {len(robust_candidates)}", "",
        "## Selected setup", "",
        f"- Regime filter: **{best['regime']}**",
        f"- ETF selection: strongest prior **{best['selection_lookback']} trading-day** return",
        f"- Entry: **{best['entry']}**",
        f"- Dynamic target: **{best['target_mult']:.2f} × ATR**, bounded to {TARGET_FLOOR:.2%}–{TARGET_CAP:.2%}",
        f"- Dynamic stop: **{best['stop_mult']:.2f} × ATR**, bounded to {STOP_FLOOR:.2%}–{STOP_CAP:.2%}",
        f"- Max hold: **{best['hold'] * 5} minutes**",
        f"- Max trades/day: **{MAX_TRADES_PER_DAY}**", "",
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
    lines += ["", "## Validation friction stress", "",
              "| One-way friction | Expectancy | Return | PF |",
              "|---:|---:|---:|---:|"]
    for b, m in result["friction_validation_2024_2025"].items():
        lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.2f} bps | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += ["", f"**Phase 3C gate: {result['gate']}**", "",
              "## Important", "",
              "The monthly doubling target is reported as an objective only. It never increases position size, leverage, trade frequency, or loss tolerance. A strategy that does not meet the target can still be a valid strategy; a strategy that reaches the target in a backtest is not guaranteed to repeat it."]
    with open("phase3c_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
