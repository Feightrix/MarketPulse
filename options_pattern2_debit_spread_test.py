import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import options_pattern2_contract_ab as ab
import options_pattern2_contract_sim as cs

RESULT_JSON = "options_pattern2_debit_spread_results.json"
RESULT_MD = "options_pattern2_debit_spread_results.md"
STARTING_BALANCE = 2500.0
MONTHLY_TARGET = 1000.0
DEVELOPMENT_START = ab.DEVELOPMENT_START
MIN_FOLD_TRADES = 15
MIN_EXTERNAL_TRADES = 10

# Freeze the successful long leg. Only the spread structure is new.
LONG_POLICY = {"name": "itm_4d_delta60", "target_dte": 4, "side_moneyness": -0.005, "target_abs_delta": 0.60}

# Predeclared spread structure: short leg same expiry, farther OTM by a fraction of spot.
SPREAD_WIDTHS = [
    {"name": "width_050", "width_spot_pct": 0.0050},
    {"name": "width_075", "width_spot_pct": 0.0075},
    {"name": "width_100", "width_spot_pct": 0.0100},
]
# Debit budget is max premium-at-risk for the whole position, not per spread unit.
DEBIT_BUDGET_FRACTIONS = [0.10, 0.15, 0.20]
ABS_MAX_DEBIT_DOLLARS = 500.0
MIN_UNIT_DEBIT_DOLLARS = 20.0
MAX_UNITS = 10
EST_ROUNDTRIP_FEES_PER_SPREAD = 0.22
FALLBACK_HALF_SPREAD_PCT = 0.005
FALLBACK_MIN_HALF_SPREAD = 0.01
ORDER_SUBMISSION_ENABLED = False


def half_spread(px):
    return max(FALLBACK_MIN_HALF_SPREAD, px * FALLBACK_HALF_SPREAD_PCT)


def leg_fill(bar, action):
    # Entry BUY pays above trade-bar open; entry SELL receives below open.
    # Exit SELL receives below close; exit BUY pays above close.
    px = float(bar.get("o") if action in ("entry_buy", "entry_sell") else bar.get("c"))
    h = half_spread(px)
    if action in ("entry_buy", "exit_buy"):
        return px + h
    return max(0.01, px - h)


def target_short_strike(side, long_strike, spot, width_pct):
    width = spot * width_pct
    return long_strike + width if side == "CALL" else long_strike - width


def short_candidates(metas, long_meta, side, spot, width_pct):
    target = target_short_strike(side, long_meta["strike"], spot, width_pct)
    out = []
    for m in metas:
        if m["expiration_date"] != long_meta["expiration_date"]:
            continue
        if side == "CALL" and m["strike"] <= long_meta["strike"]:
            continue
        if side == "PUT" and m["strike"] >= long_meta["strike"]:
            continue
        out.append(m)
    out.sort(key=lambda m: (abs(m["strike"] - target), -m["open_interest"]))
    return out[:3]


def priced_leg(meta, bars, entry_ref, exit_ref, is_long):
    eb = ab.first_bar_at_or_after(bars, entry_ref)
    xb = ab.first_bar_at_or_after(bars, exit_ref)
    if eb is None or xb is None:
        return None
    if is_long:
        entry = leg_fill(eb, "entry_buy")
        exit_px = leg_fill(xb, "exit_sell")
    else:
        entry = leg_fill(eb, "entry_sell")
        exit_px = leg_fill(xb, "exit_buy")
    return {"entry": entry, "exit": exit_px}


def long_score(candidate, policy=LONG_POLICY):
    if candidate.get("abs_delta") is None:
        return 1e9
    dte_term = abs(candidate["dte"] - policy["target_dte"]) / 3.0
    money_term = abs(candidate["side_moneyness"] - policy["side_moneyness"]) / 0.005
    delta_term = abs(candidate["abs_delta"] - policy["target_abs_delta"]) / 0.15
    return 0.30 * dte_term + 0.30 * money_term + 0.40 * delta_term


