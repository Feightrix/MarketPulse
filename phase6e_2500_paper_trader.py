import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Reuse the frozen Phase 5H signal engine and paper-only execution primitives.
import phase6b_paper_trader as core

PHASE = "6E"
DESIGN_CAPITAL = 2500.0
BOOTSTRAP_MIN = 2495.0
BOOTSTRAP_MAX = 2505.0
OPERATING_FLOOR = 2200.0
MAX_L1_TRACKING_PCT = 5.0
MAX_NET_DRIFT_PCT = 3.0
MAX_GROSS_EXPOSURE_PCT = 100.0
MAX_SINGLE_SHORT_PCT = 5.0

STATE_FILE = "phase6e_2500_state.json"
STATUS_FILE = "phase6e_2500_status.json"
STATUS_MD = "phase6e_2500_status.md"
LOG_FILE = "phase6e_2500_log.jsonl"


def dedicated_credentials():
    key = os.getenv("ALPACA_2500_PAPER_API_KEY_ID")
    secret = os.getenv("ALPACA_2500_PAPER_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Dedicated $2,500 Alpaca paper credentials are not configured")
    # core.credentials reads these at call time. Populate only from the dedicated pair.
    os.environ["ALPACA_PAPER_API_KEY_ID"] = key
    os.environ["ALPACA_PAPER_API_SECRET_KEY"] = secret
    os.environ.pop("ALPACA_API_KEY_ID", None)
    os.environ.pop("ALPACA_API_SECRET_KEY", None)


def load_state():
    if not Path(STATE_FILE).exists():
        return {"initialized": False, "last_rebalance_month": None, "rebalance_count": 0}
    return json.loads(Path(STATE_FILE).read_text())


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def append_log(event):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def nearest_whole(x):
    return int(math.floor(float(x) + 0.5))


def build_2500_quantities(weights, equity, prices, metadata):
    desired = {}
    represented = {}
    for sym, w in weights.items():
        px = float(prices[sym])
        dollars = equity * float(w)
        a = metadata[sym]
        if w >= 0:
            if not a.get("fractionable", False):
                qty = math.floor(max(dollars, 0.0) / px)
            else:
                qty = round(max(dollars, 0.0) / px, 6)
        else:
            # Alpaca does not support fractional opening shorts. Nearest whole share
            # minimizes representation error; a non-zero intended short must remain represented.
            raw = abs(dollars) / px
            qty = -nearest_whole(raw)
        desired[sym] = float(qty)
        represented[sym] = float(qty * px / equity) if equity > 0 else 0.0
    return desired, represented


def capital_fit(weights, represented, equity):
    syms = set(weights) | set(represented)
    l1 = sum(abs(float(represented.get(s, 0.0)) - float(weights.get(s, 0.0))) for s in syms) * 100.0
    target_net = sum(weights.values()) * 100.0
    represented_net = sum(represented.values()) * 100.0
    target_gross = sum(abs(v) for v in weights.values()) * 100.0
    represented_gross = sum(abs(v) for v in represented.values()) * 100.0
    short_weights = [abs(v) * 100.0 for v in represented.values() if v < 0]
    largest_short = max(short_weights) if short_weights else 0.0
    all_shorts = all(represented.get(s, 0.0) < 0 for s, w in weights.items() if w < 0)
    checks = {
        "equity_at_or_above_operating_floor": equity >= OPERATING_FLOOR,
        "l1_tracking_error_at_most_5pct": l1 <= MAX_L1_TRACKING_PCT,
        "net_exposure_drift_at_most_3pct": abs(represented_net - target_net) <= MAX_NET_DRIFT_PCT,
        "gross_exposure_at_most_100pct": represented_gross <= MAX_GROSS_EXPOSURE_PCT,
        "single_short_weight_at_most_5pct": largest_short <= MAX_SINGLE_SHORT_PCT,
        "all_intended_net_shorts_represented": all_shorts,
    }
    return {
        "gate": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "l1_tracking_error_pct": l1,
        "target_net_exposure_pct": target_net,
        "represented_net_exposure_pct": represented_net,
        "target_gross_exposure_pct": target_gross,
        "represented_gross_exposure_pct": represented_gross,
        "largest_single_short_pct": largest_short,
    }


