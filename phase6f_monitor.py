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
MAX_MEDIAN_SLIPPAGE_BPS = 15.0
MAX_P95_SLIPPAGE_BPS = 30.0
MAX_TRACKING_L1_PCT = 5.0


def creds():
    key = os.getenv("ALPACA_2500_PAPER_API_KEY_ID")
    secret = os.getenv("ALPACA_2500_PAPER_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Dedicated $2,500 Alpaca paper credentials are required")
    return key, secret


def api_get(path, timeout=45):
    if PAPER_BASE != "https://paper-api.alpaca.markets":
        raise RuntimeError("Paper endpoint invariant violated")
    key, secret = creds()
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
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def load_jsonl(path):
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def save_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def rebalance_events():
    return [x for x in load_jsonl(SIXE_LOG) if x.get("status") == "REBALANCE_COMPLETE"]


def save_daily(snapshot):
    by_date = {r.get("date_et"): r for r in load_jsonl(LOG_FILE) if r.get("date_et")}
    by_date[snapshot["date_et"]] = snapshot
    rows = [by_date[k] for k in sorted(by_date)]
    with open(LOG_FILE, "w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return rows


def positions_snapshot(rows, equity):
    detail, weights = {}, {}
    for r in rows or []:
        sym = r.get("symbol")
        mv = float(r.get("market_value") or 0.0)
        detail[sym] = {
            "qty": float(r.get("qty") or 0.0),
            "market_value": mv,
            "current_price": float(r.get("current_price") or 0.0),
            "avg_entry_price": float(r.get("avg_entry_price") or 0.0),
            "unrealized_pl": float(r.get("unrealized_pl") or 0.0),
        }
        if equity > 0:
            weights[sym] = mv / equity
    return detail, weights


def l1_tracking(actual, target):
    syms = set(actual) | set(target)
    return sum(abs(float(actual.get(s, 0.0)) - float(target.get(s, 0.0))) for s in syms) * 100.0


def reference_prices():
    refs = {}
    for event in rebalance_events():
        equity = float(event.get("account_equity") or 0.0)
        qtys = event.get("desired_quantities") or {}
        represented = event.get("represented_weights") or {}
        px_by_sym = {}
        if equity > 0:
            for sym, qty in qtys.items():
                qty = float(qty)
                w = float(represented.get(sym, 0.0))
                if qty and w:
                    px_by_sym[sym] = abs(w * equity / qty)
        for action in event.get("actions") or []:
            oid, sym = action.get("order_id"), action.get("symbol")
            if oid and sym in px_by_sym:
                refs[oid] = px_by_sym[sym]
    return refs


def execution_metrics(orders):
    terminal = [o for o in orders if o.get("status") in {"filled", "canceled", "expired", "rejected", "suspended"}]
    filled = [o for o in terminal if o.get("status") == "filled"]
    failed = [o for o in terminal if o.get("status") != "filled"]
    refs = reference_prices()
    slips = []
    for o in filled:
        ref, avg = refs.get(o.get("id")), o.get("filled_avg_price")
        if not ref or not avg:
            continue
        avg = float(avg)
        if ref <= 0 or avg <= 0:
            continue
        slip = (avg / ref - 1.0) * 10000.0 if o.get("side") == "buy" else (1.0 - avg / ref) * 10000.0
        slips.append(float(slip))
    return {
        "order_count": len(orders),
        "terminal_order_count": len(terminal),
        "filled_order_count": len(filled),
        "failed_order_count": len(failed),
        "fill_rate_pct": (len(filled) / len(terminal) * 100.0) if terminal else 100.0,
        "measured_slippage_orders": len(slips),
        "median_adverse_slippage_bps": float(statistics.median(slips)) if slips else None,
        "p95_adverse_slippage_bps": float(np.percentile(np.array(slips), 95)) if slips else None,
        "worst_adverse_slippage_bps": max(slips) if slips else None,
    }


def performance(rows, baseline, current_month):
    rows = sorted(rows, key=lambda r: r["date_et"])
    vals = [baseline] + [float(r["equity"]) for r in rows]
    peak, max_dd = vals[0], 0.0
    for e in vals:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (1.0 - e / peak) * 100.0)
    current = vals[-1]
    prior = float(rows[-2]["equity"]) if len(rows) > 1 else baseline
    daily_returns, x = [], baseline
    for r in rows:
        e = float(r["equity"])
        daily_returns.append((e / x - 1.0) * 100.0 if x > 0 else 0.0)
        x = e
    month_last = {}
    for r in rows:
        month_last[r["date_et"][:7]] = float(r["equity"])
    completed, x = {}, baseline
    for month in sorted(month_last):
        e = month_last[month]
        ret = (e / x - 1.0) * 100.0 if x > 0 else 0.0
        if month < current_month:
            completed[month] = ret
        x = e
    month_vals = list(completed.values())
    return {
        "trading_days": len(rows),
        "daily_pnl_dollars": current - prior,
        "daily_return_pct": (current / prior - 1.0) * 100.0 if prior > 0 else 0.0,
        "cumulative_pnl_dollars": current - baseline,
        "cumulative_return_pct": (current / baseline - 1.0) * 100.0 if baseline > 0 else 0.0,
        "max_drawdown_pct": max_dd,
        "positive_day_rate_pct": sum(v > 0 for v in daily_returns) / len(daily_returns) * 100.0 if daily_returns else 0.0,
        "completed_months": len(month_vals),
        "positive_month_rate_pct": sum(v > 0 for v in month_vals) / len(month_vals) * 100.0 if month_vals else 0.0,
        "worst_completed_month_pct": min(month_vals) if month_vals else 0.0,
        "monthly_returns_pct": completed,
    }


