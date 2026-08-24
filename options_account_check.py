import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAPER_BASE = "https://paper-api.alpaca.markets"
REQUIRED_OPTIONS_LEVEL = 3
STATUS_JSON = "options_account_check.json"
STATUS_MD = "options_account_check.md"


def credentials():
    key = os.getenv("ALPACA_OPTIONS_API_KEY_ID")
    secret = os.getenv("ALPACA_OPTIONS_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Options Alpaca paper credentials are not configured")
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
        "# MarketPulse — Options Account Check",
        "",
        f"**Status: {status['status']}**",
        "",
        f"- Timestamp UTC: {status['timestamp_utc']}",
        f"- Endpoint: **{PAPER_BASE}**",
        f"- Required options level: **{REQUIRED_OPTIONS_LEVEL}**",
        "- Order submission: **DISABLED**",
    ]
    if status.get("equity") is not None:
        lines.append(f"- Paper equity: **${status['equity']:,.2f}**")
    if status.get("options_buying_power") is not None:
        lines.append(f"- Options buying power: **${status['options_buying_power']:,.2f}**")
    if status.get("options_approved_level") is not None:
        lines.append(f"- Options approved level: **{status['options_approved_level']}**")
    if status.get("options_trading_level") is not None:
        lines.append(f"- Options trading level: **{status['options_trading_level']}**")
    if status.get("position_count") is not None:
        lines.append(f"- Open positions: **{status['position_count']}**")
    if status.get("open_order_count") is not None:
        lines.append(f"- Open orders: **{status['open_order_count']}**")
    if status.get("problems"):
        lines += ["", "## Blockers"] + [f"- {p}" for p in status["problems"]]
    lines += [
        "",
        "This check is read-only. It cannot submit, replace, cancel, or exercise orders.",
    ]
    Path(STATUS_MD).write_text("\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "strategy": "options_pattern_v1",
        "timestamp_utc": now,
        "paper_base": PAPER_BASE,
        "required_options_level": REQUIRED_OPTIONS_LEVEL,
        "order_submission_enabled": False,
    }

    try:
        account = api("/v2/account")
        positions = api("/v2/positions")
        orders = api("/v2/orders?status=open&limit=500")
    except Exception as exc:
        write_status({**base, "status": "AUTH_OR_ACCOUNT_READ_FAILED", "problems": [str(exc)]})
        return

    def as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    approved_level = as_int(account.get("options_approved_level"))
    trading_level = as_int(account.get("options_trading_level"))
    equity = as_float(account.get("equity"))
    options_buying_power = as_float(account.get("options_buying_power"))

    problems = []
    if account.get("status") != "ACTIVE":
        problems.append(f"Account status is {account.get('status')!r}, expected 'ACTIVE'")
    if bool(account.get("trading_blocked")):
        problems.append("Trading is blocked on the options paper account")
    if approved_level is None or approved_level < REQUIRED_OPTIONS_LEVEL:
        problems.append(
            f"Options approved level must be {REQUIRED_OPTIONS_LEVEL}; received {approved_level!r}"
        )
    if trading_level is None or trading_level < REQUIRED_OPTIONS_LEVEL:
        problems.append(
            f"Options trading level must be {REQUIRED_OPTIONS_LEVEL}; received {trading_level!r}"
        )

    status = {
        **base,
        "status": "PASS" if not problems else "BLOCKED",
        "equity": equity,
        "options_buying_power": options_buying_power,
        "account_status": account.get("status"),
        "trading_blocked": bool(account.get("trading_blocked")),
        "options_approved_level": approved_level,
        "options_trading_level": trading_level,
        "position_count": len(positions),
        "open_order_count": len(orders),
    }
    if problems:
        status["problems"] = problems
    write_status(status)


if __name__ == "__main__":
    main()
