import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2
import options_pattern2_trend_refinement as p2t
import options_pattern2_contract_sim as cs

RESULT_JSON = "options_pattern2_contract_ab_results.json"
RESULT_MD = "options_pattern2_contract_ab_results.md"
STARTING_BALANCE = 2500.0
LOOKBACK_DAYS = 900
DEVELOPMENT_START = datetime(2024, 9, 4).date()
MAX_PREMIUM_FRACTION = 0.20
MIN_PREMIUM_DOLLARS = 30.0
ENTRY_DELAY_MINUTES = 1
EXIT_BAR_OFFSET_MINUTES = 4
FALLBACK_HALF_SPREAD_PCT = 0.005
FALLBACK_MIN_HALF_SPREAD = 0.01
EST_ROUNDTRIP_FEES = 0.11
MAX_SHORTLIST_PER_POLICY = 3

# Frozen underlying signal. No signal parameters are tuned here.
FROZEN_TREND_CFG = {
    "max_vwap_slope_atr": 0.50,
    "max_efficiency": 0.65,
    "min_rsi_turn": 3.0,
}

# Predeclared contract profiles. side_moneyness: positive=OTM, zero=ATM, negative=ITM.
POLICIES = [
    {"name": "control_4d_otm35", "target_dte": 4, "side_moneyness": 0.005, "target_abs_delta": 0.35},
    {"name": "atm_2d_delta50", "target_dte": 2, "side_moneyness": 0.000, "target_abs_delta": 0.50},
    {"name": "atm_4d_delta50", "target_dte": 4, "side_moneyness": 0.000, "target_abs_delta": 0.50},
    {"name": "itm_4d_delta60", "target_dte": 4, "side_moneyness": -0.005, "target_abs_delta": 0.60},
    {"name": "atm_7d_delta50", "target_dte": 7, "side_moneyness": 0.000, "target_abs_delta": 0.50},
    {"name": "itm_7d_delta60", "target_dte": 7, "side_moneyness": -0.005, "target_abs_delta": 0.60},
]


def build_frozen_signals():
    old = base.LOOKBACK_DAYS
    base.LOOKBACK_DAYS = LOOKBACK_DAYS
    try:
        raw = base.fetch_bars()
    finally:
        base.LOOKBACK_DAYS = old

    by_day = base.regular_session_bars(raw)
    days = []
    bar_lookup = {}
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        p2.add_atr_rsi(bars)
        days.append((day, bars))
        for b in bars:
            bar_lookup[b["ts"].isoformat()] = b

    signals = p2t.evaluate(days, FROZEN_TREND_CFG)
    enriched = []
    for sig in signals:
        b = bar_lookup.get(sig["entry_ts"])
        if not b:
            continue
        item = dict(sig)
        item["underlying_entry_spot"] = round(float(b["c"]), 4)
        enriched.append(item)
    return enriched, len(days)


def side_moneyness(side, strike, spot):
    # Positive means OTM for either side; negative means ITM.
    if side == "CALL":
        return strike / spot - 1.0
    return 1.0 - strike / spot


def fetch_contracts_wide(signal_date, side, spot):
    day = datetime.fromisoformat(signal_date).date()
    exp_gte = day + timedelta(days=1)
    exp_lte = day + timedelta(days=10)
    option_type = "call" if side == "CALL" else "put"
    contracts = []
    for status in ("inactive", "active"):
        data = cs.request_json(
            f"{cs.PAPER_BASE}/v2/options/contracts",
            params={
                "underlying_symbols": cs.UNDERLYING,
                "status": status,
                "expiration_date_gte": exp_gte.isoformat(),
                "expiration_date_lte": exp_lte.isoformat(),
                "type": option_type,
                "strike_price_gte": f"{spot * 0.97:.2f}",
                "strike_price_lte": f"{spot * 1.03:.2f}",
                "limit": 1000,
            },
        )
        if data:
            contracts.extend(data.get("option_contracts", []))
    return list({c.get("symbol"): c for c in contracts if c.get("symbol")}.values())


def contract_meta(c, signal_date, side, spot):
    try:
        exp = datetime.fromisoformat(c["expiration_date"]).date()
        strike = float(c["strike_price"])
    except Exception:
        return None
    day = datetime.fromisoformat(signal_date).date()
    dte = (exp - day).days
    if not (1 <= dte <= 10):
        return None
    return {
        "contract": c,
        "symbol": c["symbol"],
        "expiration_date": c["expiration_date"],
        "dte": dte,
        "strike": strike,
        "side_moneyness": side_moneyness(side, strike, spot),
        "open_interest": int(c.get("open_interest") or 0),
    }


