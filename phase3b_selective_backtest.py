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
COST_BUFFER_MULTIPLE = 8.0
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
    d["session"] = d.index.date
    parts = []
    for _, x in d.groupby("session", sort=True):
        x = x.copy()
        x["ema9"] = x.close.ewm(span=9, adjust=False).mean()
        x["ema21"] = x.close.ewm(span=21, adjust=False).mean()
        x["ema50"] = x.close.ewm(span=50, adjust=False).mean()
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
        x["hi12"] = x.high.shift(1).rolling(12, min_periods=12).max()
        x["lo6"] = x.low.shift(1).rolling(6, min_periods=6).min()
        first6 = x.iloc[:6]
        x["or_high"] = float(first6.high.max()) if len(first6) else np.nan
        x["trend_strength"] = (x.ema9 / x.ema21 - 1).clip(lower=0)
        x["slope21"] = x.ema21 / x.ema21.shift(3) - 1
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


def make_variants(d):
    variants = {}
    for vr, atr_min, trend_min in product([1.0, 1.4], [0.0008, 0.0012], [0.0003, 0.0006]):
        common = (
            (d.vr.fillna(0) >= vr)
            & (d.atr_pct.fillna(0) >= atr_min)
            & (d.trend_strength >= trend_min)
            & (d.ema9 > d.ema21)
            & (d.ema21 > d.ema50)
            & (d.close > d.vwap)
            & (d.slope21 > 0)
        )
        tag = f"vr{int(vr*10)}_atr{int(atr_min*10000)}_tr{int(trend_min*10000)}"

        cross6 = (d.close > d.hi6) & (d.close.shift(1) <= d.hi6.shift(1))
        variants[f"breakout6_{tag}"] = (cross6 & common & d.time_ok).shift(1, fill_value=False)

        cross12 = (d.close > d.hi12) & (d.close.shift(1) <= d.hi12.shift(1))
        variants[f"breakout12_{tag}"] = (cross12 & common & d.time_ok).shift(1, fill_value=False)

        reclaim9 = (d.close.shift(1) <= d.ema9.shift(1)) & (d.close > d.ema9)
        pullback_ok = (d.low.shift(1) <= d.ema21.shift(1) * 1.001) & (d.rsi7.shift(1) < 55) & (d.rsi7 >= 50)
        variants[f"pullback9_{tag}"] = (reclaim9 & pullback_ok & common & d.time_ok).shift(1, fill_value=False)

        vwap_cross = (d.close.shift(1) <= d.vwap.shift(1)) & (d.close > d.vwap)
        variants[f"vwap_reclaim_{tag}"] = (vwap_cross & common & (d.rsi7 >= 50) & d.time_ok).shift(1, fill_value=False)

        orb_cross = (d.close > d.or_high) & (d.close.shift(1) <= d.or_high)
        variants[f"orb_{tag}"] = (orb_cross & common & d.morning).shift(1, fill_value=False)
    return variants


def quality_score(d):
    trend = np.maximum(0.0, d.ema9 / d.ema21 - 1) * 800
    vwap = np.maximum(0.0, d.close / d.vwap - 1) * 300
    vol = np.minimum(d.vr.fillna(0), 3.0) * 0.15
    slope = np.maximum(0.0, d.slope21.fillna(0)) * 500
    return (trend + vwap + vol + slope).shift(1)


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
    names = None
    for s in SYMBOLS:
        d = frames[s]
        a = d.reindex(idx)
        variants = make_variants(d)
        if names is None:
            names = list(variants)
        pack["symbols"][s] = {
            "valid": a.open.notna().to_numpy(),
            "open": a.open.to_numpy(float),
            "high": a.high.to_numpy(float),
            "low": a.low.to_numpy(float),
            "close": a.close.to_numpy(float),
            "atr_pct": a.atr_pct.to_numpy(float),
            "quality": quality_score(d).reindex(idx).to_numpy(float),
            "signals": {k: v.reindex(idx, fill_value=False).to_numpy(bool) for k, v in variants.items()},
        }
    pack["variants"] = names or []
    return pack


