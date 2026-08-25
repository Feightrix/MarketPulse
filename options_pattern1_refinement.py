import json
from datetime import datetime, time, timezone
from itertools import product
from pathlib import Path

import options_pattern1_backtest as base

RESULT_JSON = "options_pattern1_refinement_results.json"
RESULT_MD = "options_pattern1_refinement_results.md"
STARTING_BALANCE = 2500.0
RISK_PCT = 0.01
RISK_DOLLARS = STARTING_BALANCE * RISK_PCT
TRAIN_FRACTION = 0.70

# Structured confirmation grid. This intentionally stays small and interpretable.
BODY_RATIO_MIN = [0.45, 0.60]
BREAKOUT_EXCURSION_PCT = [0.0004, 0.0007, 0.0010]
VWAP_SLOPE_PCT = [0.0, 0.00010]
RETEST_CLOSE_LOCATION = [0.50, 0.65]
MAX_RETEST_PENETRATION_PCT = [0.0004, 0.0008]
MIN_TRAIN_TRADES = 15


def bar_close_location(bar):
    span = bar["h"] - bar["l"]
    if span <= 0:
        return 0.5
    return (bar["c"] - bar["l"]) / span


def vwap_slope_pct(bars, i, lookback=3):
    if i < lookback:
        return 0.0
    anchor = bars[i - lookback]["vwap"]
    current = bars[i]["vwap"]
    price = bars[i]["c"]
    if price <= 0:
        return 0.0
    return (current - anchor) / price


def find_side_trade(day, bars, side, or_high, or_low, cfg):
    boundary = or_high if side == "CALL" else or_low
    start_index = next((i for i, b in enumerate(bars) if b["ts"].time() >= time(9, 45)), None)
    if start_index is None:
        return None

    for i in range(start_index, len(bars)):
        b = bars[i]
        if b["ts"].time() > base.LATEST_ENTRY:
            break
        if i == 0:
            continue
        prev = bars[i - 1]
        rng = b["h"] - b["l"]
        body_ratio = abs(b["c"] - b["o"]) / rng if rng > 0 else 0.0
        slope = vwap_slope_pct(bars, i)

        if side == "CALL":
            excursion = (b["c"] - boundary) / boundary
            broke = (
                prev["c"] <= boundary
                and b["c"] > boundary
                and b["c"] > b["vwap"]
                and b["c"] > b["o"]
                and body_ratio >= cfg["body_ratio_min"]
                and excursion >= cfg["breakout_excursion_pct"]
                and slope >= cfg["vwap_slope_pct"]
            )
        else:
            excursion = (boundary - b["c"]) / boundary
            broke = (
                prev["c"] >= boundary
                and b["c"] < boundary
                and b["c"] < b["vwap"]
                and b["c"] < b["o"]
                and body_ratio >= cfg["body_ratio_min"]
                and excursion >= cfg["breakout_excursion_pct"]
                and slope <= -cfg["vwap_slope_pct"]
            )
        if not broke:
            continue

        retest_end = min(i + base.RETEST_WINDOW_BARS, len(bars) - 1)
        for j in range(i + 1, retest_end + 1):
            rbar = bars[j]
            if rbar["ts"].time() > base.LATEST_ENTRY:
                break
            loc = bar_close_location(rbar)

            if side == "CALL":
                penetration = max(0.0, (boundary - rbar["l"]) / boundary)
                retest = (
                    rbar["l"] <= boundary * (1 + base.RETEST_TOLERANCE_PCT)
                    and penetration <= cfg["max_retest_penetration_pct"]
                    and rbar["c"] >= boundary
                    and rbar["c"] >= rbar["vwap"]
                    and loc >= cfg["retest_close_location"]
                )
            else:
                penetration = max(0.0, (rbar["h"] - boundary) / boundary)
                retest = (
                    rbar["h"] >= boundary * (1 - base.RETEST_TOLERANCE_PCT)
                    and penetration <= cfg["max_retest_penetration_pct"]
                    and rbar["c"] <= boundary
                    and rbar["c"] <= rbar["vwap"]
                    and loc <= (1.0 - cfg["retest_close_location"])
                )
            if not retest:
                continue

            trigger_end = min(j + base.TRIGGER_WINDOW_BARS, len(bars) - 1)
            for k in range(j + 1, trigger_end + 1):
                tbar = bars[k]
                if tbar["ts"].time() > base.LATEST_ENTRY:
                    break

                if side == "CALL":
                    trigger = rbar["h"] + base.TICK
                    if tbar["h"] < trigger:
                        continue
                    entry = max(trigger, tbar["o"])
                    stop = rbar["l"] - base.TICK
                    risk = entry - stop
                    if risk <= 0:
                        continue
                    target = entry + base.TARGET_R * risk
                else:
                    trigger = rbar["l"] - base.TICK
                    if tbar["l"] > trigger:
                        continue
                    entry = min(trigger, tbar["o"])
                    stop = rbar["h"] + base.TICK
                    risk = stop - entry
                    if risk <= 0:
                        continue
                    target = entry - base.TARGET_R * risk

                sim = base.simulate_trade(bars, k, side, entry, stop, target)
                if sim is None:
                    continue
                return {
                    "date": str(day),
                    "side": side,
                    "entry_ts": tbar["ts"].isoformat(),
                    "breakout_body_ratio": round(body_ratio, 4),
                    "breakout_excursion_pct": round(excursion, 6),
                    "vwap_slope_pct": round(slope, 6),
                    "retest_close_location": round(loc, 4),
                    "entry": round(entry, 4),
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    **sim,
                }
    return None


