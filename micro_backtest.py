import json
import math
import os
import time
import urllib.parse
import urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
SYMBOLS = ["SPY", "QQQ", "IWM"]
START = "2021-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
STARTING_EQUITY = 100.0
CAPITAL_FRACTION = 0.95
MAX_TRADES_PER_DAY = 3
COOLDOWN_BARS = 3
BASE_FRICTION_BPS = 2.0

TRAIN_END = pd.Timestamp("2023-12-31", tz="America/New_York")
VALIDATION_END = pd.Timestamp("2024-12-31", tz="America/New_York")


def api_headers():
    key = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError(
            "Missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY. "
            "Use paper-account API keys stored as GitHub Actions secrets."
        )
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch_symbol(symbol):
    headers = api_headers()
    rows = []
    token = None
    while True:
        params = {
            "timeframe": "5Min",
            "start": START,
            "end": END,
            "adjustment": "all",
            "feed": "iex",
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            params["page_token"] = token
        url = BASE_URL.format(symbol=symbol) + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode("utf-8"))
        rows.extend(payload.get("bars", []))
        token = payload.get("next_page_token")
        if not token:
            break
        time.sleep(0.05)

    if not rows:
        raise RuntimeError(f"No bars returned for {symbol}")

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = df.set_index("timestamp").sort_index()
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def rsi(series, n=7):
    d = series.diff()
    up = d.clip(lower=0)
    down = -d.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def prepare_symbol(symbol, raw):
    # Regular-hours only. We deliberately avoid extended-hours microstructure in Phase 3.
    x = raw.between_time("09:30", "16:00", inclusive="left").copy()
    x["symbol"] = symbol
    x["session"] = x.index.date

    pieces = []
    for _, d in x.groupby("session", sort=True):
        d = d.copy()
        d["ema9"] = d["close"].ewm(span=9, adjust=False).mean()
        d["ema21"] = d["close"].ewm(span=21, adjust=False).mean()
        d["rsi7"] = rsi(d["close"], 7)
        typical = (d["high"] + d["low"] + d["close"]) / 3.0
        cumvol = d["volume"].cumsum().replace(0, np.nan)
        d["vwap"] = (typical * d["volume"]).cumsum() / cumvol
        d["vol_ma20"] = d["volume"].rolling(20, min_periods=5).mean()
        d["vol_ratio"] = d["volume"] / d["vol_ma20"].replace(0, np.nan)
        pieces.append(d)
    x = pd.concat(pieces).sort_index()

    t = x.index.time
    morning = (t >= pd.Timestamp("09:45").time()) & (t <= pd.Timestamp("11:30").time())
    afternoon = (t >= pd.Timestamp("14:00").time()) & (t <= pd.Timestamp("15:30").time())
    x["time_ok"] = morning | afternoon
    return x


def build_data():
    out = {}
    for s in SYMBOLS:
        print(f"Downloading {s} 5-minute IEX bars...")
        out[s] = prepare_symbol(s, fetch_symbol(s))
        print(f"  {len(out[s]):,} regular-hours bars")
    return out


def signal_mask(df, rsi_level, min_vol_ratio=0.8):
    reclaim_ema = (df["close"].shift(1) <= df["ema9"].shift(1)) & (df["close"] > df["ema9"])
    rsi_reclaim = (df["rsi7"].shift(1) < rsi_level) & (df["rsi7"] >= rsi_level)
    trend = (df["ema9"] > df["ema21"]) & (df["close"] > df["vwap"])
    volume_ok = df["vol_ratio"].fillna(0) >= min_vol_ratio
    raw_signal = reclaim_ema & rsi_reclaim & trend & volume_ok & df["time_ok"]
    # A signal formed on bar t is only actionable at the next bar open.
    return raw_signal.shift(1, fill_value=False)


def candidate_score(row):
    trend = max(0.0, row["ema9"] / row["ema21"] - 1.0)
    vwap = max(0.0, row["close"] / row["vwap"] - 1.0)
    rsi_component = max(0.0, (row["rsi7"] - 50.0) / 100.0)
    return trend * 4.0 + vwap * 2.0 + rsi_component


