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
BASE_BPS = 2.0
ENTRY_CUTOFF = 780  # 1:00 p.m. ET
MIN_EXIT_MINUTE = 0

FAMILIES = {
    "NASDAQ": {"signal": "QQQ", "bull": "TQQQ", "bear": "SQQQ"},
    "SP500": {"signal": "SPY", "bull": "SPXL", "bear": "SPXS"},
}
SYMS = sorted({s for family in FAMILIES.values() for s in family.values()})

IMPULSE_MINS = [30, 45, 60]
IMPULSE_BPS = [30.0, 50.0, 75.0]
RANGE_MULTS = [1.0, 1.25]
PULLBACK_BPS = [5.0, 12.0]
STOP_ATRS = [0.8, 1.1]
TARGET_RS = [2.0, 3.0]
HOLDS = [30, 60]

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
        query = {
            "timeframe": "1Min",
            "start": START,
            "end": END,
            "adjustment": "all",
            "feed": "iex",
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            query["page_token"] = token
        url = BASE.format(symbol=sym) + "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers=headers())
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode())
        rows.extend(payload.get("bars", []))
        token = payload.get("next_page_token")
        if not token:
            break
        time.sleep(0.28)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("No bars returned for " + sym)

    ts = pd.to_datetime(frame["t"], utc=True).dt.tz_convert(TZ)
    frame["ts"] = ts
    frame["date"] = ts.dt.date
    frame["minute"] = ts.dt.hour * 60 + ts.dt.minute
    frame = frame[(frame["minute"] >= 570) & (frame["minute"] <= 960)].copy()
    frame = frame.rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
    })
    return (
        frame[["ts", "date", "minute", "open", "high", "low", "close", "volume"]]
        .drop_duplicates("ts")
        .sort_values("ts")
        .reset_index(drop=True)
    )


def add_signal_features(frame):
    x = frame.copy()
    grouped = x.groupby("date", sort=False)
    x["ema9"] = grouped["close"].transform(lambda s: s.ewm(span=9, adjust=False).mean())
    x["ema20"] = grouped["close"].transform(lambda s: s.ewm(span=20, adjust=False).mean())
    x["_pv"] = x["close"] * x["volume"]
    x["cum_pv"] = x.groupby("date", sort=False)["_pv"].cumsum()
    x["cum_v"] = x.groupby("date", sort=False)["volume"].cumsum().replace(0, np.nan)
    x["vwap"] = x["cum_pv"] / x["cum_v"]
    x["vol_med20"] = x.groupby("date", sort=False)["volume"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).median()
    )
    x["prev_high"] = x.groupby("date", sort=False)["high"].shift(1)
    x["prev_low"] = x.groupby("date", sort=False)["low"].shift(1)
    return x.drop(columns=["_pv", "cum_pv", "cum_v"])


def add_exec_features(frame):
    x = frame.copy()
    prev_close = x.groupby("date", sort=False)["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["atr14"] = tr.groupby(x["date"]).transform(
        lambda s: s.shift(1).rolling(14, min_periods=10).mean()
    )
    return x


def prepare_family(raw, spec):
    sig = add_signal_features(raw[spec["signal"]]).rename(columns={
        "open": "sig_open", "high": "sig_high", "low": "sig_low",
        "close": "sig_close", "volume": "sig_volume", "ema9": "sig_ema9",
        "ema20": "sig_ema20", "vwap": "sig_vwap", "vol_med20": "sig_vol_med20",
        "prev_high": "sig_prev_high", "prev_low": "sig_prev_low",
    })
    bull = add_exec_features(raw[spec["bull"]]).rename(columns={
        "open": "bull_open", "high": "bull_high", "low": "bull_low",
        "close": "bull_close", "volume": "bull_volume", "atr14": "bull_atr14",
    })
    bear = add_exec_features(raw[spec["bear"]]).rename(columns={
        "open": "bear_open", "high": "bear_high", "low": "bear_low",
        "close": "bear_close", "volume": "bear_volume", "atr14": "bear_atr14",
    })

    sig_cols = [
        "ts", "date", "minute", "sig_open", "sig_high", "sig_low", "sig_close",
        "sig_volume", "sig_ema9", "sig_ema20", "sig_vwap", "sig_vol_med20",
        "sig_prev_high", "sig_prev_low",
    ]
    bull_cols = [
        "ts", "bull_open", "bull_high", "bull_low", "bull_close", "bull_volume", "bull_atr14"
    ]
    bear_cols = [
        "ts", "bear_open", "bear_high", "bear_low", "bear_close", "bear_volume", "bear_atr14"
    ]
    x = sig[sig_cols].merge(bull[bull_cols], on="ts", how="inner")
    x = x.merge(bear[bear_cols], on="ts", how="inner")
    return x.sort_values("ts").reset_index(drop=True)


