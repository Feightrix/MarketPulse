import json
import math
from pathlib import Path

CAPITAL = 2500.0
ALPACA_SHORT_MIN_EQUITY = 2000.0
OPERATING_EQUITY_FLOOR = 2200.0
MAX_L1_TRACKING_ERROR_PCT = 5.0
MAX_NET_EXPOSURE_DRIFT_PCT = 3.0
MAX_GROSS_EXPOSURE_PCT = 100.0
MAX_SINGLE_SHORT_WEIGHT_PCT = 5.0

SIXB_LOG = Path("phase6b_paper_log.jsonl")
SIXC_LOG = Path("phase6c_forward_log.jsonl")
OUT_JSON = Path("phase6d_2500_capital_fit.json")
OUT_MD = Path("phase6d_2500_capital_fit.md")


def load_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def latest_rebalance():
    rows = [r for r in load_jsonl(SIXB_LOG) if r.get("status") == "REBALANCE_COMPLETE"]
    if not rows:
        raise RuntimeError("No completed Phase 6B rebalance found")
    return rows[-1]


def latest_snapshot():
    rows = load_jsonl(SIXC_LOG)
    if not rows:
        return None
    return rows[-1]


def round_short_nearest(target_shares):
    # Alpaca does not support fractional short sales. Use the nearest whole share,
    # not floor(), because nearest-share sizing minimizes capital-induced tracking error.
    return int(math.floor(float(target_shares) + 0.5))


def build_fit(event):
    weights = {s: float(w) for s, w in (event.get("target_weights") or {}).items()}
    tracking = event.get("tracking") or {}
    if not weights or not tracking:
        raise RuntimeError("Rebalance event lacks target weights/tracking references")

    rows = []
    represented = {}
    quantities = {}
    for sym, weight in weights.items():
        ref = float((tracking.get(sym) or {}).get("reference_price") or 0.0)
        if ref <= 0:
            raise RuntimeError(f"Missing reference price for {sym}")
        target_dollars = CAPITAL * weight
        if weight >= 0:
            qty = round(target_dollars / ref, 6)
        else:
            qty = -round_short_nearest(abs(target_dollars) / ref)
        represented_dollars = qty * ref
        actual_weight = represented_dollars / CAPITAL
        quantities[sym] = qty
        represented[sym] = actual_weight
        rows.append({
            "symbol": sym,
            "target_weight_pct": weight * 100.0,
            "reference_price": ref,
            "target_dollars": target_dollars,
            "quantity": qty,
            "represented_dollars": represented_dollars,
            "represented_weight_pct": actual_weight * 100.0,
            "tracking_error_pct_points": abs(actual_weight - weight) * 100.0,
        })

    target_net = sum(weights.values()) * 100.0
    target_gross = sum(abs(x) for x in weights.values()) * 100.0
    actual_net = sum(represented.values()) * 100.0
    actual_gross = sum(abs(x) for x in represented.values()) * 100.0
    l1 = sum(abs(represented[s] - weights[s]) for s in weights) * 100.0
    net_drift = abs(actual_net - target_net)
    cash_pct = 100.0 - actual_net
    largest_short = max([abs(w) * 100.0 for w in represented.values() if w < 0] or [0.0])

    checks = {
        "capital_meets_alpaca_short_minimum": CAPITAL >= ALPACA_SHORT_MIN_EQUITY,
        "capital_above_operating_floor": CAPITAL >= OPERATING_EQUITY_FLOOR,
        "l1_tracking_error_at_most_5pct": l1 <= MAX_L1_TRACKING_ERROR_PCT,
        "net_exposure_drift_at_most_3pct": net_drift <= MAX_NET_EXPOSURE_DRIFT_PCT,
        "gross_exposure_at_most_100pct": actual_gross <= MAX_GROSS_EXPOSURE_PCT,
        "single_short_weight_at_most_5pct": largest_short <= MAX_SINGLE_SHORT_WEIGHT_PCT,
        "all_intended_net_shorts_represented": all(quantities[s] <= -1 for s, w in weights.items() if w < 0),
    }

    return {
        "capital": CAPITAL,
        "source_rebalance_utc": event.get("timestamp_utc"),
        "signal_date": event.get("signal_date"),
        "target_weights": weights,
        "quantities": quantities,
        "represented_weights": represented,
        "positions": sorted(rows, key=lambda x: x["symbol"]),
        "target_net_exposure_pct": target_net,
        "target_gross_exposure_pct": target_gross,
        "represented_net_exposure_pct": actual_net,
        "represented_gross_exposure_pct": actual_gross,
        "cash_equivalent_pct": cash_pct,
        "l1_tracking_error_pct": l1,
        "net_exposure_drift_pct": net_drift,
        "largest_short_weight_pct": largest_short,
        "checks": checks,
        "gate": "PASS" if all(checks.values()) else "FAIL",
    }


