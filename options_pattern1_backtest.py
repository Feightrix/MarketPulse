import json
import math
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_BASE = "https://data.alpaca.markets"
SYMBOL = "SPY"
TIMEFRAME = "5Min"
LOOKBACK_DAYS = 180
OPENING_RANGE_MINUTES = 15
BREAKOUT_BUFFER_PCT = 0.0002
RETEST_TOLERANCE_PCT = 0.0010
HOLD_TOLERANCE_PCT = 0.0005
RETEST_WINDOW_BARS = 6
TRIGGER_WINDOW_BARS = 3
TARGET_R = 1.5
TICK = 0.01
LATEST_ENTRY = time(14, 30)
FORCE_EXIT = time(15, 45)
EASTERN = ZoneInfo("America/New_York")
RESULT_JSON = "options_pattern1_results.json"
RESULT_MD = "options_pattern1_results.md"


def credentials():
    key = os.getenv("ALPACA_OPTIONS_API_KEY_ID")
    secret = os.getenv("ALPACA_OPTIONS_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Options Alpaca credentials are not configured")
    return key, secret


def get_json(url):
    key, secret = credentials()
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bars():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    params = {
        "timeframe": TIMEFRAME,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "adjustment": "raw",
        "feed": "iex",
        "limit": 10000,
        "sort": "asc",
    }
    bars = []
    token = None
    while True:
        q = dict(params)
        if token:
            q["page_token"] = token
        url = f"{DATA_BASE}/v2/stocks/{SYMBOL}/bars?{urllib.parse.urlencode(q)}"
        payload = get_json(url)
        bars.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    return bars


def parse_bar(raw):
    ts = datetime.fromisoformat(raw["t"].replace("Z", "+00:00")).astimezone(EASTERN)
    return {
        "ts": ts,
        "o": float(raw["o"]),
        "h": float(raw["h"]),
        "l": float(raw["l"]),
        "c": float(raw["c"]),
        "v": float(raw.get("v") or 0.0),
    }


def regular_session_bars(raw_bars):
    by_day = defaultdict(list)
    for raw in raw_bars:
        bar = parse_bar(raw)
        t = bar["ts"].time()
        if time(9, 30) <= t < time(16, 0):
            by_day[bar["ts"].date()].append(bar)
    return {day: sorted(bars, key=lambda b: b["ts"]) for day, bars in by_day.items()}


def add_session_vwap(bars):
    pv = 0.0
    volume = 0.0
    for bar in bars:
        typical = (bar["h"] + bar["l"] + bar["c"]) / 3.0
        pv += typical * bar["v"]
        volume += bar["v"]
        bar["vwap"] = pv / volume if volume > 0 else bar["c"]


def opening_range(bars):
    orb = [b for b in bars if time(9, 30) <= b["ts"].time() < time(9, 45)]
    if len(orb) < 3:
        return None
    return max(b["h"] for b in orb), min(b["l"] for b in orb)


def simulate_trade(bars, entry_index, side, entry, stop, target):
    if side == "CALL":
        risk = entry - stop
    else:
        risk = stop - entry
    if risk <= 0:
        return None

    exit_price = None
    exit_reason = None
    exit_ts = None

    for bar in bars[entry_index:]:
        if bar["ts"].time() > FORCE_EXIT:
            break
        if side == "CALL":
            stop_hit = bar["l"] <= stop
            target_hit = bar["h"] >= target
        else:
            stop_hit = bar["h"] >= stop
            target_hit = bar["l"] <= target

        # Conservative rule for ambiguous 5-minute bars: stop wins ties.
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP"
            exit_ts = bar["ts"]
            break
        if target_hit:
            exit_price = target
            exit_reason = "TARGET"
            exit_ts = bar["ts"]
            break

    if exit_price is None:
        eligible = [b for b in bars[entry_index:] if b["ts"].time() <= FORCE_EXIT]
        if not eligible:
            return None
        last = eligible[-1]
        exit_price = last["c"]
        exit_reason = "TIME"
        exit_ts = last["ts"]

    if side == "CALL":
        r_multiple = (exit_price - entry) / risk
    else:
        r_multiple = (entry - exit_price) / risk

    return {
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "exit_ts": exit_ts.isoformat(),
        "r": round(r_multiple, 4),
    }