def shortlist_for_policy(metas, policy):
    scored = []
    for m in metas:
        # Pre-price shortlist: DTE and moneyness only. Delta is used after actual premium is known.
        score = (
            abs(m["dte"] - policy["target_dte"]),
            abs(m["side_moneyness"] - policy["side_moneyness"]),
            -m["open_interest"],
        )
        scored.append((score, m))
    scored.sort(key=lambda x: x[0])
    return [m for _, m in scored[:MAX_SHORTLIST_PER_POLICY]]


def fetch_option_bars_multi(symbols, start_dt, end_dt):
    if not symbols:
        return {}
    data = cs.request_json(
        f"{cs.DATA_BASE}/v1beta1/options/bars",
        params={
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": cs.utc_iso(start_dt),
            "end": cs.utc_iso(end_dt),
            "limit": 10000,
            "sort": "asc",
        },
    )
    raw = (data or {}).get("bars", {})
    out = {}
    if isinstance(raw, dict):
        for symbol, bars in raw.items():
            for b in bars:
                if "t" in b:
                    b["_ts"] = cs.parse_ts(b["t"])
            out[symbol] = bars
    return out


def conservative_fill(bar, is_entry):
    px = float(bar.get("o") if is_entry else bar.get("c"))
    haircut = max(FALLBACK_MIN_HALF_SPREAD, px * FALLBACK_HALF_SPREAD_PCT)
    return px + haircut if is_entry else max(0.01, px - haircut)


def first_bar_at_or_after(bars, target_dt):
    for b in bars:
        if b.get("_ts") and b["_ts"] >= target_dt:
            return b
    return None


def priced_candidate(meta, bars, sig, entry_ref, exit_ref):
    entry_bar = first_bar_at_or_after(bars, entry_ref)
    exit_bar = first_bar_at_or_after(bars, exit_ref)
    if entry_bar is None or exit_bar is None:
        return None
    entry_fill = conservative_fill(entry_bar, True)
    exit_fill = conservative_fill(exit_bar, False)
    premium = entry_fill * 100.0
    if premium < MIN_PREMIUM_DOLLARS or premium > STARTING_BALANCE * MAX_PREMIUM_FRACTION:
        return None
    iv, delta = cs.implied_vol_and_delta(
        float(sig["underlying_entry_spot"]), meta["strike"], entry_fill, meta["dte"], sig["side"]
    )
    if delta is None:
        return None
    gross = (exit_fill - entry_fill) * 100.0
    net = gross - EST_ROUNDTRIP_FEES
    return {
        **meta,
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "premium_dollars": premium,
        "iv_proxy": iv,
        "delta_proxy": delta,
        "abs_delta": abs(delta),
        "gross_pl_dollars": gross,
        "net_pl_dollars": net,
    }


def final_policy_score(candidate, policy):
    # Balanced DTE / moneyness / delta objective, fixed before seeing results.
    dte_term = abs(candidate["dte"] - policy["target_dte"]) / 3.0
    money_term = abs(candidate["side_moneyness"] - policy["side_moneyness"]) / 0.005
    delta_term = abs(candidate["abs_delta"] - policy["target_abs_delta"]) / 0.15
    return 0.30 * dte_term + 0.30 * money_term + 0.40 * delta_term