def simulate(data, start_date, end_date, tp_pct, sl_pct, max_hold_bars, rsi_level, friction_bps):
    start_ts = pd.Timestamp(start_date, tz="America/New_York")
    end_ts = pd.Timestamp(end_date, tz="America/New_York") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

    frames = {}
    signals = {}
    all_times = set()
    for s, full in data.items():
        f = full[(full.index >= start_ts) & (full.index <= end_ts)].copy()
        frames[s] = f
        signals[s] = signal_mask(f, rsi_level)
        all_times.update(f.index)
    timeline = sorted(all_times)

    equity = STARTING_EQUITY
    peak = equity
    max_dd = 0.0
    position = None
    trades = []
    trades_by_day = {}
    cooldown_until = None
    one_way = friction_bps / 10000.0

    for ts in timeline:
        day = ts.date()
        trades_by_day.setdefault(day, 0)

        # Manage open trade first using this bar's OHLC for the held symbol.
        if position is not None:
            s = position["symbol"]
            f = frames[s]
            if ts in f.index:
                bar = f.loc[ts]
                position["bars"] += 1
                stop_price = position["entry"] * (1.0 - sl_pct)
                target_price = position["entry"] * (1.0 + tp_pct)

                exit_reason = None
                raw_exit = None
                # Conservative convention if both target and stop are touched in one bar.
                if bar["low"] <= stop_price:
                    raw_exit = stop_price
                    exit_reason = "STOP"
                elif bar["high"] >= target_price:
                    raw_exit = target_price
                    exit_reason = "TARGET"
                elif position["bars"] >= max_hold_bars:
                    raw_exit = bar["close"]
                    exit_reason = "TIME"
                elif ts.time() >= pd.Timestamp("15:50").time():
                    raw_exit = bar["close"]
                    exit_reason = "EOD"

                if raw_exit is not None:
                    exit_price = raw_exit * (1.0 - one_way)
                    pnl = position["qty"] * (exit_price - position["entry"])
                    ret = pnl / position["equity_before"]
                    equity += pnl
                    peak = max(peak, equity)
                    max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
                    trades.append(
                        {
                            "symbol": s,
                            "entry_time": position["entry_time"],
                            "exit_time": ts,
                            "entry": position["entry"],
                            "exit": exit_price,
                            "pnl": pnl,
                            "return": ret,
                            "reason": exit_reason,
                            "bars": position["bars"],
                            "equity": equity,
                        }
                    )
                    trades_by_day[day] += 1
                    cooldown_until = ts + pd.Timedelta(minutes=COOLDOWN_BARS * 5)
                    position = None
            continue

        if cooldown_until is not None and ts <= cooldown_until:
            continue
        if trades_by_day[day] >= MAX_TRADES_PER_DAY:
            continue

        candidates = []
        for s, f in frames.items():
            if ts not in f.index:
                continue
            loc = f.index.get_loc(ts)
            if isinstance(loc, slice) or loc <= 0:
                continue
            if not bool(signals[s].iloc[loc]):
                continue
            row = f.iloc[loc - 1]  # rank using the completed signal bar, not the entry bar
            entry_bar = f.iloc[loc]
            if not bool(row["time_ok"]):
                continue
            candidates.append((candidate_score(row), s, entry_bar))

        if candidates:
            _, s, entry_bar = max(candidates, key=lambda z: z[0])
            entry_price = float(entry_bar["open"]) * (1.0 + one_way)
            notional = equity * CAPITAL_FRACTION
            qty = notional / entry_price
            position = {
                "symbol": s,
                "entry_time": ts,
                "entry": entry_price,
                "qty": qty,
                "equity_before": equity,
                "bars": 0,
            }

    # Force-close any remaining position at the final available close.
    if position is not None:
        s = position["symbol"]
        f = frames[s]
        tail = f[f.index >= position["entry_time"]]
        if len(tail):
            ts = tail.index[-1]
            raw_exit = float(tail.iloc[-1]["close"])
            exit_price = raw_exit * (1.0 - one_way)
            pnl = position["qty"] * (exit_price - position["entry"])
            ret = pnl / position["equity_before"]
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
            trades.append(
                {
                    "symbol": s,
                    "entry_time": position["entry_time"],
                    "exit_time": ts,
                    "entry": position["entry"],
                    "exit": exit_price,
                    "pnl": pnl,
                    "return": ret,
                    "reason": "FINAL",
                    "bars": position["bars"],
                    "equity": equity,
                }
            )

    return metrics(trades, equity, max_dd)


