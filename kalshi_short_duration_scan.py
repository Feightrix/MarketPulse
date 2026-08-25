import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES = {
    "KXINXHUD": "S&P 500 Hourly Up/Down",
    "KXNDQHUD": "NASDAQ-100 Hourly Up/Down",
}
RESULT_JSON = "kalshi_short_duration_scan_results.json"
RESULT_MD = "kalshi_short_duration_scan_results.md"


def get_json(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "MarketPulse-Kalshi-Scanner/1.1",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fnum(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_market(m, label):
    bid = fnum(m.get("yes_bid_dollars"))
    ask = fnum(m.get("yes_ask_dollars"))
    spread = None
    if bid is not None and ask is not None:
        spread = round(max(0.0, ask - bid), 4)
    return {
        "ticker": m.get("ticker"),
        "title": m.get("title") or label,
        "subtitle": m.get("subtitle") or m.get("yes_sub_title"),
        "status": m.get("status"),
        "open_time": m.get("open_time"),
        "close_time": m.get("close_time") or m.get("expected_expiration_time"),
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "yes_spread_dollars": spread,
        "last_price_dollars": fnum(m.get("last_price_dollars")),
        "volume_24h_fp": fnum(m.get("volume_24h_fp")),
        "open_interest_fp": fnum(m.get("open_interest_fp")),
        "liquidity_dollars": fnum(m.get("liquidity_dollars")),
        "floor_strike": m.get("floor_strike"),
        "can_close_early": bool(m.get("can_close_early")),
    }


def fetch_status(series_ticker, label, status):
    data = get_json("/markets", {
        "series_ticker": series_ticker,
        "status": status,
        "limit": 100,
    })
    markets = [normalize_market(m, label) for m in (data.get("markets") or [])]
    markets.sort(key=lambda x: (x.get("close_time") or "", x.get("ticker") or ""))
    return markets


def scan_series(series_ticker, label):
    open_markets = fetch_status(series_ticker, label, "open")
    upcoming_markets = fetch_status(series_ticker, label, "unopened")
    return {
        "series_ticker": series_ticker,
        "label": label,
        "open_market_count": len(open_markets),
        "upcoming_market_count": len(upcoming_markets),
        "open_markets": open_markets,
        "upcoming_markets": upcoming_markets,
    }


def market_line(m):
    bid = "—" if m["yes_bid_dollars"] is None else f"${m['yes_bid_dollars']:.2f}"
    ask = "—" if m["yes_ask_dollars"] is None else f"${m['yes_ask_dollars']:.2f}"
    spread = "—" if m["yes_spread_dollars"] is None else f"${m['yes_spread_dollars']:.2f}"
    return (
        f"- `{m['ticker']}` | {m['status']} | opens {m['open_time']} | closes {m['close_time']} "
        f"| YES {bid}/{ask} | spread {spread} | 24h vol {m['volume_24h_fp']}"
    )


def main():
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Kalshi public production Trade API",
        "order_submission_enabled": False,
        "authentication_required": False,
        "series": [],
    }
    for ticker, label in SERIES.items():
        result["series"].append(scan_series(ticker, label))

    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# MarketPulse — Kalshi Short-Duration Market Scan",
        "",
        "**Public market-data scan only. No authentication and no order submission.**",
        "",
        f"Generated: **{result['generated_utc']}**",
        "",
    ]
    for block in result["series"]:
        lines += [
            f"## {block['label']} ({block['series_ticker']})",
            f"Open markets: **{block['open_market_count']}** | Upcoming markets: **{block['upcoming_market_count']}**",
            "",
            "### Open",
        ]
        lines.extend(market_line(m) for m in block["open_markets"][:12])
        if not block["open_markets"]:
            lines.append("- None")
        lines += ["", "### Upcoming"]
        lines.extend(market_line(m) for m in block["upcoming_markets"][:12])
        if not block["upcoming_markets"]:
            lines.append("- None")
        lines.append("")

    Path(RESULT_MD).write_text("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