def calc_metrics(trades, eq, maxdd):
    if not trades:
        return {
            "trades": 0, "final_equity": START_EQ, "total_return": 0.0, "win_rate": 0.0,
            "expectancy_bps": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
            "daily_sharpe": 0.0, "positive_days": 0.0, "target_rate": 0.0,
            "avg_win_bps": 0.0, "avg_loss_bps": 0.0, "symbols_positive": 0,
        }
    t = pd.DataFrame(trades)
    wins = t.loc[t.pnl > 0, "pnl"].sum()
    losses = -t.loc[t.pnl < 0, "pnl"].sum()
    daily = t.groupby("day")["ret"].sum()
    sd = daily.std(ddof=0)
    sharpe = float(daily.mean() / sd * math.sqrt(252)) if len(daily) > 1 and sd > 0 else 0.0
    pos = t.loc[t.ret > 0, "ret"]
    neg = t.loc[t.ret < 0, "ret"]
    by_symbol = t.groupby("symbol")["ret"].mean()
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
        "target_rate": float((t.reason == "TARGET").mean()),
        "avg_win_bps": float(pos.mean() * 10000) if len(pos) else 0.0,
        "avg_loss_bps": float(neg.mean() * 10000) if len(neg) else 0.0,
        "symbols_positive": int((by_symbol > 0).sum()),
    }


def sim(pack, variant, target_mult, stop_mult, hold_bars, friction_bps):
    ts_ns, day_ns, minute = pack["ts_ns"], pack["day_ns"], pack["minute"]
    syms = pack["symbols"]
    fr = friction_bps / 10000.0
    roundtrip_cost = 2 * fr
    min_cost_adjusted_target = roundtrip_cost * COST_BUFFER_MULTIPLE
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
                        "day": day, "symbol": pos["symbol"], "pnl": pnl, "ret": ret,
                        "reason": why, "target_pct": pos["target_pct"], "stop_pct": pos["stop_pct"],
                    })
                    perday[day] += 1
                    pos = None
            continue

        if perday[day] >= MAX_TRADES_PER_DAY:
            continue

        best = None
        for s in SYMBOLS:
            arr = syms[s]
            if not arr["valid"][i] or not arr["signals"][variant][i]:
                continue
            q, op, atrp = arr["quality"][i], arr["open"][i], arr["atr_pct"][i]
            if np.isnan(q) or np.isnan(op) or np.isnan(atrp) or atrp <= 0:
                continue
            target_pct = float(np.clip(atrp * target_mult, TARGET_FLOOR, TARGET_CAP))
            stop_pct = float(np.clip(atrp * stop_mult, STOP_FLOOR, STOP_CAP))
            if target_pct < min_cost_adjusted_target:
                continue
            reward_risk = target_pct / stop_pct
            if reward_risk < 1.25:
                continue
            rank = float(q) + reward_risk * 0.25 + target_pct * 100
            if best is None or rank > best[0]:
                best = (rank, s, float(op), target_pct, stop_pct)

        if best is not None:
            _, s, op, target_pct, stop_pct = best
            entry_px = op * (1 + fr)
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
                "day": int(day_ns[j]), "symbol": pos["symbol"], "pnl": pnl, "ret": ret,
                "reason": "FINAL", "target_pct": pos["target_pct"], "stop_pct": pos["stop_pct"],
            })
    return calc_metrics(trades, eq, maxdd)


def rounded(m):
    out = dict(m)
    for k, v in list(out.items()):
        if isinstance(v, (float, np.floating)):
            out[k] = round(float(v), 6 if k in {"total_return", "win_rate", "max_drawdown", "positive_days", "target_rate"} else 4)
    return out


def dev_score(m):
    if m["trades"] < 50 or m["expectancy_bps"] <= 1.0 or m["profit_factor"] <= 1.05:
        return -1e9
    return (
        m["daily_sharpe"]
        + 0.10 * min(m["expectancy_bps"], 20)
        + 0.75 * min(m["profit_factor"], 2.5)
        + 0.5 * m["symbols_positive"]
        - 10 * max(0.0, m["max_drawdown"] - 0.08)
    )


