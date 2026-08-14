import json
import os
import time
import urllib.parse
import urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
TZ = "America/New_York"
START = "2021-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"

START_EQ = 2500.0
RISK_PER_TRADE = 0.005
GROSS_CAP = 0.95
DAILY_STOP = 0.015
MAX_TRADES_DAY = 3
MAX_CONSEC_LOSSES = 2
COOLDOWN_MIN = 15
BASE_BPS = 2.0

FAMILIES = {
    "NASDAQ": {"signal": "QQQ", "bull": "TQQQ", "bear": "SQQQ"},
    "SP500": {"signal": "SPY", "bull": "SPXL", "bear": "SPXS"},
}
SYMS = sorted({s for f in FAMILIES.values() for s in f.values()})

OR_MINS = [5, 15, 30]
BREAKOUT_BPS = [0.0, 5.0]
RVOLS = [1.0, 1.25]
STOP_ATRS = [0.75, 1.0]
TARGET_RS = [1.25, 1.75]
HOLDS = [20, 45]

DEV_START = pd.Timestamp("2021-01-01").date()
DEV_END = pd.Timestamp("2023-12-31").date()
Y24_START = pd.Timestamp("2024-01-01").date()
Y24_END = pd.Timestamp("2024-12-31").date()
Y25_START = pd.Timestamp("2025-01-01").date()
Y25_END = pd.Timestamp("2025-12-31").date()
V_START = Y24_START
V_END = Y25_END
Y26_START = pd.Timestamp("2026-01-01").date()
Y26_END = pd.Timestamp("2026-07-31").date()


def headers():
    k = os.getenv("ALPACA_API_KEY_ID")
    s = os.getenv("ALPACA_API_SECRET_KEY")
    if not k or not s:
        raise RuntimeError("Missing Alpaca market-data credentials")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def fetch(sym):
    rows = []
    token = None
    while True:
        q = {
            "timeframe": "1Min",
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
            z = json.loads(r.read().decode())
        rows.extend(z.get("bars", []))
        token = z.get("next_page_token")
        if not token:
            break
        time.sleep(0.28)

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError("No bars for " + sym)

    ts = pd.to_datetime(d["t"], utc=True).dt.tz_convert(TZ)
    d["ts"] = ts
    d["date"] = ts.dt.date
    d["minute"] = ts.dt.hour * 60 + ts.dt.minute
    d = d[(d["minute"] >= 570) & (d["minute"] <= 960)].copy()
    d = d.rename(
        columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        }
    )
    return (
        d[["ts", "date", "minute", "open", "high", "low", "close", "volume"]]
        .drop_duplicates("ts")
        .sort_values("ts")
        .reset_index(drop=True)
    )


def add_signal_features(d):
    x = d.copy()
    x["ema9"] = x.groupby("date")["close"].transform(lambda s: s.ewm(span=9, adjust=False).mean())
    x["ema20"] = x.groupby("date")["close"].transform(lambda s: s.ewm(span=20, adjust=False).mean())
    x["_pv"] = x["close"] * x["volume"]
    x["cum_pv"] = x.groupby("date")["_pv"].cumsum()
    x["cum_v"] = x.groupby("date")["volume"].cumsum().replace(0, np.nan)
    x["vwap"] = x["cum_pv"] / x["cum_v"]
    x["vol_med20"] = x.groupby("date")["volume"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).median()
    )
    x["prev_close"] = x.groupby("date")["close"].shift(1)
    return x.drop(columns=["_pv", "cum_pv", "cum_v"])


