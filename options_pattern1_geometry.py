import json
from datetime import datetime, time, timezone
from itertools import product
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern1_refinement as refined

RESULT_JSON = "options_pattern1_geometry_results.json"
RESULT_MD = "options_pattern1_geometry_results.md"
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 25.0
TRAIN_FRACTION = 0.70
TARGET_R = 1.5

# Keep the winning Pattern #1 signal definition fixed. Only entry/stop geometry changes.
PATTERN_CFG = {
    "body_ratio_min": 0.45,
    "breakout_excursion_pct": 0.0004,
    "vwap_slope_pct": 0.0001,
    "retest_close_location": 0.65,
    "max_retest_penetration_pct": 0.0004,
}

ENTRY_MODES = ["touch", "close_confirm"]
TRIGGER_RANGE_BUFFER = [0.0, 0.10, 0.25]
STOP_RANGE_PAD = [0.0, 0.25, 0.50]
STOP_BASES = ["retest", "boundary_extreme"]
MIN_FOLD_TRADES = 5


def close_location(bar):
    span = bar["h"] - bar["l"]
    return 0.5 if span <= 0 else (bar["c"] - bar["l"]) / span


def find_candidates(day, bars, side, or_high, or_low):
    boundary = or_high if side == "CALL" else or_low
    start = next((i for i, b in enumerate(bars) if b["ts"].time() >= time(9, 45)), None)
    if start is None:
        return []

    out = []
    for i in range(start, len(bars)):
        b = bars[i]
        if b["ts"].time() > base.LATEST_ENTRY or i == 0:
            break
        prev = bars[i - 1]
        rng = b["h"] - b["l"]
        body_ratio = abs(b["c"] - b["o"]) / rng if rng > 0 else 0.0
        slope = refined.vwap_slope_pct(bars, i)

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

        retest_end = min(i + base.RETEST_WINDOW_BARS, len(bars) - 1)
        for j in range(i + 1, retest_end + 1):
            r = bars[j]
            if r["ts"].time() > base.LATEST_ENTRY:
                break
            loc = close_location(r)
            if side == "CALL":
                penetration = max(0.0, (boundary - r["l"]) / boundary)
                valid = (
                    r["l"] <= boundary * (1 + base.RETEST_TOLERANCE_PCT)
                    and penetration <= PATTERN_CFG["max_retest_penetration_pct"]
                    and r["c"] >= boundary
                    and r["c"] >= r["vwap"]
                    and loc >= PATTERN_CFG["retest_close_location"]
                )
            else:
                penetration = max(0.0, (r["h"] - boundary) / boundary)
                valid = (
                    r["h"] >= boundary * (1 - base.RETEST_TOLERANCE_PCT)
                    and penetration <= PATTERN_CFG["max_retest_penetration_pct"]
                    and r["c"] <= boundary
                    and r["c"] <= r["vwap"]
                    and loc <= 1.0 - PATTERN_CFG["retest_close_location"]
                )
            if valid:
                out.append((i, j, boundary))
    return out


