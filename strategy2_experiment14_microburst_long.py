import json
import math
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

EXPERIMENT = "S2-E14-MICROBURST-LONG-V1"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False

DATA_BASE = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")
START_EQ = 2500.0
UNIVERSE = ["NVDA", "TSLA", "AMD"]
PILOT_DATES = ["2026-08-19", "2026-08-20", "2026-08-21"]
WINDOW_STARTS_ET = ["10:00", "10:05", "10:10", "10:15", "10:20", "10:25"]
WINDOW_SECONDS = 65
FEATURE_SECONDS = 5
HORIZON_SECONDS = 30
SIGNAL_THRESHOLD = 70
PROFIT_BPS = 15.0
STOP_BPS = 8.0
IMPACT_BPS_PER_SIDE = 1.0
MAX_NOTIONAL_PCT = 0.50
COOLDOWN_SECONDS = 45
MAX_SPREAD_BPS = 5.0


def headers():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca credentials required")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def request_json(url, params, tries=7):
    for attempt in range(tries):
        r = requests.get(url, headers=headers(), params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(1.5 + attempt * 1.5)
            continue
        if r.status_code == 403:
            raise PermissionError(f"Forbidden market-data request: {r.text[:200]}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Repeated rate limit: {url}")


def detect_feed():
    start = "2026-08-21T14:00:00Z"
    end = "2026-08-21T14:00:02Z"
    try:
        request_json(f"{DATA_BASE}/v2/stocks/NVDA/quotes", {"start": start, "end": end, "feed": "sip", "limit": 10})
        return "sip", "SIP_FULL_MARKET"
    except PermissionError:
        request_json(f"{DATA_BASE}/v2/stocks/NVDA/quotes", {"start": start, "end": end, "feed": "iex", "limit": 10})
        return "iex", "IEX_EXPLORATORY_FALLBACK"


def utc_window(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    start = datetime.fromisoformat(day).replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=ET)
    end = start + timedelta(seconds=WINDOW_SECONDS)
    return start.astimezone(ZoneInfo("UTC")).isoformat(), end.astimezone(ZoneInfo("UTC")).isoformat()


def fetch_pages(symbol, kind, start, end, feed):
    rows, token, pages = [], None, 0
    while True:
        params = {"start": start, "end": end, "feed": feed, "limit": 10000, "sort": "asc"}
        if token:
            params["page_token"] = token
        payload = request_json(f"{DATA_BASE}/v2/stocks/{symbol}/{kind}", params)
        rows.extend(payload.get(kind) or [])
        token = payload.get("next_page_token")
        pages += 1
        if not token:
            break
        if pages > 250:
            raise RuntimeError(f"Too many pages for {symbol} {kind} {start}")
    return rows


def to_frames(quotes, trades):
    if not quotes:
        return pd.DataFrame()
    q = pd.DataFrame(quotes).rename(columns={"t":"ts", "bp":"bid", "ap":"ask", "bs":"bid_size", "as":"ask_size"})
    q["ts"] = pd.to_datetime(q["ts"], utc=True)
    q = q[["ts", "bid", "ask", "bid_size", "ask_size"]].sort_values("ts")
    q = q[(q["bid"] > 0) & (q["ask"] > q["bid"]) & (q["bid_size"] >= 0) & (q["ask_size"] >= 0)]
    if q.empty:
        return pd.DataFrame()

    q["sec"] = q["ts"].dt.floor("s")
    q_last = q.groupby("sec", as_index=False).last()
    q_count = q.groupby("sec").size().rename("quote_count")
    q_last = q_last.set_index("sec").join(q_count)

    if trades:
        t = pd.DataFrame(trades).rename(columns={"t":"ts", "p":"price", "s":"size"})
        t["ts"] = pd.to_datetime(t["ts"], utc=True)
        t = t[["ts", "price", "size"]].sort_values("ts")
        aligned = pd.merge_asof(t, q[["ts", "bid", "ask"]], on="ts", direction="backward", tolerance=pd.Timedelta(seconds=2))
        aligned = aligned.dropna(subset=["bid", "ask"])
        mid = (aligned["bid"] + aligned["ask"]) / 2.0
        signed = np.where(aligned["price"] >= aligned["ask"], aligned["size"], np.where(aligned["price"] <= aligned["bid"], -aligned["size"], np.where(aligned["price"] >= mid, aligned["size"], -aligned["size"])))
        aligned["signed_size"] = signed
        aligned["sec"] = aligned["ts"].dt.floor("s")
        tg = aligned.groupby("sec").agg(trade_count=("size", "size"), trade_volume=("size", "sum"), signed_volume=("signed_size", "sum"), last_trade=("price", "last"))
        q_last = q_last.join(tg, how="left")
    else:
        q_last[["trade_count", "trade_volume", "signed_volume", "last_trade"]] = np.nan

    start_sec = q_last.index.min()
    end_sec = q_last.index.max()
    idx = pd.date_range(start_sec, end_sec, freq="1s", tz="UTC")
    df = q_last.reindex(idx)
    for c in ["bid", "ask", "bid_size", "ask_size", "last_trade"]:
        df[c] = df[c].ffill()
    for c in ["quote_count", "trade_count", "trade_volume", "signed_volume"]:
        df[c] = df[c].fillna(0.0)
    df = df.dropna(subset=["bid", "ask", "bid_size", "ask_size"])
    return df


def add_features(df):
    x = df.copy()
    mid = (x["bid"] + x["ask"]) / 2.0
    denom = (x["bid_size"] + x["ask_size"]).replace(0, np.nan)
    x["imbalance"] = (x["bid_size"] - x["ask_size"]) / denom
    micro = (x["ask"] * x["bid_size"] + x["bid"] * x["ask_size"]) / denom
    x["micro_disp_bps"] = (micro / mid - 1.0) * 10000.0
    x["spread_bps"] = (x["ask"] - x["bid"]) / mid * 10000.0
    x["bid_velocity_bps"] = (x["bid"] / x["bid"].shift(FEATURE_SECONDS) - 1.0) * 10000.0
    x["mid_mom_3s_bps"] = (mid / mid.shift(3) - 1.0) * 10000.0

    vol5 = x["trade_volume"].rolling(FEATURE_SECONDS, min_periods=1).sum()
    signed5 = x["signed_volume"].rolling(FEATURE_SECONDS, min_periods=1).sum()
    x["buy_imbalance"] = (signed5 / vol5.replace(0, np.nan)).fillna(0.0)

    tr2 = x["trade_count"].rolling(2, min_periods=1).sum()
    tr20 = x["trade_count"].rolling(20, min_periods=5).sum() / 10.0
    qr2 = x["quote_count"].rolling(2, min_periods=1).sum()
    qr20 = x["quote_count"].rolling(20, min_periods=5).sum() / 10.0
    x["trade_accel"] = tr2 / tr20.replace(0, np.nan)
    x["quote_accel"] = qr2 / qr20.replace(0, np.nan)

    ask_med = x["ask_size"].rolling(FEATURE_SECONDS, min_periods=2).median()
    x["ask_depletion"] = (1.0 - x["ask_size"] / ask_med.replace(0, np.nan)).clip(lower=-2, upper=2).fillna(0.0)

    score = pd.Series(0.0, index=x.index)
    score += np.where(x["imbalance"] >= 0.35, 20, 0)
    score += np.where(x["micro_disp_bps"] >= 0.15, 15, 0)
    score += np.where(x["buy_imbalance"] >= 0.35, 20, 0)
    score += np.where(x["trade_accel"] >= 1.50, 15, 0)
    score += np.where(x["quote_accel"] >= 1.20, 10, 0)
    score += np.where(x["bid_velocity_bps"] > 0.0, 10, 0)
    score += np.where(x["spread_bps"] <= 3.0, 10, 0)
    score += np.where(x["ask_depletion"] >= 0.20, 5, 0)
    x["score"] = score.clip(upper=100)
    return x


def outcome(df, i):
    row = df.iloc[i]
    ask = float(row["ask"])
    if ask <= 0 or float(row["spread_bps"]) > MAX_SPREAD_BPS:
        return None
    entry = ask * (1.0 + IMPACT_BPS_PER_SIDE / 10000.0)
    upper = entry * (1.0 + PROFIT_BPS / 10000.0)
    lower = entry * (1.0 - STOP_BPS / 10000.0)
    future = df.iloc[i+1:i+1+HORIZON_SECONDS]
    if len(future) < HORIZON_SECONDS:
        return None
    exit_px = None
    label = "TIMEOUT"
    held = 0
    for held, (_, r) in enumerate(future.iterrows(), start=1):
        bid = float(r["bid"])
        if bid >= upper:
            exit_px = upper
            label = "WIN"
            break
        if bid <= lower:
            exit_px = lower
            label = "LOSS"
            break
    if exit_px is None:
        exit_px = float(future.iloc[-1]["bid"])
    exit_fill = exit_px * (1.0 - IMPACT_BPS_PER_SIDE / 10000.0)
    ret_bps = (exit_fill / entry - 1.0) * 10000.0
    return {"entry": entry, "exit": exit_fill, "ret_bps": ret_bps, "label": label, "held_seconds": held}


def candidate_indices(df):
    out = []
    last = -10**9
    for i in range(max(FEATURE_SECONDS, 20), len(df) - HORIZON_SECONDS - 1):
        r = df.iloc[i]
        if float(r["score"]) < SIGNAL_THRESHOLD or float(r["spread_bps"]) > MAX_SPREAD_BPS:
            continue
        if i - last < COOLDOWN_SECONDS:
            continue
        out.append(i)
        last = i
    return out


def baseline_indices(df):
    start = max(20, FEATURE_SECONDS)
    return [i for i in range(start, len(df) - HORIZON_SECONDS - 1, 10) if float(df.iloc[i]["spread_bps"]) <= MAX_SPREAD_BPS]


def summarize(trades):
    eq = START_EQ
    curve = [eq]
    gp = gl = 0.0
    wins = losses = timeouts = 0
    pnls = []
    for t in trades:
        notional = eq * MAX_NOTIONAL_PCT
        shares = notional / t["entry"]
        pnl = shares * (t["exit"] - t["entry"])
        eq += pnl
        curve.append(eq)
        pnls.append(pnl)
        if pnl > 0:
            gp += pnl
        elif pnl < 0:
            gl += -pnl
        wins += t["label"] == "WIN"
        losses += t["label"] == "LOSS"
        timeouts += t["label"] == "TIMEOUT"
    s = pd.Series(curve, dtype=float)
    dd = float((1.0 - s / s.cummax()).max() * 100.0) if len(s) else 0.0
    return {
        "trades": len(trades), "wins": wins, "losses": losses, "timeouts": timeouts,
        "barrier_win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "ending_equity": eq, "return_pct": (eq / START_EQ - 1.0) * 100.0,
        "avg_trade_pnl": float(np.mean(pnls)) if pnls else 0.0,
        "profit_factor": gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0),
        "max_drawdown_pct": dd,
        "best_trade": max(pnls, default=0.0), "worst_trade": min(pnls, default=0.0),
        "avg_ret_bps": float(np.mean([t["ret_bps"] for t in trades])) if trades else 0.0,
        "avg_hold_seconds": float(np.mean([t["held_seconds"] for t in trades])) if trades else 0.0,
    }


