import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# SECURITY: monitor is read-only and hard-wired to Alpaca paper trading.
PAPER_BASE = "https://paper-api.alpaca.markets"
STATUS_JSON = "phase6c_forward_status.json"
STATUS_MD = "phase6c_forward_status.md"
LOG_FILE = "phase6c_forward_log.jsonl"
STATE_FILE = "phase6c_forward_state.json"
SIXB_LOG = "phase6b_paper_log.jsonl"
SIXB_STATE = "phase6b_paper_state.json"
SIXB_STATUS = "phase6b_paper_status.json"

MIN_TRADING_DAYS = 126
MIN_REBALANCES = 6
MAX_DRAWDOWN_PCT = 5.0
MIN_POSITIVE_MONTH_RATE_PCT = 66.7
WORST_MONTH_FLOOR_PCT = -2.5
MIN_FILL_RATE_PCT = 99.0
MAX_MEDIAN_ADVERSE_SLIPPAGE_BPS = 15.0
MAX_P95_ADVERSE_SLIPPAGE_BPS = 30.0
MAX_TRACKING_L1_ERROR_PCT = 8.0
ET = ZoneInfo("America/New_York")


def credentials():
    key = os.getenv("ALPACA_PAPER_API_KEY_ID")
    secret = os.getenv("ALPACA_PAPER_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Dedicated Alpaca paper credentials are required")
    return key, secret


def headers():
    key, secret = credentials()
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}


