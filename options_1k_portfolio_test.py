import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2
import options_pattern2_trend_refinement as p2t
import options_pattern2_contract_sim as cs
import options_pattern3_fast_research as p3
import options_pattern6_broad_reclaim_2m as p6

RESULT_JSON = "options_1k_portfolio_results.json"
RESULT_MD = "options_1k_portfolio_results.md"

# Goal and account constraints are fixed before the run.
STARTING_BALANCE = 2500.0
MONTHLY_TARGET = 1000.0
LOOKBACK_DAYS = 180
MAX_DAILY_ENTRIES = 5
MAX_CONCURRENT = 2
MAX_OPEN_PREMIUM_FRACTION = 0.40
MAX_TRADE_PREMIUM_FRACTION = 0.20
ABS_MAX_TRADE_PREMIUM = 500.0
MIN_PREMIUM_DOLLARS = 30.0
DAILY_LOSS_LOCK = 75.0
EST_ROUNDTRIP_FEES = 0.11

# Frozen contract mapping from the successful Pattern #2 A/B leader.
TARGET_DTE = 4
TARGET_SIDE_MONEYNESS = -0.005  # ~0.5% ITM in direction-aware terms.
TARGET_ABS_DELTA = 0.60
DTE_MIN = 1
DTE_MAX = 10
MAX_SHORTLIST = 8
FALLBACK_HALF_SPREAD_PCT = 0.005
FALLBACK_MIN_HALF_SPREAD = 0.01

# Pattern #2 core signal is unchanged. SPY is the validated core; other ETFs are a transfer test.
P2_SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
P2_VALIDATED_CORE = {"SPY"}
FROZEN_P2_CFG = {
    "max_vwap_slope_atr": 0.50,
    "max_efficiency": 0.65,
    "min_rsi_turn": 3.0,
}

# Pattern #6 is frozen to the strongest pre-existing version: edges_125.
P6_SYMBOLS = list(p6.p3.SYMBOLS)
FROZEN_P6_VARIANT = {
    "name": "edges_125",
    "target_r": 1.25,
    "timeout": 10,
    "session": "edges",
    "min_sep": 0.06,
    "min_slope": 0.02,
    "min_vol": 0.70,
}

ORDER_SUBMISSION_ENABLED = False
_contract_cache = {}


def aggregate_bars(raw, n):
    out = []
    for i in range(0, len(raw) - (n - 1), n):
        g = raw[i:i + n]
        if len(g) < n:
            continue
        out.append({
            "ts": g[0]["ts"],
            "o": g[0]["o"],
            "h": max(x["h"] for x in g),
            "l": min(x["l"] for x in g),
            "c": g[-1]["c"],
            "v": sum(x["v"] for x in g),
        })
    return out


def fetch_underlying_sessions():
    old_lookback = p3.LOOKBACK_DAYS
    p3.LOOKBACK_DAYS = LOOKBACK_DAYS
    sessions = {}
    try:
        for symbol in sorted(set(P6_SYMBOLS) | set(P2_SYMBOLS)):
            print("underlying", symbol, flush=True)
            sessions[symbol] = p3.parse_sessions(p3.fetch_bars(symbol))
    finally:
        p3.LOOKBACK_DAYS = old_lookback
    return sessions


def build_p2_signals(sessions):
    out = []
    for symbol in P2_SYMBOLS:
        day_items = []
        lookup = {}
        for day, raw in sorted(sessions.get(symbol, {}).items()):
            bars = aggregate_bars(raw, 5)
            if len(bars) < 50:
                continue
            base.add_session_vwap(bars)
            p2.add_atr_rsi(bars)
            day_items.append((day, bars))
            for b in bars:
                lookup[b["ts"].isoformat()] = b
        trades = p2t.evaluate(day_items, FROZEN_P2_CFG)
        for t in trades:
            bar = lookup.get(t["entry_ts"])
            if not bar:
                continue
            item = dict(t)
            item.update({
                "symbol": symbol,
                "strategy": "P2_CORE" if symbol in P2_VALIDATED_CORE else "P2_TRANSFER",
                "underlying_entry_spot": round(float(bar["c"]), 4),
                "entry_delay_minutes": 1,
                "exit_offset_minutes": 4,
            })
            out.append(item)
    return sorted(out, key=lambda x: x["entry_ts"])


