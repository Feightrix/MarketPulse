import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import phase6b_paper_trader as core
import phase6e_2500_paper_trader as control2500

# Blind protocol locked before results were viewed.
# Trigger commit after workflow existed on main: 2026-08-21 ET.
TEST_START = "2026-01-02"
TEST_END = "2026-07-31"
START_EQUITY = 2500.0
NY = ZoneInfo("America/New_York")

# Exact Strategy 2 micro overlay settings as deployed on 2026-08-21.
ARM_PROFIT_PCT = 0.0035
BASE_TRAIL_PCT = 0.0010
TIGHTEN_PROFIT_PCT = 0.0075
TIGHT_TRAIL_PCT = 0.0007
EXECUTION_COST_BPS_PER_FILL = 10.0
REENTRY_COOLDOWN_SECONDS = 30
MAX_ROUND_TRIPS_PER_DAY = 200
MAX_ROUND_TRIPS_PER_SYMBOL_PER_DAY = 30
DAILY_DRAWDOWN_KILL_PCT = 0.0125

# Backtest approximation: deployed monitor samples every 15 seconds, but the blind
# historical test uses Alpaca IEX 1-minute bars and one close-price observation per
# eligible monitor minute. Re-entry therefore occurs no earlier than the next minute.
TIMEFRAME = "1Min"
DATA_FEED = "iex"

RESULT_FILE = "strategy2_micro_blind_results.json"
SUMMARY_FILE = "strategy2_micro_blind_summary.md"


def credentials():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 paper credentials are not configured")
    os.environ["ALPACA_PAPER_API_KEY_ID"] = key
    os.environ["ALPACA_PAPER_API_SECRET_KEY"] = secret
    os.environ.pop("ALPACA_API_KEY_ID", None)
    os.environ.pop("ALPACA_API_SECRET_KEY", None)


def request_json(url, retries=5):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=core.data_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            if attempt + 1 == retries:
                break
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"Data request failed after {retries} attempts: {last}")