def api(path, timeout=45):
    if PAPER_BASE != "https://paper-api.alpaca.markets":
        raise RuntimeError("Paper endpoint security invariant violated")
    req = urllib.request.Request(PAPER_BASE + path, headers=headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Paper API GET {path} failed: HTTP {e.code}: {detail}") from e


def load_json(path, default=None):
    if not Path(path).exists():
        return default
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_json(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def save_daily_snapshot(snapshot):
    rows = load_jsonl(LOG_FILE)
    by_date = {r.get("date_et"): r for r in rows if r.get("date_et")}
    by_date[snapshot["date_et"]] = snapshot
    ordered = [by_date[k] for k in sorted(by_date)]
    with open(LOG_FILE, "w") as f:
        for row in ordered:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return ordered


def first_rebalance_event():
    for event in load_jsonl(SIXB_LOG):
        if event.get("status") == "REBALANCE_COMPLETE":
            return event
    return None


def current_target_weights():
    s = load_json(SIXB_STATUS, {}) or {}
    return {k: float(v) for k, v in (s.get("target_weights") or {}).items()}


def positions_snapshot(rows, equity):
    pos = {}
    weights = {}
    for r in rows or []:
        sym = r.get("symbol")
        mv = float(r.get("market_value") or 0.0)
        qty = float(r.get("qty") or 0.0)
        pos[sym] = {
            "qty": qty,
            "market_value": mv,
            "current_price": float(r.get("current_price") or 0.0),
            "avg_entry_price": float(r.get("avg_entry_price") or 0.0),
            "unrealized_pl": float(r.get("unrealized_pl") or 0.0),
        }
        if equity > 0:
            weights[sym] = mv / equity
    return pos, weights


def tracking_error(actual, target):
    if not target:
        return None
    syms = set(actual) | set(target)
    # L1 distance in percentage points. Cash/unallocated weight is intentionally excluded.
    return sum(abs(actual.get(s, 0.0) - target.get(s, 0.0)) for s in syms) * 100.0


def metrics_from_snapshots(rows, baseline_equity):
    if not rows:
        return {
            "trading_days": 0,
            "cumulative_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "positive_day_rate_pct": 0.0,
            "monthly_returns_pct": {},
            "completed_months": 0,
            "positive_month_rate_pct": 0.0,
            "worst_month_pct": 0.0,
        }
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date_et"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    e = df["equity"].astype(float)
    cumulative = float((e.iloc[-1] / baseline_equity - 1.0) * 100.0) if baseline_equity > 0 else 0.0
    peak = e.cummax()
    max_dd = float(((1.0 - e / peak).max()) * 100.0)
    daily = e.pct_change().dropna()
    pos_days = float((daily > 0).mean() * 100.0) if len(daily) else 0.0

    month_end = e.resample("ME").last()
    month_ret = month_end.pct_change()
    if len(month_end):
        first_month = month_end.index[0]
        month_ret.iloc[0] = month_end.iloc[0] / baseline_equity - 1.0
    now_month = pd.Timestamp(datetime.now(ET).date()).to_period("M")
    completed = {str(idx.to_period("M")): float(ret * 100.0) for idx, ret in month_ret.dropna().items()
                 if idx.to_period("M") < now_month}
    vals = list(completed.values())
    pos_month_rate = float(sum(v > 0 for v in vals) / len(vals) * 100.0) if vals else 0.0
    worst_month = min(vals) if vals else 0.0
    return {
        "trading_days": int(len(e)),
        "cumulative_return_pct": cumulative,
        "max_drawdown_pct": max_dd,
        "positive_day_rate_pct": pos_days,
        "monthly_returns_pct": completed,
        "completed_months": len(vals),
        "positive_month_rate_pct": pos_month_rate,
        "worst_month_pct": worst_month,
    }


def all_marketpulse_orders(after_iso):
    q = {
        "status": "all",
        "limit": 500,
        "direction": "asc",
        "after": after_iso,
    }
    rows = api("/v2/orders?" + urllib.parse.urlencode(q)) or []
    return [r for r in rows if str(r.get("client_order_id", "")).startswith("mp6b-")]


def execution_reference_map():
    refs = {}
    for event in load_jsonl(SIXB_LOG):
        if event.get("status") != "REBALANCE_COMPLETE":
            continue
        tracking = event.get("tracking") or {}
        for action in event.get("actions") or []:
            oid = action.get("order_id")
            sym = action.get("symbol")
            if oid and sym in tracking:
                refs[oid] = float(tracking[sym].get("reference_price") or 0.0)
    return refs


def percentile(values, q):
    if not values:
        return None
    return float(np.percentile(np.array(values, dtype=float), q))


def execution_metrics(orders):
    terminal = [o for o in orders if o.get("status") in {"filled", "canceled", "expired", "rejected", "suspended"}]
    filled = [o for o in terminal if o.get("status") == "filled"]
    failed = [o for o in terminal if o.get("status") != "filled"]
    fill_rate = float(len(filled) / len(terminal) * 100.0) if terminal else 100.0
    refs = execution_reference_map()
    adverse = []
    for o in filled:
        ref = refs.get(o.get("id"))
        avg = o.get("filled_avg_price")
        if not ref or not avg:
            continue
        avg = float(avg)
        if ref <= 0 or avg <= 0:
            continue
        if o.get("side") == "buy":
            slip = (avg / ref - 1.0) * 10000.0
        else:
            slip = (1.0 - avg / ref) * 10000.0
        adverse.append(float(slip))
    return {
        "order_count": len(orders),
        "terminal_order_count": len(terminal),
        "filled_order_count": len(filled),
        "failed_order_count": len(failed),
        "fill_rate_pct": fill_rate,
        "measured_slippage_orders": len(adverse),
        "median_adverse_slippage_bps": float(statistics.median(adverse)) if adverse else None,
        "p95_adverse_slippage_bps": percentile(adverse, 95),
        "worst_adverse_slippage_bps": max(adverse) if adverse else None,
    }


def gate_checks(perf, execution, rebalances, tracking_l1):
    matured = perf["trading_days"] >= MIN_TRADING_DAYS and rebalances >= MIN_REBALANCES
    checks = {
        "minimum_126_trading_days": perf["trading_days"] >= MIN_TRADING_DAYS,
        "minimum_6_rebalances": rebalances >= MIN_REBALANCES,
        "cumulative_return_positive": perf["cumulative_return_pct"] > 0.0,
        "max_drawdown_at_most_5pct": perf["max_drawdown_pct"] <= MAX_DRAWDOWN_PCT,
        "positive_month_rate_at_least_66_7pct": perf["positive_month_rate_pct"] >= MIN_POSITIVE_MONTH_RATE_PCT if perf["completed_months"] else False,
        "worst_completed_month_above_minus_2_5pct": perf["worst_month_pct"] >= WORST_MONTH_FLOOR_PCT if perf["completed_months"] else False,
        "fill_rate_at_least_99pct": execution["fill_rate_pct"] >= MIN_FILL_RATE_PCT,
        "zero_failed_orders": execution["failed_order_count"] == 0,
        "median_adverse_slippage_at_most_15bps": execution["median_adverse_slippage_bps"] is None or execution["median_adverse_slippage_bps"] <= MAX_MEDIAN_ADVERSE_SLIPPAGE_BPS,
        "p95_adverse_slippage_at_most_30bps": execution["p95_adverse_slippage_bps"] is None or execution["p95_adverse_slippage_bps"] <= MAX_P95_ADVERSE_SLIPPAGE_BPS,
        "tracking_l1_error_at_most_8pct": tracking_l1 is None or tracking_l1 <= MAX_TRACKING_L1_ERROR_PCT,
    }
    performance_checks = {k: v for k, v in checks.items() if not k.startswith("minimum_")}
    if not matured:
        decision = "FORWARD_TEST_ACCUMULATING"
    elif all(performance_checks.values()):
        decision = "PROMOTION_REVIEW_ELIGIBLE"
    else:
        decision = "FORWARD_TEST_FAIL"
    return checks, decision


def write_status(status):
    save_json(STATUS_JSON, status)
    perf = status.get("performance") or {}
    exe = status.get("execution") or {}
    lines = [
        "# MarketPulse Phase 6C — Forward-Test Governance",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Timestamp UTC: {status.get('timestamp_utc')}",
        f"- Paper endpoint only: **{PAPER_BASE}**",
        f"- Real-money trading: **LOCKED**",
    ]
    if status.get("baseline_date"):
        lines += [
            f"- Baseline: **{status['baseline_date']}** at **${status['baseline_equity']:,.2f}**",
            f"- Current equity: **${status['current_equity']:,.2f}**",
            f"- Trading days observed: **{perf.get('trading_days', 0)} / {MIN_TRADING_DAYS}**",
            f"- Rebalances observed: **{status.get('rebalance_count', 0)} / {MIN_REBALANCES}**",
            "",
            "## Forward performance",
            f"- Cumulative return: **{perf.get('cumulative_return_pct', 0):+.2f}%**",
            f"- Max drawdown: **{perf.get('max_drawdown_pct', 0):.2f}%**",
            f"- Positive completed months: **{perf.get('positive_month_rate_pct', 0):.1f}%**",
            f"- Worst completed month: **{perf.get('worst_month_pct', 0):+.2f}%**",
            "",
            "## Execution",
            f"- Filled orders: **{exe.get('filled_order_count', 0)} / {exe.get('terminal_order_count', 0)}**",
            f"- Fill rate: **{exe.get('fill_rate_pct', 100):.1f}%**",
            f"- Failed orders: **{exe.get('failed_order_count', 0)}**",
        ]
        med = exe.get("median_adverse_slippage_bps")
        p95 = exe.get("p95_adverse_slippage_bps")
        lines.append(f"- Median adverse slippage: **{'N/A' if med is None else f'{med:.2f} bps'}**")
        lines.append(f"- 95th percentile adverse slippage: **{'N/A' if p95 is None else f'{p95:.2f} bps'}**")
        te = status.get("tracking_l1_error_pct")
        lines.append(f"- Current target tracking L1 error: **{'N/A' if te is None else f'{te:.2f}%'}**")
    if status.get("problems"):
        lines += ["", "## Blockers"] + [f"- {x}" for x in status["problems"]]
    if status.get("gate_checks"):
        lines += ["", "## Promotion gate"]
        for k, v in status["gate_checks"].items():
            lines.append(f"- {'PASS' if v else 'WAIT/FAIL'} — {k}")
    lines += [
        "",
        "## Rule",
        "Phase 6C can only mark the strategy **PROMOTION_REVIEW_ELIGIBLE**. It cannot enable live trading or modify Phase 5H parameters.",
        "",
        "Paper trading is simulated and does not guarantee live-trading results.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    base = {"phase": "6C", "timestamp_utc": now.isoformat(), "paper_base": PAPER_BASE, "live_trading_locked": True}

    sixa = load_json("phase6a_results.json", {}) or {}
    if sixa.get("gate") != "PASS" or not sixa.get("paper_trading_authorized"):
        write_status({**base, "status": "BLOCKED_PHASE6A", "problems": ["Phase 6A authorization is missing"]})
        return

    first = first_rebalance_event()
    if not first:
        write_status({**base, "status": "WAITING_FIRST_PAPER_REBALANCE",
                      "problems": ["No completed Phase 6B paper rebalance has occurred yet"]})
        return

    try:
        account = api("/v2/account")
        positions = api("/v2/positions") or []
    except Exception as e:
        write_status({**base, "status": "PAPER_READ_FAILED", "problems": [str(e)]})
        return

    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        write_status({**base, "status": "PAPER_ACCOUNT_BLOCKED", "problems": ["Paper account is not active/tradable"]})
        return

    baseline_equity = float(first.get("account_equity") or 0.0)
    baseline_ts = first.get("timestamp_utc")
    if baseline_equity <= 0 or not baseline_ts:
        write_status({**base, "status": "BASELINE_INVALID", "problems": ["First paper rebalance lacks a valid equity baseline"]})
        return

    equity = float(account.get("equity") or 0.0)
    pos_detail, actual_weights = positions_snapshot(positions, equity)
    target = current_target_weights()
    tracking_l1 = tracking_error(actual_weights, target)
    snapshot = {
        "date_et": now.astimezone(ET).date().isoformat(),
        "timestamp_utc": now.isoformat(),
        "equity": equity,
        "cash": float(account.get("cash") or 0.0),
        "buying_power": float(account.get("buying_power") or 0.0),
        "long_market_value": float(account.get("long_market_value") or 0.0),
        "short_market_value": float(account.get("short_market_value") or 0.0),
        "positions": pos_detail,
        "actual_weights": actual_weights,
        "target_weights": target,
        "tracking_l1_error_pct": tracking_l1,
    }
    snapshots = save_daily_snapshot(snapshot)
    perf = metrics_from_snapshots(snapshots, baseline_equity)

    sixb_state = load_json(SIXB_STATE, {}) or {}
    rebalances = int(sixb_state.get("rebalance_count") or 0)
    try:
        orders = all_marketpulse_orders(baseline_ts)
        execution = execution_metrics(orders)
    except Exception as e:
        write_status({**base, "status": "ORDER_HISTORY_READ_FAILED", "baseline_date": baseline_ts,
                      "baseline_equity": baseline_equity, "current_equity": equity, "problems": [str(e)]})
        return

    checks, decision = gate_checks(perf, execution, rebalances, tracking_l1)
    status = {
        **base,
        "status": decision,
        "baseline_date": baseline_ts,
        "baseline_equity": baseline_equity,
        "current_equity": equity,
        "rebalance_count": rebalances,
        "performance": perf,
        "execution": execution,
        "tracking_l1_error_pct": tracking_l1,
        "target_weights": target,
        "actual_weights": actual_weights,
        "gate_checks": checks,
        "promotion_is_review_only": True,
        "strategy_parameters_frozen": True,
    }
    save_json(STATE_FILE, {"baseline_date": baseline_ts, "baseline_equity": baseline_equity,
                           "last_monitor_utc": now.isoformat(), "status": decision})
    write_status(status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