def build_p6_signals(sessions):
    subset = {s: sessions.get(s, {}) for s in P6_SYMBOLS}
    raw = p6.generate_variant(dict(FROZEN_P6_VARIANT), subset)
    out = []
    for t in raw:
        item = dict(t)
        item.update({
            "strategy": "P6_RECLAIM",
            "underlying_entry_spot": float(t["entry"]),
            "entry_delay_minutes": 0,
            "exit_offset_minutes": 1,
        })
        out.append(item)
    return sorted(out, key=lambda x: x["entry_ts"])


def side_moneyness(side, strike, spot):
    return strike / spot - 1.0 if side == "CALL" else 1.0 - strike / spot


def fetch_contracts(underlying, signal_date, side, spot):
    key = (underlying, signal_date, side, round(spot, 1))
    if key in _contract_cache:
        return _contract_cache[key]
    day = datetime.fromisoformat(signal_date).date()
    option_type = "call" if side == "CALL" else "put"
    contracts = []
    for status in ("inactive", "active"):
        data = cs.request_json(
            f"{cs.PAPER_BASE}/v2/options/contracts",
            params={
                "underlying_symbols": underlying,
                "status": status,
                "expiration_date_gte": (day + timedelta(days=DTE_MIN)).isoformat(),
                "expiration_date_lte": (day + timedelta(days=DTE_MAX)).isoformat(),
                "type": option_type,
                "strike_price_gte": f"{max(0.5, spot * 0.88):.2f}",
                "strike_price_lte": f"{spot * 1.12:.2f}",
                "limit": 1000,
            },
        )
        if data:
            contracts.extend(data.get("option_contracts", []))
    unique = list({c.get("symbol"): c for c in contracts if c.get("symbol")}.values())
    _contract_cache[key] = unique
    return unique


def contract_metas(contracts, signal_date, side, spot):
    day = datetime.fromisoformat(signal_date).date()
    out = []
    for c in contracts:
        try:
            exp = datetime.fromisoformat(c["expiration_date"]).date()
            strike = float(c["strike_price"])
        except Exception:
            continue
        dte = (exp - day).days
        if not DTE_MIN <= dte <= DTE_MAX:
            continue
        out.append({
            "symbol": c["symbol"],
            "expiration_date": c["expiration_date"],
            "strike": strike,
            "dte": dte,
            "side_moneyness": side_moneyness(side, strike, spot),
            "open_interest": int(c.get("open_interest") or 0),
        })
    out.sort(key=lambda m: (
        abs(m["dte"] - TARGET_DTE),
        abs(m["side_moneyness"] - TARGET_SIDE_MONEYNESS),
        -m["open_interest"],
    ))
    return out[:MAX_SHORTLIST]


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


def first_bar_at_or_after(bars, target):
    for b in bars:
        if b.get("_ts") and b["_ts"] >= target:
            return b
    return None


def conservative_fill(bar, is_entry):
    px = float(bar.get("o") if is_entry else bar.get("c"))
    haircut = max(FALLBACK_MIN_HALF_SPREAD, px * FALLBACK_HALF_SPREAD_PCT)
    return px + haircut if is_entry else max(0.01, px - haircut)


