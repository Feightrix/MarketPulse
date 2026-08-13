import json, math, os, time, urllib.parse, urllib.request
from itertools import product

import numpy as np
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
SYMBOLS = ["SPY", "QQQ", "IWM"]
START = "2021-01-01T00:00:00Z"
END = "2026-08-01T00:00:00Z"
START_EQ = 100.0
CAPITAL = 0.95
MAX_TRADES = 3
COOLDOWN_MIN = 15
BASE_FRICTION_BPS = 2.0
TZ = "America/New_York"
RSI_LEVELS = [48, 50]


def headers():
    key = os.getenv("ALPACA_API_KEY_ID")
    sec = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch(symbol):
    out, token, h = [], None, headers()
    while True:
        p = {
            "timeframe": "5Min",
            "start": START,
            "end": END,
            "adjustment": "all",
            "feed": "iex",
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            p["page_token"] = token
        url = BASE.format(symbol=symbol) + "?" + urllib.parse.urlencode(p)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            js = json.loads(r.read().decode())
        out.extend(js.get("bars", []))
        token = js.get("next_page_token")
        if not token:
            break
        time.sleep(0.03)
    if not out:
        raise RuntimeError(f"No bars for {symbol}")
    d = pd.DataFrame(out)
    d["ts"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert(TZ)
    d = (
        d.set_index("ts")
        .sort_index()
        .rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    )
    return d[["open", "high", "low", "close", "volume"]].astype(float)


def rsi(s, n=7):
    delta = s.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def prep(symbol, d):
    d = d.between_time("09:30", "16:00", inclusive="left").copy()
    d["symbol"] = symbol
    d["session"] = d.index.date
    chunks = []
    for _, x in d.groupby("session", sort=True):
        x = x.copy()
        x["ema9"] = x.close.ewm(span=9, adjust=False).mean()
        x["ema21"] = x.close.ewm(span=21, adjust=False).mean()
        x["rsi7"] = rsi(x.close)
        typ = (x.high + x.low + x.close) / 3
        cv = x.volume.cumsum().replace(0, np.nan)
        x["vwap"] = (typ * x.volume).cumsum() / cv
        x["vr"] = x.volume / x.volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
        chunks.append(x)
    d = pd.concat(chunks).sort_index()
    mins = d.index.hour * 60 + d.index.minute
    d["time_ok"] = ((mins >= 9 * 60 + 45) & (mins <= 11 * 60 + 30)) | (
        (mins >= 14 * 60) & (mins <= 15 * 60 + 30)
    )
    return d


def load_data():
    data = {}
    for s in SYMBOLS:
        print("Downloading", s, flush=True)
        data[s] = prep(s, fetch(s))
        print(s, len(data[s]), "bars", flush=True)
    return data


def signal_series(d, rsi_level):
    reclaim = (d.close.shift(1) <= d.ema9.shift(1)) & (d.close > d.ema9)
    rr = (d.rsi7.shift(1) < rsi_level) & (d.rsi7 >= rsi_level)
    trend = (d.ema9 > d.ema21) & (d.close > d.vwap)
    vol = d.vr.fillna(0) >= 0.8
    return (reclaim & rr & trend & vol & d.time_ok).shift(1, fill_value=False)


def score_series(d):
    trend = np.maximum(0.0, d.ema9 / d.ema21 - 1.0) * 4.0
    vwap = np.maximum(0.0, d.close / d.vwap - 1.0) * 2.0
    r = np.maximum(0.0, (d.rsi7 - 50.0) / 100.0)
    return (trend + vwap + r).shift(1)


def pack_period(data, start, end):
    st = pd.Timestamp(start, tz=TZ)
    en = pd.Timestamp(end, tz=TZ) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    frames = {s: d[(d.index >= st) & (d.index <= en)].copy() for s, d in data.items()}
    idx = frames[SYMBOLS[0]].index
    for s in SYMBOLS[1:]:
        idx = idx.union(frames[s].index)
    idx = idx.sort_values()

    pack = {
        "idx": idx,
        "ts_ns": idx.asi8,
        "day_ns": idx.normalize().asi8,
        "minute": (idx.hour * 60 + idx.minute).to_numpy(dtype=np.int16),
        "symbols": {},
    }
    for s in SYMBOLS:
        d = frames[s]
        a = d.reindex(idx)
        sym = {
            "valid": a["open"].notna().to_numpy(),
            "open": a["open"].to_numpy(dtype=float),
            "high": a["high"].to_numpy(dtype=float),
            "low": a["low"].to_numpy(dtype=float),
            "close": a["close"].to_numpy(dtype=float),
            "score": score_series(d).reindex(idx).to_numpy(dtype=float),
            "signals": {},
        }
        for rr in RSI_LEVELS:
            sym["signals"][rr] = (
                signal_series(d, rr).reindex(idx, fill_value=False).to_numpy(dtype=bool)
            )
        pack["symbols"][s] = sym
    return pack


def metrics(trades, eq, maxdd):
    if not trades:
        return {
            "trades": 0,
            "final_equity": START_EQ,
            "total_return": 0,
            "win_rate": 0,
            "expectancy_bps": 0,
            "profit_factor": 0,
            "max_drawdown": 0,
            "daily_sharpe": 0,
            "positive_days": 0,
        }
    t = pd.DataFrame(trades)
    wins = t.loc[t.pnl > 0, "pnl"].sum()
    losses = -t.loc[t.pnl < 0, "pnl"].sum()
    daily = t.groupby("day")["ret"].sum()
    sd = daily.std(ddof=0)
    sharpe = float(daily.mean() / sd * math.sqrt(252)) if len(daily) > 1 and sd > 0 else 0
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
    }


def sim(pack, tp, sl, hold, rsi_level, friction_bps):
    ts_ns = pack["ts_ns"]
    day_ns = pack["day_ns"]
    minute = pack["minute"]
    syms = pack["symbols"]
    fr = friction_bps / 10000.0
    cooldown_ns = COOLDOWN_MIN * 60 * 1_000_000_000

    eq = START_EQ
    peak = eq
    maxdd = 0.0
    pos = None
    trades = []
    perday = {}
    cooldown_until = -1

    for i in range(len(ts_ns)):
        now = int(ts_ns[i])
        day = int(day_ns[i])
        perday.setdefault(day, 0)

        if pos is not None:
            arr = syms[pos["s"]]
            if arr["valid"][i]:
                pos["bars"] += 1
                stop = pos["entry"] * (1 - sl)
                target = pos["entry"] * (1 + tp)
                raw = None
                why = None
                lo = arr["low"][i]
                hi = arr["high"][i]
                if lo <= stop:
                    raw, why = stop, "STOP"
                elif hi >= target:
                    raw, why = target, "TARGET"
                elif pos["bars"] >= hold:
                    raw, why = arr["close"][i], "TIME"
                elif minute[i] >= 15 * 60 + 50:
                    raw, why = arr["close"][i], "EOD"

                if raw is not None:
                    xp = float(raw) * (1 - fr)
                    pnl = pos["qty"] * (xp - pos["entry"])
                    ret = pnl / pos["eq0"]
                    eq += pnl
                    peak = max(peak, eq)
                    maxdd = max(maxdd, (peak - eq) / peak if peak else 0.0)
                    trades.append({"day": day, "pnl": pnl, "ret": ret, "reason": why})
                    perday[day] += 1
                    cooldown_until = now + cooldown_ns
                    pos = None
            continue

        if now <= cooldown_until:
            continue
        if perday[day] >= MAX_TRADES:
            continue

        best = None
        for s in SYMBOLS:
            arr = syms[s]
            if not arr["valid"][i] or not arr["signals"][rsi_level][i]:
                continue
            sc = arr["score"][i]
            op = arr["open"][i]
            if np.isnan(sc) or np.isnan(op):
                continue
            if best is None or sc > best[0]:
                best = (float(sc), s, float(op))

        if best is not None:
            _, s, op = best
            ep = op * (1 + fr)
            qty = (eq * CAPITAL) / ep
            pos = {"s": s, "entry": ep, "qty": qty, "eq0": eq, "bars": 0, "start_i": i}

    if pos is not None:
        arr = syms[pos["s"]]
        valid_idx = np.flatnonzero(arr["valid"][pos["start_i"] :])
        if valid_idx.size:
            j = pos["start_i"] + int(valid_idx[-1])
            xp = float(arr["close"][j]) * (1 - fr)
            pnl = pos["qty"] * (xp - pos["entry"])
            ret = pnl / pos["eq0"]
            eq += pnl
            peak = max(peak, eq)
            maxdd = max(maxdd, (peak - eq) / peak if peak else 0.0)
            trades.append({"day": int(day_ns[j]), "pnl": pnl, "ret": ret, "reason": "FINAL"})

    return metrics(trades, eq, maxdd)


def sel_score(m):
    if m["trades"] < 150 or m["expectancy_bps"] <= 0:
        return -1e9
    return (
        m["daily_sharpe"]
        + 1.5 * m["win_rate"]
        + 0.25 * min(m["profit_factor"], 3)
        + 0.05 * min(m["expectancy_bps"], 10)
        - max(0, m["max_drawdown"] - 0.12) * 10
    )


def rounded(m):
    r = dict(m)
    for k in [
        "final_equity",
        "total_return",
        "win_rate",
        "expectancy_bps",
        "profit_factor",
        "max_drawdown",
        "daily_sharpe",
        "positive_days",
    ]:
        r[k] = round(float(r[k]), 4 if k not in ["total_return", "win_rate", "max_drawdown", "positive_days"] else 6)
    return r


def main():
    data = load_data()
    print("Packing periods", flush=True)
    train_pack = pack_period(data, "2021-01-01", "2023-12-31")
    val_pack = pack_period(data, "2024-01-01", "2024-12-31")
    hold_pack = pack_period(data, "2025-01-01", "2026-07-31")

    grid = list(product([0.002, 0.003, 0.004], [0.0015, 0.002, 0.0025], [4, 6], RSI_LEVELS))
    train = []
    for n, (tp, sl, hold, rr) in enumerate(grid, 1):
        m = sim(train_pack, tp, sl, hold, rr, BASE_FRICTION_BPS)
        train.append({"tp": tp, "sl": sl, "hold": hold, "rsi": rr, "m": m, "score": sel_score(m)})
        print("train", n, "/", len(grid), "score", round(sel_score(m), 4), flush=True)

    finalists = sorted(train, key=lambda x: x["score"], reverse=True)[:8]
    vals = []
    for n, c in enumerate(finalists, 1):
        m = sim(val_pack, c["tp"], c["sl"], c["hold"], c["rsi"], BASE_FRICTION_BPS)
        vals.append({**c, "val": m, "vscore": sel_score(m)})
        print("validation", n, "/", len(finalists), "score", round(sel_score(m), 4), flush=True)

    best = max(vals, key=lambda x: x["vscore"])
    tp, sl, hold, rr = best["tp"], best["sl"], best["hold"], best["rsi"]
    holdout = sim(hold_pack, tp, sl, hold, rr, BASE_FRICTION_BPS)
    stress = {str(b): sim(hold_pack, tp, sl, hold, rr, b) for b in [2.0, 4.0, 6.0, 10.0]}

    neigh = []
    for tp2 in sorted(set([max(0.0015, tp - 0.0005), tp, tp + 0.0005])):
        for sl2 in sorted(set([max(0.001, sl - 0.0005), sl, sl + 0.0005])):
            neigh.append(sim(hold_pack, tp2, sl2, hold, rr, BASE_FRICTION_BPS))
    npos = sum(m["expectancy_bps"] > 0 for m in neigh)

    gate = (
        holdout["trades"] >= 100
        and holdout["expectancy_bps"] > 0
        and holdout["profit_factor"] > 1.05
        and holdout["max_drawdown"] < 0.15
        and stress["6.0"]["expectancy_bps"] > 0
        and npos >= math.ceil(len(neigh) * 0.65)
    )

    result = {
        "data": {
            "source": "Alpaca Market Data API / IEX",
            "symbols": SYMBOLS,
            "timeframe": "5Min",
            "range": "2021-01-01 through 2026-07-31",
            "train": "2021-2023",
            "validation": "2024",
            "holdout": "2025-2026-07-31",
        },
        "selected": {
            "take_profit_pct": tp,
            "stop_loss_pct": sl,
            "max_hold_minutes": hold * 5,
            "rsi_reclaim": rr,
            "capital_fraction": CAPITAL,
            "max_trades_per_day": MAX_TRADES,
            "cooldown_minutes": COOLDOWN_MIN,
        },
        "train": rounded(best["m"]),
        "validation": rounded(best["val"]),
        "holdout": rounded(holdout),
        "friction_stress": {k: rounded(v) for k, v in stress.items()},
        "neighbor_positive": npos,
        "neighbor_total": len(neigh),
        "gate": "PASS" if gate else "FAIL",
        "warning": "Backtests cannot guarantee future profit. Paper trading is required before live money.",
    }

    with open("micro_backtest_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Micro — Phase 3 Intraday Validation",
        "",
        "**Data:** Alpaca IEX 5-minute bars, 2021-01-01 through 2026-07-31  ",
        f"**Universe:** {', '.join(SYMBOLS)}  ",
        "**Starting capital model:** $100, 1x buying power, long-only  ",
        f"**Base friction:** {BASE_FRICTION_BPS:.1f} bps one-way  ",
        "",
        "## Selected micro setup",
        "",
        f"- Take profit: **{tp:.2%}**",
        f"- Stop loss: **{sl:.2%}**",
        f"- Maximum hold: **{hold * 5} minutes**",
        f"- RSI reclaim: **{rr}**",
        f"- Max trades/day: **{MAX_TRADES}**",
        f"- Cooldown: **{COOLDOWN_MIN} minutes**",
        "",
        "## Results",
        "",
        "| Period | Trades | Return | Win rate | Expectancy | Profit factor | Daily Sharpe | Max DD | Positive days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, m in [
        ("Train 2021-2023", result["train"]),
        ("Validation 2024", result["validation"]),
        ("Untouched holdout 2025-2026-07", result["holdout"]),
    ]:
        lines.append(
            f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | "
            f"{m['expectancy_bps']:.2f} bps/trade | {m['profit_factor']:.2f} | "
            f"{m['daily_sharpe']:.2f} | {m['max_drawdown']:.2%} | {m['positive_days']:.2%} |"
        )

    lines += [
        "",
        "## Friction stress — untouched holdout",
        "",
        "| One-way friction | Expectancy | Return | Profit factor |",
        "|---:|---:|---:|---:|",
    ]
    for b, m in result["friction_stress"].items():
        lines.append(
            f"| {float(b):.0f} bps | {m['expectancy_bps']:.2f} bps/trade | "
            f"{m['total_return']:.2%} | {m['profit_factor']:.2f} |"
        )
    lines += [
        "",
        f"**Nearby parameter combinations profitable on holdout:** {npos}/{len(neigh)}  ",
        f"**Phase 3 historical gate:** {result['gate']}",
        "",
        "## Important",
        "",
        "This is a historical simulation, not a guarantee or promise of profit. Micro strategies are especially sensitive to spread, slippage, fills, data-feed differences, taxes, and market regime changes. The exact locked strategy must pass live paper trading before any real-money automation is considered.",
    ]
    with open("micro_backtest_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