def find_side_trade(day, bars, side, or_high, or_low):
    boundary = or_high if side == "CALL" else or_low
    start_index = next((i for i, b in enumerate(bars) if b["ts"].time() >= time(9, 45)), None)
    if start_index is None:
        return None

    for i in range(start_index, len(bars)):
        bar = bars[i]
        if bar["ts"].time() > LATEST_ENTRY:
            break
        prev = bars[i - 1] if i > 0 else None
        if prev is None:
            continue

        if side == "CALL":
            broke = (
                prev["c"] <= or_high
                and bar["c"] > or_high * (1 + BREAKOUT_BUFFER_PCT)
                and bar["c"] > bar["vwap"]
            )
        else:
            broke = (
                prev["c"] >= or_low
                and bar["c"] < or_low * (1 - BREAKOUT_BUFFER_PCT)
                and bar["c"] < bar["vwap"]
            )
        if not broke:
            continue

        retest_end = min(i + RETEST_WINDOW_BARS, len(bars) - 1)
        for j in range(i + 1, retest_end + 1):
            rbar = bars[j]
            if rbar["ts"].time() > LATEST_ENTRY:
                break

            if side == "CALL":
                retest = (
                    rbar["l"] <= or_high * (1 + RETEST_TOLERANCE_PCT)
                    and rbar["c"] >= or_high * (1 - HOLD_TOLERANCE_PCT)
                    and rbar["c"] >= rbar["vwap"] * (1 - HOLD_TOLERANCE_PCT)
                )
            else:
                retest = (
                    rbar["h"] >= or_low * (1 - RETEST_TOLERANCE_PCT)
                    and rbar["c"] <= or_low * (1 + HOLD_TOLERANCE_PCT)
                    and rbar["c"] <= rbar["vwap"] * (1 + HOLD_TOLERANCE_PCT)
                )
            if not retest:
                continue

            trigger_end = min(j + TRIGGER_WINDOW_BARS, len(bars) - 1)
            for k in range(j + 1, trigger_end + 1):
                tbar = bars[k]
                if tbar["ts"].time() > LATEST_ENTRY:
                    break

                if side == "CALL":
                    trigger = rbar["h"] + TICK
                    if tbar["h"] < trigger:
                        continue
                    entry = max(trigger, tbar["o"])
                    stop = rbar["l"] - TICK
                    risk = entry - stop
                    if risk <= 0:
                        continue
                    target = entry + TARGET_R * risk
                else:
                    trigger = rbar["l"] - TICK
                    if tbar["l"] > trigger:
                        continue
                    entry = min(trigger, tbar["o"])
                    stop = rbar["h"] + TICK
                    risk = stop - entry
                    if risk <= 0:
                        continue
                    target = entry - TARGET_R * risk

                sim = simulate_trade(bars, k, side, entry, stop, target)
                if sim is None:
                    continue
                return {
                    "date": str(day),
                    "side": side,
                    "breakout_ts": bar["ts"].isoformat(),
                    "retest_ts": rbar["ts"].isoformat(),
                    "entry_ts": tbar["ts"].isoformat(),
                    "or_high": round(or_high, 4),
                    "or_low": round(or_low, 4),
                    "entry": round(entry, 4),
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    **sim,
                }
    return None


