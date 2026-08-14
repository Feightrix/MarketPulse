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
START = "2020-11-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
TIMEFRAME = "5Min"

START_EQ = 2500.0
RISK_PER_TRADE = 0.005
GROSS_CAP = 0.95
BASE_BPS = 2.0

FAMILIES = {
    "NASDAQ": {"signal": "QQQ", "bull": "TQQQ", "bear": "SQQQ"},
    "SP500": {"signal": "SPY", "bull": "SPXL", "bear": "SPXS"},
}
SYMS = sorted({s for f in FAMILIES.values() for s in f.values()})

IMPULSE_MINS = [60, 90, 120]
IMPULSE_BPS = [35.0, 55.0, 75.0]
RANGE_MULTS = [1.0, 1.25]
VOLUME_MULTS = [0.9, 1.1]
TREND_LOOKBACKS = [5, 10]
STOP_BPS = [45.0, 65.0, 85.0]
TARGET_BPS = [140.0, 200.0, 260.0]

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
        time.sleep(0.20)

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError("No bars for " + sym)
    ts = pd.to_datetime(d["t"], utc=True).dt.tz_convert(TZ)
    d["ts"] = ts
    d["date"] = ts.dt.date
    d["minute"] = ts.dt.hour * 60 + ts.dt.minute
    d = d[(d["minute"] >= 570) & (d["minute"] <= 960)].copy()
    d = d.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return (
        d[["ts", "date", "minute", "open", "high", "low", "close", "volume"]]
        .drop_duplicates("ts")
        .sort_values("ts")
        .reset_index(drop=True)
    )


def prepare_family(raw, spec):
    sig = raw[spec["signal"]].rename(columns={
        "open": "sig_open", "high": "sig_high", "low": "sig_low",
        "close": "sig_close", "volume": "sig_volume",
    })
    bull = raw[spec["bull"]].rename(columns={
        "open": "bull_open", "high": "bull_high", "low": "bull_low",
        "close": "bull_close", "volume": "bull_volume",
    })
    bear = raw[spec["bear"]].rename(columns={
        "open": "bear_open", "high": "bear_high", "low": "bear_low",
        "close": "bear_close", "volume": "bear_volume",
    })
    sig_cols = ["ts", "date", "minute", "sig_open", "sig_high", "sig_low", "sig_close", "sig_volume"]
    bull_cols = ["ts", "bull_open", "bull_high", "bull_low", "bull_close", "bull_volume"]
    bear_cols = ["ts", "bear_open", "bear_high", "bear_low", "bear_close", "bear_volume"]
    x = sig[sig_cols].merge(bull[bull_cols], on="ts", how="inner")
    x = x.merge(bear[bear_cols], on="ts", how="inner")
    return x.sort_values("ts").reset_index(drop=True)