def evaluate_signal(sig):
    entry_ts = cs.parse_ts(sig["entry_ts"])
    exit_ts = cs.parse_ts(sig["exit_ts"])
    signal_date = entry_ts.date().isoformat()
    spot = float(sig["underlying_entry_spot"])
    contracts = fetch_contracts_wide(signal_date, sig["side"], spot)
    metas = [m for c in contracts if (m := contract_meta(c, signal_date, sig["side"], spot))]
    if not metas:
        return {}, "no_contracts"

    policy_shortlists = {p["name"]: shortlist_for_policy(metas, p) for p in POLICIES}
    symbols = sorted({m["symbol"] for lst in policy_shortlists.values() for m in lst})
    entry_ref = entry_ts + timedelta(minutes=ENTRY_DELAY_MINUTES)
    exit_ref = exit_ts + timedelta(minutes=EXIT_BAR_OFFSET_MINUTES)
    if exit_ref <= entry_ref:
        exit_ref = entry_ref + timedelta(minutes=1)
    bar_map = fetch_option_bars_multi(symbols, entry_ts, exit_ref + timedelta(minutes=2))

    priced = {}
    for symbol in symbols:
        meta = next((m for m in metas if m["symbol"] == symbol), None)
        if meta and bar_map.get(symbol):
            pc = priced_candidate(meta, bar_map[symbol], sig, entry_ref, exit_ref)
            if pc:
                priced[symbol] = pc

    results = {}
    for p in POLICIES:
        candidates = [priced[m["symbol"]] for m in policy_shortlists[p["name"]] if m["symbol"] in priced]
        if not candidates:
            continue
        chosen = min(candidates, key=lambda c: final_policy_score(c, p))
        net = chosen["net_pl_dollars"]
        results[p["name"]] = {
            "date": signal_date,
            "side": sig["side"],
            "entry_ts": sig["entry_ts"],
            "exit_ts": sig["exit_ts"],
            "underlying_r": sig["r"],
            "underlying_exit_reason": sig["exit_reason"],
            "underlying_entry_spot": spot,
            "contract": chosen["symbol"],
            "expiration_date": chosen["expiration_date"],
            "dte": chosen["dte"],
            "strike": chosen["strike"],
            "side_moneyness_pct": round(chosen["side_moneyness"] * 100.0, 3),
            "delta_proxy": round(chosen["delta_proxy"], 4),
            "iv_proxy": chosen["iv_proxy"],
            "entry_fill": round(chosen["entry_fill"], 4),
            "exit_fill": round(chosen["exit_fill"], 4),
            "premium_dollars": round(chosen["premium_dollars"], 2),
            "net_pl_dollars": round(net, 2),
            "return_on_premium_pct": round(net / chosen["premium_dollars"] * 100.0, 2),
            "fill_mode": "trade_bar_conservative",
        }
    return results, None


def summarize(trades):
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "net_pl_dollars": 0.0,
            "ending_balance_dollars": STARTING_BALANCE, "return_pct": 0.0, "profit_factor": None,
            "avg_win_dollars": 0.0, "avg_loss_dollars": 0.0, "max_drawdown_dollars": 0.0,
            "avg_premium_dollars": 0.0, "avg_abs_delta": 0.0, "avg_dte": 0.0,
        }
    ordered = sorted(trades, key=lambda x: x["entry_ts"])
    pls = [float(t["net_pl_dollars"]) for t in ordered]
    wins = [x for x in pls if x > 0]
    losses = [x for x in pls if x <= 0]
    equity = STARTING_BALANCE
    peak = equity
    max_dd = 0.0
    for pl in pls:
        equity += pl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": len(pls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(pls) * 100.0, 2),
        "net_pl_dollars": round(sum(pls), 2),
        "ending_balance_dollars": round(STARTING_BALANCE + sum(pls), 2),
        "return_pct": round(sum(pls) / STARTING_BALANCE * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "avg_win_dollars": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_dollars": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_dollars": round(max_dd, 2),
        "avg_premium_dollars": round(sum(t["premium_dollars"] for t in trades) / len(trades), 2),
        "avg_abs_delta": round(sum(abs(t["delta_proxy"]) for t in trades) / len(trades), 3),
        "avg_dte": round(sum(t["dte"] for t in trades) / len(trades), 2),
    }


