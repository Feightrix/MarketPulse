import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAPER_BASE = "https://paper-api.alpaca.markets"
QTY_TOLERANCE = 1e-5


def api_get(path, key, secret, timeout=30):
    if PAPER_BASE != "https://paper-api.alpaca.markets":
        raise RuntimeError("Paper endpoint security invariant violated")
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(PAPER_BASE + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Paper API GET {path} failed: HTTP {e.code}: {detail}") from e


def load_state(path):
    p = Path(path)
    if not p.exists():
        return {"initialized": False}
    return json.loads(p.read_text())


def latest_expected_positions(log_path):
    p = Path(log_path)
    if not p.exists():
        return None
    latest = None
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        final_positions = event.get("final_positions")
        if isinstance(final_positions, dict) and final_positions:
            latest = {str(s): float(q) for s, q in final_positions.items() if abs(float(q)) > QTY_TOLERANCE}
    return latest


def actual_positions(rows):
    out = {}
    for row in rows or []:
        qty = float(row.get("qty") or 0.0)
        if abs(qty) > QTY_TOLERANCE:
            out[str(row["symbol"])] = qty
    return out


def compare_positions(expected, actual):
    drift = []
    for sym in sorted(set(expected) | set(actual)):
        exp = float(expected.get(sym, 0.0))
        act = float(actual.get(sym, 0.0))
        delta = act - exp
        if abs(delta) > QTY_TOLERANCE:
            drift.append({
                "symbol": sym,
                "expected_qty": exp,
                "actual_qty": act,
                "delta_qty": delta,
            })
    return drift


def write_blocked_status(args, account, expected, actual, drift, problem):
    existing = {}
    status_path = Path(args.status_file)
    if status_path.exists():
        try:
            existing = json.loads(status_path.read_text())
        except Exception:
            existing = {}

    status = {
        **existing,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "paper_base": PAPER_BASE,
        "live_trading_locked": True,
        "account_equity": float(account.get("equity") or 0.0) if account else existing.get("account_equity"),
        "account_status": account.get("status") if account else existing.get("account_status"),
        "trading_blocked": bool(account.get("trading_blocked")) if account else existing.get("trading_blocked"),
        "status": "POSITION_DRIFT_BLOCKED",
        "position_reconciliation": {
            "gate": "FAIL",
            "source": "last successful rebalance final_positions",
            "expected_positions": expected or {},
            "actual_positions": actual or {},
            "drift": drift or [],
        },
        "problems": [problem],
    }
    status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--secret-env", required=True)
    args = parser.parse_args()

    key = os.getenv(args.key_env)
    secret = os.getenv(args.secret_env)
    if not key or not secret:
        raise RuntimeError(f"Missing dedicated paper credentials for {args.label}")

    state = load_state(args.state_file)
    expected = latest_expected_positions(args.log_file)

    # Before the first successful rebalance there is no holdings baseline to reconcile.
    if not state.get("initialized"):
        print(f"{args.label}: position guard skipped before initial rebalance")
        return

    account = api_get("/v2/account", key, secret)
    actual = actual_positions(api_get("/v2/positions", key, secret))

    if not expected:
        problem = "Initialized account has no recoverable last-rebalance position baseline"
        write_blocked_status(args, account, {}, actual, [], problem)
        raise SystemExit(f"{args.label}: POSITION_DRIFT_BLOCKED — {problem}")

    drift = compare_positions(expected, actual)
    if drift:
        details = "; ".join(
            f"{d['symbol']} expected {d['expected_qty']:+g}, actual {d['actual_qty']:+g}"
            for d in drift
        )
        problem = f"Broker positions differ from last successful rebalance: {details}"
        write_blocked_status(args, account, expected, actual, drift, problem)
        raise SystemExit(f"{args.label}: POSITION_DRIFT_BLOCKED — {details}")

    print(f"{args.label}: actual-position reconciliation PASS ({len(actual)} positions)")


if __name__ == "__main__":
    main()