def price_signal(sig):
    entry_ts = cs.parse_ts(sig["entry_ts"])
    exit_ts = cs.parse_ts(sig["exit_ts"])
    signal_date = entry_ts.date().isoformat()
    underlying = sig["symbol"]
    spot = float(sig["underlying_entry_spot"])
    contracts = fetch_contracts(underlying, signal_date, sig["side"], spot)
    metas = contract_metas(contracts, signal_date, sig["side"], spot)
    if not metas:
        return None, "no_contracts"

    entry_ref = entry_ts + timedelta(minutes=int(sig["entry_delay_minutes"]))
    exit_ref = exit_ts + timedelta(minutes=int(sig["exit_offset_minutes"]))
    if exit_ref <= entry_ref:
        exit_ref = entry_ref + timedelta(minutes=1)
    bars = fetch_option_bars_multi([m["symbol"] for m in metas], entry_ts, exit_ref + timedelta(minutes=2))

    candidates = []
    for m in metas:
        obars = bars.get(m["symbol"])
        if not obars:
            continue
        eb = first_bar_at_or_after(obars, entry_ref)
        xb = first_bar_at_or_after(obars, exit_ref)
        if eb is None or xb is None:
            continue
        entry_fill = conservative_fill(eb, True)
        exit_fill = conservative_fill(xb, False)
        premium = entry_fill * 100.0
        if premium < MIN_PREMIUM_DOLLARS or premium > ABS_MAX_TRADE_PREMIUM:
            continue
        iv, delta = cs.implied_vol_and_delta(spot, m["strike"], entry_fill, m["dte"], sig["side"])
        if delta is None:
            continue
        abs_delta = abs(delta)
        score = (
            0.30 * abs(m["dte"] - TARGET_DTE) / 3.0
            + 0.30 * abs(m["side_moneyness"] - TARGET_SIDE_MONEYNESS) / 0.005
            + 0.40 * abs(abs_delta - TARGET_ABS_DELTA) / 0.15
        )
        gross = (exit_fill - entry_fill) * 100.0
        net = gross - EST_ROUNDTRIP_FEES
        candidates.append((score, {
            "strategy": sig["strategy"],
            "underlying": underlying,
            "date": signal_date,
            "side": sig["side"],
            "entry_ts": sig["entry_ts"],
            "exit_ts": sig["exit_ts"],
            "contract_entry_ref": cs.utc_iso(entry_ref),
            "contract_exit_ref": cs.utc_iso(exit_ref),
            "contract": m["symbol"],
            "expiration_date": m["expiration_date"],
            "dte": m["dte"],
            "strike": m["strike"],
            "side_moneyness_pct": round(m["side_moneyness"] * 100.0, 3),
            "delta_proxy": round(delta, 4),
            "iv_proxy": iv,
            "entry_fill": round(entry_fill, 4),
            "exit_fill": round(exit_fill, 4),
            "premium_dollars": round(premium, 2),
            "net_pl_dollars": round(net, 2),
            "return_on_premium_pct": round(net / premium * 100.0, 2),
            "fill_mode": "trade_bar_conservative",
            "underlying_r": sig.get("r"),
            "underlying_exit_reason": sig.get("exit_reason"),
        }))
    if not candidates:
        return None, "no_affordable_priced_contract"
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1], None


def summarize_trades(trades, starting_balance=STARTING_BALANCE):
    ordered = sorted(trades, key=lambda t: t["entry_ts"])
    pls = [float(t["net_pl_dollars"]) for t in ordered]
    wins = [x for x in pls if x > 0]
    losses = [x for x in pls if x <= 0]
    equity = starting_balance
    peak = equity
    dd = 0.0
    for pl in pls:
        equity += pl
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    gp, gl = sum(wins), -sum(losses)
    return {
        "trades": len(ordered),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(ordered), 2) if ordered else 0.0,
        "net_pl_dollars": round(sum(pls), 2),
        "ending_balance_dollars": round(starting_balance + sum(pls), 2),
        "return_pct": round(100.0 * sum(pls) / starting_balance, 2) if starting_balance else 0.0,
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "avg_win_dollars": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_dollars": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "expectancy_dollars": round(sum(pls) / len(pls), 2) if pls else 0.0,
        "max_drawdown_dollars": round(dd, 2),
        "avg_premium_dollars": round(sum(t["premium_dollars"] for t in ordered) / len(ordered), 2) if ordered else 0.0,
    }


