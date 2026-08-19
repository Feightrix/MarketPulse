import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAPER_BASE = "https://paper-api.alpaca.markets"
STATUS_FILE = "strategy2_xlu_repair_status.json"
LOG_FILE = "strategy2_log.jsonl"
REPAIR_SYMBOL = "XLU"
REPAIR_EXPECTED_QTY = -1.0
QTY_TOLERANCE = 1e-5
CLIENT_ORDER_ID = "mps2-repair-xlu-20260819"


def credentials():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 dedicated paper credentials are not configured")
    return key, secret


def api(method, path, key, secret, body=None, timeout=30):
    if PAPER_BASE != "https://paper-api.alpaca.markets":
        raise RuntimeError("Paper endpoint security invariant violated")
    data = None if body is None else json.dumps(body).encode()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(PAPER_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Paper API {method} {path} failed: HTTP {e.code}: {detail}") from e


def write_status(status, **extra):
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "paper_base": PAPER_BASE,
        "live_trading_locked": True,
        "repair_symbol": REPAIR_SYMBOL,
        "repair_expected_qty": REPAIR_EXPECTED_QTY,
        "status": status,
        **extra,
    }
    Path(STATUS_FILE).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))


def latest_expected_positions():
    p = Path(LOG_FILE)
    if not p.exists():
        raise RuntimeError("Strategy 2 log is missing")
    latest = None
    for line in p.read_text().splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        final_positions = event.get("final_positions")
        if event.get("status") == "REBALANCE_COMPLETE" and isinstance(final_positions, dict):
            latest = {str(s): float(q) for s, q in final_positions.items() if abs(float(q)) > QTY_TOLERANCE}
    if not latest:
        raise RuntimeError("No successful Strategy 2 rebalance baseline found")
    return latest


def actual_positions(rows):
    out = {}
    for row in rows or []:
        qty = float(row.get("qty") or 0.0)
        if abs(qty) > QTY_TOLERANCE:
            out[str(row["symbol"])] = qty
    return out


def compare(expected, actual):
    drift = []
    for sym in sorted(set(expected) | set(actual)):
        exp = float(expected.get(sym, 0.0))
        act = float(actual.get(sym, 0.0))
        if abs(act - exp) > QTY_TOLERANCE:
            drift.append({"symbol": sym, "expected_qty": exp, "actual_qty": act, "delta_qty": act - exp})
    return drift


def main():
    key, secret = credentials()
    account = api("GET", "/v2/account", key, secret)
    clock = api("GET", "/v2/clock", key, secret)
    rows = api("GET", "/v2/positions", key, secret)
    open_orders = api("GET", "/v2/orders?status=open&limit=100&direction=desc", key, secret)

    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        write_status("BLOCKED_ACCOUNT", account_status=account.get("status"), trading_blocked=bool(account.get("trading_blocked")))
        return

    if open_orders:
        write_status("BLOCKED_OPEN_ORDERS", open_order_count=len(open_orders))
        return

    expected = latest_expected_positions()
    actual = actual_positions(rows)
    drift = compare(expected, actual)

    # Idempotent exit: if the XLU short has already returned and everything matches, do nothing.
    if not drift:
        write_status("ALREADY_REPAIRED", expected_positions=expected, actual_positions=actual, post_repair_reconciliation="PASS")
        return

    # This authorization is deliberately narrow: only the known missing XLU short may be repaired.
    exact_known_drift = (
        len(drift) == 1
        and drift[0]["symbol"] == REPAIR_SYMBOL
        and abs(drift[0]["expected_qty"] - REPAIR_EXPECTED_QTY) <= QTY_TOLERANCE
        and abs(drift[0]["actual_qty"]) <= QTY_TOLERANCE
    )
    if not exact_known_drift:
        write_status("BLOCKED_UNEXPECTED_DRIFT", expected_positions=expected, actual_positions=actual, drift=drift)
        return

    # Never queue this repair outside regular market hours.
    if not clock.get("is_open"):
        write_status(
            "WAITING_FOR_MARKET_OPEN",
            expected_positions=expected,
            actual_positions=actual,
            drift=drift,
            order_submitted=False,
        )
        return

    asset = api("GET", f"/v2/assets/{REPAIR_SYMBOL}", key, secret)
    if not asset.get("tradable") or not asset.get("shortable"):
        write_status(
            "BLOCKED_ASSET_NOT_SHORTABLE",
            tradable=bool(asset.get("tradable")),
            shortable=bool(asset.get("shortable")),
        )
        return

    order = api(
        "POST",
        "/v2/orders",
        key,
        secret,
        {
            "symbol": REPAIR_SYMBOL,
            "qty": "1",
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
            "extended_hours": False,
            "client_order_id": CLIENT_ORDER_ID,
        },
    )
    order_id = order.get("id")
    if not order_id:
        raise RuntimeError("Repair order returned no order id")

    terminal = {"filled", "canceled", "expired", "rejected", "suspended"}
    order_status = str(order.get("status") or "")
    latest_order = order
    for _ in range(30):
        if order_status in terminal:
            break
        time.sleep(1)
        latest_order = api("GET", f"/v2/orders/{order_id}", key, secret)
        order_status = str(latest_order.get("status") or "")

    actual_after = actual_positions(api("GET", "/v2/positions", key, secret))
    drift_after = compare(expected, actual_after)
    if order_status == "filled" and not drift_after:
        write_status(
            "REPAIR_COMPLETE",
            order_submitted=True,
            order_id=order_id,
            client_order_id=CLIENT_ORDER_ID,
            order_status=order_status,
            filled_qty=float(latest_order.get("filled_qty") or 0.0),
            filled_avg_price=float(latest_order.get("filled_avg_price") or 0.0),
            expected_positions=expected,
            actual_positions=actual_after,
            post_repair_reconciliation="PASS",
        )
        return

    write_status(
        "REPAIR_NOT_CONFIRMED",
        order_submitted=True,
        order_id=order_id,
        client_order_id=CLIENT_ORDER_ID,
        order_status=order_status,
        expected_positions=expected,
        actual_positions=actual_after,
        remaining_drift=drift_after,
        post_repair_reconciliation="FAIL",
    )


if __name__ == "__main__":
    main()