def add_day1_shadow(result):
    snap = latest_snapshot()
    if not snap:
        return result
    pos = snap.get("positions") or {}
    pnl = {}
    total = 0.0
    for sym, qty in result["quantities"].items():
        p = pos.get(sym) or {}
        entry = float(p.get("avg_entry_price") or 0.0)
        current = float(p.get("current_price") or 0.0)
        if not entry or not current:
            continue
        if qty >= 0:
            value = qty * (current - entry)
        else:
            value = abs(qty) * (entry - current)
        pnl[sym] = value
        total += value
    result["shadow_mark_date"] = snap.get("date_et")
    result["shadow_pnl_by_symbol"] = pnl
    result["shadow_pnl_dollars"] = total
    result["shadow_return_pct"] = total / CAPITAL * 100.0
    result["shadow_equity"] = CAPITAL + total
    result["shadow_is_estimate"] = True
    return result


def write(result):
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = [
        "# MarketPulse Phase 6D — $2,500 Capital Fit",
        "",
        f"**Gate: {result['gate']}**",
        "",
        f"- Design capital: **${result['capital']:,.2f}**",
        f"- Source signal date: **{result.get('signal_date')}**",
        f"- Target net exposure: **{result['target_net_exposure_pct']:.2f}%**",
        f"- Represented net exposure: **{result['represented_net_exposure_pct']:.2f}%**",
        f"- Target gross exposure: **{result['target_gross_exposure_pct']:.2f}%**",
        f"- Represented gross exposure: **{result['represented_gross_exposure_pct']:.2f}%**",
        f"- Capital-fit L1 tracking error: **{result['l1_tracking_error_pct']:.2f}%**",
        f"- Cash-equivalent allocation: **{result['cash_equivalent_pct']:.2f}%**",
        f"- Largest single short: **{result['largest_short_weight_pct']:.2f}%**",
    ]
    if "shadow_equity" in result:
        lines += [
            "",
            "## $2,500 shadow mark",
            f"- Mark date: **{result.get('shadow_mark_date')}**",
            f"- Estimated equity: **${result['shadow_equity']:,.2f}**",
            f"- Estimated P/L: **${result['shadow_pnl_dollars']:+.2f} ({result['shadow_return_pct']:+.4f}%)**",
            "- This uses the $100k paper account's observed prices/fills to estimate the $2,500-sized portfolio; it is not a separate $2,500 broker account fill record.",
        ]
    lines += ["", "## $2,500 quantities"]
    for r in result["positions"]:
        lines.append(
            f"- {r['symbol']}: {r['quantity']:+g} shares | target {r['target_weight_pct']:+.2f}% | represented {r['represented_weight_pct']:+.2f}%"
        )
    lines += ["", "## Capital-fit checks"]
    for k, v in result["checks"].items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += [
        "",
        "## Design rule",
        "Long positions use fractional shares. Net short positions use nearest whole-share sizing because Alpaca does not support fractional short sales. If the capital-fit gate fails, MarketPulse must not pretend the $2,500 account can reproduce the frozen signal safely.",
        "",
        "This is a paper/shadow implementation test, not a profit guarantee.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n")


def main():
    result = build_fit(latest_rebalance())
    result = add_day1_shadow(result)
    write(result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