def evaluate_signal(sig):
    entry_ts = cs.parse_ts(sig["entry_ts"])
    exit_ts = cs.parse_ts(sig["exit_ts"])
    signal_date = entry_ts.date().isoformat()
    spot = float(sig["underlying_entry_spot"])
    contracts = ab.fetch_contracts_wide(signal_date, sig["side"], spot)
    metas = [m for c in contracts if (m := ab.contract_meta(c, signal_date, sig["side"], spot))]
    if not metas:
        return {}, "no_contracts"

    long_list = ab.challenger_shortlist(metas, LONG_POLICY)
    pairs = defaultdict(list)
    all_symbols = set()
    for long_meta in long_list:
        all_symbols.add(long_meta["symbol"])
        for w in SPREAD_WIDTHS:
            shorts = short_candidates(metas, long_meta, sig["side"], spot, w["width_spot_pct"])
            for sm in shorts:
                all_symbols.add(sm["symbol"])
                pairs[w["name"]].append((long_meta, sm))

    entry_ref = entry_ts + timedelta(minutes=ab.ENTRY_DELAY_MINUTES)
    exit_ref = exit_ts + timedelta(minutes=ab.EXIT_BAR_OFFSET_MINUTES)
    if exit_ref <= entry_ref:
        exit_ref = entry_ref + timedelta(minutes=1)
    bar_map = ab.fetch_option_bars_multi(sorted(all_symbols), entry_ts, exit_ref + timedelta(minutes=2))

    # Price long legs once, including reconstructed delta used by the already-frozen long policy.
    long_priced = {}
    for lm in long_list:
        bars = bar_map.get(lm["symbol"])
        if not bars:
            continue
        fills = priced_leg(lm, bars, entry_ref, exit_ref, True)
        if not fills:
            continue
        iv, delta = cs.implied_vol_and_delta(spot, lm["strike"], fills["entry"], lm["dte"], sig["side"])
        if delta is None:
            continue
        long_priced[lm["symbol"]] = {**lm, **fills, "iv_proxy": iv, "delta_proxy": delta, "abs_delta": abs(delta)}

    results = {}
    for w in SPREAD_WIDTHS:
        candidates = []
        for lm, sm in pairs[w["name"]]:
            lp = long_priced.get(lm["symbol"])
            sbars = bar_map.get(sm["symbol"])
            if not lp or not sbars:
                continue
            sf = priced_leg(sm, sbars, entry_ref, exit_ref, False)
            if not sf:
                continue
            entry_debit = lp["entry"] - sf["entry"]
            exit_credit = lp["exit"] - sf["exit"]
            if entry_debit <= 0:
                continue
            # A same-expiry long vertical cannot have negative intrinsic value; noisy bar reconstruction can.
            exit_credit = max(0.0, exit_credit)
            unit_debit = entry_debit * 100.0
            if unit_debit < MIN_UNIT_DEBIT_DOLLARS or unit_debit > ABS_MAX_DEBIT_DOLLARS:
                continue
            unit_net = (exit_credit - entry_debit) * 100.0 - EST_ROUNDTRIP_FEES_PER_SPREAD
            realized_width = abs(sm["strike"] - lm["strike"])
            expected_width = spot * w["width_spot_pct"]
            width_error = abs(realized_width - expected_width) / max(expected_width, 0.01)
            score = long_score(lp) + 0.25 * width_error
            candidates.append((score, {
                "date": signal_date,
                "side": sig["side"],
                "entry_ts": sig["entry_ts"],
                "exit_ts": sig["exit_ts"],
                "underlying_entry_spot": spot,
                "underlying_r": sig["r"],
                "underlying_exit_reason": sig["exit_reason"],
                "long_contract": lp["symbol"],
                "short_contract": sm["symbol"],
                "expiration_date": lp["expiration_date"],
                "dte": lp["dte"],
                "long_strike": lp["strike"],
                "short_strike": sm["strike"],
                "spread_width_dollars": realized_width,
                "long_delta_proxy": lp["delta_proxy"],
                "long_iv_proxy": lp["iv_proxy"],
                "long_entry": lp["entry"],
                "short_entry": sf["entry"],
                "long_exit": lp["exit"],
                "short_exit": sf["exit"],
                "unit_debit_dollars": unit_debit,
                "unit_net_pl_dollars": unit_net,
                "unit_return_on_debit_pct": unit_net / unit_debit * 100.0,
                "fill_mode": "two_leg_trade_bar_conservative",
            }))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            results[w["name"]] = candidates[0][1]
    return results, None