def add_exec_features(d):
    x = d.copy()
    prev_close = x.groupby("date")["close"].shift(1)
    tr = pd.concat(
        [
            x["high"] - x["low"],
            (x["high"] - prev_close).abs(),
            (x["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr14"] = tr.groupby(x["date"]).transform(
        lambda s: s.shift(1).rolling(14, min_periods=10).mean()
    )
    x["vol_med20"] = x.groupby("date")["volume"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).median()
    )
    return x


def prepare_family(raw, spec):
    sig = add_signal_features(raw[spec["signal"]]).rename(
        columns={
            "open": "sig_open",
            "high": "sig_high",
            "low": "sig_low",
            "close": "sig_close",
            "volume": "sig_volume",
            "ema9": "sig_ema9",
            "ema20": "sig_ema20",
            "vwap": "sig_vwap",
            "vol_med20": "sig_vol_med20",
            "prev_close": "sig_prev_close",
        }
    )
    bull = add_exec_features(raw[spec["bull"]]).rename(
        columns={
            "open": "bull_open",
            "high": "bull_high",
            "low": "bull_low",
            "close": "bull_close",
            "volume": "bull_volume",
            "atr14": "bull_atr14",
            "vol_med20": "bull_vol_med20",
        }
    )
    bear = add_exec_features(raw[spec["bear"]]).rename(
        columns={
            "open": "bear_open",
            "high": "bear_high",
            "low": "bear_low",
            "close": "bear_close",
            "volume": "bear_volume",
            "atr14": "bear_atr14",
            "vol_med20": "bear_vol_med20",
        }
    )

    sig_cols = [
        "ts", "date", "minute", "sig_open", "sig_high", "sig_low", "sig_close",
        "sig_volume", "sig_ema9", "sig_ema20", "sig_vwap", "sig_vol_med20",
        "sig_prev_close",
    ]
    exec_cols_bull = [
        "ts", "bull_open", "bull_high", "bull_low", "bull_close", "bull_volume",
        "bull_atr14", "bull_vol_med20",
    ]
    exec_cols_bear = [
        "ts", "bear_open", "bear_high", "bear_low", "bear_close", "bear_volume",
        "bear_atr14", "bear_vol_med20",
    ]
    x = sig[sig_cols].merge(bull[exec_cols_bull], on="ts", how="inner")
    x = x.merge(bear[exec_cols_bear], on="ts", how="inner")
    return x.sort_values("ts").reset_index(drop=True)


def day_slices(df, start, end):
    mask = (df["date"] >= start) & (df["date"] <= end)
    z = df.loc[mask]
    return [(d, g.index.to_numpy()) for d, g in z.groupby("date", sort=True)]


def trade_cost(entry_price, exit_price, qty, bps):
    return (bps / 10000.0) * qty * (entry_price + exit_price)


def simulate(df, start, end, or_min, breakout_bps, rvol, stop_atr, target_r, hold, bps=BASE_BPS, return_trades=False):
    eq = START_EQ
    trades = []
    daily_rows = []

    for d, idxs in day_slices(df, start, end):
        if len(idxs) < 60:
            continue
        day = df.loc[idxs].reset_index()
        or_end = 570 + or_min - 1
        opening = day[(day["minute"] >= 570) & (day["minute"] <= or_end)]
        if opening.empty:
            continue
        or_high = float(opening["sig_high"].max())
        or_low = float(opening["sig_low"].min())

        day_start_eq = eq
        ntr = 0
        consec_losses = 0
        cooldown_until = -1
        i = int(opening.index.max()) + 1
        day_trade_pnl = 0.0

        while i < len(day) - 1:
            row = day.iloc[i]
            minute = int(row["minute"])

            if minute >= 930:
                break
            if ntr >= MAX_TRADES_DAY or consec_losses >= MAX_CONSEC_LOSSES:
                break
            if eq / day_start_eq - 1 <= -DAILY_STOP:
                break
            if i <= cooldown_until:
                i += 1
                continue

            required = [
                row["sig_close"], row["sig_prev_close"], row["sig_vwap"],
                row["sig_ema9"], row["sig_ema20"], row["sig_volume"],
                row["sig_vol_med20"],
            ]
            if not np.all(np.isfinite(required)) or row["sig_vol_med20"] <= 0:
                i += 1
                continue

            vol_ok = row["sig_volume"] >= row["sig_vol_med20"] * rvol
            up_level = or_high * (1.0 + breakout_bps / 10000.0)
            dn_level = or_low * (1.0 - breakout_bps / 10000.0)

            bull_signal = (
                vol_ok
                and row["sig_prev_close"] <= up_level
                and row["sig_close"] > up_level
                and row["sig_close"] > row["sig_vwap"]
                and row["sig_ema9"] > row["sig_ema20"]
            )
            bear_signal = (
                vol_ok
                and row["sig_prev_close"] >= dn_level
                and row["sig_close"] < dn_level
                and row["sig_close"] < row["sig_vwap"]
                and row["sig_ema9"] < row["sig_ema20"]
            )
            if not bull_signal and not bear_signal:
                i += 1
                continue

            side = "bull" if bull_signal else "bear"
            k = i + 1
            if k >= len(day):
                break
            entry_row = day.iloc[k]
            entry_price = float(entry_row[f"{side}_open"])
            atr = float(row[f"{side}_atr14"])
            vol_med = float(row[f"{side}_vol_med20"])
            exec_vol = float(row[f"{side}_volume"])

            if not np.isfinite(entry_price) or entry_price <= 0 or not np.isfinite(atr) or atr <= 0:
                i += 1
                continue
            if not np.isfinite(vol_med) or vol_med <= 0 or exec_vol <= 0:
                i += 1
                continue

            stop_dist = max(atr * stop_atr, entry_price * 0.0015)
            risk_dollars = eq * RISK_PER_TRADE
            qty_risk = risk_dollars / stop_dist
            qty_cap = (eq * GROSS_CAP) / entry_price
            qty = min(qty_risk, qty_cap)
            if qty <= 0:
                i += 1
                continue

            stop_price = entry_price - stop_dist
            target_price = entry_price + stop_dist * target_r
            last = min(k + hold - 1, len(day) - 1)
            exit_i = last
            exit_price = float(day.iloc[last][f"{side}_close"])
            reason = "TIME"

            for q in range(k, last + 1):
                rr = day.iloc[q]
                lo = float(rr[f"{side}_low"])
                hi = float(rr[f"{side}_high"])
                if lo <= stop_price:
                    exit_i = q
                    exit_price = stop_price
                    reason = "STOP"
                    break
                if hi >= target_price:
                    exit_i = q
                    exit_price = target_price
                    reason = "TARGET"
                    break
                if int(rr["minute"]) >= 950:
                    exit_i = q
                    exit_price = float(rr[f"{side}_close"])
                    reason = "EOD"
                    break

            gross = qty * (exit_price - entry_price)
            cost = trade_cost(entry_price, exit_price, qty, bps)
            pnl = gross - cost
            eq_before = eq
            eq += pnl
            day_trade_pnl += pnl
            ntr += 1
            consec_losses = consec_losses + 1 if pnl < 0 else 0
            cooldown_until = exit_i + COOLDOWN_MIN

            trades.append(
                {
                    "date": str(d),
                    "family_side": side,
                    "entry_minute": int(day.iloc[k]["minute"]),
                    "exit_minute": int(day.iloc[exit_i]["minute"]),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "qty": float(qty),
                    "gross_pnl": float(gross),
                    "turnover": float(qty * (entry_price + exit_price)),
                    "pnl": float(pnl),
                    "ret": float(pnl / eq_before),
                    "reason": reason,
                }
            )
            i = exit_i + 1

        day_ret = eq / day_start_eq - 1 if day_start_eq else 0.0
        daily_rows.append({"date": str(d), "pnl": float(day_trade_pnl), "ret": float(day_ret), "trades": int(ntr)})

    return summarize(trades, daily_rows, return_trades=return_trades)


def summarize(trades, daily_rows, return_trades=False, stress_bps=None):
    eq = START_EQ
    pnls = []
    rets = []
    for t in trades:
        if stress_bps is None:
            p = float(t["pnl"])
        else:
            p = float(t["gross_pnl"] - (stress_bps / 10000.0) * t["turnover"])
        before = eq
        eq += p
        pnls.append(p)
        rets.append(p / before if before else 0.0)

    p = np.array(pnls, dtype=float)
    r = np.array(rets, dtype=float)
    wins = p[p > 0].sum() if len(p) else 0.0
    losses = -p[p < 0].sum() if len(p) else 0.0
    pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 0.0)

    curve = np.r_[START_EQ, START_EQ + np.cumsum(p)] if len(p) else np.array([START_EQ])
    peak = np.maximum.accumulate(curve)
    maxdd = float(np.max(1 - curve / peak))

    dates = [pd.Timestamp(x["date"]).date() for x in daily_rows]
    day_pnl = {d: 0.0 for d in dates}
    for t, val in zip(trades, p):
        d = pd.Timestamp(t["date"]).date()
        if d in day_pnl:
            day_pnl[d] += float(val)

    day_rets = []
    active_day_rets = []
    running = START_EQ
    monthly_pnl = {}
    monthly_start = {}
    for d in dates:
        if (d.year, d.month) not in monthly_start:
            monthly_start[(d.year, d.month)] = running
        dp = day_pnl[d]
        dr = dp / running if running else 0.0
        day_rets.append(dr)
        if abs(dp) > 1e-12:
            active_day_rets.append(dr)
        monthly_pnl[(d.year, d.month)] = monthly_pnl.get((d.year, d.month), 0.0) + dp
        running += dp

    month_rets = []
    for m in sorted(monthly_pnl):
        start_eq = monthly_start[m]
        month_rets.append(monthly_pnl[m] / start_eq if start_eq else 0.0)

    day_rets = np.array(day_rets, dtype=float)
    active_day_rets = np.array(active_day_rets, dtype=float)
    month_rets = np.array(month_rets, dtype=float)

    out = {
        "trades": int(len(p)),
        "final_equity": round(float(eq), 2),
        "total_return": float(eq / START_EQ - 1),
        "win_rate": float(np.mean(p > 0)) if len(p) else 0.0,
        "expectancy_bps": float(np.mean(r) * 10000) if len(r) else 0.0,
        "profit_factor": float(pf),
        "max_drawdown": maxdd,
        "calendar_days": int(len(day_rets)),
        "active_days": int(len(active_day_rets)),
        "active_day_win_rate": float(np.mean(active_day_rets > 0)) if len(active_day_rets) else 0.0,
        "positive_month_rate": float(np.mean(month_rets > 0)) if len(month_rets) else 0.0,
        "median_month": float(np.median(month_rets)) if len(month_rets) else 0.0,
        "best_month": float(np.max(month_rets)) if len(month_rets) else 0.0,
        "worst_month": float(np.min(month_rets)) if len(month_rets) else 0.0,
        "median_trades_active_day": float(
            np.median([x["trades"] for x in daily_rows if x["trades"] > 0])
        ) if any(x["trades"] > 0 for x in daily_rows) else 0.0,
    }
    out["return_to_drawdown"] = (
        float(out["total_return"] / maxdd)
        if maxdd > 0
        else (99.0 if out["total_return"] > 0 else 0.0)
    )
    if return_trades:
        out["trade_records"] = trades
        out["daily_records"] = daily_rows
    return out


def score(m):
    if m["trades"] < 120:
        return -1e9
    return (
        m["total_return"] * 5
        + min(m["profit_factor"], 3.0)
        + m["expectancy_bps"] / 25
        + m["active_day_win_rate"]
        + m["positive_month_rate"] * 2
        + m["median_month"] * 20
        - m["max_drawdown"] * 5
        + min(m["return_to_drawdown"], 5) * 0.4
    )


def dev_valid(m):
    return (
        m["trades"] >= 120
        and m["expectancy_bps"] > 0
        and m["profit_factor"] > 1.08
        and m["total_return"] > 0
        and m["max_drawdown"] < 0.20
    )


def strip_records(m):
    return {k: v for k, v in m.items() if k not in {"trade_records", "daily_records"}}


def main():
    raw = {}
    for s in SYMS:
        print("Downloading", s, flush=True)
        raw[s] = fetch(s)
        print(s, len(raw[s]), flush=True)

    packs = {}
    for name, spec in FAMILIES.items():
        packs[name] = prepare_family(raw, spec)
        print("Prepared", name, len(packs[name]), flush=True)

    cfgs = []
    combos = list(product(FAMILIES.keys(), OR_MINS, BREAKOUT_BPS, RVOLS, STOP_ATRS, TARGET_RS, HOLDS))
    total = len(combos)
    for n, (family, or_min, bbps, rvol, satr, tr, hold) in enumerate(combos, start=1):
        m = simulate(packs[family], DEV_START, DEV_END, or_min, bbps, rvol, satr, tr, hold)
        cfgs.append(
            {
                "family": family,
                "opening_range_min": or_min,
                "breakout_bps": bbps,
                "signal_rvol": rvol,
                "stop_atr": satr,
                "target_r": tr,
                "hold_min": hold,
                "development": m,
                "development_valid": dev_valid(m),
                "score": score(m),
            }
        )
        if n % 24 == 0 or n == total:
            print("development", n, "/", total, flush=True)

    ranked = sorted(cfgs, key=lambda x: x["score"], reverse=True)
    finalists = [x for x in ranked if x["development_valid"]][:20]
    if not finalists:
        finalists = ranked[:20]

    checked = []
    for x in finalists:
        df = packs[x["family"]]
        args = (
            x["opening_range_min"],
            x["breakout_bps"],
            x["signal_rvol"],
            x["stop_atr"],
            x["target_r"],
            x["hold_min"],
        )
        y24 = simulate(df, Y24_START, Y24_END, *args)
        y25 = simulate(df, Y25_START, Y25_END, *args)
        val = simulate(df, V_START, V_END, *args, return_trades=True)

        core = (
            val["trades"] >= 80
            and val["total_return"] > 0
            and y24["expectancy_bps"] > 0
            and y25["expectancy_bps"] > 0
            and y24["profit_factor"] > 1.05
            and y25["profit_factor"] > 1.05
            and val["max_drawdown"] < 0.15
            and val["positive_month_rate"] >= 0.55
        )
        vscore = score(val) + min(y24["profit_factor"], y25["profit_factor"])
        checked.append(
            {
                **x,
                "validation_2024": y24,
                "validation_2025": y25,
                "validation": val,
                "core_gate": core,
                "validation_score": vscore,
            }
        )

    pool = [x for x in checked if x["core_gate"]] or checked
    best = max(pool, key=lambda x: x["validation_score"])
    df = packs[best["family"]]
    args = (
        best["opening_range_min"],
        best["breakout_bps"],
        best["signal_rvol"],
        best["stop_atr"],
        best["target_r"],
        best["hold_min"],
    )
    y26 = simulate(df, Y26_START, Y26_END, *args)

    base_val_full = best["validation"]
    trade_records = base_val_full["trade_records"]
    daily_records = base_val_full["daily_records"]
    base_val = strip_records(base_val_full)
    stress = {
        str(bps): summarize(trade_records, daily_records, stress_bps=bps)
        for bps in [2, 5, 10]
    }
    high_friction_ok = (
        stress["10"]["expectancy_bps"] > 0
        and stress["10"]["total_return"] > 0
        and stress["10"]["profit_factor"] > 1.0
    )
    gate = bool(
        best["core_gate"]
        and high_friction_ok
        and y26["expectancy_bps"] > 0
        and y26["profit_factor"] > 1.0
        and y26["total_return"] > 0
    )

    top20 = []
    for x in checked:
        top20.append(
            {
                "family": x["family"],
                "opening_range_min": x["opening_range_min"],
                "breakout_bps": x["breakout_bps"],
                "signal_rvol": x["signal_rvol"],
                "stop_atr": x["stop_atr"],
                "target_r": x["target_r"],
                "hold_min": x["hold_min"],
                "development": x["development"],
                "validation_2024": x["validation_2024"],
                "validation_2025": x["validation_2025"],
                "validation_2024_2025": strip_records(x["validation"]),
                "core_gate": x["core_gate"],
                "validation_score": x["validation_score"],
            }
        )
    top20 = sorted(top20, key=lambda x: x["validation_score"], reverse=True)

    spec = FAMILIES[best["family"]]
    result = {
        "phase": "4C",
        "mission": "Long-only leveraged ETF opening-range micro-momentum research; use bull/inverse ETFs to avoid short selling.",
        "starting_equity": START_EQ,
        "risk_per_trade": RISK_PER_TRADE,
        "daily_stop": DAILY_STOP,
        "families": FAMILIES,
        "candidate_count": len(cfgs),
        "development_valid_count": sum(1 for x in cfgs if x["development_valid"]),
        "selected": {
            "family": best["family"],
            "signal_symbol": spec["signal"],
            "bull_symbol": spec["bull"],
            "bear_symbol": spec["bear"],
            "opening_range_min": best["opening_range_min"],
            "breakout_bps": best["breakout_bps"],
            "signal_rvol": best["signal_rvol"],
            "stop_atr": best["stop_atr"],
            "target_r": best["target_r"],
            "hold_min": best["hold_min"],
        },
        "development": best["development"],
        "validation_2024": best["validation_2024"],
        "validation_2025": best["validation_2025"],
        "validation_2024_2025": base_val,
        "check_2026": y26,
        "same_trade_friction_stress": stress,
        "top20_checked": top20,
        "gate": "PASS" if gate else "FAIL",
        "limitations": [
            "IEX-only historical feed on Alpaca Basic can differ from consolidated SIP data.",
            "One-minute bars cannot reconstruct exact bid/ask spreads, queue priority, or intraminute path.",
            "Leveraged and inverse ETFs have path dependence and can move sharply; this research does not imply live suitability.",
            "Stops and targets use conservative stop-first handling when both are touched inside one bar.",
            "Historical results do not guarantee future profit.",
        ],
    }

    with open("phase4c_results.json", "w") as f:
        json.dump(result, f, indent=2)

    m = base_val
    s = result["selected"]
    with open("phase4c_summary.md", "w") as f:
        f.write("# MarketPulse — Phase 4C Long-Only Leveraged ETF Micro-Momentum\n\n")
        f.write(f"**Gate: {result['gate']}**\n\n")
        f.write(
            f"Selected family: **{s['family']}** using **{s['signal_symbol']}** as the signal, "
            f"**{s['bull_symbol']}** for bullish trades, and **{s['bear_symbol']}** for bearish trades.\n\n"
        )
        f.write(
            f"Config: opening range **{s['opening_range_min']} min** · breakout **{s['breakout_bps']} bps** · "
            f"signal RVOL **{s['signal_rvol']}×** · stop **{s['stop_atr']} ATR** · "
            f"target **{s['target_r']}R** · time stop **{s['hold_min']} min**.\n\n"
        )
        f.write(
            f"Development-valid candidates: **{result['development_valid_count']} / {result['candidate_count']}**.\n\n"
        )
        f.write(
            f"2024–2025 validation: **{m['total_return']:.2%}** return, **{m['trades']}** trades, "
            f"**{m['expectancy_bps']:.2f} bps/trade** expectancy, **PF {m['profit_factor']:.2f}**, "
            f"**{m['active_day_win_rate']:.2%} active-day win rate**, **{m['positive_month_rate']:.2%} positive months**, "
            f"median month **{m['median_month']:.2%}**, max DD **{m['max_drawdown']:.2%}**. "
            f"Trade win rate: {m['win_rate']:.2%} (diagnostic only).\n\n"
        )
        f.write(
            f"2026 holdout: **{y26['total_return']:.2%}** return, **{y26['trades']}** trades, "
            f"**{y26['expectancy_bps']:.2f} bps/trade**, PF **{y26['profit_factor']:.2f}**.\n\n"
        )
        f.write(
            f"Same-trade 10 bps friction: **{stress['10']['total_return']:.2%}** return, "
            f"**{stress['10']['expectancy_bps']:.2f} bps/trade**, PF **{stress['10']['profit_factor']:.2f}**.\n\n"
        )
        f.write(
            "This phase is research only. A PASS means the historical gate was met; it is not a profit guarantee.\n"
        )

    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
