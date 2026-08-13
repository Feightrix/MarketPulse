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


def headers():
    key, sec = os.getenv("ALPACA_API_KEY_ID"), os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing Alpaca credentials")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch(symbol):
    rows, token, h = [], None, headers()
    while True:
        p = {
            "timeframe": "5Min", "start": START, "end": END, "adjustment": "all",
            "feed": "iex", "limit": 10000, "sort": "asc",
        }
        if token:
            p["page_token"] = token
        req = urllib.request.Request(
            BASE.format(symbol=symbol) + "?" + urllib.parse.urlencode(p), headers=h
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            js = json.loads(r.read().decode())
        rows.extend(js.get("bars", []))
        token = js.get("next_page_token")
        if not token:
            break
        time.sleep(0.03)
    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError(f"No data for {symbol}")
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
    d["session"] = d.index.date
    chunks = []
    for _, x in d.groupby("session", sort=True):
        x = x.copy()
        x["ema9"] = x.close.ewm(span=9, adjust=False).mean()
        x["ema21"] = x.close.ewm(span=21, adjust=False).mean()
        x["rsi7"] = rsi(x.close)
        typical = (x.high + x.low + x.close) / 3
        volcum = x.volume.cumsum().replace(0, np.nan)
        x["vwap"] = (typical * x.volume).cumsum() / volcum
        x["vr"] = x.volume / x.volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
        x["open0"] = float(x.open.iloc[0])
        x["hi6"] = x.high.shift(1).rolling(6, min_periods=6).max()
        x["hi12"] = x.high.shift(1).rolling(12, min_periods=12).max()
        first6 = x.iloc[:6]
        x["or_high"] = float(first6.high.max()) if len(first6) else np.nan
        x["ema21_slope3"] = x.ema21 / x.ema21.shift(3) - 1
        chunks.append(x)
    d = pd.concat(chunks).sort_index()
    mins = d.index.hour * 60 + d.index.minute
    d["minute"] = mins
    d["time_ok"] = ((mins >= 9 * 60 + 45) & (mins <= 11 * 60 + 30)) | (
        (mins >= 14 * 60) & (mins <= 15 * 60 + 30)
    )
    d["morning_ok"] = (mins >= 10 * 60) & (mins <= 11 * 60 + 30)
    return d


def load_data():
    data = {}
    for s in SYMBOLS:
        print("Downloading", s, flush=True)
        data[s] = prep(s, fetch(s))
        print(s, len(data[s]), "bars", flush=True)
    return data


def signal_variants(d):
    out = {}
    for rlevel in [45, 50]:
        reclaim = (d.close.shift(1) <= d.ema9.shift(1)) & (d.close > d.ema9)
        rr = (d.rsi7.shift(1) < rlevel) & (d.rsi7 >= rlevel)
        cond = reclaim & rr & (d.ema9 > d.ema21) & (d.close > d.vwap) & (d.vr.fillna(0) >= 0.6) & d.time_ok
        out[f"trend_reclaim_rsi{rlevel}"] = cond.shift(1, fill_value=False)

    for dev, rlevel in [(0.002, 35), (0.003, 40)]:
        oversold_prev = (d.close.shift(1) < d.vwap.shift(1) * (1 - dev)) & (d.rsi7.shift(1) < rlevel)
        bounce = (d.close > d.ema9) & (d.close > d.close.shift(1)) & (d.close < d.vwap * 1.002)
        safe = (d.close / d.open0 > 0.99) & (d.vr.fillna(0) >= 0.6)
        cond = oversold_prev & bounce & safe & d.time_ok
        out[f"vwap_bounce_d{int(dev*10000)}_r{rlevel}"] = cond.shift(1, fill_value=False)

    for n in [6, 12]:
        hi = d[f"hi{n}"]
        cross = (d.close > hi) & (d.close.shift(1) <= hi.shift(1))
        cond = cross & (d.ema9 > d.ema21) & (d.close > d.vwap) & (d.vr.fillna(0) >= 1.0) & d.time_ok
        out[f"breakout_{n}bar"] = cond.shift(1, fill_value=False)

    for vr in [0.8, 1.2]:
        cross = (d.close.shift(1) <= d.vwap.shift(1)) & (d.close > d.vwap)
        cond = cross & (d.ema9 > d.ema21) & (d.rsi7 >= 50) & (d.vr.fillna(0) >= vr) & d.time_ok
        out[f"vwap_cross_vr{int(vr*10)}"] = cond.shift(1, fill_value=False)

    for vr in [1.0, 1.2]:
        cross = (d.close > d.or_high) & (d.close.shift(1) <= d.or_high)
        cond = cross & (d.ema9 > d.ema21) & (d.close > d.vwap) & (d.vr.fillna(0) >= vr) & d.morning_ok
        out[f"orb_vr{int(vr*10)}"] = cond.shift(1, fill_value=False)

    return out


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
        "ts_ns": idx.asi8,
        "day_ns": idx.normalize().asi8,
        "minute": (idx.hour * 60 + idx.minute).to_numpy(dtype=np.int16),
        "symbols": {},
    }
    variant_names = None
    for s in SYMBOLS:
        d, a = frames[s], frames[s].reindex(idx)
        variants = signal_variants(d)
        if variant_names is None:
            variant_names = list(variants)
        pack["symbols"][s] = {
            "valid": a.open.notna().to_numpy(),
            "open": a.open.to_numpy(float),
            "high": a.high.to_numpy(float),
            "low": a.low.to_numpy(float),
            "close": a.close.to_numpy(float),
            "score": score_series(d).reindex(idx).to_numpy(float),
            "signals": {k: v.reindex(idx, fill_value=False).to_numpy(bool) for k, v in variants.items()},
        }
    pack["variants"] = variant_names
    return pack


