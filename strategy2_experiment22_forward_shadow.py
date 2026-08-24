import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import strategy2_experiment17b_volatile_5m_pattern_scan as base
import strategy2_experiment18_volatile_leader_trendday as e18
import strategy2_experiment20_exceptional_runner as e20
import strategy2_experiment22_exceptional_runner_early_turn as e22

EXPERIMENT = "S2-E22-EARLY-TURN-FORWARD-SHADOW"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LIVE_TRADING_LOCKED = True
LONG_ONLY = True
LEVERAGE = False
FEED = "sip"
ET = ZoneInfo("America/New_York")
DATA_BASE = "https://data.alpaca.markets"
START_EQ = 2500.0
LOOKBACK_CALENDAR_DAYS = 150
LOG_PATH = Path("strategy2_experiment22_forward_shadow_log.json")
SUMMARY_PATH = Path("strategy2_experiment22_forward_shadow_summary.md")

# Forward promotion gate is predeclared before any new-session results.
FORWARD_MIN_TRADES = 15
FORWARD_MIN_PROFIT_FACTOR = 1.30
FORWARD_MAX_DRAWDOWN_PCT = 5.0


def headers():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca credentials required")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_json(url, params, tries=8):
    for attempt in range(tries):
        r = requests.get(url, headers=headers(), params=params, timeout=90)
        if r.status_code == 429:
            time.sleep(1.5 + 1.5 * attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Repeated rate limit: {url}")


def fetch_symbol_bars(symbol, start_day, end_day):
    rows, token = [], None
    params = {
        "timeframe": "5Min",
        "start": f"{start_day}T12:00:00Z",
        "end": f"{end_day}T23:00:00Z",
        "limit": 10000,
        "adjustment": "all",
        "feed": FEED,
        "sort": "asc",
    }
    while True:
        p = dict(params)
        if token:
            p["page_token"] = token
        payload = get_json(f"{DATA_BASE}/v2/stocks/{symbol}/bars", p)
        rows.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    x = pd.DataFrame(rows)
    x["ts"] = pd.to_datetime(x["t"], utc=True).dt.tz_convert(ET)
    x = x.set_index("ts").sort_index()
    return x[(x.index.time >= dtime(9, 30)) & (x.index.time <= dtime(16, 0))].copy()


def load_panels(today):
    start = today - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    end = today + timedelta(days=1)
    symbols = list(base.CANDIDATES) + list(base.BENCHMARKS)
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_symbol_bars, s, start.isoformat(), end.isoformat()): s for s in symbols}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


def load_log():
    if not LOG_PATH.exists():
        return {"experiment": EXPERIMENT, "start_equity": START_EQ, "entries": []}
    with LOG_PATH.open() as f:
        data = json.load(f)
    data.setdefault("experiment", EXPERIMENT)
    data.setdefault("start_equity", START_EQ)
    data.setdefault("entries", [])
    return data


def equity_before_day(log, day_str):
    prior = [e for e in log["entries"] if str(e.get("date", "")) < day_str]
    if not prior:
        return START_EQ
    prior.sort(key=lambda x: x["date"])
    return float(prior[-1].get("equity_after", START_EQ))


def compute_running_stats(entries):
    trades = [e["trade"] for e in entries if isinstance(e.get("trade"), dict)]
    eq_curve = [START_EQ]
    eq = START_EQ
    gp = gl = 0.0
    wins = 0
    returns = []
    for e in sorted(entries, key=lambda x: x["date"]):
        if not isinstance(e.get("trade"), dict):
            continue
        pnl = float(e["trade"].get("pnl", 0.0))
        prev = eq
        eq += pnl
        eq_curve.append(eq)
        ret = pnl / prev * 100.0 if prev > 0 else 0.0
        returns.append(ret)
        if pnl > 0:
            wins += 1
            gp += pnl
        elif pnl < 0:
            gl += -pnl
    s = pd.Series(eq_curve, dtype=float)
    dd = float((1.0 - s / s.cummax()).max() * 100.0) if len(s) else 0.0
    pf = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)
    return {
        "trades": len(trades),
        "wins": wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "equity": eq,
        "return_pct": (eq / START_EQ - 1.0) * 100.0,
        "profit_factor": pf,
        "max_drawdown_pct": dd,
        "avg_trade_pnl": float(np.mean([float(t.get("pnl", 0.0)) for t in trades])) if trades else 0.0,
        "avg_account_return_on_trade_days_pct": float(np.mean(returns)) if returns else 0.0,
        "days_ge_1pct": int(sum(x >= 1.0 for x in returns)),
        "days_ge_3pct": int(sum(x >= 3.0 for x in returns)),
        "days_ge_5pct": int(sum(x >= 5.0 for x in returns)),
    }