def dollarize(summary):
    net_pl = summary["net_r"] * RISK_DOLLARS
    max_dd = summary["max_drawdown_r"] * RISK_DOLLARS
    return {
        **summary,
        "risk_dollars_per_1r": round(RISK_DOLLARS, 2),
        "net_pl_dollars": round(net_pl, 2),
        "ending_balance_dollars": round(STARTING_BALANCE + net_pl, 2),
        "return_pct": round((net_pl / STARTING_BALANCE) * 100.0, 2),
        "max_drawdown_dollars": round(max_dd, 2),
    }


def evaluate_days(day_items, cfg):
    trades = []
    for day, bars in day_items:
        orb = base.opening_range(bars)
        if orb is None:
            continue
        or_high, or_low = orb
        for side in ("CALL", "PUT"):
            trade = find_side_trade(day, bars, side, or_high, or_low, cfg)
            if trade:
                trades.append(trade)
    trades.sort(key=lambda t: t["entry_ts"])
    return trades


def config_grid():
    for body, excursion, slope, retest_loc, penetration in product(
        BODY_RATIO_MIN,
        BREAKOUT_EXCURSION_PCT,
        VWAP_SLOPE_PCT,
        RETEST_CLOSE_LOCATION,
        MAX_RETEST_PENETRATION_PCT,
    ):
        yield {
            "body_ratio_min": body,
            "breakout_excursion_pct": excursion,
            "vwap_slope_pct": slope,
            "retest_close_location": retest_loc,
            "max_retest_penetration_pct": penetration,
        }