def metrics(trades, eq, maxdd):
    if not trades:
        return dict(trades=0, final_equity=START_EQ, total_return=0, win_rate=0,
                    expectancy_bps=0, profit_factor=0, max_drawdown=0,
                    daily_sharpe=0, positive_days=0)
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


def sim(pack, variant, tp, sl, hold, friction_bps):
    ts_ns, day_ns, minute, syms = pack["ts_ns"], pack["day_ns"], pack["minute"], pack["symbols"]
    fr = friction_bps / 10000.0
    cooldown_ns = COOLDOWN_MIN * 60 * 1_000_000_000
    eq, peak, maxdd = START_EQ, START_EQ, 0.0
    pos, trades, perday, cooldown_until = None, [], {}, -1

    for i in range(len(ts_ns)):
        now, day = int(ts_ns[i]), int(day_ns[i])
        perday.setdefault(day, 0)
        if pos is not None:
            arr = syms[pos["s"]]
            if arr["valid"][i]:
                pos["bars"] += 1
                stop, target = pos["entry"] * (1 - sl), pos["entry"] * (1 + tp)
                raw = why = None
                if arr["low"][i] <= stop:
                    raw, why = stop, "STOP"
                elif arr["high"][i] >= target:
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
                    maxdd = max(maxdd, (peak - eq) / peak if peak else 0)
                    trades.append({"day": day, "pnl": pnl, "ret": ret, "reason": why})
                    perday[day] += 1
                    cooldown_until = now + cooldown_ns
                    pos = None
            continue

        if now <= cooldown_until or perday[day] >= MAX_TRADES:
            continue

        best = None
        for s in SYMBOLS:
            arr = syms[s]
            if not arr["valid"][i] or not arr["signals"][variant][i]:
                continue
            sc, op = arr["score"][i], arr["open"][i]
            if np.isnan(sc) or np.isnan(op):
                continue
            if best is None or sc > best[0]:
                best = (float(sc), s, float(op))
        if best is not None:
            _, s, op = best
            ep = op * (1 + fr)
            pos = {"s": s, "entry": ep, "qty": eq * CAPITAL / ep, "eq0": eq, "bars": 0, "start_i": i}

    if pos is not None:
        arr = syms[pos["s"]]
        valid = np.flatnonzero(arr["valid"][pos["start_i"]:])
        if valid.size:
            j = pos["start_i"] + int(valid[-1])
            xp = float(arr["close"][j]) * (1 - fr)
            pnl = pos["qty"] * (xp - pos["entry"])
            ret = pnl / pos["eq0"]
            eq += pnl
            peak = max(peak, eq)
            maxdd = max(maxdd, (peak - eq) / peak if peak else 0)
            trades.append({"day": int(day_ns[j]), "pnl": pnl, "ret": ret, "reason": "FINAL"})
    return metrics(trades, eq, maxdd)


