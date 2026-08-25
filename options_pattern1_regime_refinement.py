import json
import statistics
from datetime import datetime, time, timezone
from itertools import product
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern1_refinement as ref

RESULT_JSON = "options_pattern1_regime_results.json"
RESULT_MD = "options_pattern1_regime_results.md"
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 25.0
TRAIN_FRACTION = 0.70
MIN_TRAIN_TRADES = 18
MIN_FOLD_TRADES = 7

# Keep the pattern itself fixed. This stage only asks: under what market
# conditions does the already-refined pattern work best?
PATTERN_CFG = {
    "body_ratio_min": 0.45,
    "breakout_excursion_pct": 0.0004,
    "vwap_slope_pct": 0.0001,
    "retest_close_location": 0.65,
    "max_retest_penetration_pct": 0.0004,
}

# Small, interpretable regime grid.
LATEST_ENTRY_CANDIDATES = [time(11, 0), time(12, 0), time(13, 0), time(14, 30)]
OR_WIDTH_MIN_PCT = [0.0, 0.0020, 0.0030]
OR_WIDTH_MAX_PCT = [0.0060, 0.0100, 0.0200]
EFFICIENCY_MIN = [0.0, 0.25, 0.40]
MAX_VWAP_CROSSES = [1, 2, 4]
BREAKOUT_VOLUME_RATIO_MIN = [0.0, 0.90, 1.10]


def dollarize(summary):
    net_pl = summary["net_r"] * RISK_DOLLARS
    max_dd = summary["max_drawdown_r"] * RISK_DOLLARS
    return {
        **summary,
        "risk_dollars_per_1r": RISK_DOLLARS,
        "net_pl_dollars": round(net_pl, 2),
        "ending_balance_dollars": round(STARTING_BALANCE + net_pl, 2),
        "return_pct": round((net_pl / STARTING_BALANCE) * 100.0, 2),
        "max_drawdown_dollars": round(max_dd, 2),
    }


def opening_range_width_pct(or_high, or_low):
    mid = (or_high + or_low) / 2.0
    return (or_high - or_low) / mid if mid > 0 else 0.0


def directional_efficiency(bars, i, side, lookback=6):
    start = max(0, i - lookback)
    closes = [b["c"] for b in bars[start : i + 1]]
    if len(closes) < 2:
        return 0.0
    path = sum(abs(closes[j] - closes[j - 1]) for j in range(1, len(closes)))
    if path <= 0:
        return 0.0
    signed_net = closes[-1] - closes[0]
    if side == "PUT":
        signed_net = -signed_net
    return signed_net / path


def count_vwap_crosses(bars, i, lookback=8):
    start = max(0, i - lookback)
    signs = []
    for b in bars[start : i + 1]:
        diff = b["c"] - b["vwap"]
        signs.append(1 if diff > 0 else (-1 if diff < 0 else 0))
    nonzero = [s for s in signs if s != 0]
    return sum(1 for a, b in zip(nonzero, nonzero[1:]) if a != b)


def breakout_volume_ratio(bars, i, lookback=6):
    start = max(0, i - lookback)
    prior = [b["v"] for b in bars[start:i] if b["v"] > 0]
    if not prior:
        return 1.0
    med = statistics.median(prior)
    return bars[i]["v"] / med if med > 0 else 1.0