def simulate_geometry(day, bars, side, cfg, or_high, or_low):
    for breakout_i, retest_i, boundary in find_candidates(day, bars, side, or_high, or_low):
        r = bars[retest_i]
        retest_range = max(base.TICK, r["h"] - r["l"])
        trigger_extra = cfg["trigger_range_buffer"] * retest_range
        stop_pad = cfg["stop_range_pad"] * retest_range

        if side == "CALL":
            trigger = r["h"] + base.TICK + trigger_extra
            stop_anchor = r["l"] if cfg["stop_base"] == "retest" else min(r["l"], boundary)
            stop = stop_anchor - base.TICK - stop_pad
        else:
            trigger = r["l"] - base.TICK - trigger_extra
            stop_anchor = r["h"] if cfg["stop_base"] == "retest" else max(r["h"], boundary)
            stop = stop_anchor + base.TICK + stop_pad

        trigger_end = min(retest_i + base.TRIGGER_WINDOW_BARS, len(bars) - 1)
        for k in range(retest_i + 1, trigger_end + 1):
            t = bars[k]
            if t["ts"].time() > base.LATEST_ENTRY:
                break

            if cfg["entry_mode"] == "touch":
                if side == "CALL" and t["h"] < trigger:
                    continue
                if side == "PUT" and t["l"] > trigger:
                    continue
                entry_i = k
                entry = max(trigger, t["o"]) if side == "CALL" else min(trigger, t["o"])
            else:
                confirmed = t["c"] >= trigger if side == "CALL" else t["c"] <= trigger
                if not confirmed or k + 1 >= len(bars):
                    continue
                nxt = bars[k + 1]
                if nxt["ts"].time() > base.LATEST_ENTRY:
                    continue
                entry_i = k + 1
                entry = nxt["o"]

            risk = entry - stop if side == "CALL" else stop - entry
            if risk <= 0:
                continue
            target = entry + TARGET_R * risk if side == "CALL" else entry - TARGET_R * risk
            sim = base.simulate_trade(bars, entry_i, side, entry, stop, target)
            if sim is None:
                continue
            return {
                "date": str(day),
                "side": side,
                "entry_ts": bars[entry_i]["ts"].isoformat(),
                "entry": round(entry, 4),
                "stop": round(stop, 4),
                "target": round(target, 4),
                "entry_mode": cfg["entry_mode"],
                "trigger_range_buffer": cfg["trigger_range_buffer"],
                "stop_range_pad": cfg["stop_range_pad"],
                "stop_base": cfg["stop_base"],
                **sim,
            }
    return None


def evaluate(day_items, cfg):
    trades = []
    for day, bars in day_items:
        orb = base.opening_range(bars)
        if orb is None:
            continue
        hi, lo = orb
        for side in ("CALL", "PUT"):
            t = simulate_geometry(day, bars, side, cfg, hi, lo)
            if t:
                trades.append(t)
    trades.sort(key=lambda x: x["entry_ts"])
    return trades


def dollarize(summary):
    pl = summary["net_r"] * RISK_DOLLARS
    dd = summary["max_drawdown_r"] * RISK_DOLLARS
    return {
        **summary,
        "net_pl_dollars": round(pl, 2),
        "ending_balance_dollars": round(STARTING_BALANCE + pl, 2),
        "return_pct": round(pl / STARTING_BALANCE * 100.0, 2),
        "max_drawdown_dollars": round(dd, 2),
        "risk_dollars_per_1r": RISK_DOLLARS,
    }


def grid():
    for entry_mode, trigger_buffer, stop_pad, stop_base in product(
        ENTRY_MODES, TRIGGER_RANGE_BUFFER, STOP_RANGE_PAD, STOP_BASES
    ):
        yield {
            "entry_mode": entry_mode,
            "trigger_range_buffer": trigger_buffer,
            "stop_range_pad": stop_pad,
            "stop_base": stop_base,
        }