def score(m):
    if m["trades"] < 80 or m["expectancy_bps"] <= 0 or m["profit_factor"] <= 1:
        return -1e9
    return (
        m["daily_sharpe"] + 1.5 * m["win_rate"] + 0.5 * min(m["profit_factor"], 2.5)
        + 0.08 * min(m["expectancy_bps"], 15) - max(0, m["max_drawdown"] - 0.10) * 12
    )


def rounded(m):
    r = dict(m)
    for k in ["final_equity", "total_return", "win_rate", "expectancy_bps", "profit_factor",
              "max_drawdown", "daily_sharpe", "positive_days"]:
        digits = 6 if k in ["total_return", "win_rate", "max_drawdown", "positive_days"] else 4
        r[k] = round(float(r[k]), digits)
    return r


def main():
    data = load_data()
    print("Packing periods", flush=True)
    dev = pack_period(data, "2021-01-01", "2023-12-31")
    y2024 = pack_period(data, "2024-01-01", "2024-12-31")
    y2025 = pack_period(data, "2025-01-01", "2025-12-31")
    y2026 = pack_period(data, "2026-01-01", "2026-07-31")

    exits = list(product([0.003, 0.0045, 0.006], [0.002, 0.003, 0.004], [6, 12]))
    candidates = []
    total = len(dev["variants"]) * len(exits)
    n = 0
    for variant in dev["variants"]:
        for tp, sl, hold in exits:
            n += 1
            m = sim(dev, variant, tp, sl, hold, BASE_FRICTION_BPS)
            candidates.append({"variant": variant, "tp": tp, "sl": sl, "hold": hold, "dev": m, "score": score(m)})
            if n % 15 == 0 or n == total:
                print("development", n, "/", total, flush=True)

    valid_dev = [c for c in candidates if c["score"] > -1e8]
    finalists = sorted(valid_dev, key=lambda x: x["score"], reverse=True)[:20]
    checked = []
    for i, c in enumerate(finalists, 1):
        m24 = sim(y2024, c["variant"], c["tp"], c["sl"], c["hold"], BASE_FRICTION_BPS)
        m25 = sim(y2025, c["variant"], c["tp"], c["sl"], c["hold"], BASE_FRICTION_BPS)
        robust = (
            m24["trades"] >= 20 and m25["trades"] >= 20
            and m24["expectancy_bps"] > 0 and m25["expectancy_bps"] > 0
            and m24["profit_factor"] > 1 and m25["profit_factor"] > 1
        )
        vscore = (
            min(m24["expectancy_bps"], m25["expectancy_bps"])
            + 2 * min(m24["daily_sharpe"], m25["daily_sharpe"])
            + min(m24["profit_factor"], m25["profit_factor"])
        ) if robust else -1e9
        checked.append({**c, "y2024": m24, "y2025": m25, "robust": robust, "vscore": vscore})
        print("validation", i, "/", len(finalists), c["variant"], robust, flush=True)

    robust_candidates = [c for c in checked if c["robust"]]
    if robust_candidates:
        best = max(robust_candidates, key=lambda x: x["vscore"])
    elif checked:
        best = max(checked, key=lambda x: (min(x["y2024"]["expectancy_bps"], x["y2025"]["expectancy_bps"]), x["score"]))
    else:
        best = max(candidates, key=lambda x: x["score"])
        best = {**best, "y2024": metrics([], START_EQ, 0), "y2025": metrics([], START_EQ, 0), "robust": False, "vscore": -1e9}

    m26 = sim(y2026, best["variant"], best["tp"], best["sl"], best["hold"], BASE_FRICTION_BPS)
    stress = {str(b): sim(y2026, best["variant"], best["tp"], best["sl"], best["hold"], b)
              for b in [2.0, 4.0, 6.0, 10.0]}

    gate = (
        best.get("robust", False)
        and m26["trades"] >= 10
        and m26["expectancy_bps"] > 0
        and m26["profit_factor"] > 1.05
        and m26["max_drawdown"] < 0.10
        and stress["6.0"]["expectancy_bps"] > 0
    )

    result = {
        "method": "Fixed family comparison; development 2021-2023, validation 2024 and 2025, 2026 forward-like check",
        "candidate_count": total,
        "valid_development_candidates": len(valid_dev),
        "robust_validation_candidates": len(robust_candidates),
        "selected": {
            "variant": best["variant"], "take_profit_pct": best["tp"], "stop_loss_pct": best["sl"],
            "max_hold_minutes": best["hold"] * 5, "capital_fraction": CAPITAL,
            "max_trades_per_day": MAX_TRADES, "cooldown_minutes": COOLDOWN_MIN,
        },
        "development": rounded(best["dev"]),
        "validation_2024": rounded(best["y2024"]),
        "validation_2025": rounded(best["y2025"]),
        "check_2026": rounded(m26),
        "friction_stress_2026": {k: rounded(v) for k, v in stress.items()},
        "gate": "PASS" if gate else "FAIL",
        "warning": "No historical test ensures future profit. Paper trading is the next forward test if and only if the gate passes.",
    }
    with open("micro_family_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Micro — Strategy Family Comparison", "",
        f"**Candidates tested:** {total}",
        f"**Development-valid candidates:** {len(valid_dev)}",
        f"**Candidates positive in both 2024 and 2025 validation:** {len(robust_candidates)}", "",
        "## Selected candidate", "",
        f"- Family: **{best['variant']}**",
        f"- Take profit: **{best['tp']:.2%}**",
        f"- Stop loss: **{best['sl']:.2%}**",
        f"- Maximum hold: **{best['hold'] * 5} minutes**", "",
        "## Period results", "",
        "| Period | Trades | Return | Win rate | Expectancy | Profit factor | Sharpe | Max DD | Positive days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, m in [
        ("Development 2021-2023", result["development"]),
        ("Validation 2024", result["validation_2024"]),
        ("Validation 2025", result["validation_2025"]),
        ("2026 check through Jul", result["check_2026"]),
    ]:
        lines.append(
            f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | "
            f"{m['expectancy_bps']:.2f} bps/trade | {m['profit_factor']:.2f} | {m['daily_sharpe']:.2f} | "
            f"{m['max_drawdown']:.2%} | {m['positive_days']:.2%} |"
        )
    lines += ["", "## 2026 friction stress", "",
              "| One-way friction | Expectancy | Return | Profit factor |",
              "|---:|---:|---:|---:|"]
    for b, m in result["friction_stress_2026"].items():
        lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.2f} bps/trade | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += ["", f"**Micro family gate: {result['gate']}**", "",
              "## Important", "",
              "This comparison deliberately tests multiple fixed micro-trading families rather than repeatedly tuning one failed setup. Because earlier experiments have already exposed later-year data, 2026 is described as a forward-like check rather than a pristine untouched holdout. A passing historical gate still requires live paper trading before any real-money use."]
    with open("micro_family_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