def main():
    feed, data_quality = detect_feed()
    signal_trades, baseline = [], []
    windows_loaded = 0
    data_counts = {"quotes": 0, "trades": 0}
    feature_examples = []

    for day in PILOT_DATES:
        for symbol in UNIVERSE:
            for hhmm in WINDOW_STARTS_ET:
                start, end = utc_window(day, hhmm)
                quotes = fetch_pages(symbol, "quotes", start, end, feed)
                trades = fetch_pages(symbol, "trades", start, end, feed)
                data_counts["quotes"] += len(quotes)
                data_counts["trades"] += len(trades)
                df = to_frames(quotes, trades)
                if len(df) < 55:
                    continue
                df = add_features(df)
                windows_loaded += 1

                for i in candidate_indices(df):
                    o = outcome(df, i)
                    if o is None:
                        continue
                    r = df.iloc[i]
                    o.update({"symbol": symbol, "day": day, "time": str(df.index[i]), "score": float(r["score"]),
                              "imbalance": float(r["imbalance"]), "micro_disp_bps": float(r["micro_disp_bps"]),
                              "buy_imbalance": float(r["buy_imbalance"]), "trade_accel": float(r["trade_accel"]) if np.isfinite(r["trade_accel"]) else 0.0,
                              "quote_accel": float(r["quote_accel"]) if np.isfinite(r["quote_accel"]) else 0.0,
                              "spread_bps": float(r["spread_bps"])})
                    signal_trades.append(o)
                    if len(feature_examples) < 20:
                        feature_examples.append(o)

                for i in baseline_indices(df):
                    o = outcome(df, i)
                    if o is not None:
                        baseline.append(o)

    sig = summarize(signal_trades)
    base_sum = summarize(baseline)
    baseline_wr = base_sum["barrier_win_rate_pct"]
    edge_pp = sig["barrier_win_rate_pct"] - baseline_wr

    checks = {
        "sip_required_for_valid_pass": feed == "sip",
        "at_least_20_signal_trades": sig["trades"] >= 20,
        "barrier_win_rate_beats_baseline_by_8pp": edge_pp >= 8.0,
        "positive_avg_trade_after_spread_and_impact": sig["avg_trade_pnl"] > 0,
        "profit_factor_at_least_1_25": sig["profit_factor"] >= 1.25,
        "max_drawdown_at_most_3pct": sig["max_drawdown_pct"] <= 3.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": EXPERIMENT, "research_only": RESEARCH_ONLY, "broker_orders": BROKER_ORDERS,
        "long_only": LONG_ONLY, "leverage": LEVERAGE, "feed_used": feed, "data_quality": data_quality,
        "pilot_dates": PILOT_DATES, "universe": UNIVERSE, "windows_et": WINDOW_STARTS_ET,
        "locked_rules": {
            "feature_seconds": FEATURE_SECONDS, "horizon_seconds": HORIZON_SECONDS,
            "signal_threshold": SIGNAL_THRESHOLD, "profit_barrier_bps": PROFIT_BPS, "stop_barrier_bps": STOP_BPS,
            "impact_bps_per_side": IMPACT_BPS_PER_SIDE, "max_notional_pct": MAX_NOTIONAL_PCT * 100.0,
            "cooldown_seconds": COOLDOWN_SECONDS, "max_spread_bps": MAX_SPREAD_BPS,
            "score_components": {
                "quote_imbalance_ge_0_35": 20, "microprice_disp_ge_0_15bps": 15,
                "buy_trade_imbalance_ge_0_35": 20, "trade_accel_ge_1_5": 15,
                "quote_accel_ge_1_2": 10, "bid_velocity_positive": 10,
                "spread_le_3bps": 10, "ask_depletion_ge_0_20": 5,
            },
        },
        "data_counts": data_counts, "windows_loaded": windows_loaded,
        "signal": sig, "baseline": base_sum, "signal_minus_baseline_win_rate_pp": edge_pp,
        "checks": checks, "gate": "PASS" if passed else "FAIL", "activate": False,
        "sample_signal_trades": feature_examples,
        "notes": "IEX fallback is exploratory only; only SIP can satisfy the validity gate.",
    }
    with open("strategy2_experiment14_microburst_long_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 14 — MicroBurst Long v1", "", f"**Gate: {result['gate']}**", "",
        f"Feed: **{feed}** ({data_quality}) | windows loaded: {windows_loaded}",
        f"Quotes/trades processed: {data_counts['quotes']:,} / {data_counts['trades']:,}", "",
        "## Signal",
        f"- Trades {sig['trades']} | barrier wins {sig['wins']} | losses {sig['losses']} | timeouts {sig['timeouts']}",
        f"- Barrier win rate {sig['barrier_win_rate_pct']:.2f}% vs baseline {baseline_wr:.2f}% | edge {edge_pp:+.2f}pp",
        f"- Ending equity ${sig['ending_equity']:.2f} | return {sig['return_pct']:+.3f}% | avg trade ${sig['avg_trade_pnl']:+.3f}",
        f"- PF {sig['profit_factor']:.3f} | DD {sig['max_drawdown_pct']:.3f}% | avg return {sig['avg_ret_bps']:+.2f}bps | avg hold {sig['avg_hold_seconds']:.1f}s", "",
        "## Checks",
    ]
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "Activation remains OFF. This is a small pilot; any promising result requires a larger walk-forward SIP test.", ""]
    with open("strategy2_experiment14_microburst_long_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