def make_day_records(frame):
    days = []
    prior_closes = []
    range_hist = {m: [] for m in IMPULSE_MINS}
    volume_hist = {m: [] for m in IMPULSE_MINS}

    for date, g in frame.groupby("date", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 60:
            continue
        minute = g["minute"].to_numpy(np.int32)
        sig_open = g["sig_open"].to_numpy(float)
        sig_high = g["sig_high"].to_numpy(float)
        sig_low = g["sig_low"].to_numpy(float)
        sig_close = g["sig_close"].to_numpy(float)
        sig_volume = g["sig_volume"].to_numpy(float)

        first = np.flatnonzero(minute >= 570)
        if first.size == 0:
            continue
        day_open = float(sig_open[first[0]])
        day_close = float(sig_close[-1])

        trend = {}
        for lb in TREND_LOOKBACKS:
            if len(prior_closes) >= max(20, lb):
                prev_close = float(prior_closes[-1])
                ema20 = float(pd.Series(prior_closes[-20:]).ewm(span=20, adjust=False).mean().iloc[-1])
                anchor = float(prior_closes[-lb])
                ret = prev_close / anchor - 1.0 if anchor > 0 else np.nan
                trend[lb] = {"prev_close": prev_close, "ema20": ema20, "ret": ret}
            else:
                trend[lb] = {"prev_close": np.nan, "ema20": np.nan, "ret": np.nan}

        rec = {
            "date": date,
            "minute": minute,
            "sig_open": sig_open,
            "sig_high": sig_high,
            "sig_low": sig_low,
            "sig_close": sig_close,
            "sig_volume": sig_volume,
            "bull_open": g["bull_open"].to_numpy(float),
            "bull_high": g["bull_high"].to_numpy(float),
            "bull_low": g["bull_low"].to_numpy(float),
            "bull_close": g["bull_close"].to_numpy(float),
            "bear_open": g["bear_open"].to_numpy(float),
            "bear_high": g["bear_high"].to_numpy(float),
            "bear_low": g["bear_low"].to_numpy(float),
            "bear_close": g["bear_close"].to_numpy(float),
            "day_open": day_open,
            "trend": trend,
            "impulse": {},
        }

        for m in IMPULSE_MINS:
            end_minute = 570 + m - 5
            idx = np.flatnonzero((minute >= 570) & (minute <= end_minute))
            if idx.size < max(6, m // 10):
                rec["impulse"][m] = None
                continue
            j = int(idx[-1])
            hi = float(np.nanmax(sig_high[idx]))
            lo = float(np.nanmin(sig_low[idx]))
            close0 = float(sig_close[j])
            rng = (hi - lo) / day_open if day_open > 0 else np.nan
            vol = float(np.nansum(sig_volume[idx]))
            range_base = float(np.nanmedian(range_hist[m][-20:])) if len(range_hist[m]) >= 10 else np.nan
            volume_base = float(np.nanmedian(volume_hist[m][-20:])) if len(volume_hist[m]) >= 10 else np.nan
            span = hi - lo
            location = (close0 - lo) / span if span > 0 else 0.5
            typical = (sig_high[idx] + sig_low[idx] + sig_close[idx]) / 3.0
            denom = float(np.nansum(sig_volume[idx]))
            vwap = float(np.nansum(typical * sig_volume[idx]) / denom) if denom > 0 else np.nan
            rec["impulse"][m] = {
                "idx": j,
                "close": close0,
                "range": rng,
                "range_base": range_base,
                "volume": vol,
                "volume_base": volume_base,
                "location": location,
                "vwap": vwap,
            }
            if np.isfinite(rng):
                range_hist[m].append(rng)
            if np.isfinite(vol):
                volume_hist[m].append(vol)

        days.append(rec)
        if np.isfinite(day_close) and day_close > 0:
            prior_closes.append(day_close)

    return days


def setup_for_day(rec, config):
    info = rec["impulse"].get(config["impulse_min"])
    if not info:
        return None
    vals = [
        rec["day_open"], info["close"], info["range"], info["range_base"],
        info["volume"], info["volume_base"], info["location"], info["vwap"],
    ]
    if not np.all(np.isfinite(vals)) or rec["day_open"] <= 0 or info["range_base"] <= 0 or info["volume_base"] <= 0:
        return None
    if info["range"] < info["range_base"] * config["range_mult"]:
        return None
    if info["volume"] < info["volume_base"] * config["volume_mult"]:
        return None

    impulse_bps = (info["close"] / rec["day_open"] - 1.0) * 10000.0
    tr = rec["trend"].get(config["trend_lb"], {})
    prev_close = tr.get("prev_close", np.nan)
    ema20 = tr.get("ema20", np.nan)
    trend_ret = tr.get("ret", np.nan)
    if not np.all(np.isfinite([prev_close, ema20, trend_ret])):
        return None

    bull = (
        impulse_bps >= config["impulse_bps"]
        and info["location"] >= 0.72
        and info["close"] > info["vwap"]
        and prev_close > ema20
        and trend_ret > 0
    )
    bear = (
        impulse_bps <= -config["impulse_bps"]
        and info["location"] <= 0.28
        and info["close"] < info["vwap"]
        and prev_close < ema20
        and trend_ret < 0
    )
    if not bull and not bear:
        return None
    direction = "bull" if bull else "bear"
    entry_i = info["idx"] + 1
    if entry_i >= len(rec["minute"]):
        return None
    if int(rec["minute"][entry_i]) >= 840:
        return None
    return direction, entry_i, impulse_bps, info["range"] / info["range_base"], info["volume"] / info["volume_base"]


def trade_cost(entry, exit_price, qty, bps):
    return (bps / 10000.0) * qty * (entry + exit_price)


def summarize(final_eq, max_dd, trades):
    n = len(trades)
    total_return = (final_eq / START_EQ - 1.0) * 100.0
    if n == 0:
        return {
            "final_equity": final_eq, "total_return_pct": total_return, "trades": 0,
            "win_rate_pct": 0.0, "expectancy_bps": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": max_dd * 100.0, "positive_month_rate_pct": 0.0,
            "median_month_pct": 0.0, "best_month_pct": 0.0, "worst_month_pct": 0.0,
            "return_over_dd": 0.0,
        }
    pnls = np.array([t["net_pnl"] for t in trades], dtype=float)
    bps_arr = np.array([t["net_bps"] for t in trades], dtype=float)
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    pf = float(wins / losses) if losses > 0 else 999.0
    monthly = {}
    for t in trades:
        month = t["date"][:7]
        monthly[month] = monthly.get(month, 0.0) + t["net_pnl"]
    month_returns = np.array([x / START_EQ * 100.0 for x in monthly.values()], dtype=float)
    return {
        "final_equity": final_eq,
        "total_return_pct": total_return,
        "trades": n,
        "win_rate_pct": float((pnls > 0).mean() * 100.0),
        "expectancy_bps": float(np.mean(bps_arr)),
        "profit_factor": pf,
        "max_drawdown_pct": max_dd * 100.0,
        "positive_month_rate_pct": float((month_returns > 0).mean() * 100.0) if month_returns.size else 0.0,
        "median_month_pct": float(np.median(month_returns)) if month_returns.size else 0.0,
        "best_month_pct": float(np.max(month_returns)) if month_returns.size else 0.0,
        "worst_month_pct": float(np.min(month_returns)) if month_returns.size else 0.0,
        "return_over_dd": total_return / (max_dd * 100.0) if max_dd > 0 else 999.0,
    }


def simulate(days, start, end, config, bps=BASE_BPS, return_trades=False):
    eq = START_EQ
    peak = eq
    max_dd = 0.0
    trades = []

    for rec in days:
        if not (start <= rec["date"] <= end):
            continue
        setup = setup_for_day(rec, config)
        if not setup:
            continue
        direction, entry_i, impulse, range_ratio, volume_ratio = setup
        prefix = "bull" if direction == "bull" else "bear"
        entry = float(rec[prefix + "_open"][entry_i])
        if not np.isfinite(entry) or entry <= 0:
            continue

        stop_dist = entry * config["stop_bps"] / 10000.0
        target_dist = entry * config["target_bps"] / 10000.0
        risk_dollars = eq * RISK_PER_TRADE
        qty_risk = int(risk_dollars // stop_dist) if stop_dist > 0 else 0
        qty_cap = int((eq * GROSS_CAP) // entry)
        qty = min(qty_risk, qty_cap)
        if qty < 1:
            continue

        stop = entry - stop_dist
        target = entry + target_dist
        exit_price = None
        exit_i = None
        exit_reason = None
        last_i = len(rec["minute"]) - 1
        # Exit by the final regular-session bar; same-bar ambiguity is resolved pessimistically.
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
        cost = trade_cost(entry, exit_price, qty, bps)
        net_pnl = gross_pnl - cost
        notional = qty * entry
        net_bps = net_pnl / notional * 10000.0 if notional > 0 else 0.0
        eq_before = eq
        eq += net_pnl
        peak = max(peak, eq)
        dd = 1.0 - eq / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        trades.append({
            "date": str(rec["date"]), "direction": direction,
            "entry_minute": int(rec["minute"][entry_i]), "exit_minute": int(rec["minute"][exit_i]),
            "entry": entry, "exit": exit_price, "qty": qty,
            "gross_pnl": gross_pnl, "cost": cost, "net_pnl": net_pnl, "net_bps": net_bps,
            "exit_reason": exit_reason, "impulse_bps": impulse,
            "range_ratio": range_ratio, "volume_ratio": volume_ratio,
            "equity_before": eq_before, "equity_after": eq,
        })

    metrics = summarize(eq, max_dd, trades)
    return (metrics, trades) if return_trades else metrics


def dev_valid(base, stress10):
    return (
        base["trades"] >= 45
        and base["total_return_pct"] > 0
        and base["expectancy_bps"] >= 25.0
        and base["profit_factor"] > 1.15
        and base["max_drawdown_pct"] <= 8.0
        and base["positive_month_rate_pct"] >= 50.0
        and stress10["total_return_pct"] > 0
        and stress10["expectancy_bps"] >= 5.0
        and stress10["profit_factor"] > 1.08
    )


def score(base, stress10):
    trade_weight = min(base["trades"] / 45.0, 1.0)
    dd = max(base["max_drawdown_pct"], 0.25)
    return (
        stress10["expectancy_bps"] * trade_weight
        + 0.50 * stress10["total_return_pct"]
        + 0.10 * base["positive_month_rate_pct"]
        + 0.15 * base["total_return_pct"] / dd
    )


def gate(dev_ok, y24, y25, val, val10, y26, y26_10):
    checks = {
        "development_valid": bool(dev_ok),
        "2024_min_trades": y24["trades"] >= 8,
        "2024_profitable": y24["total_return_pct"] > 0 and y24["profit_factor"] > 1.0,
        "2025_min_trades": y25["trades"] >= 8,
        "2025_profitable": y25["total_return_pct"] > 0 and y25["profit_factor"] > 1.0,
        "validation_min_trades": val["trades"] >= 20,
        "validation_profitable": val["total_return_pct"] > 0 and val["expectancy_bps"] > 0 and val["profit_factor"] > 1.05,
        "validation_drawdown": val["max_drawdown_pct"] <= 8.0,
        "validation_positive_months": val["positive_month_rate_pct"] >= 50.0,
        "validation_10bps_profitable": val10["total_return_pct"] > 0 and val10["expectancy_bps"] > 0 and val10["profit_factor"] > 1.02,
        "2026_min_trades": y26["trades"] >= 5,
        "2026_profitable": y26["total_return_pct"] > 0 and y26["profit_factor"] > 1.0,
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
    print("Fetching 5-minute Alpaca data for Phase 4E...")
    raw = {sym: fetch(sym) for sym in SYMS}
    family_days = {name: make_day_records(prepare_family(raw, spec)) for name, spec in FAMILIES.items()}

    candidates = []
    configs = list(product(
        IMPULSE_MINS, IMPULSE_BPS, RANGE_MULTS, VOLUME_MULTS,
        TREND_LOOKBACKS, STOP_BPS, TARGET_BPS,
    ))
    total = len(configs) * len(FAMILIES)
    print(f"Testing {total} Phase 4E candidates...")

    for family, days in family_days.items():
        for vals in configs:
            config = dict(zip(
                ["impulse_min", "impulse_bps", "range_mult", "volume_mult", "trend_lb", "stop_bps", "target_bps"],
                vals,
            ))
            base = simulate(days, DEV_START, DEV_END, config, BASE_BPS)
            stress10 = simulate(days, DEV_START, DEV_END, config, 10.0)
            ok = dev_valid(base, stress10)
            candidates.append({
                "family": family, "config": config, "development": base,
                "development_10bps": stress10, "dev_valid": ok,
                "score": score(base, stress10),
            })

    valid = [c for c in candidates if c["dev_valid"]]
    pool = valid if valid else candidates
    selected = max(pool, key=lambda c: c["score"])
    family = selected["family"]
    config = selected["config"]
    days = family_days[family]

    y24 = simulate(days, Y24_START, Y24_END, config, BASE_BPS)
    y25 = simulate(days, Y25_START, Y25_END, config, BASE_BPS)
    val = simulate(days, V_START, V_END, config, BASE_BPS)
    val5 = simulate(days, V_START, V_END, config, 5.0)
    val10, val_trades = simulate(days, V_START, V_END, config, 10.0, True)
    y26 = simulate(days, Y26_START, Y26_END, config, BASE_BPS)
    y26_5 = simulate(days, Y26_START, Y26_END, config, 5.0)
    y26_10, y26_trades = simulate(days, Y26_START, Y26_END, config, 10.0, True)

    checks, passed = gate(selected["dev_valid"], y24, y25, val, val10, y26, y26_10)
    result = {
        "phase": "4E",
        "strategy": "trend-day continuation",
        "starting_equity": START_EQ,
        "base_friction_bps_per_side": BASE_BPS,
        "candidate_count": total,
        "valid_candidate_count": len(valid),
        "selected": selected,
        "validation_2024": y24,
        "validation_2025": y25,
        "validation_combined_2024_2025": val,
        "validation_5bps": val5,
        "validation_10bps": val10,
        "holdout_2026": y26,
        "holdout_2026_5bps": y26_5,
        "holdout_2026_10bps": y26_10,
        "validation_10bps_trades": val_trades,
        "holdout_2026_10bps_trades": y26_trades,
        "gate_checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "research_only": True,
        "note": "A PASS is not a guarantee of future profit and requires a separate paper-trading gate before live use.",
    }
    with open("phase4e_results.json", "w") as f:
        json.dump(result, f, indent=2)

    spec = FAMILIES[family]
    fail_reasons = [k for k, v in checks.items() if not v]
    summary = f"""# MarketPulse Phase 4E — Trend-Day Continuation\n\n**Gate: {result['gate']}**\n\n## Objective\nCapture fewer, materially larger intraday moves so that a 10 bps-per-side friction assumption is small relative to expected trade profit.\n\n## Selected setup\n- Family: **{family}**\n- Signal: **{spec['signal']}**\n- Bull ETF: **{spec['bull']}**\n- Bear ETF: **{spec['bear']}**\n- Impulse window: **{config['impulse_min']} min**\n- Minimum impulse: **{config['impulse_bps']} bps**\n- Opening range expansion: **{config['range_mult']}× prior 20-day median**\n- Opening volume: **{config['volume_mult']}× prior 20-day median**\n- Trend lookback: **{config['trend_lb']} sessions**\n- Stop: **{config['stop_bps']} bps on execution ETF**\n- Target: **{config['target_bps']} bps on execution ETF**\n- Maximum frequency: **1 trade/day**\n\n## Development 2021–2023\n- Valid candidates: **{len(valid)} / {total}**\n- Base 2 bps: {fmt(selected['development'])}\n- Stress 10 bps: {fmt(selected['development_10bps'])}\n\n## 2024 validation\n- {fmt(y24)}\n\n## 2025 validation\n- {fmt(y25)}\n\n## 2024–2025 combined validation\n- Base 2 bps: {fmt(val)}\n- 5 bps stress: {fmt(val5)}\n- 10 bps stress: {fmt(val10)}\n\n## 2026 holdout (Jan–Jul)\n- Base 2 bps: {fmt(y26)}\n- 5 bps stress: {fmt(y26_5)}\n- 10 bps stress: {fmt(y26_10)}\n\n## Gate checks\n"""
    for k, v in checks.items():
        summary += f"- {'PASS' if v else 'FAIL'} — {k}\n"
    summary += "\n## Failure reasons\n"
    summary += "- None\n" if not fail_reasons else "".join(f"- {x}\n" for x in fail_reasons)
    summary += "\n## Research status\nResearch only. Even a PASS would not guarantee future profits. Do not promote to live trading without a separate paper-trading gate.\n"
    with open("phase4e_summary.md", "w") as f:
        f.write(summary)

    print(summary)


if __name__ == "__main__":
    main()