def gates(perf, exe, rebalances, tracking, fit_gate, equity):
    checks = {
        "minimum_126_trading_days": perf["trading_days"] >= MIN_TRADING_DAYS,
        "minimum_6_rebalances": rebalances >= MIN_REBALANCES,
        "cumulative_return_positive": perf["cumulative_return_pct"] > 0,
        "max_drawdown_at_most_5pct": perf["max_drawdown_pct"] <= MAX_DRAWDOWN_PCT,
        "positive_month_rate_at_least_66_7pct": perf["positive_month_rate_pct"] >= MIN_POSITIVE_MONTH_RATE_PCT if perf["completed_months"] else False,
        "worst_completed_month_above_minus_2_5pct": perf["worst_completed_month_pct"] >= WORST_MONTH_FLOOR_PCT if perf["completed_months"] else False,
        "fill_rate_at_least_99pct": exe["fill_rate_pct"] >= MIN_FILL_RATE_PCT,
        "zero_failed_orders": exe["failed_order_count"] == 0,
        "median_adverse_slippage_at_most_15bps": exe["median_adverse_slippage_bps"] is None or exe["median_adverse_slippage_bps"] <= MAX_MEDIAN_SLIPPAGE_BPS,
        "p95_adverse_slippage_at_most_30bps": exe["p95_adverse_slippage_bps"] is None or exe["p95_adverse_slippage_bps"] <= MAX_P95_SLIPPAGE_BPS,
        "executed_portfolio_tracking_at_most_5pct": tracking <= MAX_TRACKING_L1_PCT,
        "current_capital_fit_gate_pass": fit_gate == "PASS",
        "equity_at_or_above_2200_operating_floor": equity >= OPERATING_FLOOR,
    }
    mature = checks["minimum_126_trading_days"] and checks["minimum_6_rebalances"]
    non_min = [v for k, v in checks.items() if not k.startswith("minimum_")]
    if equity < OPERATING_FLOOR:
        decision = "OPERATING_FLOOR_BLOCK"
    elif not mature:
        decision = "FORWARD_TEST_ACCUMULATING"
    elif all(non_min):
        decision = "PROMOTION_REVIEW_ELIGIBLE"
    else:
        decision = "FORWARD_TEST_FAIL"
    return checks, decision


def fmt_bps(v):
    return "N/A" if v is None else f"{v:.2f} bps"