def find_side_trade(day, bars, side, or_high, or_low, regime):
    or_width = opening_range_width_pct(or_high, or_low)
    if or_width < regime["or_width_min_pct"] or or_width > regime["or_width_max_pct"]:
        return None

    boundary = or_high if side == "CALL" else or_low
    start_index = next((i for i, b in enumerate(bars) if b["ts"].time() >= time(9, 45)), None)
    if start_index is None:
        return None

    for i in range(start_index, len(bars)):
        b = bars[i]
        if b["ts"].time() > regime["latest_entry"]:
            break
        if i == 0:
            continue
        prev = bars[i - 1]
        rng = b["h"] - b["l"]
        body_ratio = abs(b["c"] - b["o"]) / rng if rng > 0 else 0.0
        slope = ref.vwap_slope_pct(bars, i)

        if side == "CALL":
            excursion = (b["c"] - boundary) / boundary
            broke = (
                prev["c"] <= boundary
                and b["c"] > boundary
                and b["c"] > b["vwap"]
                and b["c"] > b["o"]
                and body_ratio >= PATTERN_CFG["body_ratio_min"]
                and excursion >= PATTERN_CFG["breakout_excursion_pct"]
                and slope >= PATTERN_CFG["vwap_slope_pct"]
            )
        else:
            excursion = (boundary - b["c"]) / boundary
            broke = (
                prev["c"] >= boundary
                and b["c"] < boundary
                and b["c"] < b["vwap"]
                and b["c"] < b["o"]
                and body_ratio >= PATTERN_CFG["body_ratio_min"]
                and excursion >= PATTERN_CFG["breakout_excursion_pct"]
                and slope <= -PATTERN_CFG["vwap_slope_pct"]
            )
        if not broke:
            continue

        efficiency = directional_efficiency(bars, i, side)
        vwap_crosses = count_vwap_crosses(bars, i)
        volume_ratio = breakout_volume_ratio(bars, i)
        if efficiency < regime["efficiency_min"]:
            continue
        if vwap_crosses > regime["max_vwap_crosses"]:
            continue
        if volume_ratio < regime["breakout_volume_ratio_min"]:
            continue

        retest_end = min(i + base.RETEST_WINDOW_BARS, len(bars) - 1)
        for j in range(i + 1, retest_end + 1):
            rbar = bars[j]
            if rbar["ts"].time() > regime["latest_entry"]:
                break
            loc = ref.bar_close_location(rbar)

            if side == "CALL":
                penetration = max(0.0, (boundary - rbar["l"]) / boundary)
                retest = (
                    rbar["l"] <= boundary * (1 + base.RETEST_TOLERANCE_PCT)
                    and penetration <= PATTERN_CFG["max_retest_penetration_pct"]
                    and rbar["c"] >= boundary
                    and rbar["c"] >= rbar["vwap"]
                    and loc >= PATTERN_CFG["retest_close_location"]
                )
            else:
                penetration = max(0.0, (rbar["h"] - boundary) / boundary)
                retest = (
                    rbar["h"] >= boundary * (1 - base.RETEST_TOLERANCE_PCT)
                    and penetration <= PATTERN_CFG["max_retest_penetration_pct"]
                    and rbar["c"] <= boundary
                    and rbar["c"] <= rbar["vwap"]
                    and loc <= (1.0 - PATTERN_CFG["retest_close_location"])
                )
            if not retest:
                continue

            trigger_end = min(j + base.TRIGGER_WINDOW_BARS, len(bars) - 1)
            for k in range(j + 1, trigger_end + 1):
                tbar = bars[k]
                if tbar["ts"].time() > regime["latest_entry"]:
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
                    "entry_hour": tbar["ts"].hour,
                    "or_width_pct": round(or_width, 6),
                    "efficiency": round(efficiency, 4),
                    "vwap_crosses": vwap_crosses,
                    "breakout_volume_ratio": round(volume_ratio, 3),
                    "entry": round(entry, 4),
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    **sim,
                }
    return None


def evaluate_days(day_items, regime):
    trades = []
    for day, bars in day_items:
        orb = base.opening_range(bars)
        if orb is None:
            continue
        or_high, or_low = orb
        for side in ("CALL", "PUT"):
            trade = find_side_trade(day, bars, side, or_high, or_low, regime)
            if trade:
                trades.append(trade)
    trades.sort(key=lambda t: t["entry_ts"])
    return trades