def max_drawdown(r_values):
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for r in r_values:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def summarize(trades):
    total = len(trades)
    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] < 0]
    flat = total - len(wins) - len(losses)
    gross_win = sum(t["r"] for t in wins)
    gross_loss = abs(sum(t["r"] for t in losses))
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    payoff = avg_win / avg_loss if avg_loss > 0 else None
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    expectancy = sum(t["r"] for t in trades) / total if total else 0.0
    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "flat": flat,
        "win_rate_pct": round((len(wins) / total * 100.0) if total else 0.0, 2),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "payoff_ratio": round(payoff, 3) if payoff is not None else None,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "expectancy_r": round(expectancy, 4),
        "net_r": round(sum(t["r"] for t in trades), 3),
        "max_drawdown_r": round(max_drawdown([t["r"] for t in trades]), 3),
    }


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    overall = result["overall"]
    call = result["calls"]
    put = result["puts"]
    lines = [
        "# MarketPulse — Options Pattern 1 Baseline",
        "",
        "**Research only. Order submission is disabled.**",
        "",
        "## Pattern",
        "- Underlying: **SPY**",
        "- Bars: **5 minute**",
        "- Opening range: **9:30–9:45 ET**",
        "- Bullish: opening-range breakout + retest/hold + momentum re-entry → CALL signal",
        "- Bearish: exact mirrored rule → PUT signal",
        f"- Baseline reward:risk target: **{TARGET_R:.1f}R : 1R**",
        "- Conservative same-bar assumption: **stop before target**",
        "",
        "## Baseline Results",
        f"- Trades: **{overall['trades']}**",
        f"- Win rate: **{overall['win_rate_pct']:.2f}%**",
        f"- Net expectancy: **{overall['expectancy_r']:.4f}R/trade**",
        f"- Net R: **{overall['net_r']:.3f}R**",
        f"- Profit factor: **{overall['profit_factor']}**",
        f"- Average win: **{overall['avg_win_r']:.3f}R**",
        f"- Average loss: **{overall['avg_loss_r']:.3f}R**",
        f"- Payoff ratio: **{overall['payoff_ratio']}**",
        f"- Max drawdown: **{overall['max_drawdown_r']:.3f}R**",
        "",
        "## Direction Split",
        f"- CALLS: **{call['trades']} trades | {call['win_rate_pct']:.2f}% wins | {call['expectancy_r']:.4f}R expectancy**",
        f"- PUTS: **{put['trades']} trades | {put['win_rate_pct']:.2f}% wins | {put['expectancy_r']:.4f}R expectancy**",
        "",
        "## Target Check",
        f"- Desired win-rate band: **60–80%**",
        f"- Baseline inside band: **{'YES' if 60 <= overall['win_rate_pct'] <= 80 else 'NO'}**",
        "",
        "This stage validates the repeating underlying pattern only. Contract selection and real option premium P/L are intentionally not optimized yet.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    raw = fetch_bars()
    by_day = regular_session_bars(raw)
    trades = []
    complete_days = 0

    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        add_session_vwap(bars)
        orb = opening_range(bars)
        if orb is None:
            continue
        complete_days += 1
        or_high, or_low = orb
        call = find_side_trade(day, bars, "CALL", or_high, or_low)
        put = find_side_trade(day, bars, "PUT", or_high, or_low)
        if call:
            trades.append(call)
        if put:
            trades.append(put)

    trades.sort(key=lambda t: t["entry_ts"])
    calls = [t for t in trades if t["side"] == "CALL"]
    puts = [t for t in trades if t["side"] == "PUT"]
    result = {
        "strategy": "options_pattern1_or_vwap_retest",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "symbol": SYMBOL,
        "lookback_days": LOOKBACK_DAYS,
        "complete_sessions": complete_days,
        "config": {
            "timeframe": TIMEFRAME,
            "opening_range_minutes": OPENING_RANGE_MINUTES,
            "breakout_buffer_pct": BREAKOUT_BUFFER_PCT,
            "retest_tolerance_pct": RETEST_TOLERANCE_PCT,
            "hold_tolerance_pct": HOLD_TOLERANCE_PCT,
            "retest_window_bars": RETEST_WINDOW_BARS,
            "trigger_window_bars": TRIGGER_WINDOW_BARS,
            "target_r": TARGET_R,
            "latest_entry_et": LATEST_ENTRY.isoformat(),
            "force_exit_et": FORCE_EXIT.isoformat(),
        },
        "overall": summarize(trades),
        "calls": summarize(calls),
        "puts": summarize(puts),
        "trades": trades,
    }
    write_results(result)
    print(json.dumps({k: result[k] for k in ("complete_sessions", "overall", "calls", "puts")}, indent=2))


if __name__ == "__main__":
    main()