def main():
    data = load_data()
    print("Packing periods", flush=True)
    dev = pack_period(data, "2021-01-01", "2023-12-31")
    y24 = pack_period(data, "2024-01-01", "2024-12-31")
    y25 = pack_period(data, "2025-01-01", "2025-12-31")
    y26 = pack_period(data, "2026-01-01", "2026-07-31")
    v2425 = pack_period(data, "2024-01-01", "2025-12-31")

    exits = list(product([1.5, 2.0], [0.8, 1.0], [6, 12]))
    candidates = []
    total = len(dev["variants"]) * len(exits)
    n = 0
    for variant in dev["variants"]:
        for tm, sm, hold in exits:
            n += 1
            m = sim(dev, variant, tm, sm, hold, BASE_FRICTION_BPS)
            candidates.append({
                "variant": variant, "target_mult": tm, "stop_mult": sm, "hold": hold,
                "development": m, "score": dev_score(m),
            })
            if n % 40 == 0 or n == total:
                print("development", n, "/", total, flush=True)

    dev_ok = [c for c in candidates if c["score"] > -1e8]
    finalists = sorted(dev_ok, key=lambda c: c["score"], reverse=True)[:40]
    checked = []
    for i, c in enumerate(finalists, 1):
        m24 = sim(y24, c["variant"], c["target_mult"], c["stop_mult"], c["hold"], BASE_FRICTION_BPS)
        m25 = sim(y25, c["variant"], c["target_mult"], c["stop_mult"], c["hold"], BASE_FRICTION_BPS)
        robust = (
            m24["trades"] >= 12 and m25["trades"] >= 12
            and m24["expectancy_bps"] > 2.0 and m25["expectancy_bps"] > 2.0
            and m24["profit_factor"] > 1.10 and m25["profit_factor"] > 1.10
            and m24["max_drawdown"] < 0.08 and m25["max_drawdown"] < 0.08
        )
        vscore = (
            min(m24["expectancy_bps"], m25["expectancy_bps"])
            + 2.0 * min(m24["daily_sharpe"], m25["daily_sharpe"])
            + min(m24["profit_factor"], m25["profit_factor"])
            + 0.5 * min(m24["symbols_positive"], m25["symbols_positive"])
        ) if robust else -1e9
        checked.append({**c, "validation_2024": m24, "validation_2025": m25, "robust": robust, "vscore": vscore})
        print("validation", i, "/", len(finalists), robust, flush=True)

    robust_candidates = [c for c in checked if c["robust"]]
    if robust_candidates:
        best = max(robust_candidates, key=lambda c: c["vscore"])
    elif checked:
        best = max(checked, key=lambda c: (
            min(c["validation_2024"]["expectancy_bps"], c["validation_2025"]["expectancy_bps"]),
            c["score"],
        ))
    else:
        best = max(candidates, key=lambda c: c["score"])
        zero = calc_metrics([], START_EQ, 0.0)
        best = {**best, "validation_2024": zero, "validation_2025": zero, "robust": False, "vscore": -1e9}

    combined_base = sim(v2425, best["variant"], best["target_mult"], best["stop_mult"], best["hold"], BASE_FRICTION_BPS)
    friction_validation = {
        str(b): sim(v2425, best["variant"], best["target_mult"], best["stop_mult"], best["hold"], b)
        for b in [2.0, 4.0, 6.0, 10.0]
    }
    check26 = sim(y26, best["variant"], best["target_mult"], best["stop_mult"], best["hold"], BASE_FRICTION_BPS)
    friction_2026 = {
        str(b): sim(y26, best["variant"], best["target_mult"], best["stop_mult"], best["hold"], b)
        for b in [2.0, 4.0, 6.0, 10.0]
    }

    neighbor_tests = []
    for tm in sorted(set([max(1.25, best["target_mult"] - 0.25), best["target_mult"], best["target_mult"] + 0.25])):
        for sm in sorted(set([max(0.6, best["stop_mult"] - 0.1), best["stop_mult"], best["stop_mult"] + 0.1])):
            m = sim(v2425, best["variant"], tm, sm, best["hold"], BASE_FRICTION_BPS)
            neighbor_tests.append({"target_mult": tm, "stop_mult": sm, "metrics": m})
    neighbor_positive = sum(
        1 for x in neighbor_tests
        if x["metrics"]["expectancy_bps"] > 0 and x["metrics"]["profit_factor"] > 1.0
    )

    gate = (
        best.get("robust", False)
        and combined_base["expectancy_bps"] > 2.0
        and combined_base["profit_factor"] > 1.10
        and friction_validation["6.0"]["expectancy_bps"] > 0
        and check26["trades"] >= 8
        and check26["expectancy_bps"] > 0
        and check26["profit_factor"] > 1.05
        and friction_2026["6.0"]["expectancy_bps"] > 0
        and neighbor_positive >= math.ceil(len(neighbor_tests) * 0.65)
    )

    result = {
        "phase": "3B",
        "method": "Selective intraday fixed-family comparison; no more than one trade/day; dynamic ATR exits; explicit execution-cost buffer",
        "candidate_count": total,
        "development_valid_candidates": len(dev_ok),
        "robust_2024_2025_candidates": len(robust_candidates),
        "selected": {
            "variant": best["variant"],
            "target_atr_multiple": best["target_mult"],
            "stop_atr_multiple": best["stop_mult"],
            "target_floor_pct": TARGET_FLOOR,
            "target_cap_pct": TARGET_CAP,
            "stop_floor_pct": STOP_FLOOR,
            "stop_cap_pct": STOP_CAP,
            "max_hold_minutes": best["hold"] * 5,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "capital_fraction": CAPITAL,
            "cost_buffer_multiple": COST_BUFFER_MULTIPLE,
        },
        "development": rounded(best["development"]),
        "validation_2024": rounded(best["validation_2024"]),
        "validation_2025": rounded(best["validation_2025"]),
        "validation_2024_2025": rounded(combined_base),
        "check_2026": rounded(check26),
        "friction_validation_2024_2025": {k: rounded(v) for k, v in friction_validation.items()},
        "friction_2026": {k: rounded(v) for k, v in friction_2026.items()},
        "neighbor_positive": neighbor_positive,
        "neighbor_total": len(neighbor_tests),
        "gate": "PASS" if gate else "FAIL",
        "warning": "Historical and paper results cannot ensure future profit. Real money remains prohibited until a separate live-paper phase passes.",
    }
    with open("phase3b_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Micro — Phase 3B Selective Intraday Validation", "",
        f"**Candidates tested:** {total}",
        f"**Development-valid candidates:** {len(dev_ok)}",
        f"**Candidates passing both 2024 and 2025 validation:** {len(robust_candidates)}", "",
        "## Selected setup", "",
        f"- Variant: **{best['variant']}**",
        f"- Dynamic target: **{best['target_mult']:.2f} × ATR**, bounded to {TARGET_FLOOR:.2%}–{TARGET_CAP:.2%}",
        f"- Dynamic stop: **{best['stop_mult']:.2f} × ATR**, bounded to {STOP_FLOOR:.2%}–{STOP_CAP:.2%}",
        f"- Maximum hold: **{best['hold'] * 5} minutes**",
        f"- Maximum trades/day: **{MAX_TRADES_PER_DAY}**",
        f"- Required target/cost buffer: **{COST_BUFFER_MULTIPLE:.0f}× estimated round-trip friction**", "",
        "## Results", "",
        "| Period | Trades | Return | Win rate | Expectancy | Profit factor | Sharpe | Max DD | Target exits |",
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
            f"| {label} | {m['trades']} | {m['total_return']:.2%} | {m['win_rate']:.2%} | "
            f"{m['expectancy_bps']:.2f} bps/trade | {m['profit_factor']:.2f} | {m['daily_sharpe']:.2f} | "
            f"{m['max_drawdown']:.2%} | {m['target_rate']:.2%} |"
        )
    lines += ["", "## Execution-friction stress — 2024-2025 validation", "",
              "| One-way friction | Expectancy | Return | Profit factor |",
              "|---:|---:|---:|---:|"]
    for b, m in result["friction_validation_2024_2025"].items():
        lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.2f} bps/trade | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += ["", "## 2026 execution-friction check", "",
              "| One-way friction | Expectancy | Return | Profit factor |",
              "|---:|---:|---:|---:|"]
    for b, m in result["friction_2026"].items():
        lines.append(f"| {float(b):.0f} bps | {m['expectancy_bps']:.2f} bps/trade | {m['total_return']:.2%} | {m['profit_factor']:.2f} |")
    lines += [
        "", f"**Nearby exit settings profitable on 2024-2025:** {neighbor_positive}/{len(neighbor_tests)}",
        f"**Phase 3B gate: {result['gate']}**", "", "## Important", "",
        "This phase deliberately trades less often. It requires trend, liquidity, volatility, and execution-cost headroom before an entry is permitted. Because 2026 was observed during earlier experiments, it is a forward-like check rather than a pristine holdout. A PASS still does not authorize real-money trading; it only permits the next paper-order phase.",
    ]
    with open("phase3b_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