def regime_grid():
    for latest, or_min, or_max, eff, crosses, vol in product(
        LATEST_ENTRY_CANDIDATES,
        OR_WIDTH_MIN_PCT,
        OR_WIDTH_MAX_PCT,
        EFFICIENCY_MIN,
        MAX_VWAP_CROSSES,
        BREAKOUT_VOLUME_RATIO_MIN,
    ):
        if or_min >= or_max:
            continue
        yield {
            "latest_entry": latest,
            "or_width_min_pct": or_min,
            "or_width_max_pct": or_max,
            "efficiency_min": eff,
            "max_vwap_crosses": crosses,
            "breakout_volume_ratio_min": vol,
        }


def serialize_regime(regime):
    return {
        **regime,
        "latest_entry": regime["latest_entry"].strftime("%H:%M"),
    }


def summarize_by_bucket(trades, key_fn):
    buckets = {}
    for trade in trades:
        key = str(key_fn(trade))
        buckets.setdefault(key, []).append(trade)
    return {key: dollarize(base.summarize(items)) for key, items in sorted(buckets.items())}


def candidate_key(fold_a, fold_b, train):
    # Robustness first: favor configurations that are profitable in both
    # development folds. Then maximize weakest-fold expectancy and full-train P/L.
    both_positive = int(fold_a["net_r"] > 0 and fold_b["net_r"] > 0)
    weakest_expectancy = min(fold_a["expectancy_r"], fold_b["expectancy_r"])
    weakest_net = min(fold_a["net_r"], fold_b["net_r"])
    drawdown_penalty = 0.20 * train["max_drawdown_r"]
    return (
        both_positive,
        weakest_expectancy,
        weakest_net,
        train["net_r"] - drawdown_penalty,
        train["win_rate_pct"],
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    train = result["train"]
    hold = result["holdout"]
    full = result["full_sample"]
    cfg = result["selected_regime"]
    lines = [
        "# MarketPulse — Options Pattern 1 Regime Refinement",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        "## What Changed",
        "- Pattern rules stayed fixed; only market-regime filters were tested.",
        "- Filters tested: entry cutoff, opening-range width, directional efficiency, VWAP chop, breakout relative volume.",
        "- First 70% remained development data and was split into two folds.",
        "- Final 30% remained untouched until after regime selection.",
        "- CALL and PUT rules remain mirrored.",
        "",
        "## Selected Regime",
        f"- Latest entry: **{cfg['latest_entry']} ET**",
        f"- Opening-range width: **{cfg['or_width_min_pct']:.2%} to {cfg['or_width_max_pct']:.2%}**",
        f"- Minimum directional efficiency: **{cfg['efficiency_min']:.2f}**",
        f"- Maximum recent VWAP crosses: **{cfg['max_vwap_crosses']}**",
        f"- Minimum breakout relative volume: **{cfg['breakout_volume_ratio_min']:.2f}x**",
        "",
        "## Training 70%",
        f"- Trades: **{train['trades']}** | Win rate: **{train['win_rate_pct']:.2f}%**",
        f"- P/L: **${train['net_pl_dollars']:,.2f}** | Return: **{train['return_pct']:.2f}%**",
        f"- Profit factor: **{train['profit_factor']}** | Expectancy: **{train['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${train['max_drawdown_dollars']:,.2f}**",
        "",
        "## Untouched Holdout 30%",
        f"- Trades: **{hold['trades']}** | Win rate: **{hold['win_rate_pct']:.2f}%**",
        f"- P/L: **${hold['net_pl_dollars']:,.2f}** | Return: **{hold['return_pct']:.2f}%**",
        f"- Profit factor: **{hold['profit_factor']}** | Expectancy: **{hold['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${hold['max_drawdown_dollars']:,.2f}**",
        "",
        "## Full 180-Day Sample",
        f"- Trades: **{full['trades']}** | Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- P/L: **${full['net_pl_dollars']:,.2f}** | Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%** | Profit factor: **{full['profit_factor']}**",
        f"- Expectancy: **{full['expectancy_r']:.4f}R/trade** | Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        "",
        "## Pass/Fail",
        f"- Holdout profitable: **{'YES' if hold['net_pl_dollars'] > 0 else 'NO'}**",
        f"- Holdout 60–80% win-rate target: **{'YES' if 60 <= hold['win_rate_pct'] <= 80 else 'NO'}**",
        "",
        "Dollar P/L is still risk-normalized underlying-pattern P/L at $25 per 1R. Actual option premium P/L comes after the underlying edge survives validation.",
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
    fold_split = max(1, len(train_days) // 2)
    fold_a_days = train_days[:fold_split]
    fold_b_days = train_days[fold_split:]

    candidates = []
    tested = 0
    for regime in regime_grid():
        tested += 1
        train_trades = evaluate_days(train_days, regime)
        if len(train_trades) < MIN_TRAIN_TRADES:
            continue
        fold_a_trades = [t for t in train_trades if t["date"] <= str(fold_a_days[-1][0])]
        fold_b_trades = [t for t in train_trades if t["date"] > str(fold_a_days[-1][0])]
        if len(fold_a_trades) < MIN_FOLD_TRADES or len(fold_b_trades) < MIN_FOLD_TRADES:
            continue
        train_summary = base.summarize(train_trades)
        fold_a_summary = base.summarize(fold_a_trades)
        fold_b_summary = base.summarize(fold_b_trades)
        candidates.append((candidate_key(fold_a_summary, fold_b_summary, train_summary), regime, train_summary, fold_a_summary, fold_b_summary))

    if not candidates:
        raise RuntimeError("No regime configuration produced enough development trades")

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, selected, train_raw, fold_a_raw, fold_b_raw = candidates[0]
    holdout_trades = evaluate_days(holdout_days, selected)
    full_trades = evaluate_days(day_items, selected)

    # Baseline refined pattern under no additional regime restriction, for comparison.
    no_regime = {
        "latest_entry": time(14, 30),
        "or_width_min_pct": 0.0,
        "or_width_max_pct": 1.0,
        "efficiency_min": -1.0,
        "max_vwap_crosses": 99,
        "breakout_volume_ratio_min": 0.0,
    }
    prior_holdout = evaluate_days(holdout_days, no_regime)
    prior_full = evaluate_days(day_items, no_regime)

    result = {
        "strategy": "options_pattern1_regime_refinement",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "symbol": base.SYMBOL,
        "lookback_days": base.LOOKBACK_DAYS,
        "complete_sessions": len(day_items),
        "train_sessions": len(train_days),
        "holdout_sessions": len(holdout_days),
        "starting_balance": STARTING_BALANCE,
        "risk_dollars_per_1r": RISK_DOLLARS,
        "pattern_config_fixed": PATTERN_CFG,
        "selected_regime": serialize_regime(selected),
        "grid_tested": tested,
        "qualified_candidates": len(candidates),
        "fold_a": dollarize(fold_a_raw),
        "fold_b": dollarize(fold_b_raw),
        "train": dollarize(train_raw),
        "holdout": dollarize(base.summarize(holdout_trades)),
        "full_sample": dollarize(base.summarize(full_trades)),
        "prior_refined_holdout_recomputed": dollarize(base.summarize(prior_holdout)),
        "prior_refined_full_recomputed": dollarize(base.summarize(prior_full)),
        "direction_split_full": {
            "calls": dollarize(base.summarize([t for t in full_trades if t["side"] == "CALL"])),
            "puts": dollarize(base.summarize([t for t in full_trades if t["side"] == "PUT"])),
        },
        "entry_hour_full": summarize_by_bucket(full_trades, lambda t: t["entry_hour"]),
        "trades": full_trades,
    }
    write_results(result)


if __name__ == "__main__":
    main()