def account_sim(priced):
    # Event-based long-option debit accounting. Equity marks open positions at cost until exit,
    # which is intentionally conservative/simple; realized P/L changes equity only at exit.
    signals = sorted(priced, key=lambda t: (t["entry_ts"], 0 if t["strategy"] == "P2_CORE" else 1))
    cash = STARTING_BALANCE
    realized_equity = STARTING_BALANCE
    open_positions = []
    accepted = []
    skipped = Counter()
    daily_entries = defaultdict(int)
    daily_realized = defaultdict(float)

    def close_due(now):
        nonlocal cash, realized_equity, open_positions
        still = []
        for pos in open_positions:
            if cs.parse_ts(pos["exit_ts"]) <= now:
                pl = float(pos["net_pl_dollars"])
                cash += float(pos["premium_dollars"]) + pl
                realized_equity += pl
                daily_realized[pos["date"]] += pl
            else:
                still.append(pos)
        open_positions = still

    for trade in signals:
        et = cs.parse_ts(trade["entry_ts"])
        close_due(et)
        day = trade["date"]
        if daily_entries[day] >= MAX_DAILY_ENTRIES:
            skipped["daily_entry_cap"] += 1
            continue
        if daily_realized[day] <= -DAILY_LOSS_LOCK:
            skipped["daily_loss_lock"] += 1
            continue
        if len(open_positions) >= MAX_CONCURRENT:
            skipped["concurrency_cap"] += 1
            continue
        if any(p["underlying"] == trade["underlying"] for p in open_positions):
            skipped["same_underlying_open"] += 1
            continue

        locked = sum(float(p["premium_dollars"]) for p in open_positions)
        account_equity = cash + locked
        premium = float(trade["premium_dollars"])
        max_trade = min(ABS_MAX_TRADE_PREMIUM, account_equity * MAX_TRADE_PREMIUM_FRACTION)
        if premium > max_trade or premium > cash:
            skipped["premium_or_cash_cap"] += 1
            continue
        if locked + premium > account_equity * MAX_OPEN_PREMIUM_FRACTION:
            skipped["aggregate_premium_cap"] += 1
            continue

        cash -= premium
        open_positions.append(trade)
        accepted.append(trade)
        daily_entries[day] += 1

    close_due(datetime.max.replace(tzinfo=timezone.utc))
    summary = summarize_trades(accepted)
    summary["ending_cash_dollars"] = round(cash, 2)
    summary["skips"] = dict(skipped)
    summary["max_concurrent"] = MAX_CONCURRENT
    summary["max_daily_entries"] = MAX_DAILY_ENTRIES
    summary["daily_loss_lock_dollars"] = DAILY_LOSS_LOCK
    return accepted, summary


def monthly_stats(trades, all_dates):
    if not all_dates:
        return {}
    months = []
    cur = min(all_dates).replace(day=1)
    last = max(all_dates).replace(day=1)
    while cur <= last:
        months.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    by_month = defaultdict(float)
    count_month = defaultdict(int)
    for t in trades:
        key = t["date"][:7]
        by_month[key] += float(t["net_pl_dollars"])
        count_month[key] += 1
    vals = [by_month[m] for m in months]
    details = [{"month": m, "pl_dollars": round(by_month[m], 2), "trades": count_month[m]} for m in months]
    avg = statistics.mean(vals) if vals else 0.0
    median = statistics.median(vals) if vals else 0.0
    return {
        "months": len(months),
        "average_monthly_pl_dollars": round(avg, 2),
        "median_monthly_pl_dollars": round(median, 2),
        "best_month_dollars": round(max(vals), 2) if vals else 0.0,
        "worst_month_dollars": round(min(vals), 2) if vals else 0.0,
        "positive_months_pct": round(100.0 * sum(v > 0 for v in vals) / len(vals), 2) if vals else 0.0,
        "months_at_or_above_1000": sum(v >= MONTHLY_TARGET for v in vals),
        "target_attainment_pct_of_average": round(100.0 * avg / MONTHLY_TARGET, 2) if MONTHLY_TARGET else 0.0,
        "details": details,
    }