def fetch_minute_bars(symbols):
    start = f"{TEST_START}T13:00:00Z"
    end = "2026-08-01T01:00:00Z"
    rows = {s: [] for s in symbols}
    token = None
    pages = 0
    while True:
        q = {
            "symbols": ",".join(symbols),
            "timeframe": TIMEFRAME,
            "start": start,
            "end": end,
            "adjustment": "all",
            "feed": DATA_FEED,
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            q["page_token"] = token
        url = f"{core.DATA_BASE}/v2/stocks/bars?" + urllib.parse.urlencode(q)
        payload = request_json(url)
        for sym, vals in (payload.get("bars") or {}).items():
            if sym in rows:
                rows[sym].extend(vals or [])
        token = payload.get("next_page_token")
        pages += 1
        if not token:
            break
        if pages > 500:
            raise RuntimeError("Minute-bar pagination exceeded safety limit")
    frames = {}
    for sym in symbols:
        vals = rows.get(sym) or []
        if not vals:
            raise RuntimeError(f"No blind minute bars returned for {sym}")
        d = pd.DataFrame(vals)
        d["ts"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert(NY)
        d = d.set_index("ts").sort_index()
        frames[sym] = d[["o", "h", "l", "c"]].astype(float)
    return frames, pages


def combined_at(c, i):
    trend = core.trend_target(c, i)
    neutral = core.neutral_target(c, i)
    out = {}
    for s, w in trend.items():
        out[s] = out.get(s, 0.0) + core.TREND_WEIGHT * float(w)
    for s, w in neutral.items():
        out[s] = out.get(s, 0.0) + core.NEUTRAL_WEIGHT * float(w)
    return {s: float(w) for s, w in out.items() if abs(float(w)) > 1e-9}


def month_signal_schedule(closes):
    idx = list(closes.index)
    test_dates = [d for d in idx if TEST_START <= str(d) <= TEST_END]
    if not test_dates:
        raise RuntimeError("No daily dates in blind interval")
    by_month = {}
    for d in test_dates:
        key = str(d)[:7]
        by_month.setdefault(key, d)
    schedule = {}
    union = set()
    for month, first_day in sorted(by_month.items()):
        pos = idx.index(first_day)
        if pos <= 0:
            raise RuntimeError(f"No prior signal day for {first_day}")
        signal_i = pos - 1
        weights = combined_at(closes, signal_i)
        schedule[month] = {
            "first_trading_day": str(first_day),
            "signal_date": str(idx[signal_i]),
            "weights": weights,
        }
        union.update(weights)
    return schedule, sorted(union)


def monitor_active(ts):
    local = ts.astimezone(NY)
    hhmm = local.hour * 60 + local.minute
    if hhmm < 9 * 60 + 35 or hhmm >= 16 * 60:
        return False
    return local.minute >= 35 or local.minute <= 29


def mark_value(cash, positions, prices):
    return float(cash + sum(float(q) * float(prices[s]) for s, q in positions.items()))


def apply_control_rebalance(cash, positions, desired, prices):
    cost_rate = EXECUTION_COST_BPS_PER_FILL / 10000.0
    for sym in sorted(set(positions) | set(desired)):
        cur = float(positions.get(sym, 0.0))
        tgt = float(desired.get(sym, 0.0))
        delta = tgt - cur
        if abs(delta) < 1e-12:
            continue
        px = float(prices[sym])
        cash -= delta * px
        cash -= abs(delta) * px * cost_rate
    positions = {s: float(q) for s, q in desired.items() if abs(float(q)) > 1e-12}
    return cash, positions


def entry_effective(qty, raw):
    c = EXECUTION_COST_BPS_PER_FILL / 10000.0
    return raw * (1.0 + c) if qty > 0 else raw * (1.0 - c)


def exit_effective(qty, raw):
    c = EXECUTION_COST_BPS_PER_FILL / 10000.0
    return raw * (1.0 - c) if qty > 0 else raw * (1.0 + c)


def enter_micro(state, sym, raw, ts):
    qty = float(state["template"].get(sym, 0.0))
    if abs(qty) < 1e-12:
        return
    d = state["daily"]
    if d["kill_switch"] or d["round_trips"] >= MAX_ROUND_TRIPS_PER_DAY:
        return
    if d["per_symbol_round_trips"].get(sym, 0) >= MAX_ROUND_TRIPS_PER_SYMBOL_PER_DAY:
        return
    eff = entry_effective(qty, raw)
    state["cash"] -= qty * eff
    state["positions"][sym] = qty
    state["cycle"][sym] = {
        "entry_price": raw,
        "entry_effective_price": eff,
        "best_price": raw,
        "armed": False,
        "stop_price": None,
        "entry_ts": ts,
    }
    d["entries"] += 1
    d["modeled_costs"] += abs(qty) * abs(raw - eff)


def exit_micro(state, sym, raw, ts, reason):
    if sym not in state["positions"]:
        return
    qty = float(state["positions"][sym])
    cyc = state["cycle"].get(sym, {})
    eff = exit_effective(qty, raw)
    state["cash"] += qty * eff
    entry_eff = float(cyc.get("entry_effective_price", cyc.get("entry_price", raw)))
    pnl = qty * (eff - entry_eff) if qty > 0 else abs(qty) * (entry_eff - eff)
    d = state["daily"]
    d["exits"] += 1
    d["round_trips"] += 1
    d["realized_pnl"] += pnl
    d["modeled_costs"] += abs(qty) * abs(raw - eff)
    d["per_symbol_round_trips"][sym] = d["per_symbol_round_trips"].get(sym, 0) + 1
    state["total_realized_pnl"] += pnl
    state["positions"].pop(sym, None)
    state["cycle"].pop(sym, None)
    state["next_reentry"][sym] = ts + timedelta(seconds=REENTRY_COOLDOWN_SECONDS)
    state["events"].append({
        "ts": ts.isoformat(),
        "event": "EXIT",
        "reason": reason,
        "symbol": sym,
        "net_pnl": pnl,
    })


def update_stops(state, prices, ts):
    c = EXECUTION_COST_BPS_PER_FILL / 10000.0
    for sym in sorted(list(state["positions"])):
        qty = float(state["positions"][sym])
        px = float(prices[sym])
        cyc = state["cycle"][sym]
        entry = float(cyc["entry_price"])
        best = float(cyc["best_price"])
        best = max(best, px) if qty > 0 else min(best, px)
        cyc["best_price"] = best
        gain = best / entry - 1.0 if qty > 0 else entry / best - 1.0
        if (not cyc["armed"]) and gain >= ARM_PROFIT_PCT:
            cyc["armed"] = True
        if not cyc["armed"]:
            continue
        trail = TIGHT_TRAIL_PCT if gain >= TIGHTEN_PROFIT_PCT else BASE_TRAIL_PCT
        if qty > 0:
            be = entry * (1.0 + c) / (1.0 - c)
            candidate = best * (1.0 - trail)
            stop = max(float(cyc.get("stop_price") or 0.0), be, candidate)
            hit = px <= stop
        else:
            be = entry * (1.0 - c) / (1.0 + c)
            candidate = best * (1.0 + trail)
            prior = cyc.get("stop_price")
            stop = min(float(prior) if prior is not None else float("inf"), be, candidate)
            hit = px >= stop
        cyc["stop_price"] = stop
        if hit:
            exit_micro(state, sym, px, ts, "MICRO_PROFIT_LOCK")


def flatten_micro(state, prices, ts, reason):
    for sym in sorted(list(state["positions"])):
        exit_micro(state, sym, float(prices[sym]), ts, reason)


def reset_daily(state, date_key, prices):
    start_eq = mark_value(state["cash"], state["positions"], prices)
    state["daily"] = {
        "date": date_key,
        "start_equity": start_eq,
        "entries": 0,
        "exits": 0,
        "round_trips": 0,
        "per_symbol_round_trips": {},
        "realized_pnl": 0.0,
        "modeled_costs": 0.0,
        "kill_switch": False,
    }


def summary_stats(curve, start_equity):
    s = pd.Series([v for _, v in curve], index=pd.to_datetime([d for d, _ in curve]))
    ret = float((s.iloc[-1] / start_equity - 1.0) * 100.0)
    dd = float((1.0 - s / s.cummax()).max() * 100.0)
    daily_change = s.diff().dropna()
    return {
        "final_equity": float(s.iloc[-1]),
        "total_return_pct": ret,
        "max_drawdown_pct": dd,
        "avg_daily_pnl": float(daily_change.mean()) if len(daily_change) else 0.0,
        "median_daily_pnl": float(daily_change.median()) if len(daily_change) else 0.0,
        "positive_day_rate_pct": float((daily_change > 0).mean() * 100.0) if len(daily_change) else 0.0,
        "trading_days": int(len(s)),
    }


def main():
    credentials()
    _, closes = core.load_panel()
    schedule, active_symbols = month_signal_schedule(closes)
    metadata = {s: core.asset(s) for s in active_symbols}
    frames, pages = fetch_minute_bars(active_symbols)

    panel = pd.concat({s: f["c"] for s, f in frames.items()}, axis=1).sort_index()
    panel = panel[(panel.index.date >= pd.Timestamp(TEST_START).date()) &
                  (panel.index.date <= pd.Timestamp(TEST_END).date())]
    panel = panel.between_time("09:30", "16:00", inclusive="left").ffill()
    panel = panel.dropna(how="any")
    if panel.empty:
        raise RuntimeError("Blind minute panel is empty")

    control_cash = START_EQUITY
    control_positions = {}
    micro = {
        "cash": START_EQUITY,
        "positions": {},
        "cycle": {},
        "next_reentry": {},
        "template": {},
        "daily": {},
        "events": [],
        "total_realized_pnl": 0.0,
        "round_trips": 0,
        "kill_days": 0,
    }

    current_date = None
    monthly_template = None
    control_curve = []
    micro_curve = []
    daily_records = []
    first_active_seen = False
    first_days = {m: v["first_trading_day"] for m, v in schedule.items()}

    for ts, row in panel.iterrows():
        ts = ts.to_pydatetime()
        date_key = ts.date().isoformat()
        month = date_key[:7]
        prices = {s: float(row[s]) for s in active_symbols}

        if current_date != date_key:
            if current_date is not None:
                c_eq = mark_value(control_cash, control_positions, last_prices)
                m_eq = mark_value(micro["cash"], micro["positions"], last_prices)
                control_curve.append((current_date, c_eq))
                micro_curve.append((current_date, m_eq))
                d = dict(micro["daily"])
                d["end_equity"] = m_eq
                daily_records.append(d)
                micro["round_trips"] += int(d["round_trips"])
                if d["kill_switch"]:
                    micro["kill_days"] += 1
            current_date = date_key
            reset_daily(micro, date_key, prices)
        last_prices = prices

        if month in schedule and date_key == first_days[month] and ts.hour == 11 and ts.minute == 5:
            signal = schedule[month]
            weights = signal["weights"]
            control_eq = mark_value(control_cash, control_positions, prices)
            desired, _ = control2500.build_2500_quantities(weights, control_eq, prices, metadata)
            control_cash, control_positions = apply_control_rebalance(
                control_cash, control_positions, desired, prices
            )
            monthly_template = desired

        if month in schedule and date_key == first_days[month] and ts.hour == 11 and ts.minute == 10:
            if monthly_template is None:
                raise RuntimeError(f"Missing Control template for {month}")
            if micro["positions"]:
                flatten_micro(micro, prices, ts, "MONTHLY_TEMPLATE_TRANSITION")
            micro["template"] = dict(monthly_template)
            micro["cycle"] = {}
            micro["next_reentry"] = {}
            first_active_seen = True

        if first_active_seen and monitor_active(ts):
            d = micro["daily"]
            if not d["kill_switch"]:
                for sym in sorted(micro["template"]):
                    if sym in micro["positions"]:
                        continue
                    next_ts = micro["next_reentry"].get(sym)
                    if next_ts is not None and ts < next_ts:
                        continue
                    enter_micro(micro, sym, prices[sym], ts)
                update_stops(micro, prices, ts)
                eq = mark_value(micro["cash"], micro["positions"], prices)
                start_eq = float(d["start_equity"])
                if start_eq > 0 and eq <= start_eq * (1.0 - DAILY_DRAWDOWN_KILL_PCT):
                    flatten_micro(micro, prices, ts, "DAILY_DRAWDOWN_KILL")
                    d["kill_switch"] = True

    if current_date is not None:
        c_eq = mark_value(control_cash, control_positions, last_prices)
        m_eq = mark_value(micro["cash"], micro["positions"], last_prices)
        control_curve.append((current_date, c_eq))
        micro_curve.append((current_date, m_eq))
        d = dict(micro["daily"])
        d["end_equity"] = m_eq
        daily_records.append(d)
        micro["round_trips"] += int(d["round_trips"])
        if d["kill_switch"]:
            micro["kill_days"] += 1

    control_stats = summary_stats(control_curve, START_EQUITY)
    micro_stats = summary_stats(micro_curve, START_EQUITY)
    avg_rts = float(np.mean([d["round_trips"] for d in daily_records])) if daily_records else 0.0
    median_rts = float(np.median([d["round_trips"] for d in daily_records])) if daily_records else 0.0
    max_rts = int(max((d["round_trips"] for d in daily_records), default=0))
    total_costs = float(sum(d["modeled_costs"] for d in daily_records))

    checks = {
        "micro_net_positive_after_costs": micro_stats["final_equity"] > START_EQUITY,
        "micro_beats_control_total_return": micro_stats["total_return_pct"] > control_stats["total_return_pct"],
        "micro_drawdown_within_control_plus_1pp":
            micro_stats["max_drawdown_pct"] <= control_stats["max_drawdown_pct"] + 1.0,
    }
    stretch = {
        "avg_daily_pnl_at_least_7_50": micro_stats["avg_daily_pnl"] >= 7.50,
        "avg_round_trips_per_day_at_least_25": avg_rts >= 25.0,
    }
    gate = "PASS" if all(checks.values()) else "FAIL"

    result = {
        "experiment": "CONTROL_CLONE_MICRO_PROFIT_LOCK_BLIND_BACKTEST",
        "blind_protocol_locked_before_results": True,
        "test_start": TEST_START,
        "test_end": TEST_END,
        "data": {
            "source": "Alpaca IEX historical bars",
            "timeframe": TIMEFRAME,
            "minute_bar_pages": pages,
            "approximation": "one close-price sample per eligible monitor minute; deployed live shadow samples every 15 seconds",
            "signal_rule": "monthly Control weights generated from prior trading day's daily close to avoid look-ahead",
        },
        "parameters": {
            "arm_profit_pct": ARM_PROFIT_PCT,
            "base_trail_pct": BASE_TRAIL_PCT,
            "tighten_profit_pct": TIGHTEN_PROFIT_PCT,
            "tight_trail_pct": TIGHT_TRAIL_PCT,
            "execution_cost_bps_per_fill": EXECUTION_COST_BPS_PER_FILL,
            "reentry_cooldown_seconds": REENTRY_COOLDOWN_SECONDS,
            "max_round_trips_per_day": MAX_ROUND_TRIPS_PER_DAY,
            "max_round_trips_per_symbol_per_day": MAX_ROUND_TRIPS_PER_SYMBOL_PER_DAY,
            "daily_drawdown_kill_pct": DAILY_DRAWDOWN_KILL_PCT,
        },
        "monthly_signal_schedule": schedule,
        "control": control_stats,
        "micro": {
            **micro_stats,
            "total_round_trips": int(micro["round_trips"]),
            "avg_round_trips_per_day": avg_rts,
            "median_round_trips_per_day": median_rts,
            "max_round_trips_in_day": max_rts,
            "kill_switch_days": int(micro["kill_days"]),
            "total_realized_exit_pnl": float(micro["total_realized_pnl"]),
            "total_modeled_execution_costs": total_costs,
        },
        "gate_checks": checks,
        "stretch_checks": stretch,
        "gate": gate,
        "no_post_result_tuning": True,
        "research_only": True,
    }
    Path(RESULT_FILE).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Strategy 2 Micro Profit-Lock — Blind Backtest",
        "",
        f"**Gate: {gate}**",
        "",
        "## Locked protocol",
        f"- Holdout: **{TEST_START} through {TEST_END}**",
        "- Parameters were frozen before results were viewed.",
        "- Historical data: Alpaca IEX **1-minute** bars.",
        "- Live monitor is 15-second sampling; this test uses one close-price observation per eligible minute.",
        f"- Modeled execution cost: **{EXECUTION_COST_BPS_PER_FILL:.0f} bps per fill**.",
        "- No parameter changes are permitted after viewing this result.",
        "",
        "## Control",
        f"- Final equity: **${control_stats['final_equity']:.2f}**",
        f"- Total return: **{control_stats['total_return_pct']:+.2f}%**",
        f"- Max drawdown: **{control_stats['max_drawdown_pct']:.2f}%**",
        f"- Average daily P&L: **${control_stats['avg_daily_pnl']:+.2f}**",
        "",
        "## Micro profit-lock",
        f"- Final equity: **${micro_stats['final_equity']:.2f}**",
        f"- Total return: **{micro_stats['total_return_pct']:+.2f}%**",
        f"- Max drawdown: **{micro_stats['max_drawdown_pct']:.2f}%**",
        f"- Average daily P&L: **${micro_stats['avg_daily_pnl']:+.2f}**",
        f"- Positive-day rate: **{micro_stats['positive_day_rate_pct']:.1f}%**",
        f"- Total round trips: **{micro['round_trips']}**",
        f"- Average round trips/day: **{avg_rts:.1f}**",
        f"- Max round trips in one day: **{max_rts}**",
        f"- Kill-switch days: **{micro['kill_days']}**",
        f"- Total modeled execution costs: **${total_costs:.2f}**",
        "",
        "## Blind gate checks",
    ]
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "## Stretch checks"]
    for k, v in stretch.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += [
        "",
        "## Interpretation rule",
        "If the gate fails, the current micro-profit concept is rejected as configured. We do not loosen the gate or retune these parameters using this holdout.",
        "",
        "Research only. Paper/simulated performance does not guarantee live execution or returns.",
    ]
    Path(SUMMARY_FILE).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