def size_stream(unit_trades, budget_fraction, starting_balance=STARTING_BALANCE):
    equity = starting_balance
    peak = equity
    max_dd = 0.0
    out = []
    for t in sorted(unit_trades, key=lambda x: x["entry_ts"]):
        budget = min(ABS_MAX_DEBIT_DOLLARS, equity * budget_fraction)
        units = min(MAX_UNITS, int(budget // float(t["unit_debit_dollars"])))
        if units < 1:
            continue
        pl = float(t["unit_net_pl_dollars"]) * units
        debit = float(t["unit_debit_dollars"]) * units
        rec = dict(t)
        rec.update({
            "units": units,
            "position_debit_dollars": round(debit, 2),
            "net_pl_dollars": round(pl, 2),
        })
        out.append(rec)
        equity += pl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return out, round(max_dd, 2)


def summarize(trades, max_dd=None, starting_balance=STARTING_BALANCE):
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "net_pl_dollars": 0.0, "ending_balance_dollars": starting_balance, "return_pct": 0.0, "profit_factor": None, "expectancy_dollars": 0.0, "max_drawdown_dollars": 0.0, "avg_position_debit_dollars": 0.0, "avg_units": 0.0}
    pls = [float(t["net_pl_dollars"]) for t in trades]
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p <= 0]
    gp, gl = sum(wins), -sum(losses)
    if max_dd is None:
        eq = starting_balance; peak = eq; dd = 0.0
        for p in pls:
            eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
        max_dd = dd
    net = sum(pls)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 2),
        "net_pl_dollars": round(net, 2), "ending_balance_dollars": round(starting_balance + net, 2),
        "return_pct": round(100 * net / starting_balance, 2),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "expectancy_dollars": round(net / len(trades), 2),
        "max_drawdown_dollars": round(max_dd, 2),
        "max_drawdown_pct_starting": round(100 * max_dd / starting_balance, 2),
        "avg_position_debit_dollars": round(statistics.mean(float(t["position_debit_dollars"]) for t in trades), 2),
        "avg_units": round(statistics.mean(float(t["units"]) for t in trades), 2),
    }


def month_summary(trades, first_date, last_date):
    months = []
    cur = first_date.replace(day=1)
    last = last_date.replace(day=1)
    while cur <= last:
        months.append(cur.strftime("%Y-%m"))
        cur = cur.replace(year=cur.year + (1 if cur.month == 12 else 0), month=1 if cur.month == 12 else cur.month + 1)
    by = defaultdict(float)
    for t in trades:
        by[t["date"][:7]] += float(t["net_pl_dollars"])
    vals = [by[m] for m in months]
    return {
        "months": len(months),
        "average_monthly_pl_dollars": round(statistics.mean(vals), 2) if vals else 0.0,
        "median_monthly_pl_dollars": round(statistics.median(vals), 2) if vals else 0.0,
        "best_month_dollars": round(max(vals), 2) if vals else 0.0,
        "worst_month_dollars": round(min(vals), 2) if vals else 0.0,
        "positive_months_pct": round(100 * sum(v > 0 for v in vals) / len(vals), 2) if vals else 0.0,
        "months_at_or_above_target": sum(v >= MONTHLY_TARGET for v in vals),
        "details": [{"month": m, "pl_dollars": round(by[m], 2)} for m in months],
    }


def split_by_bounds(trades, bounds):
    return ab.split_dev_by_bounds(trades, bounds)


