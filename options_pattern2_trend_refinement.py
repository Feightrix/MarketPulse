import json
from datetime import datetime, time, timezone
from itertools import product
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2

RESULT_JSON = "options_pattern2_trend_results.json"
RESULT_MD = "options_pattern2_trend_results.md"
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 25.0
LOOKBACK_DAYS = 360
VALIDATION_FRACTION = 0.25  # oldest data: not used in the prior 180-day research
MIN_FOLD_TRADES = 8

BASE_CFG = {
    "deviation_atr": 1.0,
    "wick_ratio_min": 0.40,
    "rsi_extreme": 35,
    "target_r": 1.25,
    "stop_pad_atr": 0.10,
}

MAX_VWAP_SLOPE_ATR = [0.15, 0.30, 0.50]
MAX_EFFICIENCY = [0.35, 0.50, 0.65]
MIN_RSI_TURN = [0.0, 3.0, 6.0]


def trend_features(bars, i, slope_lookback=6, efficiency_lookback=12):
    if i < max(slope_lookback, efficiency_lookback):
        return None
    atr = bars[i].get("atr", 0.0)
    if atr <= 0:
        return None
    vwap_slope_atr = (bars[i]["vwap"] - bars[i - slope_lookback]["vwap"]) / atr
    window = bars[i - efficiency_lookback:i + 1]
    path = sum(abs(window[j]["c"] - window[j - 1]["c"]) for j in range(1, len(window)))
    net = abs(window[-1]["c"] - window[0]["c"])
    efficiency = net / path if path > 0 else 0.0
    return vwap_slope_atr, efficiency


def find_trade(day, bars, side, cfg):
    start_i = next((i for i, b in enumerate(bars) if b["ts"].time() >= p2.START_TIME), None)
    if start_i is None:
        return None

    for i in range(max(start_i, 15), len(bars) - 1):
        b = bars[i]
        if b["ts"].time() > p2.LATEST_ENTRY:
            break
        atr = b.get("atr", 0.0)
        if atr <= 0 or i == 0:
            continue
        features = trend_features(bars, i)
        if features is None:
            continue
        slope_atr, efficiency = features
        upper_wick, lower_wick, close_loc = p2.wick_ratios(b)
        prev_rsi = bars[i - 1].get("rsi", 50.0)

        if side == "CALL":
            deviation = b["vwap"] - b["l"]
            stretched = deviation >= BASE_CFG["deviation_atr"] * atr
            exhausted = (
                b["c"] > b["o"]
                and lower_wick >= BASE_CFG["wick_ratio_min"]
                and close_loc >= 0.60
                and b["rsi"] <= BASE_CFG["rsi_extreme"]
                and b["rsi"] - prev_rsi >= cfg["min_rsi_turn"]
            )
            continuity = all(x["c"] < x["vwap"] for x in bars[max(0, i - 2): i + 1])
            trend_ok = slope_atr >= -cfg["max_vwap_slope_atr"]
        else:
            deviation = b["h"] - b["vwap"]
            stretched = deviation >= BASE_CFG["deviation_atr"] * atr
            exhausted = (
                b["c"] < b["o"]
                and upper_wick >= BASE_CFG["wick_ratio_min"]
                and close_loc <= 0.40
                and b["rsi"] >= 100.0 - BASE_CFG["rsi_extreme"]
                and prev_rsi - b["rsi"] >= cfg["min_rsi_turn"]
            )
            continuity = all(x["c"] > x["vwap"] for x in bars[max(0, i - 2): i + 1])
            trend_ok = slope_atr <= cfg["max_vwap_slope_atr"]

        if not (stretched and exhausted and continuity and trend_ok and efficiency <= cfg["max_efficiency"]):
            continue

        trigger_end = min(i + p2.TRIGGER_WINDOW_BARS, len(bars) - 1)
        for k in range(i + 1, trigger_end + 1):
            t = bars[k]
            if t["ts"].time() > p2.LATEST_ENTRY:
                break
            if side == "CALL":
                trigger = b["h"] + base.TICK
                if t["h"] < trigger:
                    continue
                entry = max(trigger, t["o"])
                stop = b["l"] - base.TICK - BASE_CFG["stop_pad_atr"] * atr
                risk = entry - stop
                if risk <= 0:
                    continue
                target = entry + BASE_CFG["target_r"] * risk
                if target > t["vwap"]:
                    continue
            else:
                trigger = b["l"] - base.TICK
                if t["l"] > trigger:
                    continue
                entry = min(trigger, t["o"])
                stop = b["h"] + base.TICK + BASE_CFG["stop_pad_atr"] * atr
                risk = stop - entry
                if risk <= 0:
                    continue
                target = entry - BASE_CFG["target_r"] * risk
                if target < t["vwap"]:
                    continue

            sim = p2.simulate_trade(bars, k, side, entry, stop, target)
            if sim is None:
                continue
            return {
                "date": str(day),
                "side": side,
                "entry_ts": t["ts"].isoformat(),
                "vwap_slope_atr": round(slope_atr, 4),
                "efficiency": round(efficiency, 4),
                "rsi_turn": round((b["rsi"] - prev_rsi) if side == "CALL" else (prev_rsi - b["rsi"]), 2),
                **sim,
            }
    return None