def build_entry(today, panels, log):
    day_str = today.isoformat()
    spy_today = base.day_slice(panels.get("SPY", pd.DataFrame()), today)
    if spy_today.empty or len(spy_today) < 60:
        eq = equity_before_day(log, day_str)
        return {"date": day_str, "status": "NO_REGULAR_SESSION_DATA", "equity_before": eq, "equity_after": eq, "trade": None}

    dstats = {s: base.daily_stats(panels[s]) for s in base.CANDIDATES + base.BENCHMARKS}
    ostats = {s: e18.opening_stats(panels[s]) for s in e22.CANDIDATES}
    otables = {s: e20.opening_table(panels[s], dstats[s]) for s in e22.CANDIDATES}

    eq_before = equity_before_day(log, day_str)
    snap = e20.exceptional_snapshot(today, panels, dstats, otables)
    if snap is None or not snap["passes"]:
        return {"date": day_str, "status": "NO_EXCEPTIONAL_RUNNER", "equity_before": eq_before, "equity_after": eq_before, "trade": None}

    sym = snap["leader"]["symbol"]
    original_sig = e18.candidate_signal(sym, today, panels, dstats, ostats)
    if original_sig is None:
        return {
            "date": day_str,
            "status": "GATE_PASS_NO_TREND_CANDIDATE",
            "leader": sym,
            "gate": snap,
            "equity_before": eq_before,
            "equity_after": eq_before,
            "trade": None,
        }

    g = base.day_slice(panels[sym], today)
    turn = e22.find_early_turn(g, original_sig)
    if turn is None:
        return {
            "date": day_str,
            "status": "GATE_PASS_NO_EARLY_TURN",
            "leader": sym,
            "gate": snap,
            "equity_before": eq_before,
            "equity_after": eq_before,
            "trade": None,
        }

    sig = dict(original_sig)
    sig["signal_ts"] = turn["signal_ts"]
    tr = e18.simulate(sig, g, eq_before)
    if tr is None:
        return {
            "date": day_str,
            "status": "TURN_FOUND_NO_EXECUTABLE_SHADOW_TRADE",
            "leader": sym,
            "equity_before": eq_before,
            "equity_after": eq_before,
            "trade": None,
        }

    tr["early_turn"] = {
        "pullback_fraction": turn["pullback_fraction"],
        "pullback_time": turn["pullback_time"].isoformat(),
        "turn_time": turn["signal_ts"].isoformat(),
        "turn_close": turn["turn_close"],
        "turn_close_location": turn["turn_close_location"],
        "volume_contraction_ratio": turn["volume_contraction_ratio"],
        "buyer_reexpansion_ratio": turn["buyer_reexpansion_ratio"],
        "turn_vwap": turn["turn_vwap"],
        "opening_high": turn["opening_high"],
    }
    tr["exceptional_gate"] = {
        "return_percentile": snap["leader"]["return_percentile"],
        "volume_percentile": snap["leader"]["volume_percentile"],
        "range_atr_percentile": snap["leader"]["range_atr_percentile"],
        "leader_separation_pct": snap["leader_separation_pct"],
        "relative_strength_pct": snap["leader"]["relative_strength"] * 100.0,
    }
    eq_after = eq_before + float(tr["pnl"])
    return {
        "date": day_str,
        "status": "SHADOW_TRADE_REPLAYED",
        "leader": sym,
        "equity_before": eq_before,
        "equity_after": eq_after,
        "trade": tr,
    }


def write_outputs(log):
    entries = sorted(log["entries"], key=lambda x: x["date"])
    stats = compute_running_stats(entries)
    gate = {
        "min_trades": FORWARD_MIN_TRADES,
        "min_profit_factor": FORWARD_MIN_PROFIT_FACTOR,
        "positive_avg_trade": stats["avg_trade_pnl"] > 0,
        "max_drawdown_pct": FORWARD_MAX_DRAWDOWN_PCT,
    }
    eligible = (
        stats["trades"] >= FORWARD_MIN_TRADES
        and stats["profit_factor"] >= FORWARD_MIN_PROFIT_FACTOR
        and stats["avg_trade_pnl"] > 0
        and stats["max_drawdown_pct"] <= FORWARD_MAX_DRAWDOWN_PCT
    )
    log["running_stats"] = stats
    log["promotion_gate"] = gate
    log["promotion_eligible"] = bool(eligible)
    log["live_trading_locked"] = True
    with LOG_PATH.open("w") as f:
        json.dump(log, f, indent=2)

    lines = [
        "# Experiment 22 — Forward Shadow",
        "",
        "Frozen early-turn rules on new sessions only. This workflow places no broker orders; after the close it causally replays whether the intraday signal would have occurred and records the modeled outcome.",
        "",
        f"- Sessions recorded: {len(entries)}",
        f"- Shadow trades: {stats['trades']}",
        f"- Win rate: {stats['win_rate_pct']:.1f}%",
        f"- Profit factor: {stats['profit_factor']:.2f}",
        f"- Virtual equity: ${stats['equity']:.2f}",
        f"- Total return: {stats['return_pct']:+.2f}%",
        f"- Max drawdown: {stats['max_drawdown_pct']:.2f}%",
        f"- Avg trade P&L: ${stats['avg_trade_pnl']:+.2f}",
        f"- >=1% account days: {stats['days_ge_1pct']} | >=3%: {stats['days_ge_3pct']} | >=5%: {stats['days_ge_5pct']}",
        f"- Promotion eligible: {'YES' if eligible else 'NO'} (requires >= {FORWARD_MIN_TRADES} trades, PF >= {FORWARD_MIN_PROFIT_FACTOR:.2f}, positive avg trade, DD <= {FORWARD_MAX_DRAWDOWN_PCT:.1f}%)",
        "",
        "Live trading remains locked regardless of shadow result.",
    ]
    SUMMARY_PATH.write_text("\n".join(lines) + "\n")


def main():
    today = datetime.now(ET).date()
    panels = load_panels(today)
    missing = [s for s in base.CANDIDATES + base.BENCHMARKS if s not in panels or panels[s].empty]
    if missing:
        raise RuntimeError(f"Missing SIP data: {missing}")

    log = load_log()
    entry = build_entry(today, panels, log)
    log["entries"] = [e for e in log["entries"] if e.get("date") != today.isoformat()]
    log["entries"].append(entry)
    log["entries"].sort(key=lambda x: x["date"])
    write_outputs(log)
    print(json.dumps(entry, indent=2, default=str))


if __name__ == "__main__":
    main()