def main():
    sessions = fetch_underlying_sessions()
    coverage = Counter()
    for ds in sessions.values():
        for d in ds:
            coverage[d] += 1
    eval_dates = sorted(d for d, n in coverage.items() if n >= 14)
    if len(eval_dates) < 80:
        raise RuntimeError(f"Insufficient evaluation sessions: {len(eval_dates)}")
    eval_set = set(eval_dates)
    sessions = {s: {d: b for d, b in ds.items() if d in eval_set} for s, ds in sessions.items()}

    p2_signals = build_p2_signals(sessions)
    p6_signals = build_p6_signals(sessions)
    all_signals = sorted(p2_signals + p6_signals, key=lambda x: x["entry_ts"])
    print("signals", len(all_signals), "P2", len(p2_signals), "P6", len(p6_signals), flush=True)

    priced = []
    skip_reasons = Counter()
    for idx, sig in enumerate(all_signals, 1):
        if idx % 25 == 0:
            print("pricing", idx, "/", len(all_signals), flush=True)
        trade, reason = price_signal(sig)
        if trade:
            priced.append(trade)
        else:
            skip_reasons[reason or "unknown"] += 1

    accepted, portfolio = account_sim(priced)
    cutoff = eval_dates[int(len(eval_dates) * 0.75)]
    holdout_candidates = [t for t in priced if datetime.fromisoformat(t["date"]).date() >= cutoff]
    holdout_accepted, holdout = account_sim(holdout_candidates)

    component = {}
    for name in ("P2_CORE", "P2_TRANSFER", "P6_RECLAIM"):
        component[name] = summarize_trades([t for t in priced if t["strategy"] == name])

    monthly = monthly_stats(accepted, eval_dates)
    holdout_dates = [d for d in eval_dates if d >= cutoff]
    holdout_monthly = monthly_stats(holdout_accepted, holdout_dates)
    trading_days = len(eval_dates)
    avg_daily = portfolio["net_pl_dollars"] / trading_days if trading_days else 0.0
    required_daily = MONTHLY_TARGET / 20.0
    linear_capital = None
    if monthly.get("average_monthly_pl_dollars", 0) > 0:
        linear_capital = round(STARTING_BALANCE * MONTHLY_TARGET / monthly["average_monthly_pl_dollars"], 2)

    result = {
        "strategy": "MarketPulse_1K_monthly_portfolio_test",
        "order_submission_enabled": ORDER_SUBMISSION_ENABLED,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "starting_balance_dollars": STARTING_BALANCE,
        "monthly_profit_target_dollars": MONTHLY_TARGET,
        "required_monthly_return_pct_on_starting_balance": round(MONTHLY_TARGET / STARTING_BALANCE * 100.0, 2),
        "approx_required_daily_pl_dollars": required_daily,
        "lookback_days": LOOKBACK_DAYS,
        "evaluation_sessions": trading_days,
        "holdout_start": cutoff.isoformat(),
        "frozen_components": {
            "P2": {"symbols": P2_SYMBOLS, "validated_core": sorted(P2_VALIDATED_CORE), "trend_cfg": FROZEN_P2_CFG},
            "P6": {"symbols": P6_SYMBOLS, "variant": FROZEN_P6_VARIANT},
            "contract_policy": {"target_dte": TARGET_DTE, "side_moneyness": TARGET_SIDE_MONEYNESS, "target_abs_delta": TARGET_ABS_DELTA},
        },
        "account_constraints": {
            "max_daily_entries": MAX_DAILY_ENTRIES,
            "max_concurrent": MAX_CONCURRENT,
            "daily_loss_lock_dollars": DAILY_LOSS_LOCK,
            "max_trade_premium_fraction": MAX_TRADE_PREMIUM_FRACTION,
            "max_total_open_premium_fraction": MAX_OPEN_PREMIUM_FRACTION,
            "absolute_max_trade_premium_dollars": ABS_MAX_TRADE_PREMIUM,
        },
        "signals": {"P2": len(p2_signals), "P6": len(p6_signals), "total": len(all_signals), "priced": len(priced), "pricing_skips": dict(skip_reasons)},
        "component_actual_option_results_before_account_caps": component,
        "portfolio_full": portfolio,
        "portfolio_holdout": holdout,
        "monthly_full": monthly,
        "monthly_holdout": holdout_monthly,
        "average_daily_pl_dollars": round(avg_daily, 2),
        "average_daily_target_attainment_pct": round(100.0 * avg_daily / required_daily, 2) if required_daily else 0.0,
        "illustrative_linear_capital_for_1000_monthly": linear_capital,
        "target_met": bool(monthly.get("average_monthly_pl_dollars", 0) >= MONTHLY_TARGET and holdout_monthly.get("average_monthly_pl_dollars", 0) > 0),
        "method_note": "Research only. Signals and contract policy are frozen from prior work. Historical option fills use actual 1-minute option trade bars with a conservative half-spread haircut; historical BBO is unavailable. Linear capital estimate is illustrative, not a forecast or recommendation.",
    }

    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = [
        "# MarketPulse — $1K/Month Portfolio Test",
        "",
        "**Research only. No order submission.**",
        "",
        f"Starting account: **${STARTING_BALANCE:,.2f}** | Monthly target: **${MONTHLY_TARGET:,.2f}** ({MONTHLY_TARGET / STARTING_BALANCE * 100:.1f}% of starting equity)",
        f"Evaluation sessions: **{trading_days}** | Holdout starts: **{cutoff.isoformat()}**",
        "",
        "## Frozen Portfolio",
        "- Pattern #2: unchanged VWAP stretch/trend-protected signal; SPY is core, QQQ/IWM/GLD/TLT are unchanged-parameter transfer tests.",
        "- Pattern #6: unchanged 2-minute edges_125 reclaim across its original 20-name universe.",
        "- Contract mapping: ~4 DTE, ~0.5% ITM, nearest ~0.60 absolute delta proxy.",
        "- Account: max 2 concurrent positions, max 5 entries/day, $75 realized daily-loss lock, max 20% premium/trade and 40% total open premium.",
        "",
        "## Full Account Result",
        f"- Trades: **{portfolio['trades']}** | Win rate: **{portfolio['win_rate_pct']:.2f}%** | PF: **{portfolio['profit_factor']}**",
        f"- Net P/L: **${portfolio['net_pl_dollars']:,.2f}** | Ending balance: **${portfolio['ending_balance_dollars']:,.2f}** | Return: **{portfolio['return_pct']:.2f}%**",
        f"- Max drawdown: **${portfolio['max_drawdown_dollars']:,.2f}** | Expectancy: **${portfolio['expectancy_dollars']:,.2f}/trade**",
        f"- Average daily P/L: **${avg_daily:,.2f}** vs roughly **${required_daily:,.2f}/day** needed for the target.",
        "",
        "## Monthly Production",
        f"- Average month: **${monthly.get('average_monthly_pl_dollars', 0):,.2f}**",
        f"- Median month: **${monthly.get('median_monthly_pl_dollars', 0):,.2f}**",
        f"- Best / worst: **${monthly.get('best_month_dollars', 0):,.2f} / ${monthly.get('worst_month_dollars', 0):,.2f}**",
        f"- Positive months: **{monthly.get('positive_months_pct', 0):.2f}%** | Months >= $1,000: **{monthly.get('months_at_or_above_1000', 0)}**",
        "",
        "## Holdout",
        f"- Trades: **{holdout['trades']}** | Net P/L: **${holdout['net_pl_dollars']:,.2f}** | PF: **{holdout['profit_factor']}** | Max DD: **${holdout['max_drawdown_dollars']:,.2f}**",
        f"- Holdout average month: **${holdout_monthly.get('average_monthly_pl_dollars', 0):,.2f}**",
        "",
        "## Component Actual-Option Results Before Portfolio Caps",
    ]
    for name, s in component.items():
        lines.append(f"- {name}: **{s['trades']} trades | ${s['net_pl_dollars']:,.2f} | PF {s['profit_factor']} | WR {s['win_rate_pct']:.2f}%**")
    lines += [
        "",
        f"**$1,000/month target met: {'YES' if result['target_met'] else 'NO'}**",
        "",
        "The monthly target is an aspiration, not a guarantee. This test does not increase risk or tune rules to force the target.",
    ]
    if linear_capital:
        lines.insert(-3, f"Illustrative linear capital at the observed average monthly P/L: **${linear_capital:,.2f}** (not a forecast; assumes impossible-to-guarantee linear scaling).")
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")
    print(json.dumps({"portfolio": portfolio, "monthly": monthly, "holdout": holdout, "target_met": result["target_met"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
