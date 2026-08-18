import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAPER_BASE = "https://paper-api.alpaca.markets"
DESIGN_CAPITAL = 2500.0
BOOTSTRAP_MIN = 2495.0
BOOTSTRAP_MAX = 2505.0
STATUS_JSON = "strategy2_account_check.json"
STATUS_MD = "strategy2_account_check.md"


def credentials():
    key = os.getenv("ALPACA_STRATEGY2_API_KEY_ID")
    secret = os.getenv("ALPACA_STRATEGY2_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Strategy 2 Alpaca paper credentials are not configured")
    return key, secret


def api(path):
    key, secret = credentials()
    req = urllib.request.Request(
        PAPER_BASE + path,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca paper API returned HTTP {exc.code}: {body[:300]}") from exc


def write_status(status):
    Path(STATUS_JSON).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    lines = [
        "# MarketPulse — Strategy 2 Account Check",
        "",
        f"**Status: {status['status']}**",
        "",
        f"- Timestamp UTC: {status['timestamp_utc']}",
        f"- Endpoint: **{PAPER_BASE}**",
        f"- Design capital: **${DESIGN_CAPITAL:,.2f}**",
        "- Order submission: **DISABLED**",
    ]
    if status.get("equity") is not None:
        lines.append(f"- Paper equity: **${status['equity']:,.2f}**")
    if status.get("position_count") is not None:
        lines.append(f"- Open positions: **{status['position_count']}**")
    if status.get("open_order_count") is not None:
        lines.append(f"- Open orders: **{status['open_order_count']}**")
    if status.get("problems"):
        lines += ["", "## Blockers"] + [f"- {p}" for p in status["problems"]]
    lines += [
        "",
        "This check is read-only. It cannot submit, replace, or cancel orders.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "strategy": "2",
        "timestamp_utc": now,
        "paper_base": PAPER_BASE,
        "design_capital": DESIGN_CAPITAL,
        "order_submission_enabled": False,
    }

    try:
        account = api("/v2/account")
        positions = api("/v2/positions")
        orders = api("/v2/orders?status=open&limit=500")
    except Exception as exc:
        write_status({**base, "status": "AUTH_OR_ACCOUNT_READ_FAILED", "problems": [str(exc)]})
        return

    equity = float(account.get("equity") or 0.0)
    problems = []
    if account.get("status") != "ACTIVE":
        problems.append(f"Account status is {account.get('status')!r}, expected 'ACTIVE'")
    if bool(account.get("trading_blocked")):
        problems.append("Trading is blocked on the Strategy 2 paper account")
    if not (BOOTSTRAP_MIN <= equity <= BOOTSTRAP_MAX):
        problems.append(
            f"Strategy 2 first-run equity must be about $2,500; received ${equity:,.2f}"
        )
    if positions:
        problems.append(f"Strategy 2 must begin empty; found {len(positions)} open position(s)")
    if orders:
        problems.append(f"Strategy 2 must begin with no open orders; found {len(orders)}")

    status = {
        **base,
        "status": "PASS" if not problems else "BLOCKED",
        "equity": equity,
        "account_status": account.get("status"),
        "trading_blocked": bool(account.get("trading_blocked")),
        "position_count": len(positions),
        "open_order_count": len(orders),
    }
    if problems:
        status["problems"] = problems
    write_status(status)


if __name__ == "__main__":
    main()
