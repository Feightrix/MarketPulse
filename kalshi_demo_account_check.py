import base64
import json
import os
import time
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE = "https://external-api.demo.kalshi.co"
ROOT = "/trade-api/v2"
RESULT_JSON = "kalshi_demo_account_check.json"
RESULT_MD = "kalshi_demo_account_check.md"

API_KEY_ID = os.getenv("KALSHI_DEMO_API_KEY_ID")
PRIVATE_KEY_PEM = os.getenv("KALSHI_DEMO_PRIVATE_KEY")

if not API_KEY_ID or not PRIVATE_KEY_PEM:
    raise RuntimeError("Missing KALSHI_DEMO_API_KEY_ID or KALSHI_DEMO_PRIVATE_KEY")

if "demo.kalshi.co" not in BASE:
    raise RuntimeError("Refusing non-demo Kalshi endpoint")

PRIVATE_KEY = serialization.load_pem_private_key(
    PRIVATE_KEY_PEM.replace("\\n", "\n").encode("utf-8"), password=None
)


def signed_headers(method, path):
    timestamp = str(int(time.time() * 1000))
    msg = f"{timestamp}{method}{path.split('?')[0]}".encode("utf-8")
    sig = PRIVATE_KEY.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("ascii"),
        "Accept": "application/json",
        "User-Agent": "MarketPulse-Kalshi-Demo-Check/1.0",
    }


def get(path):
    req = urllib.request.Request(BASE + path, headers=signed_headers("GET", path), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    balance = get(ROOT + "/portfolio/balance")
    orders = get(ROOT + "/portfolio/orders?status=resting&limit=100")
    positions = get(ROOT + "/portfolio/positions?limit=100")

    result = {
        "environment": "demo",
        "base_url": BASE,
        "order_submission_enabled": False,
        "api_key_present": True,
        "balance": balance,
        "resting_order_count": len(orders.get("orders") or []),
        "position_count": len(positions.get("market_positions") or []),
    }
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    bal = balance.get("balance")
    portfolio = balance.get("portfolio_value")
    lines = [
        "# MarketPulse — Kalshi Demo Account Check",
        "",
        "**Authenticated demo check only. No order submission.**",
        "",
        f"- Environment: **DEMO**",
        f"- Balance response: **{bal}**",
        f"- Portfolio value response: **{portfolio}**",
        f"- Resting orders: **{result['resting_order_count']}**",
        f"- Positions: **{result['position_count']}**",
        "- Order submission enabled: **NO**",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