def write_status(status):
    save_json(STATUS_JSON, status)
    p, e = status.get("performance") or {}, status.get("execution") or {}
    lines = [
        "# MarketPulse Phase 6F — $2,500 Forward Test", "", f"**Status: {status.get('status')}**", "",
        f"- Timestamp UTC: {status.get('timestamp_utc')}",
        f"- Dedicated paper equity: **${status.get('current_equity', 0):,.2f}**",
        f"- Daily P/L: **${p.get('daily_pnl_dollars', 0):+.2f} ({p.get('daily_return_pct', 0):+.4f}%)**",
        f"- Cumulative P/L: **${p.get('cumulative_pnl_dollars', 0):+.2f} ({p.get('cumulative_return_pct', 0):+.4f}%)**",
        f"- Max drawdown: **{p.get('max_drawdown_pct', 0):.2f}%**",
        f"- Trading days: **{p.get('trading_days', 0)} / {MIN_TRADING_DAYS}**",
        f"- Rebalances: **{status.get('rebalance_count', 0)} / {MIN_REBALANCES}**",
        f"- Operating-floor buffer: **${status.get('operating_floor_buffer_dollars', 0):,.2f}** above ${OPERATING_FLOOR:,.0f}",
        "", "## Execution",
        f"- Filled orders: **{e.get('filled_order_count', 0)} / {e.get('terminal_order_count', 0)}**",
        f"- Fill rate: **{e.get('fill_rate_pct', 100):.1f}%**",
        f"- Failed orders: **{e.get('failed_order_count', 0)}**",
        f"- Median adverse slippage: **{fmt_bps(e.get('median_adverse_slippage_bps'))}**",
        f"- 95th percentile adverse slippage: **{fmt_bps(e.get('p95_adverse_slippage_bps'))}**",
        f"- Executed-portfolio tracking L1 error: **{status.get('executed_tracking_l1_error_pct', 0):.2f}%**",
        f"- Current capital-fit gate: **{status.get('current_capital_fit_gate')}**",
        "", "## Holdings",
    ]
    for sym, pos in sorted((status.get("positions") or {}).items()):
        lines.append(f"- {sym}: {pos.get('qty', 0):+g} shares | value ${pos.get('market_value', 0):+,.2f} | unrealized ${pos.get('unrealized_pl', 0):+,.2f}")
    lines += ["", "## Forward-test gate"]
    for k, v in (status.get("gate_checks") or {}).items():
        lines.append(f"- {'PASS' if v else 'WAIT/FAIL'} — {k}")
    lines += ["", "## Rule", "Phase 6F is read-only. It cannot place orders, modify Phase 5H, or enable live-money trading.", "", "Paper trading is simulated and does not guarantee live results."]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    today = now.astimezone(ET).date().isoformat()
    current_month = today[:7]
    base = {"phase": "6F", "timestamp_utc": now.isoformat(), "paper_base": PAPER_BASE, "live_trading_locked": True}
    state = load_json(SIXE_STATE, {}) or {}
    events = rebalance_events()
    if not state.get("initialized") or not events:
        write_status({**base, "status": "WAITING_FIRST_2500_REBALANCE", "current_equity": 0.0, "performance": {}, "execution": {}, "positions": {}, "gate_checks": {}})
        return
    baseline = float(state.get("baseline_equity") or DESIGN_CAPITAL)
    baseline_utc = state.get("baseline_utc") or events[0].get("timestamp_utc")
    latest_rebalance = events[-1]
    rebalances = int(state.get("rebalance_count") or 0)
    try:
        account = api_get("/v2/account")
        raw_positions = api_get("/v2/positions") or []
        q = urllib.parse.urlencode({"status": "all", "limit": 500, "direction": "asc", "after": baseline_utc})
        all_orders = api_get("/v2/orders?" + q) or []
        orders = [o for o in all_orders if str(o.get("client_order_id", "")).startswith("mp6e-")]
    except Exception as exc:
        write_status({**base, "status": "PAPER_READ_FAILED", "current_equity": 0.0, "performance": {}, "execution": {}, "positions": {}, "gate_checks": {}, "problems": [str(exc)]})
        return
    equity = float(account.get("equity") or 0.0)
    positions, actual_weights = positions_snapshot(raw_positions, equity)
    executed_weights = {k: float(v) for k, v in (latest_rebalance.get("represented_weights") or {}).items()}
    tracking = l1_tracking(actual_weights, executed_weights)
    sixe_status = load_json(SIXE_STATUS, {}) or {}
    fit = sixe_status.get("capital_fit") or {}
    fit_gate = fit.get("gate")
    snapshot = {"date_et": today, "timestamp_utc": now.isoformat(), "equity": equity, "cash": float(account.get("cash") or 0.0), "buying_power": float(account.get("buying_power") or 0.0), "long_market_value": float(account.get("long_market_value") or 0.0), "short_market_value": float(account.get("short_market_value") or 0.0), "positions": positions, "actual_weights": actual_weights, "executed_weights": executed_weights, "executed_tracking_l1_error_pct": tracking, "current_capital_fit_gate": fit_gate}
    rows = save_daily(snapshot)
    perf = performance(rows, baseline, current_month)
    exe = execution_metrics(orders)
    checks, decision = gates(perf, exe, rebalances, tracking, fit_gate, equity)
    status = {**base, "status": decision, "baseline_equity": baseline, "baseline_utc": baseline_utc, "current_equity": equity, "operating_floor_dollars": OPERATING_FLOOR, "operating_floor_buffer_dollars": equity - OPERATING_FLOOR, "rebalance_count": rebalances, "performance": perf, "execution": exe, "positions": positions, "actual_weights": actual_weights, "executed_weights": executed_weights, "executed_tracking_l1_error_pct": tracking, "current_capital_fit_gate": fit_gate, "current_capital_fit": fit, "gate_checks": checks, "promotion_is_review_only": True, "strategy_parameters_frozen": True}
    save_json(STATE_FILE, {"baseline_equity": baseline, "baseline_utc": baseline_utc, "last_monitor_utc": now.isoformat(), "status": decision})
    write_status(status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