def selection_key(summary):
    # Money first: favor positive net R, then expectancy, then win rate, while penalizing drawdown.
    return (
        summary["net_r"] - 0.20 * summary["max_drawdown_r"],
        summary["expectancy_r"],
        summary["win_rate_pct"],
        summary["trades"],
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    train = result["train"]
    hold = result["holdout"]
    full = result["full_sample"]
    cfg = result["selected_config"]
    lines = [
        "# MarketPulse — Options Pattern 1 Refinement",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        "## Refinement",
        "- Same underlying pattern: SPY 5-minute opening-range breakout + retest",
        "- Added confirmation: breakout body strength and distance beyond the range",
        "- Added confirmation: VWAP slope agrees with trade direction",
        "- Added confirmation: retest holds the breakout boundary/VWAP and closes strongly",
        "- Bullish CALL and bearish PUT rules remain exact mirrors",
        f"- Reward:risk target: **{base.TARGET_R:.1f}R : 1R**",
        f"- Dollar reporting assumption: **1R = 1% of $2,500 = ${RISK_DOLLARS:.2f}**",
        "",
        "## Selected Confirmation Settings",
        f"- Minimum breakout body/range: **{cfg['body_ratio_min']:.0%}**",
        f"- Minimum breakout excursion: **{cfg['breakout_excursion_pct']:.2%}**",
        f"- Minimum 3-bar VWAP slope: **{cfg['vwap_slope_pct']:.3%}**",
        f"- Retest close-location strength: **{cfg['retest_close_location']:.0%}**",
        f"- Maximum retest penetration: **{cfg['max_retest_penetration_pct']:.2%}**",
        "",
        "## Training Sample (first 70%)",
        f"- Trades: **{train['trades']}**",
        f"- Win rate: **{train['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${train['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${train['ending_balance_dollars']:,.2f}**",
        f"- Return: **{train['return_pct']:.2f}%**",
        f"- Profit factor: **{train['profit_factor']}**",
        f"- Expectancy: **{train['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${train['max_drawdown_dollars']:,.2f} ({train['max_drawdown_r']:.2f}R)**",
        "",
        "## Holdout Sample (final 30%, untouched during selection)",
        f"- Trades: **{hold['trades']}**",
        f"- Win rate: **{hold['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${hold['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${hold['ending_balance_dollars']:,.2f}**",
        f"- Return: **{hold['return_pct']:.2f}%**",
        f"- Profit factor: **{hold['profit_factor']}**",
        f"- Expectancy: **{hold['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${hold['max_drawdown_dollars']:,.2f} ({hold['max_drawdown_r']:.2f}R)**",
        "",
        "## Full 180-Day Sample",
        f"- Trades: **{full['trades']}**",
        f"- Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${full['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%**",
        f"- Net R: **{full['net_r']:.3f}R**",
        f"- Profit factor: **{full['profit_factor']}**",
        f"- Expectancy: **{full['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f} ({full['max_drawdown_r']:.2f}R)**",
        "",
        "## Pass/Fail",
        f"- Holdout profitable: **{'YES' if hold['net_pl_dollars'] > 0 else 'NO'}**",
        f"- Holdout 60–80% win-rate target: **{'YES' if 60 <= hold['win_rate_pct'] <= 80 else 'NO'}**",
        "",
        "Dollar P/L here is risk-normalized underlying-pattern P/L, not yet actual option-contract premium P/L. Contract selection, bid/ask spread, slippage, delta, theta, and IV are the next layer after the underlying pattern proves profitable out of sample.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    raw = base.fetch_bars()
    by_day = base.regular_session_bars(raw)
    day_items = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        if base.opening_range(bars) is None:
            continue
        day_items.append((day, bars))

    split = max(1, int(len(day_items) * TRAIN_FRACTION))
    train_days = day_items[:split]
    holdout_days = day_items[split:]

    candidates = []
    for cfg in config_grid():
        trades = evaluate_days(train_days, cfg)
        summary = base.summarize(trades)
        if summary["trades"] >= MIN_TRAIN_TRADES:
            candidates.append((selection_key(summary), cfg, summary))

    if not candidates:
        raise RuntimeError("No refinement configuration produced enough training trades")

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, selected, train_summary_raw = candidates[0]

    holdout_trades = evaluate_days(holdout_days, selected)
    full_trades = evaluate_days(day_items, selected)

    result = {
        "strategy": "options_pattern1_refinement",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "symbol": base.SYMBOL,
        "lookback_days": base.LOOKBACK_DAYS,
        "complete_sessions": len(day_items),
        "train_sessions": len(train_days),
        "holdout_sessions": len(holdout_days),
        "starting_balance": STARTING_BALANCE,
        "risk_pct": RISK_PCT,
        "risk_dollars_per_1r": RISK_DOLLARS,
        "selected_config": selected,
        "train": dollarize(train_summary_raw),
        "holdout": dollarize(base.summarize(holdout_trades)),
        "full_sample": dollarize(base.summarize(full_trades)),
        "direction_split_full": {
            "calls": dollarize(base.summarize([t for t in full_trades if t["side"] == "CALL"])),
            "puts": dollarize(base.summarize([t for t in full_trades if t["side"] == "PUT"])),
        },
        "trade_count_grid_candidates": len(candidates),
    }
    write_results(result)


if __name__ == "__main__":
    main()