def make_day_records(frame):
    days = []
    for date, g in frame.groupby("date", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 120:
            continue
        rec = {
            "date": date,
            "minute": g["minute"].to_numpy(np.int32),
            "sig_open": g["sig_open"].to_numpy(float),
            "sig_high": g["sig_high"].to_numpy(float),
            "sig_low": g["sig_low"].to_numpy(float),
            "sig_close": g["sig_close"].to_numpy(float),
            "sig_volume": g["sig_volume"].to_numpy(float),
            "sig_ema9": g["sig_ema9"].to_numpy(float),
            "sig_ema20": g["sig_ema20"].to_numpy(float),
            "sig_vwap": g["sig_vwap"].to_numpy(float),
            "sig_vol_med20": g["sig_vol_med20"].to_numpy(float),
            "sig_prev_high": g["sig_prev_high"].to_numpy(float),
            "sig_prev_low": g["sig_prev_low"].to_numpy(float),
            "bull_open": g["bull_open"].to_numpy(float),
            "bull_high": g["bull_high"].to_numpy(float),
            "bull_low": g["bull_low"].to_numpy(float),
            "bull_close": g["bull_close"].to_numpy(float),
            "bull_atr14": g["bull_atr14"].to_numpy(float),
            "bear_open": g["bear_open"].to_numpy(float),
            "bear_high": g["bear_high"].to_numpy(float),
            "bear_low": g["bear_low"].to_numpy(float),
            "bear_close": g["bear_close"].to_numpy(float),
            "bear_atr14": g["bear_atr14"].to_numpy(float),
            "or_range": {},
            "or_baseline": {},
            "impulse_idx": {},
        }
        days.append(rec)

    for impulse_min in IMPULSE_MINS:
        rolling_ranges = []
        for rec in days:
            minutes = rec["minute"]
            target_minute = 570 + impulse_min - 1
            eligible = np.flatnonzero((minutes >= 570) & (minutes <= target_minute))
            if eligible.size < max(10, impulse_min // 2):
                rec["impulse_idx"][impulse_min] = None
                rec["or_range"][impulse_min] = np.nan
                rec["or_baseline"][impulse_min] = np.nan
                continue
            idx = int(eligible[-1])
            op = float(rec["sig_open"][eligible[0]])
            hi = float(np.nanmax(rec["sig_high"][eligible]))
            lo = float(np.nanmin(rec["sig_low"][eligible]))
            rng = (hi - lo) / op if op > 0 else np.nan
            baseline = np.nan
            if len(rolling_ranges) >= 10:
                baseline = float(np.nanmedian(rolling_ranges[-20:]))
            rec["impulse_idx"][impulse_min] = idx
            rec["or_range"][impulse_min] = rng
            rec["or_baseline"][impulse_min] = baseline
            if np.isfinite(rng):
                rolling_ranges.append(rng)
    return days


def in_period(date, start, end):
    return start <= date <= end


def find_entry(rec, impulse_min, impulse_bps, range_mult, pullback_bps):
    j0 = rec["impulse_idx"].get(impulse_min)
    if j0 is None or j0 + 2 >= len(rec["minute"]):
        return None

    baseline = rec["or_baseline"].get(impulse_min, np.nan)
    opening_range = rec["or_range"].get(impulse_min, np.nan)
    if not np.isfinite(baseline) or baseline <= 0 or not np.isfinite(opening_range):
        return None
    if opening_range < baseline * range_mult:
        return None

    first = np.flatnonzero(rec["minute"] >= 570)
    if first.size == 0:
        return None
    open_px = float(rec["sig_open"][first[0]])
    close0 = float(rec["sig_close"][j0])
    if not np.isfinite(open_px) or open_px <= 0 or not np.isfinite(close0):
        return None
    impulse = (close0 / open_px - 1.0) * 10000.0

    ema9 = float(rec["sig_ema9"][j0])
    ema20 = float(rec["sig_ema20"][j0])
    vwap = float(rec["sig_vwap"][j0])
    opening_eligible = np.flatnonzero((rec["minute"] >= 570) & (rec["minute"] <= rec["minute"][j0]))
    hi = float(np.nanmax(rec["sig_high"][opening_eligible]))
    lo = float(np.nanmin(rec["sig_low"][opening_eligible]))
    span = hi - lo
    close_location = (close0 - lo) / span if span > 0 else 0.5

    bull = (
        impulse >= impulse_bps
        and np.isfinite(ema9) and np.isfinite(ema20) and np.isfinite(vwap)
        and close0 > vwap and ema9 > ema20 and close_location >= 0.60
    )
    bear = (
        impulse <= -impulse_bps
        and np.isfinite(ema9) and np.isfinite(ema20) and np.isfinite(vwap)
        and close0 < vwap and ema9 < ema20 and close_location <= 0.40
    )
    if not bull and not bear:
        return None

    direction = "bull" if bull else "bear"
    pulled_back = False
    pb = pullback_bps / 10000.0
    vwap_fail = 10.0 / 10000.0

    for i in range(j0 + 1, len(rec["minute"]) - 1):
        minute = int(rec["minute"][i])
        if minute > ENTRY_CUTOFF:
            break

        c = float(rec["sig_close"][i])
        e9 = float(rec["sig_ema9"][i])
        vw = float(rec["sig_vwap"][i])
        vol = float(rec["sig_volume"][i])
        volmed = float(rec["sig_vol_med20"][i])
        ph = float(rec["sig_prev_high"][i])
        pl = float(rec["sig_prev_low"][i])
        if not np.all(np.isfinite([c, e9, vw, vol, volmed, ph, pl])) or volmed <= 0:
            continue

        if direction == "bull":
            if c < vw * (1.0 - vwap_fail):
                return None
            if not pulled_back and c <= e9 * (1.0 + pb) and c >= vw * (1.0 - 0.0005):
                pulled_back = True
                continue
            reclaim = pulled_back and c > e9 and c > ph and vol >= volmed
        else:
            if c > vw * (1.0 + vwap_fail):
                return None
            if not pulled_back and c >= e9 * (1.0 - pb) and c <= vw * (1.0 + 0.0005):
                pulled_back = True
                continue
            reclaim = pulled_back and c < e9 and c < pl and vol >= volmed

        if reclaim:
            entry_i = i + 1
            if entry_i >= len(rec["minute"]):
                return None
            return direction, entry_i, impulse, opening_range / baseline
    return None


def trade_cost(entry_price, exit_price, qty, bps):
    return (bps / 10000.0) * qty * (entry_price + exit_price)


def simulate(days, start, end, config, bps=BASE_BPS, return_trades=False):
    eq = START_EQ
    peak = eq
    max_dd = 0.0
    trades = []
    daily = []

    for rec in days:
        date = rec["date"]
        if not in_period(date, start, end):
            continue

        setup = find_entry(
            rec,
            config["impulse_min"],
            config["impulse_bps"],
            config["range_mult"],
            config["pullback_bps"],
        )
        if setup is None:
            daily.append((date, eq, 0.0, 0))
            continue

        direction, entry_i, impulse, range_ratio = setup
        prefix = "bull" if direction == "bull" else "bear"
        entry = float(rec[prefix + "_open"][entry_i])
        atr = float(rec[prefix + "_atr14"][entry_i])
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(atr) or atr <= 0:
            daily.append((date, eq, 0.0, 0))
            continue

        stop_dist = atr * config["stop_atr"]
        if stop_dist <= 0 or stop_dist >= entry * 0.10:
            daily.append((date, eq, 0.0, 0))
            continue

        risk_dollars = eq * RISK_PER_TRADE
        qty_risk = int(risk_dollars // stop_dist)
        qty_cap = int((eq * GROSS_CAP) // entry)
        qty = min(qty_risk, qty_cap)
        if qty < 1:
            daily.append((date, eq, 0.0, 0))
            continue

        stop = entry - stop_dist
        target = entry + stop_dist * config["target_r"]
        last_i = min(len(rec["minute"]) - 1, entry_i + config["hold"])
        exit_price = None
        exit_reason = None
        exit_i = None

        for k in range(entry_i, last_i + 1):
            low = float(rec[prefix + "_low"][k])
            high = float(rec[prefix + "_high"][k])
            if not np.isfinite(low) or not np.isfinite(high):
                continue
            stop_hit = low <= stop
            target_hit = high >= target
            if stop_hit and target_hit:
                exit_price = stop
                exit_reason = "stop_same_bar"
                exit_i = k
                break
            if stop_hit:
                exit_price = stop
                exit_reason = "stop"
                exit_i = k
                break
            if target_hit:
                exit_price = target
                exit_reason = "target"
                exit_i = k
                break

        if exit_price is None:
            exit_i = last_i
            exit_price = float(rec[prefix + "_close"][exit_i])
            if not np.isfinite(exit_price):
                daily.append((date, eq, 0.0, 0))
                continue
            exit_reason = "time"

        gross_pnl = qty * (exit_price - entry)
        cost = trade_cost(entry, exit_price, qty, bps)
        net_pnl = gross_pnl - cost
        notional = qty * entry
        net_bps = (net_pnl / notional) * 10000.0 if notional > 0 else 0.0
        eq_before = eq
        eq += net_pnl
        peak = max(peak, eq)
        dd = 1.0 - eq / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

        trade = {
            "date": str(date),
            "direction": direction,
            "entry_minute": int(rec["minute"][entry_i]),
            "exit_minute": int(rec["minute"][exit_i]),
            "entry": entry,
            "exit": exit_price,
            "qty": qty,
            "gross_pnl": gross_pnl,
            "cost": cost,
            "net_pnl": net_pnl,
            "net_bps": net_bps,
            "exit_reason": exit_reason,
            "impulse_bps": impulse,
            "range_ratio": range_ratio,
            "equity_before": eq_before,
            "equity_after": eq,
        }
        trades.append(trade)
        daily.append((date, eq, net_pnl, 1))

    metrics = summarize(eq, max_dd, trades, daily)
    if return_trades:
        return metrics, trades
    return metrics


def summarize(final_eq, max_dd, trades, daily):
    n = len(trades)
    if n == 0:
        return {
            "final_equity": final_eq,
            "total_return_pct": (final_eq / START_EQ - 1.0) * 100.0,
            "trades": 0,
            "win_rate_pct": 0.0,
            "expectancy_bps": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": max_dd * 100.0,
            "positive_month_rate_pct": 0.0,
            "median_month_pct": 0.0,
            "best_month_pct": 0.0,
            "worst_month_pct": 0.0,
            "return_over_dd": 0.0,
        }

    pnls = np.array([t["net_pnl"] for t in trades], dtype=float)
    bps = np.array([t["net_bps"] for t in trades], dtype=float)
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    pf = float(wins / losses) if losses > 0 else 999.0
    win_rate = float((pnls > 0).mean() * 100.0)

    monthly = {}
    for t in trades:
        month = t["date"][:7]
        monthly[month] = monthly.get(month, 0.0) + t["net_pnl"]
    month_returns = np.array([p / START_EQ * 100.0 for p in monthly.values()], dtype=float)
    positive_month_rate = float((month_returns > 0).mean() * 100.0) if month_returns.size else 0.0
    total_return = (final_eq / START_EQ - 1.0) * 100.0
    return_over_dd = total_return / (max_dd * 100.0) if max_dd > 0 else 999.0

    return {
        "final_equity": final_eq,
        "total_return_pct": total_return,
        "trades": n,
        "win_rate_pct": win_rate,
        "expectancy_bps": float(np.mean(bps)),
        "profit_factor": pf,
        "max_drawdown_pct": max_dd * 100.0,
        "positive_month_rate_pct": positive_month_rate,
        "median_month_pct": float(np.median(month_returns)) if month_returns.size else 0.0,
        "best_month_pct": float(np.max(month_returns)) if month_returns.size else 0.0,
        "worst_month_pct": float(np.min(month_returns)) if month_returns.size else 0.0,
        "return_over_dd": return_over_dd,
    }


def dev_valid(base, stress10):
    return (
        base["trades"] >= 60
        and base["total_return_pct"] > 0
        and base["expectancy_bps"] >= 18.0
        and base["profit_factor"] > 1.10
        and base["max_drawdown_pct"] <= 8.0
        and base["positive_month_rate_pct"] >= 50.0
        and stress10["total_return_pct"] > 0
        and stress10["expectancy_bps"] > 0
        and stress10["profit_factor"] > 1.05
    )


def score_candidate(base, stress10):
    trade_weight = min(base["trades"] / 60.0, 1.0)
    dd_penalty = max(base["max_drawdown_pct"], 0.25)
    return (
        stress10["expectancy_bps"] * trade_weight
        + 0.30 * stress10["total_return_pct"]
        + 0.10 * base["positive_month_rate_pct"]
        + 0.20 * (base["total_return_pct"] / dd_penalty)
    )


def validation_gate(dev_is_valid, y24, y25, val, val10, y26, y26_10):
    checks = {
        "development_valid": bool(dev_is_valid),
        "2024_min_trades": y24["trades"] >= 10,
        "2024_profitable": y24["total_return_pct"] > 0 and y24["expectancy_bps"] > 0 and y24["profit_factor"] > 1.0,
        "2025_min_trades": y25["trades"] >= 10,
        "2025_profitable": y25["total_return_pct"] > 0 and y25["expectancy_bps"] > 0 and y25["profit_factor"] > 1.0,
        "validation_min_trades": val["trades"] >= 30,
        "validation_drawdown": val["max_drawdown_pct"] <= 8.0,
        "validation_positive_months": val["positive_month_rate_pct"] >= 50.0,
        "validation_10bps_profitable": val10["total_return_pct"] > 0 and val10["expectancy_bps"] > 0 and val10["profit_factor"] > 1.05,
        "2026_min_trades": y26["trades"] >= 8,
        "2026_profitable": y26["total_return_pct"] > 0 and y26["expectancy_bps"] > 0 and y26["profit_factor"] > 1.0,
        "2026_10bps_profitable": y26_10["total_return_pct"] > 0 and y26_10["expectancy_bps"] > 0 and y26_10["profit_factor"] > 1.0,
    }
    return all(checks.values()), checks


def fmt(m):
    return (
        f"{m['trades']} trades | return {m['total_return_pct']:+.4f}% | "
        f"expectancy {m['expectancy_bps']:+.2f} bps/trade | PF {m['profit_factor']:.3f} | "
        f"max DD {m['max_drawdown_pct']:.3f}% | positive months {m['positive_month_rate_pct']:.2f}%"
    )


def main():
    print("Phase 4D: downloading adjusted 1-minute IEX data...")
    raw = {}
    for sym in SYMS:
        print("  fetching", sym, flush=True)
        raw[sym] = fetch(sym)

    prepared = {}
    for family, spec in FAMILIES.items():
        print("Preparing", family, flush=True)
        prepared[family] = make_day_records(prepare_family(raw, spec))

    candidates = []
    configs = list(product(
        IMPULSE_MINS, IMPULSE_BPS, RANGE_MULTS, PULLBACK_BPS, STOP_ATRS, TARGET_RS, HOLDS
    ))
    total = len(configs) * len(FAMILIES)
    print("Testing", total, "development candidates", flush=True)

    tested = 0
    for family in FAMILIES:
        days = prepared[family]
        for impulse_min, impulse_bps, range_mult, pullback_bps, stop_atr, target_r, hold in configs:
            config = {
                "family": family,
                "signal": FAMILIES[family]["signal"],
                "bull": FAMILIES[family]["bull"],
                "bear": FAMILIES[family]["bear"],
                "impulse_min": impulse_min,
                "impulse_bps": impulse_bps,
                "range_mult": range_mult,
                "pullback_bps": pullback_bps,
                "stop_atr": stop_atr,
                "target_r": target_r,
                "hold": hold,
            }
            base = simulate(days, DEV_START, DEV_END, config, bps=BASE_BPS)
            stress10 = simulate(days, DEV_START, DEV_END, config, bps=10.0)
            valid = dev_valid(base, stress10)
            candidates.append({
                "config": config,
                "development": base,
                "development_10bps": stress10,
                "development_valid": valid,
                "score": score_candidate(base, stress10),
            })
            tested += 1
            if tested % 100 == 0:
                print(f"  {tested}/{total}", flush=True)

    valid_candidates = [c for c in candidates if c["development_valid"]]
    selection_pool = valid_candidates if valid_candidates else [
        c for c in candidates if c["development"]["trades"] >= 30
    ]
    if not selection_pool:
        selection_pool = candidates
    selected = max(selection_pool, key=lambda c: c["score"])
    config = selected["config"]
    days = prepared[config["family"]]

    dev, dev_trades = simulate(days, DEV_START, DEV_END, config, bps=BASE_BPS, return_trades=True)
    dev10 = simulate(days, DEV_START, DEV_END, config, bps=10.0)
    y24 = simulate(days, Y24_START, Y24_END, config, bps=BASE_BPS)
    y25 = simulate(days, Y25_START, Y25_END, config, bps=BASE_BPS)
    val, val_trades = simulate(days, V_START, V_END, config, bps=BASE_BPS, return_trades=True)
    val5 = simulate(days, V_START, V_END, config, bps=5.0)
    val10 = simulate(days, V_START, V_END, config, bps=10.0)
    y26, y26_trades = simulate(days, Y26_START, Y26_END, config, bps=BASE_BPS, return_trades=True)
    y26_5 = simulate(days, Y26_START, Y26_END, config, bps=5.0)
    y26_10 = simulate(days, Y26_START, Y26_END, config, bps=10.0)

    gate, checks = validation_gate(selected["development_valid"], y24, y25, val, val10, y26, y26_10)

    result = {
        "phase": "4D",
        "strategy": "Regime-filtered impulse / first-pullback / reclaim continuation using long leveraged ETFs",
        "starting_equity": START_EQ,
        "base_friction_bps_per_side": BASE_BPS,
        "gate": "PASS" if gate else "FAIL",
        "candidate_count": len(candidates),
        "development_valid_candidates": len(valid_candidates),
        "selected": config,
        "development": dev,
        "development_10bps": dev10,
        "validation_2024": y24,
        "validation_2025": y25,
        "validation_2024_2025": val,
        "validation_friction": {"2bps": val, "5bps": val5, "10bps": val10},
        "holdout_2026": y26,
        "holdout_2026_friction": {"2bps": y26, "5bps": y26_5, "10bps": y26_10},
        "gate_checks": checks,
        "selected_trades": {
            "development": dev_trades,
            "validation_2024_2025": val_trades,
            "holdout_2026": y26_trades,
        },
        "candidate_summaries": candidates,
        "research_note": "Research only. A PASS is not a guarantee of future profits and does not authorize live trading.",
    }

    with open("phase4d_results.json", "w") as f:
        json.dump(result, f, indent=2)

    failed = [name for name, ok in checks.items() if not ok]
    lines = [
        "# MarketPulse Phase 4D — High-Expectancy Regime Continuation",
        "",
        f"**Gate: {'PASS' if gate else 'FAIL'}**",
        "",
        "## Objective",
        "Trade fewer, stronger intraday moves and require the selected strategy to remain profitable under a 10 bps-per-side friction stress test.",
        "",
        "## Selected setup",
        f"- Family: **{config['family']}**",
        f"- Signal: **{config['signal']}**",
        f"- Bull ETF: **{config['bull']}**",
        f"- Bear ETF: **{config['bear']}**",
        f"- Impulse window: **{config['impulse_min']} min**",
        f"- Minimum impulse: **{config['impulse_bps']} bps**",
        f"- Opening-range expansion: **{config['range_mult']}× prior 20-day median**",
        f"- EMA9 pullback tolerance: **{config['pullback_bps']} bps**",
        f"- Stop: **{config['stop_atr']} ATR**",
        f"- Target: **{config['target_r']}R**",
        f"- Time stop: **{config['hold']} min**",
        "- Maximum frequency: **1 trade/day**",
        "",
        "## Development 2021–2023",
        f"- Valid candidates: **{len(valid_candidates)} / {len(candidates)}**",
        f"- Base 2 bps: {fmt(dev)}",
        f"- Stress 10 bps: {fmt(dev10)}",
        "",
        "## 2024 validation",
        f"- {fmt(y24)}",
        "",
        "## 2025 validation",
        f"- {fmt(y25)}",
        "",
        "## 2024–2025 combined validation",
        f"- Base 2 bps: {fmt(val)}",
        f"- 5 bps stress: {fmt(val5)}",
        f"- 10 bps stress: {fmt(val10)}",
        "",
        "## 2026 holdout (Jan–Jul)",
        f"- Base 2 bps: {fmt(y26)}",
        f"- 5 bps stress: {fmt(y26_5)}",
        f"- 10 bps stress: {fmt(y26_10)}",
        "",
        "## Gate checks",
    ]
    for name, ok in checks.items():
        lines.append(f"- {'PASS' if ok else 'FAIL'} — {name}")
    if failed:
        lines.extend(["", "## Failure reasons", *[f"- {x}" for x in failed]])
    lines.extend([
        "",
        "## Research status",
        "Research only. Even a PASS would not guarantee future profits. Do not promote to live trading without a separate paper-trading gate.",
    ])
    with open("phase4d_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    print("Selected:", config)
    print("Development:", fmt(dev))
    print("Validation:", fmt(val))
    print("Validation 10bps:", fmt(val10))
    print("2026:", fmt(y26))
    print("2026 10bps:", fmt(y26_10))
    print("GATE:", "PASS" if gate else "FAIL")
    if failed:
        print("Failed checks:", ", ".join(failed))


if __name__ == "__main__":
    main()
