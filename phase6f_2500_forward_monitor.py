import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

PAPER_BASE = "https://paper-api.alpaca.markets"
ET = ZoneInfo("America/New_York")

STATUS_JSON = "phase6f_2500_forward_status.json"
STATUS_MD = "phase6f_2500_forward_status.md"
LOG_FILE = "phase6f_2500_forward_log.jsonl"
STATE_FILE = "phase6f_2500_forward_state.json"
SIXE_LOG = "phase6e_2500_log.jsonl"
SIXE_STATE = "phase6e_2500_state.json"
SIXE_STATUS = "phase6e_2500_status.json"

DESIGN_CAPITAL = 2500.0
OPERATING_FLOOR = 2200.0
MIN_TRADING_DAYS = 126
MIN_REBALANCES = 6
MAX_DRAWDOWN_PCT = 5.0
MIN_POSITIVE_MONTH_RATE_PCT = 66.7
WORST_MONTH_FLOOR_PCT = -2.5
MIN_FILL_RATE_PCT = 99.0
MAX_MEDIAN_ADVERSE_SLIPPAGE_BPS = 15.0
MAX_P95_ADVERSE_SLIPPAGE_BPS = 30.0
MAX_EXECUTED_TRACKING_L1_PCT = 5.0


def credentials():
    key = os.getenv("ALPACA_2500_PAPER_API_KEY_ID")
    secret = os.getenv("ALPACA_2500_PAPER_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Dedicated $2,500 Alpaca paper credentials are required")
    return key, secret


def api_get(path, timeout=45):
    if PAPER_BASE != "https://paper-api.alpaca.markets":
        raise RuntimeError("Paper endpoint security invariant violated")
    key, secret = credentials()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}
    req = urllib.request.Request(PAPER_BASE + path, headers=headers, method="GET")
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
    return json.loads(Path(path).read_text())