def evaluate(day_items, cfg):
    trades = []
    for day, bars in day_items:
        for side in ("CALL", "PUT"):
            t = find_trade(day, bars, side, cfg)
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
    for slope, eff, turn in product(MAX_VWAP_SLOPE_ATR, MAX_EFFICIENCY, MIN_RSI_TURN):
        yield {
            "max_vwap_slope_atr": slope,
            "max_efficiency": eff,
            "min_rsi_turn": turn,
        }


def score(summaries):
    return (
        min(s["net_r"] for s in summaries),
        min(s["expectancy_r"] for s in summaries),
        sum(s["net_r"] for s in summaries),
        sum(s["win_rate_pct"] for s in summaries) / len(summaries),
        -sum(s["max_drawdown_r"] for s in summaries),
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    v = result["unseen_older_validation"]
    full = result["full_360"]
    cfg = result["selected_trend_filter"]
    lines = [
        "# MarketPulse — Options Pattern 2 Trend Refinement",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        "## Why this refinement exists",
        "- Baseline Pattern 2 failed its recent holdout because mean reversion kept fading strong directional moves.",
        "- The only new logic is mirrored trend-strength protection: do not fade when VWAP trend/price efficiency is too directional.",
        "- RSI must also turn back toward the mean by a minimum amount before entry.",
        "",
        "## Selected Trend Filter",
        f"- Max adverse 6-bar VWAP slope: **{cfg['max_vwap_slope_atr']:.2f} ATR**",
        f"- Max 12-bar price efficiency: **{cfg['max_efficiency']:.2f}**",
        f"- Minimum RSI turn: **{cfg['min_rsi_turn']:.1f} points**",
        "",
        "## Unseen Older Historical Validation",
        "This block predates the prior 180-day research and was not used in those earlier selections.",
        f"- Trades: **{v['trades']}**",
        f"- Win rate: **{v['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${v['net_pl_dollars']:,.2f}**",
        f"- Profit factor: **{v['profit_factor']}**",
        f"- Max drawdown: **${v['max_drawdown_dollars']:,.2f}**",
        "",
        "## Full 360-Day Sample",
        f"- Trades: **{full['trades']}**",
        f"- Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${full['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%**",
        f"- Profit factor: **{full['profit_factor']}**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        "",
        "## Validation",
        f"- Trend configurations tested: **{result['grid_tested']}**",
        f"- Profitable in all 3 development folds: **{result['profitable_all_three_folds']}**",
        f"- Unseen older validation profitable: **{'YES' if v['net_pl_dollars'] > 0 else 'NO'}**",
        f"- Unseen validation 60–80% win-rate target: **{'YES' if 60 <= v['win_rate_pct'] <= 80 else 'NO'}**",
        "",
        "Dollar P/L remains risk-normalized underlying-pattern P/L at $25 per 1R, not actual option-premium P/L.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    old_lookback = base.LOOKBACK_DAYS
    base.LOOKBACK_DAYS = LOOKBACK_DAYS
    try:
        raw = base.fetch_bars()
    finally:
        base.LOOKBACK_DAYS = old_lookback

    by_day = base.regular_session_bars(raw)
    days = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        p2.add_atr_rsi(bars)
        days.append((day, bars))

    validation_n = max(1, int(len(days) * VALIDATION_FRACTION))
    unseen_validation = days[:validation_n]
    dev = days[validation_n:]
    one = len(dev) // 3
    folds = [dev[:one], dev[one:2 * one], dev[2 * one:]]

    all_candidates = []
    robust = []
    for cfg in grid():
        summaries = []
        enough = True
        for fold in folds:
            s = base.summarize(evaluate(fold, cfg))
            summaries.append(s)
            if s["trades"] < MIN_FOLD_TRADES:
                enough = False
        if not enough:
            continue
        item = (score(summaries), cfg, summaries)
        all_candidates.append(item)
        if all(s["net_r"] > 0 for s in summaries):
            robust.append(item)

    if not all_candidates:
        raise RuntimeError("Pattern 2 trend refinement produced too few trades")

    pool = robust if robust else all_candidates
    pool.sort(key=lambda x: x[0], reverse=True)
    _, selected, fold_summaries = pool[0]

    validation_trades = evaluate(unseen_validation, selected)
    full_trades = evaluate(days, selected)
    result = {
        "strategy": "options_pattern2_trend_refinement",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "lookback_days": LOOKBACK_DAYS,
        "complete_sessions": len(days),
        "grid_tested": len(list(grid())),
        "eligible_configs": len(all_candidates),
        "profitable_all_three_folds": len(robust),
        "robust_development_pass": bool(robust),
        "base_pattern_config": BASE_CFG,
        "selected_trend_filter": selected,
        "development_folds": [dollarize(s) for s in fold_summaries],
        "unseen_older_validation": dollarize(base.summarize(validation_trades)),
        "full_360": dollarize(base.summarize(full_trades)),
        "direction_split_full": {
            "calls": dollarize(base.summarize([t for t in full_trades if t["side"] == "CALL"])),
            "puts": dollarize(base.summarize([t for t in full_trades if t["side"] == "PUT"])),
        },
    }
    write_results(result)


if __name__ == "__main__":
    main()