def split_three(trades):
    ordered = sorted(trades, key=lambda x: x["entry_ts"])
    n = len(ordered)
    if n == 0:
        return [[], [], []]
    one = max(1, n // 3)
    return [ordered[:one], ordered[one:2 * one], ordered[2 * one:]]


def robust_key(folds, full):
    return (
        min(f["net_pl_dollars"] for f in folds),
        min((f["profit_factor"] or 0.0) for f in folds),
        full["net_pl_dollars"],
        -full["max_drawdown_dollars"],
        full["win_rate_pct"],
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    sel = result["selected_policy"]
    dev = result["selected_development"]
    ext = result["selected_external_validation"]
    full = result["selected_full_900"]
    ctl = result["control_full_900"]
    lines = [
        "# MarketPulse — Pattern 2 Contract Policy A/B",
        "",
        "**Research only. Underlying signals are frozen; order submission remains disabled.**",
        "",
        "## Selected Contract Policy",
        f"- Policy: **{sel['name']}**",
        f"- Target DTE: **{sel['target_dte']}**",
        f"- Target moneyness: **{sel['side_moneyness'] * 100:.2f}%** (positive=OTM, negative=ITM)",
        f"- Target absolute delta proxy: **{sel['target_abs_delta']:.2f}**",
        "",
        "## Development Window (Sep 2024 onward)",
        f"- Trades: **{dev['trades']}**",
        f"- Win rate: **{dev['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${dev['net_pl_dollars']:,.2f}**",
        f"- Profit factor: **{dev['profit_factor']}**",
        f"- Max drawdown: **${dev['max_drawdown_dollars']:,.2f}**",
        "",
        "## Older External Validation (before Sep 4, 2024)",
        f"- Trades: **{ext['trades']}**",
        f"- Win rate: **{ext['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${ext['net_pl_dollars']:,.2f}**",
        f"- Profit factor: **{ext['profit_factor']}**",
        f"- Max drawdown: **${ext['max_drawdown_dollars']:,.2f}**",
        "",
        "## Full 900-Day Contract Simulation",
        f"- Trades: **{full['trades']}**",
        f"- Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${full['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%**",
        f"- Profit factor: **{full['profit_factor']}**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        "",
        "## Control Comparison",
        f"- Control full P/L: **${ctl['net_pl_dollars']:,.2f}**",
        f"- Selected beats control: **{'YES' if result['beats_control_full'] else 'NO'}**",
        f"- Selected profitable in all 3 development folds: **{'YES' if result['profitable_all_dev_folds'] else 'NO'}**",
        f"- Selected profitable in older external validation: **{'YES' if result['external_profitable'] else 'NO'}**",
        "",
        "Fills use actual 1-minute historical option trade bars with the same conservative 0.5% execution haircut and estimated fees as the control simulation. Historical Greeks are not assumed; delta is reconstructed from the actual entry premium as a proxy.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    signals, sessions = build_frozen_signals()
    trades_by_policy = defaultdict(list)
    skips = Counter()
    for idx, sig in enumerate(signals, 1):
        results, reason = evaluate_signal(sig)
        if reason:
            skips[reason] += 1
        for name, trade in results.items():
            trades_by_policy[name].append(trade)
        if idx % 20 == 0:
            print(f"processed {idx}/{len(signals)} signals")

    policy_results = {}
    eligible = []
    for p in POLICIES:
        trades = trades_by_policy[p["name"]]
        development = [t for t in trades if datetime.fromisoformat(t["date"]).date() >= DEVELOPMENT_START]
        external = [t for t in trades if datetime.fromisoformat(t["date"]).date() < DEVELOPMENT_START]
        dev_folds_raw = split_three(development)
        dev_folds = [summarize(f) for f in dev_folds_raw]
        dev_summary = summarize(development)
        external_summary = summarize(external)
        full_summary = summarize(trades)
        profitable_folds = all(f["trades"] >= 8 and f["net_pl_dollars"] > 0 for f in dev_folds)
        item = {
            "policy": p,
            "development_folds": dev_folds,
            "development": dev_summary,
            "external_validation": external_summary,
            "full_900": full_summary,
            "profitable_all_dev_folds": profitable_folds,
        }
        policy_results[p["name"]] = item
        if profitable_folds:
            eligible.append((robust_key(dev_folds, dev_summary), p["name"]))

    if eligible:
        eligible.sort(reverse=True)
        selected_name = eligible[0][1]
    else:
        ranked = []
        for p in POLICIES:
            item = policy_results[p["name"]]
            ranked.append((robust_key(item["development_folds"], item["development"]), p["name"]))
        ranked.sort(reverse=True)
        selected_name = ranked[0][1]

    selected = policy_results[selected_name]
    control = policy_results["control_4d_otm35"]
    selected_trades = trades_by_policy[selected_name]
    result = {
        "strategy": "options_pattern2_contract_policy_ab",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "lookback_days": LOOKBACK_DAYS,
        "complete_sessions": sessions,
        "signals_generated": len(signals),
        "development_start": DEVELOPMENT_START.isoformat(),
        "policies_predeclared": POLICIES,
        "policy_results": policy_results,
        "selected_policy": selected["policy"],
        "selected_development": selected["development"],
        "selected_development_folds": selected["development_folds"],
        "selected_external_validation": selected["external_validation"],
        "selected_full_900": selected["full_900"],
        "control_full_900": control["full_900"],
        "control_development": control["development"],
        "control_external_validation": control["external_validation"],
        "profitable_all_dev_folds": selected["profitable_all_dev_folds"],
        "external_profitable": selected["external_validation"]["net_pl_dollars"] > 0,
        "beats_control_full": selected["full_900"]["net_pl_dollars"] > control["full_900"]["net_pl_dollars"],
        "direction_split_selected": {
            "calls": summarize([t for t in selected_trades if t["side"] == "CALL"]),
            "puts": summarize([t for t in selected_trades if t["side"] == "PUT"]),
        },
        "skip_reasons": dict(skips),
        "selected_trades": selected_trades,
    }
    write_results(result)


if __name__ == "__main__":
    main()