def load_jsonl(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def save_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def save_daily_snapshot(snapshot):
    rows = load_jsonl(LOG_FILE)
    by_date = {r.get("date_et"): r for r in rows if r.get("date_et")}
    by_date[snapshot["date_et"]] = snapshot
    ordered = [by_date[k] for k in sorted(by_date)]
    with open(LOG_FILE, "w") as f:
        for row in ordered:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return ordered


def rebalance_events():
    return [e for e in load_jsonl(SIXE_LOG) if e.get("status") == "REBALANCE_COMPLETE"]


def latest_rebalance_event():
    events = rebalance_events()
    return events[-1] if events else None


def reference_price_map():
    refs = {}
    for event in rebalance_events():
        equity = float(event.get("account_equity") or 0.0)
        qtys = event.get("desired_quantities") or {}
        represented = event.get("represented_weights") or {}
        if equity <= 0:
            continue
        symbol_px = {}
        for sym, qty in qtys.items():
            qty = float(qty)
            w = float(represented.get(sym, 0.0))
            if qty != 0 and w != 0:
                symbol_px[sym] = abs(w * equity / qty)
        for action in event.get("actions") or []:
            oid = action.get("order_id")
            sym = action.get("symbol")
            if oid and sym in symbol_px:
                refs[oid] = symbol_px[sym]
    return refs


def positions_snapshot(rows, equity):
    detail = {}
    weights = {}
    for r in rows or []:
        sym = r.get("symbol")
        mv = float(r.get("market_value") or 0.0)
        detail[sym] = {
            "qty": float(r.get("qty") or 0.0),
            "market_value": mv,
            "current_price": float(r.get("current_price") or 0.0),
            "avg_entry_price": float(r.get("avg_entry_price") or 0.0),
            "unrealized_pl": float(r.get("unrealized_pl") or 0.0),
            "unrealized_plpc": float(r.get("unrealized_plpc") or 0.0),
        }
        if equity > 0:
            weights[sym] = mv / equity
    return detail, weights


def tracking_error(actual_weights, executed_weights):
    syms = set(actual_weights) | set(executed_weights)
    return sum(abs(float(actual_weights.get(s, 0.0)) - float(executed_weights.get(s, 0.0))) for s in syms) * 100.0


def performance_metrics(rows, baseline_equity, today_date):
    ordered = sorted(rows, key=lambda r: r["date_et"])
    equities = [baseline_equity] + [float(r["equity"]) for r in ordered]
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (1.0 - e / peak) * 100.0)

    current = equities[-1]
    cumulative_pnl = current - baseline_equity
    cumulative_return = (current / baseline_equity - 1.0) * 100.0 if baseline_equity > 0 else 0.0

    prev_equity = baseline_equity
    if len(ordered) >= 2:
        prev_equity = float(ordered[-2]["equity"])
    daily_pnl = current - prev_equity
    daily_return = (current / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0

    daily_returns = []
    prior = baseline_equity
    for r in ordered:
        e = float(r["equity"])
        if prior > 0:
            daily_returns.append((e / prior - 1.0) * 100.0)
        prior = e
    positive_day_rate = (sum(x > 0 for x in daily_returns) / len(daily_returns) * 100.0) if daily_returns else 0.0

    month_last = {}
    for r in ordered:
        month_last[r["date_et"][:7]] = float(r["equity"])
    current_month = today_date[:7]
    completed = {}
    prior = baseline_equity
    for month in sorted(month_last):
        e = month_last[month]
        ret = (e / prior - 1.0) * 100.0 if prior > 0 else 0.0
        if month < current_month:
            completed[month] = ret
        prior = e
    vals = list(completed.values())
    positive_month_rate = (sum(v > 0 for v in vals) / len(vals) * 100.0) if vals else 0.0
    worst_month = min(vals) if vals else 0.0

    return {
        "trading_days": len(ordered),
        "daily_pnl_dollars": daily_pnl,
        "daily_return_pct": daily_return,
        "cumulative_pnl_dollars": cumulative_pnl,
        "cumulative_return_pct": cumulative_return,
        "max_drawdown_pct": max_dd,
        "positive_day_rate_pct": positive_day_rate,
        "completed_months": len(vals),
        "positive_month_rate_pct": positive_month_rate,
        "worst_completed_month_pct": worst_month,
        "monthly_returns_pct": completed,
    }


def all_phase6e_orders(after_iso):
    q = {"status": "all", "limit": 500, "direction": "asc", "after": after_iso}
    rows = api_get("/v2/orders?" + urllib.parse.urlencode(q)) or []
    return [r for r in rows if str(r.get("client_order_id", "")).startswith("mp6e-")]


def execution_metrics(orders):
    terminal_statuses = {"filled", "canceled", "expired", "rejected", "suspended"}
    terminal = [o for o in orders if o.get("status") in terminal_statuses]
    filled = [o for o in terminal if o.get("status") == "filled"]
    failed = [o for o in terminal if o.get("status") != "filled"]
    refs = reference_price_map()
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
    fill_rate = (len(filled) / len(terminal) * 100.0) if terminal else 100.0
    return {
        "order_count": len(orders),
        "terminal_order_count": len(terminal),
        "filled_order_count": len(filled),
        "failed_order_count": len(failed),
        "fill_rate_pct": fill_rate,
        "measured_slippage_orders": len(adverse),
        "median_adverse_slippage_bps": float(statistics.median(adverse)) if adverse else None,
        "p95_adverse_slippage_bps": float(np.percentile(np.array(adverse), 95)) if adverse else None,
        "worst_adverse_slippage_bps": max(adverse) if adverse else None,
    }


def gate_checks(perf, execution, rebalances, executed_tracking, current_fit_gate, equity):
    checks = {
        "minimum_126_trading_days": perf["trading_days"] >= MIN_TRADING_DAYS,
        "minimum_6_rebalances": rebalances >= MIN_REBALANCES,
        "cumulative_return_positive": perf["cumulative_return_pct"] > 0.0,
        "max_drawdown_at_most_5pct": perf["max_drawdown_pct"] <= MAX_DRAWDOWN_PCT,
        "positive_month_rate_at_least_66_7pct": perf["positive_month_rate_pct"] >= MIN_POSITIVE_MONTH_RATE_PCT if perf["completed_months"] else False,
        "worst_completed_month_above_minus_2_5pct": perf["worst_completed_month_pct"] >= WORST_MONTH_FLOOR_PCT if perf["completed_months"] else False,
        "fill_rate_at_least_99pct": execution["fill_rate_pct"] >= MIN_FILL_RATE_PCT,
        "zero_failed_orders": execution["failed_order_count"] == 0,
        "median_adverse_slippage_at_most_15bps": execution["median_adverse_slippage_bps"] is None or execution["median_adverse_slippage_bps"] <= MAX_MEDIAN_ADVERSE_SLIPPAGE_BPS,
        "p95_adverse_slippage_at_most_30bps": execution["p95_adverse_slippage_bps"] is None or execution["p95_adverse_slippage_bps"] <= MAX_P95_ADVERSE_SLIPPAGE_BPS,
        "executed_portfolio_tracking_at_most_5pct": executed_tracking <= MAX_EXECUTED_TRACKING_L1_PCT,
        "current_capital_fit_gate_pass": current_fit_gate == "PASS",
        "equity_at_or_above_2200_operating_floor": equity >= OPERATING_FLOOR,
    }
    matured = checks["minimum_126_trading_days"] and checks["minimum_6_rebalances"]
    performance_checks = {k: v for k, v in checks.items() if not k.startswith("minimum_")}
    if equity < OPERATING_FLOOR:
        decision = "OPERATING_FLOOR_BLOCK"
    elif not matured:
        decision = "FORWARD_TEST_ACCUMULATING"
    elif all(performance_checks.values()):
        decision = "PROMOTION_REVIEW_ELIGIBLE"
    else:
        decision = "FORWARD_TEST_FAIL"
    return checks, decision


def write_status(status):
    save_json(STATUS_JSON, status)
    p = status.get("performance") or {}
    e = status.get("execution") or {}
    lines = [
        "# MarketPulse Phase 6F — $2,500 Forward Test",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Timestamp UTC: {status.get('timestamp_utc')}",
        f"- Dedicated paper equity: **${status.get('current_equity', 0):,.2f}**",
        f"- Daily P/L: **${p.get('daily_pnl_dollars', 0):+.2f} ({p.get('daily_return_pct', 0):+.4f}%)**",
        f"- Cumulative P/L: **${p.get('cumulative_pnl_dollars', 0):+.2f} ({p.get('cumulative_return_pct', 0):+.4f}%)**",
        f"- Max drawdown: **{p.get('max_drawdown_pct', 0):.2f}%**",
        f"- Trading days: **{p.get('trading_days', 0)} / {MIN_TRADING_DAYS}**",
        f"- Rebalances: **{status.get('rebalance_count', 0)} / {MIN_REBALANCES}**",
        f"- Operating-floor buffer: **${status.get('operating_floor_buffer_dollars', 0):,.2f}** above ${OPERATING_FLOOR:,.0f}",
        "",
        "## Execution",
        f"- Filled orders: **{e.get('filled_order_count', 0)} / {e.get('terminal_order_count', 0)}**",
        f"- Fill rate: **{e.get('fill_rate_pct', 100):.1f}%**",
        f"- Failed orders: **{e.get('failed_order_count', 0)}**",
        f"- Median adverse slippage: **{'N/A' if e.get('median_adverse_slippage_bps') is None else f'{e.get('median_adverse_slippage_bps'):.2f} bps'}**",
        f"- 95th percentile adverse slippage: **{'N/A' if e.get('p95_adverse_slippage_bps') is None else f'{e.get('p95_adverse_slippage_bps'):.2f} bps'}**",
        f"- Executed-portfolio tracking L1 error: **{status.get('executed_tracking_l1_error_pct', 0):.2f}%**",
        f"- Current capital-fit gate: **{status.get('current_capital_fit_gate')}**",
        "",
        "## Holdings",
    ]
    for sym, pos in sorted((status.get("positions") or {}).items()):
        lines.append(f"- {sym}: {pos.get('qty', 0):+g} shares | value ${pos.get('market_value', 0):+,.2f} | unrealized ${pos.get('unrealized_pl', 0):+,.2f}")
    lines += ["", "## Forward-test gate"]
    for k, v in (status.get("gate_checks") or {}).items():
        lines.append(f"- {'PASS' if v else 'WAIT/FAIL'} — {k}")
    lines += [
        "",
        "## Rule",
        "Phase 6F is read-only. It cannot place orders, modify Phase 5H, or enable live-money trading.",
        "",
        "Paper trading is simulated and does not guarantee live results.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    now_et = now.astimezone(ET)
    today = now_et.date().isoformat()
    base = {"phase": "6F", "timestamp_utc": now.isoformat(), "paper_base": PAPER_BASE, "live_trading_locked": True}

    sixe_state = load_json(SIXE_STATE, {}) or {}
    latest_rebalance = latest_rebalance_event()
    if not sixe_state.get("initialized") or not latest_rebalance:
        write_status({**base, "status": "WAITING_FIRST_2500_REBALANCE", "current_equity": 0.0,
                      "performance": {}, "execution": {}, "positions": {}, "gate_checks": {}})
        return

    baseline_equity = float(sixe_state.get("baseline_equity") or DESIGN_CAPITAL)
    baseline_utc = sixe_state.get("baseline_utc") or latest_rebalance.get("timestamp_utc")
    rebalances = int(sixe_state.get("rebalance_count") or 0)

    try:
        account = api_get("/v2/account")
        positions_raw = api_get("/v2/positions") or []
        orders = all_phase6e_orders(baseline_utc)
    except Exception as exc:
        write_status({**base, "status": "PAPER_READ_FAILED", "current_equity": 0.0,
                      "performance": {}, "execution": {}, "positions": {}, "gate_checks": {},
                      "problems": [str(exc)]})
        return

    equity = float(account.get("equity") or 0.0)
    positions, actual_weights = positions_snapshot(positions_raw, equity)
    executed_weights = {k: float(v) for k, v in (latest_rebalance.get("represented_weights") or {}).items()}
    executed_tracking = tracking_error(actual_weights, executed_weights)

    sixe_status = load_json(SIXE_STATUS, {}) or {}
    current_fit = sixe_status.get("capital_fit") or {}
    current_fit_gate = current_fit.get("gate")

    previous_rows = load_jsonl(LOG_FILE)
    previous_prior_date = [r for r in previous_rows if r.get("date_et") and r.get("date_et") < today]
    previous_equity_for_snapshot = float(previous_prior_date[-1]["equity"]) if previous_prior_date else baseline_equity
    snapshot = {
        "date_et": today,
        "timestamp_utc": now.isoformat(),
        "equity": equity,
        "cash": float(account.get("cash") or 0.0),
        "buying_power": float(account.get("buying_power") or 0.0),
        "long_market_value": float(account.get("long_market_value") or 0.0),
        "short_market_value": float(account.get("short_market_value") or 0.0),
        "daily_pnl_dollars": equity - previous_equity_for_snapshot,
        "positions": positions,
        "actual_weights": actual_weights,
        "executed_weights": executed_weights,
        "executed_tracking_l1_error_pct": executed_tracking,
        "current_capital_fit_gate": current_fit_gate,
    }
    rows = save_daily_snapshot(snapshot)
    perf = performance_metrics(rows, baseline_equity, today)
    execution = execution_metrics(orders)
    checks, decision = gate_checks(perf, execution, rebalances, executed_tracking, current_fit_gate, equity)

    status = {
        **base,
        "status": decision,
        "baseline_equity": baseline_equity,
        "baseline_utc": baseline_utc,
        "current_equity": equity,
        "operating_floor_dollars": OPERATING_FLOOR,
        "operating_floor_buffer_dollars": equity - OPERATING_FLOOR,
        "rebalance_count": rebalances,
        "performance": perf,
        "execution": execution,
        "positions": positions,
        "actual_weights": actual_weights,
        "executed_weights": executed_weights,
        "executed_tracking_l1_error_pct": executed_tracking,
        "current_capital_fit_gate": current_fit_gate,
        "current_capital_fit": current_fit,
        "gate_checks": checks,
        "promotion_is_review_only": True,
        "strategy_parameters_frozen": True,
    }
    save_json(STATE_FILE, {"baseline_equity": baseline_equity, "baseline_utc": baseline_utc,
                           "last_monitor_utc": now.isoformat(), "status": decision})
    write_status(status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