def main():
    signals, sessions = ab.build_frozen_signals()
    bounds = ab.development_fold_bounds(signals)
    units_by_width = defaultdict(list)
    skips = Counter()
    for idx, sig in enumerate(signals, 1):
        results, reason = evaluate_signal(sig)
        if reason:
            skips[reason] += 1
        for name, trade in results.items():
            units_by_width[name].append(trade)
        if idx % 20 == 0:
            print(f"processed {idx}/{len(signals)}", flush=True)

    candidates = {}
    eligible = []
    all_dates = sorted({cs.parse_ts(s["entry_ts"]).date() for s in signals})
    for width in SPREAD_WIDTHS:
        unit_trades = units_by_width[width["name"]]
        for frac in DEBIT_BUDGET_FRACTIONS:
            name = f"{width['name']}_budget_{int(frac*100)}"
            full_stream, full_dd = size_stream(unit_trades, frac)
            development_units = [t for t in unit_trades if datetime.fromisoformat(t["date"]).date() >= DEVELOPMENT_START]
            external_units = [t for t in unit_trades if datetime.fromisoformat(t["date"]).date() < DEVELOPMENT_START]
            dev_stream, dev_dd = size_stream(development_units, frac)
            ext_stream, ext_dd = size_stream(external_units, frac)
            fold_unit_lists = split_by_bounds(development_units, bounds)
            fold_summaries = []
            for fold_units in fold_unit_lists:
                fs, fdd = size_stream(fold_units, frac)
                fold_summaries.append(summarize(fs, fdd))
            dev_summary = summarize(dev_stream, dev_dd)
            ext_summary = summarize(ext_stream, ext_dd)
            full_summary = summarize(full_stream, full_dd)
            robust = all(x["trades"] >= MIN_FOLD_TRADES and x["net_pl_dollars"] > 0 and (x["profit_factor"] or 0) > 1.0 for x in fold_summaries)
            candidates[name] = {
                "width": width, "debit_budget_fraction": frac,
                "development_folds": fold_summaries,
                "development": dev_summary,
                "external_validation": ext_summary,
                "full_900": full_summary,
                "robust_development": robust,
            }
            if robust:
                eligible.append(name)

    selected = None
    if eligible:
        selected = max(eligible, key=lambda n: (min(x["net_pl_dollars"] for x in candidates[n]["development_folds"]), candidates[n]["development"]["net_pl_dollars"], -candidates[n]["development"]["max_drawdown_dollars"]))

    promoted = None
    if selected:
        c = candidates[selected]
        if c["external_validation"]["trades"] >= MIN_EXTERNAL_TRADES and c["external_validation"]["net_pl_dollars"] > 0:
            promoted = selected

    # Best overall is descriptive only, not a promoted policy.
    best_full = max(candidates, key=lambda n: candidates[n]["full_900"]["net_pl_dollars"])
    best_item = candidates[best_full]

    # Monthly stats only for descriptive best and any promoted robust candidate.
    def monthly_for(name):
        c = candidates[name]
        unit_trades = units_by_width[c["width"]["name"]]
        stream, _ = size_stream(unit_trades, c["debit_budget_fraction"])
        return month_summary(stream, all_dates[0], all_dates[-1])

    result = {
        "strategy": "options_pattern2_debit_spread_capital_efficiency",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": ORDER_SUBMISSION_ENABLED,
        "starting_balance": STARTING_BALANCE,
        "monthly_target": MONTHLY_TARGET,
        "complete_sessions": sessions,
        "signals_generated": len(signals),
        "long_policy_frozen": LONG_POLICY,
        "spread_widths_predeclared": SPREAD_WIDTHS,
        "debit_budget_fractions_predeclared": DEBIT_BUDGET_FRACTIONS,
        "fees_per_spread_roundtrip": EST_ROUNDTRIP_FEES_PER_SPREAD,
        "development_start": DEVELOPMENT_START.isoformat(),
        "development_fold_boundaries": [d.isoformat() for d in bounds],
        "candidates": candidates,
        "robust_candidate_selected": selected,
        "promoted_candidate": promoted,
        "best_full_sample_descriptive": best_full,
        "best_full_monthly": monthly_for(best_full),
        "promoted_monthly": monthly_for(promoted) if promoted else None,
        "skip_reasons": dict(skips),
        "method_note": "Frozen Pattern #2 signals and frozen long-leg policy. New test only changes the defined-risk same-expiry short leg and predeclared debit budget. Both legs use actual 1-minute option trade bars with conservative 0.5% half-spread haircuts; historical BBO/Greeks are not assumed.",
    }
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    b = candidates[best_full]
    lines = [
        "# MarketPulse — Pattern #2 Debit Spread Test",
        "",
        "**Research only. No orders. Pattern #2 signal and long-leg policy are frozen.**",
        "",
        f"Signals: **{len(signals)}** | Sessions: **{sessions}**",
        "",
        "## Best Full-Sample Structure (descriptive, not automatically promoted)",
        f"- Candidate: **{best_full}**",
        f"- Trades: **{b['full_900']['trades']}** | Win rate: **{b['full_900']['win_rate_pct']:.2f}%** | PF: **{b['full_900']['profit_factor']}**",
        f"- Net P/L: **${b['full_900']['net_pl_dollars']:,.2f}** | Ending balance: **${b['full_900']['ending_balance_dollars']:,.2f}** | Return: **{b['full_900']['return_pct']:.2f}%**",
        f"- Max DD: **${b['full_900']['max_drawdown_dollars']:,.2f} ({b['full_900']['max_drawdown_pct_starting']:.2f}%)**",
        f"- Average position debit: **${b['full_900']['avg_position_debit_dollars']:,.2f}** | Avg units: **{b['full_900']['avg_units']:.2f}**",
        f"- Average month: **${result['best_full_monthly']['average_monthly_pl_dollars']:,.2f}** | Median month: **${result['best_full_monthly']['median_monthly_pl_dollars']:,.2f}**",
        "",
        "## Robustness Gate",
        f"- Robust development candidate: **{selected or 'NONE'}**",
        f"- Promoted after external validation: **{promoted or 'NONE'}**",
    ]
    if selected:
        s = candidates[selected]
        lines += [
            f"- Development: **${s['development']['net_pl_dollars']:,.2f} | PF {s['development']['profit_factor']}**",
            f"- External: **${s['external_validation']['net_pl_dollars']:,.2f} | PF {s['external_validation']['profit_factor']}**",
        ]
    lines += [
        "",
        f"**$1,000/month target reached by best descriptive structure: {'YES' if result['best_full_monthly']['average_monthly_pl_dollars'] >= MONTHLY_TARGET else 'NO'}**",
        "",
        "A debit spread caps maximum loss at the debit paid, but can still lose 100% of that debit. Historical results do not guarantee future returns.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")
    print(json.dumps({"best": best_full, "best_summary": b["full_900"], "best_monthly": result["best_full_monthly"], "selected": selected, "promoted": promoted}, indent=2), flush=True)


if __name__ == "__main__":
    main()