def write_status(status):
    Path(STATUS_FILE).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    lines = [
        "# MarketPulse Phase 6E — Dedicated $2,500 Paper Account",
        "",
        f"**Status: {status.get('status')}**",
        "",
        f"- Timestamp UTC: {status.get('timestamp_utc')}",
        f"- Paper endpoint only: **{core.PAPER_BASE}**",
        f"- Design capital: **${DESIGN_CAPITAL:,.2f}**",
        "- Live-money trading: **LOCKED**",
    ]
    if status.get("account_equity") is not None:
        lines.append(f"- Paper equity: **${status['account_equity']:,.2f}**")
    if status.get("market_open") is not None:
        lines.append(f"- Market open: **{status['market_open']}**")
    fit = status.get("capital_fit") or {}
    if fit:
        lines += [
            "",
            "## Capital fit",
            f"- Gate: **{fit.get('gate')}**",
            f"- L1 tracking error: **{fit.get('l1_tracking_error_pct', 0):.2f}%**",
            f"- Net exposure: target **{fit.get('target_net_exposure_pct', 0):.2f}%** / represented **{fit.get('represented_net_exposure_pct', 0):.2f}%**",
            f"- Gross exposure: target **{fit.get('target_gross_exposure_pct', 0):.2f}%** / represented **{fit.get('represented_gross_exposure_pct', 0):.2f}%**",
            f"- Largest short: **{fit.get('largest_single_short_pct', 0):.2f}%**",
        ]
    if status.get("desired_quantities"):
        lines += ["", "## $2,500-sized quantities"]
        for sym, qty in sorted(status["desired_quantities"].items()):
            lines.append(f"- {sym}: {qty:+g} shares")
    if status.get("actions"):
        lines += ["", "## Orders"]
        for a in status["actions"]:
            lines.append(f"- {a.get('symbol')}: {a.get('action')} {a.get('qty','')} — {a.get('status')}")
    if status.get("problems"):
        lines += ["", "## Blockers"] + [f"- {p}" for p in status["problems"]]
    lines += [
        "",
        "This workflow can trade only the dedicated $2,500 Alpaca paper account credentials. It contains no live Trading API endpoint.",
        "Paper trading is simulated and does not guarantee live results.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    base = {"phase": PHASE, "timestamp_utc": now.isoformat(), "paper_base": core.PAPER_BASE,
            "design_capital": DESIGN_CAPITAL, "live_trading_locked": True}

    try:
        sixa = json.loads(Path("phase6a_results.json").read_text())
        if sixa.get("gate") != "PASS" or not sixa.get("paper_trading_authorized"):
            raise RuntimeError("Phase 6A PASS authorization is missing")
    except Exception as e:
        status = {**base, "status": "BLOCKED_RESEARCH_GATE", "problems": [str(e)]}
        write_status(status); append_log(status); return

    try:
        dedicated_credentials()
    except Exception as e:
        status = {**base, "status": "WAITING_FOR_2500_PAPER_CREDENTIALS", "problems": [str(e)]}
        write_status(status); append_log(status); return

    try:
        account = core.api("GET", "/v2/account")
        clock = core.api("GET", "/v2/clock")
    except Exception as e:
        status = {**base, "status": "PAPER_AUTH_FAILED", "problems": [str(e)]}
        write_status(status); append_log(status); return

    equity = float(account.get("equity") or 0.0)
    common = {**base, "account_equity": equity, "market_open": bool(clock.get("is_open")),
              "account_status": account.get("status"), "trading_blocked": bool(account.get("trading_blocked"))}
    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        status = {**common, "status": "PAPER_ACCOUNT_BLOCKED", "problems": ["Dedicated paper account is not active/tradable"]}
        write_status(status); append_log(status); return

    state = load_state()
    try:
        positions = core.current_positions()
        pending = [o for o in core.open_orders() if str(o.get("client_order_id", "")).startswith("mp6e-")]
    except Exception as e:
        status = {**common, "status": "ACCOUNT_READ_FAILED", "problems": [str(e)]}
        write_status(status); append_log(status); return

    if not state.get("initialized"):
        if not (BOOTSTRAP_MIN <= equity <= BOOTSTRAP_MAX):
            status = {**common, "status": "WRONG_PAPER_ACCOUNT",
                      "problems": [f"First-run equity must be about $2,500; received ${equity:,.2f}. This lock prevents use of the $100k paper account."]}
            write_status(status); append_log(status); return
        if positions or pending:
            status = {**common, "status": "BOOTSTRAP_ACCOUNT_NOT_EMPTY",
                      "problems": ["New $2,500 paper account must begin with no positions and no MarketPulse orders"]}
            write_status(status); append_log(status); return

    if equity < OPERATING_FLOOR:
        status = {**common, "status": "OPERATING_FLOOR_BLOCK",
                  "problems": [f"Equity ${equity:,.2f} is below the ${OPERATING_FLOOR:,.2f} MarketPulse operating floor. Automated rebalancing is blocked for review."]}
        write_status(status); append_log(status); return

    try:
        _, closes = core.load_panel()
        weights, trend, neutral = core.combined_target(closes)
        signal_date = str(closes.index[-1])
        prices = closes.iloc[-1]
        metadata = {s: core.asset(s) for s in weights}
        desired, represented = build_2500_quantities(weights, equity, prices, metadata)
        fit = capital_fit(weights, represented, equity)
        broker_problems = core.preflight(weights, equity, metadata, desired)
    except Exception as e:
        status = {**common, "status": "TARGET_BUILD_FAILED", "problems": [str(e)]}
        write_status(status); append_log(status); return

    common.update({"signal_date": signal_date, "target_weights": weights, "trend_sleeve": trend,
                   "neutral_sleeve": neutral, "desired_quantities": desired,
                   "represented_weights": represented, "capital_fit": fit,
                   "last_rebalance_month": state.get("last_rebalance_month"),
                   "rebalance_count": int(state.get("rebalance_count", 0))})

    problems = list(broker_problems)
    if fit["gate"] != "PASS":
        problems.append("$2,500 capital-fit gate failed")
    if problems:
        status = {**common, "status": "CAPITAL_FIT_BLOCKED", "problems": problems}
        write_status(status); append_log(status); return

    if pending:
        status = {**common, "status": "PENDING_MARKETPULSE_2500_ORDERS", "pending_orders": pending}
        write_status(status); append_log(status); return

    current_month = pd.Timestamp(clock.get("timestamp", now.isoformat())).strftime("%Y-%m")
    if state.get("last_rebalance_month") == current_month:
        status = {**common, "status": "CURRENT_MONTH_ALREADY_REBALANCED"}
        write_status(status); append_log(status); return

    if not clock.get("is_open"):
        status = {**common, "status": "READY_WAITING_FOR_MARKET_OPEN"}
        write_status(status); append_log(status); return

    try:
        # Use the proven 6B executor, but give this account a distinct client-order namespace.
        # execute_rebalance generates mp6b tags internally, so patch only the tag-producing submit path
        # by temporarily replacing submit_market with a dedicated wrapper.
        original_submit = core.submit_market
        def submit_2500(sym, qty, side, tag):
            return original_submit(sym, qty, side, tag.replace("mp6b-", "mp6e-", 1))
        core.submit_market = submit_2500
        actions = core.execute_rebalance(desired, positions, now.strftime("%Y%m%d%H%M"))
        core.submit_market = original_submit
        final_positions = core.current_positions()
        state.update({
            "initialized": True,
            "baseline_equity": state.get("baseline_equity", equity),
            "baseline_utc": state.get("baseline_utc", now.isoformat()),
            "last_rebalance_month": current_month,
            "last_rebalance_utc": now.isoformat(),
            "last_signal_date": signal_date,
            "rebalance_count": int(state.get("rebalance_count", 0)) + 1,
        })
        save_state(state)
        status = {**common, "status": "REBALANCE_COMPLETE", "actions": actions,
                  "final_positions": final_positions, "rebalance_count": state["rebalance_count"]}
    except Exception as e:
        core.submit_market = globals().get("original_submit", core.submit_market)
        status = {**common, "status": "REBALANCE_ERROR", "problems": [str(e)]}

    write_status(status)
    append_log(status)


if __name__ == "__main__":
    main()