def metrics(trades, final_equity, max_dd):
    if not trades:
        return {
            "trades": 0,
            "final_equity": STARTING_EQUITY,
            "total_return": 0.0,
            "win_rate": 0.0,
            "expectancy_bps": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "daily_sharpe": 0.0,
            "positive_days": 0.0,
        }

    t = pd.DataFrame(trades)
    wins = t[t["pnl"] > 0]["pnl"].sum()
    losses = -t[t["pnl"] < 0]["pnl"].sum()
    win_rate = float((t["pnl"] > 0).mean())
    expectancy_bps = float(t["return"].mean() * 10000)
    profit_factor = float(wins / losses) if losses > 0 else 99.0

    t["date"] = pd.to_datetime(t["exit_time"]).dt.date
    daily = t.groupby("date")["return"].sum()
    if len(daily) > 1 and daily.std(ddof=0) > 0:
        sharpe = float(daily.mean() / daily.std(ddof=0) * math.sqrt(252))
    else:
        sharpe = 0.0

    return {
        "trades": int(len(t)),
        "final_equity": float(final_equity),
        "total_return": float(final_equity / STARTING_EQUITY - 1.0),
        "win_rate": win_rate,
        "expectancy_bps": expectancy_bps,
        "profit_factor": profit_factor,
        "max_drawdown": float(max_dd),
        "daily_sharpe": sharpe,
        "positive_days": float((daily > 0).mean()) if len(daily) else 0.0,
    }


def selection_score(m):
    if m["trades"] < 150 or m["expectancy_bps"] <= 0:
        return -1e9
    dd_penalty = max(0.0, m["max_drawdown"] - 0.12) * 10.0
    return (
        m["daily_sharpe"]
        + 1.5 * m["win_rate"]
        + 0.25 * min(m["profit_factor"], 3.0)
        + 0.05 * min(m["expectancy_bps"], 10.0)
        - dd_penalty
    )


def fmt(m):
    return {
        **m,
        "final_equity": round(m["final_equity"], 2),
        "total_return": round(m["total_return"], 6),
        "win_rate": round(m["win_rate"], 6),
        "expectancy_bps": round(m["expectancy_bps"], 4),
        "profit_factor": round(m["profit_factor"], 4),
        "max_drawdown": round(m["max_drawdown"], 6),
        "daily_sharpe": round(m["daily_sharpe"], 4),
        "positive_days": round(m["positive_days"], 6),
    }


