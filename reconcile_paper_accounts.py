import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

PAPER_BASE = "https://paper-api.alpaca.markets"


def request(key, secret, path):
    req = urllib.request.Request(
        PAPER_BASE + path,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}"}
    except Exception as e:
        return {"_error": str(e)}


def f(x):
    try:
        return float(x)
    except Exception:
        return None


def reconcile(label, key_env, secret_env):
    key = os.getenv(key_env)
    secret = os.getenv(secret_env)
    if not key or not secret:
        raise RuntimeError(f"Missing credentials for {label}")

    account = request(key, secret, "/v2/account") or {}
    positions = request(key, secret, "/v2/positions") or []
    orders = request(key, secret, "/v2/orders?" + urllib.parse.urlencode({"status":"open","limit":500})) or []

    now = datetime.now(timezone.utc)
    after = (now - timedelta(days=3)).date().isoformat()
    activities = request(key, secret, "/v2/account/activities?" + urllib.parse.urlencode({"after": after, "direction":"asc","page_size":100})) or []

    out = {
        "label": label,
        "timestamp_utc": now.isoformat(),
        "paper_base": PAPER_BASE,
        "account": {
            "status": account.get("status"),
            "equity": f(account.get("equity")),
            "last_equity": f(account.get("last_equity")),
            "cash": f(account.get("cash")),
            "portfolio_value": f(account.get("portfolio_value")),
            "long_market_value": f(account.get("long_market_value")),
            "short_market_value": f(account.get("short_market_value")),
            "initial_margin": f(account.get("initial_margin")),
            "maintenance_margin": f(account.get("maintenance_margin")),
            "buying_power": f(account.get("buying_power")),
            "trading_blocked": account.get("trading_blocked"),
        },
        "positions": [],
        "open_orders": [],
        "recent_activities": [],
    }

    if isinstance(positions, list):
        for p in positions:
            out["positions"].append({
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "qty": f(p.get("qty")),
                "avg_entry_price": f(p.get("avg_entry_price")),
                "current_price": f(p.get("current_price")),
                "market_value": f(p.get("market_value")),
                "cost_basis": f(p.get("cost_basis")),
                "unrealized_pl": f(p.get("unrealized_pl")),
                "unrealized_plpc": f(p.get("unrealized_plpc")),
            })
    else:
        out["positions_error"] = positions

    if isinstance(orders, list):
        for o in orders:
            out["open_orders"].append({
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "qty": f(o.get("qty")),
                "status": o.get("status"),
                "client_order_id": o.get("client_order_id"),
            })
    else:
        out["open_orders_error"] = orders

    if isinstance(activities, list):
        for a in activities:
            out["recent_activities"].append({
                "activity_type": a.get("activity_type"),
                "transaction_time": a.get("transaction_time"),
                "date": a.get("date"),
                "symbol": a.get("symbol"),
                "side": a.get("side"),
                "qty": f(a.get("qty")),
                "price": f(a.get("price")),
                "net_amount": f(a.get("net_amount")),
                "description": a.get("description"),
            })
    else:
        out["activities_error"] = activities

    return out


def main():
    control = reconcile("Strategy 1 / Control", "ALPACA_2500_PAPER_API_KEY_ID", "ALPACA_2500_PAPER_API_SECRET_KEY")
    s2 = reconcile("Strategy 2", "ALPACA_STRATEGY2_API_KEY_ID", "ALPACA_STRATEGY2_SECRET_KEY")
    payload = {"control": control, "strategy2": s2}
    Path("paper_account_reconciliation.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "control_equity": control["account"]["equity"],
        "strategy2_equity": s2["account"]["equity"],
        "control_positions": len(control["positions"]),
        "strategy2_positions": len(s2["positions"]),
        "control_open_orders": len(control["open_orders"]),
        "strategy2_open_orders": len(s2["open_orders"]),
        "control_recent_activities": len(control["recent_activities"]),
        "strategy2_recent_activities": len(s2["recent_activities"]),
    }, indent=2))


if __name__ == "__main__":
    main()