def robust_score(a, b):
    # Require both development folds to make money. Rank by the weaker fold first.
    return (
        min(a["expectancy_r"], b["expectancy_r"]),
        min(a["net_r"], b["net_r"]),
        (a["expectancy_r"] + b["expectancy_r"]) / 2.0,
        (a["win_rate_pct"] + b["win_rate_pct"]) / 2.0,
        -(a["max_drawdown_r"] + b["max_drawdown_r"]),
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    cfg = result["selected_geometry"]
    hold = result["holdout"]
    full = result["full_sample"]
    prior = result["prior_leader"]
    lines = [
        "# MarketPulse — Options Pattern 1 Entry/Stop Geometry",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        "## Fixed Pattern",
        "- SPY 5-minute Opening Range + VWAP retest",
        "- Prior winning signal filters unchanged",
        "- CALL/PUT rules remain mirrored",
        "- Winner target remains 1.5R",
        "",
        "## Selected Geometry",
        f"- Entry mode: **{cfg['entry_mode']}**",
        f"- Trigger buffer: **{cfg['trigger_range_buffer']:.0%} of retest-bar range**",
        f"- Stop padding: **{cfg['stop_range_pad']:.0%} of retest-bar range**",
        f"- Stop base: **{cfg['stop_base']}**",
        "",
        "## Untouched Holdout",
        f"- Trades: **{hold['trades']}**",
        f"- Win rate: **{hold['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${hold['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${hold['ending_balance_dollars']:,.2f}**",
        f"- Profit factor: **{hold['profit_factor']}**",
        f"- Expectancy: **{hold['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${hold['max_drawdown_dollars']:,.2f}**",
        "",
        "## Full 180-Day Sample",
        f"- Trades: **{full['trades']}**",
        f"- Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${full['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%**",
        f"- Profit factor: **{full['profit_factor']}**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        "",
        "## Prior Leader Comparison",
        f"- Prior leader full P/L: **${prior['full_pl_dollars']:,.2f}**",
        f"- Prior leader holdout P/L: **${prior['holdout_pl_dollars']:,.2f}**",
        "",
        "Dollar P/L is still risk-normalized underlying-pattern P/L at $25 per 1R, not actual option-premium P/L.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    raw = base.fetch_bars()
    by_day = base.regular_session_bars(raw)
    days = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        if base.opening_range(bars) is not None:
            days.append((day, bars))

    split = int(len(days) * TRAIN_FRACTION)
    dev = days[:split]
    holdout_days = days[split:]
    fold_split = len(dev) // 2
    fold_a = dev[:fold_split]
    fold_b = dev[fold_split:]

    qualified = []
    for cfg in grid():
        ta = evaluate(fold_a, cfg)
        tb = evaluate(fold_b, cfg)
        sa = base.summarize(ta)
        sb = base.summarize(tb)
        if sa["trades"] < MIN_FOLD_TRADES or sb["trades"] < MIN_FOLD_TRADES:
            continue
        if sa["net_r"] <= 0 or sb["net_r"] <= 0:
            continue
        qualified.append((robust_score(sa, sb), cfg, sa, sb))

    if not qualified:
        raise RuntimeError("No entry/stop geometry was profitable in both development folds")

    qualified.sort(key=lambda x: x[0], reverse=True)
    _, selected, sa, sb = qualified[0]
    hold_trades = evaluate(holdout_days, selected)
    full_trades = evaluate(days, selected)

    # Existing accepted leader from the prior refinement.
    prior = json.loads(Path("options_pattern1_refinement_results.json").read_text())
    result = {
        "strategy": "options_pattern1_entry_stop_geometry",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "complete_sessions": len(days),
        "target_r": TARGET_R,
        "starting_balance": STARTING_BALANCE,
        "risk_dollars_per_1r": RISK_DOLLARS,
        "pattern_config_fixed": PATTERN_CFG,
        "geometry_grid_tested": len(list(grid())),
        "qualified_geometries": len(qualified),
        "selected_geometry": selected,
        "fold_a": dollarize(sa),
        "fold_b": dollarize(sb),
        "holdout": dollarize(base.summarize(hold_trades)),
        "full_sample": dollarize(base.summarize(full_trades)),
        "direction_split_full": {
            "calls": dollarize(base.summarize([t for t in full_trades if t["side"] == "CALL"])),
            "puts": dollarize(base.summarize([t for t in full_trades if t["side"] == "PUT"])),
        },
        "prior_leader": {
            "full_pl_dollars": prior["full_sample"]["net_pl_dollars"],
            "holdout_pl_dollars": prior["holdout"]["net_pl_dollars"],
            "full_win_rate_pct": prior["full_sample"]["win_rate_pct"],
            "holdout_win_rate_pct": prior["holdout"]["win_rate_pct"],
        },
    }
    write_results(result)


if __name__ == "__main__":
    main()