def main():
    data = build_data()
    grid = list(product(
        [0.0020, 0.0030, 0.0040],  # take profit
        [0.0015, 0.0020, 0.0025],  # stop
        [4, 6],                    # max bars: 20 / 30 min
        [48, 50],                  # RSI reclaim
    ))

    train_rows = []
    for i, (tp, sl, hold, rsi_level) in enumerate(grid, 1):
        m = simulate(
            data,
            "2021-01-01",
            "2023-12-31",
            tp,
            sl,
            hold,
            rsi_level,
            BASE_FRICTION_BPS,
        )
        train_rows.append(
            {
                "tp": tp,
                "sl": sl,
                "hold": hold,
                "rsi": rsi_level,
                "score": selection_score(m),
                "metrics": m,
            }
        )
        print(f"Train {i}/{len(grid)}: TP={tp:.3%} SL={sl:.3%} hold={hold} RSI={rsi_level} score={selection_score(m):.3f}")

    # Keep the strongest training candidates, then choose using 2024 validation only.
    finalists = sorted(train_rows, key=lambda x: x["score"], reverse=True)[:8]
    validation_rows = []
    for c in finalists:
        m = simulate(
            data,
            "2024-01-01",
            "2024-12-31",
            c["tp"],
            c["sl"],
            c["hold"],
            c["rsi"],
            BASE_FRICTION_BPS,
        )
        validation_rows.append({**c, "validation": m, "validation_score": selection_score(m)})

    selected = max(validation_rows, key=lambda x: x["validation_score"])
    tp, sl, hold, rsi_level = selected["tp"], selected["sl"], selected["hold"], selected["rsi"]

    holdout = simulate(
        data,
        "2025-01-01",
        "2026-07-31",
        tp,
        sl,
        hold,
        rsi_level,
        BASE_FRICTION_BPS,
    )

    friction_stress = {}
    for bps in [2.0, 4.0, 6.0, 10.0]:
        friction_stress[str(bps)] = simulate(
            data,
            "2025-01-01",
            "2026-07-31",
            tp,
            sl,
            hold,
            rsi_level,
            bps,
        )

    # Nearby parameter stability: selected setup must not be a single isolated lucky point.
    neighbor_tests = []
    for tp2 in sorted(set([max(0.0015, tp - 0.0005), tp, tp + 0.0005])):
        for sl2 in sorted(set([max(0.0010, sl - 0.0005), sl, sl + 0.0005])):
            m = simulate(data, "2025-01-01", "2026-07-31", tp2, sl2, hold, rsi_level, BASE_FRICTION_BPS)
            neighbor_tests.append({"tp": tp2, "sl": sl2, "metrics": m})

    neighbors_positive = sum(1 for x in neighbor_tests if x["metrics"]["expectancy_bps"] > 0)
    gate = (
        holdout["trades"] >= 100
        and holdout["expectancy_bps"] > 0
        and holdout["profit_factor"] > 1.05
        and holdout["max_drawdown"] < 0.15
        and friction_stress["6.0"]["expectancy_bps"] > 0
        and neighbors_positive >= math.ceil(len(neighbor_tests) * 0.65)
    )

    result = {
        "data": {
            "source": "Alpaca Market Data API / IEX feed",
            "symbols": SYMBOLS,
            "timeframe": "5Min",
            "range": "2021-01-01 through 2026-07-31",
            "train": "2021-2023",
            "validation": "2024",
            "untouched_holdout": "2025-2026-07-31",
        },
        "selected": {
            "take_profit_pct": tp,
            "stop_loss_pct": sl,
            "max_hold_bars": hold,
            "max_hold_minutes": hold * 5,
            "rsi_reclaim": rsi_level,
            "capital_fraction": CAPITAL_FRACTION,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "cooldown_minutes": COOLDOWN_BARS * 5,
        },
        "train": fmt(selected["metrics"]),
        "validation": fmt(selected["validation"]),
        "holdout": fmt(holdout),
        "friction_stress": {k: fmt(v) for k, v in friction_stress.items()},
        "neighbor_positive": neighbors_positive,
        "neighbor_total": len(neighbor_tests),
        "gate": "PASS" if gate else "FAIL",
        "warning": "Backtests cannot guarantee future profit. Paper trading is required before any live-money connection.",
    }

    with open("micro_backtest_results.json", "w") as f:
        json.dump(result, f, indent=2)

    md = f"""# MarketPulse Micro — Phase 3 Intraday Validation\n\n"
    md += f"**Data:** Alpaca IEX 5-minute bars, 2021-01-01 through 2026-07-31  \n"
    md += f"**Universe:** {', '.join(SYMBOLS)}  \n"
    md += f"**Starting capital model:** $100, 1x buying power, long-only  \n"
    md += f"**Base friction:** {BASE_FRICTION_BPS:.1f} bps one-way  \n\n"
    md += "## Selected micro setup\n\n"
    md += f"- Take profit: **{tp:.2%}**\n"
    md += f"- Stop loss: **{sl:.2%}**\n"
    md += f"- Maximum hold: **{hold * 5} minutes**\n"
    md += f"- RSI reclaim: **{rsi_level}**\n"
    md += f"- Max trades/day: **{MAX_TRADES_PER_DAY}**\n"
    md += f"- Cooldown: **{COOLDOWN_BARS * 5} minutes**\n\n"
    md += "## Results\n\n"
    md += "| Period | Trades | Return | Win rate | Expectancy | Profit factor | Daily Sharpe | Max DD | Positive days |\n"
    md += "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    for label, m in [("Train 2021-2023", result["train"]), ("Validation 2024", result["validation"]), ("Untouched holdout 2025-2026-07", result["holdout"])]:
        md += f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | {m['expectancy_bps']:.2f} bps/trade | {m['profit_factor']:.2f} | {m['daily_sharpe']:.2f} | {m['max_drawdown']:.2%} | {m['positive_days']:.2%} |\n"
    md += "\n## Friction stress — untouched holdout\n\n"
    md += "| One-way friction | Expectancy | Return | Profit factor |\n|---:|---:|---:|---:|\n"
    for bps, m in result["friction_stress"].items():
        md += f"| {float(bps):.0f} bps | {m['expectancy_bps']:.2f} bps/trade | {m['total_return']:.2%} | {m['profit_factor']:.2f} |\n"
    md += f"\n**Nearby parameter combinations profitable on holdout:** {neighbors_positive}/{len(neighbor_tests)}  \n"
    md += f"**Phase 3 historical gate:** {result['gate']}\n\n"
    md += "## Important\n\nThis is a historical simulation, not a guarantee or promise of profit. Micro strategies are especially sensitive to spread, slippage, fills, data-feed differences, taxes, and market regime changes. The exact locked strategy must pass live paper trading before any real-money automation is considered.\n"

    with open("micro_backtest_summary.md", "w") as f:
        f.write(md)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
