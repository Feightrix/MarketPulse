import json
from datetime import datetime, time, timezone
from itertools import product
from pathlib import Path

import options_pattern1_backtest as base

RESULT_JSON = "options_pattern2_results.json"
RESULT_MD = "options_pattern2_results.md"
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 25.0
TRAIN_FRACTION = 0.70
MIN_FOLD_TRADES = 6
START_TIME = time(10, 0)
LATEST_ENTRY = time(14, 30)
FORCE_EXIT = time(15, 45)
TRIGGER_WINDOW_BARS = 2

# Small, interpretable grid. Pattern definition stays fixed; only quality thresholds vary.
DEVIATION_ATR = [1.0, 1.5, 2.0]
WICK_RATIO_MIN = [0.25, 0.40]
RSI_EXTREME = [30, 35]
TARGET_R = [1.25, 1.50]
STOP_PAD_ATR = [0.05, 0.10]


def add_atr_rsi(bars, atr_period=14, rsi_period=7):
    trs = []
    gains = []
    losses = []
    prev_close = None
    for i, b in enumerate(bars):
        if prev_close is None:
            tr = b["h"] - b["l"]
            change = 0.0
        else:
            tr = max(b["h"] - b["l"], abs(b["h"] - prev_close), abs(b["l"] - prev_close))
            change = b["c"] - prev_close
        trs.append(tr)
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
        atr_slice = trs[max(0, i - atr_period + 1): i + 1]
        b["atr"] = sum(atr_slice) / len(atr_slice)
        if i < rsi_period:
            b["rsi"] = 50.0
        else:
            g = gains[i - rsi_period + 1: i + 1]
            l = losses[i - rsi_period + 1: i + 1]
            avg_gain = sum(g) / rsi_period
            avg_loss = sum(l) / rsi_period
            if avg_loss == 0:
                b["rsi"] = 100.0
            else:
                rs = avg_gain / avg_loss
                b["rsi"] = 100.0 - (100.0 / (1.0 + rs))
        prev_close = b["c"]


def wick_ratios(bar):
    span = bar["h"] - bar["l"]
    if span <= 0:
        return 0.0, 0.0, 0.5
    upper = bar["h"] - max(bar["o"], bar["c"])
    lower = min(bar["o"], bar["c"]) - bar["l"]
    close_loc = (bar["c"] - bar["l"]) / span
    return upper / span, lower / span, close_loc


def simulate_trade(bars, entry_i, side, entry, stop, target):
    risk = entry - stop if side == "CALL" else stop - entry
    if risk <= 0:
        return None
    exit_price = None
    exit_reason = None
    exit_ts = None
    for b in bars[entry_i:]:
        if b["ts"].time() > FORCE_EXIT:
            break
        if side == "CALL":
            stop_hit = b["l"] <= stop
            target_hit = b["h"] >= target
        else:
            stop_hit = b["h"] >= stop
            target_hit = b["l"] <= target
        # Conservative ambiguous-bar rule.
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP"
            exit_ts = b["ts"]
            break
        if target_hit:
            exit_price = target
            exit_reason = "TARGET"
            exit_ts = b["ts"]
            break
    if exit_price is None:
        eligible = [b for b in bars[entry_i:] if b["ts"].time() <= FORCE_EXIT]
        if not eligible:
            return None
        last = eligible[-1]
        exit_price = last["c"]
        exit_reason = "TIME"
        exit_ts = last["ts"]
    r = (exit_price - entry) / risk if side == "CALL" else (entry - exit_price) / risk
    return {
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "exit_ts": exit_ts.isoformat(),
        "r": round(r, 4),
    }


def find_trade(day, bars, side, cfg):
    start_i = next((i for i, b in enumerate(bars) if b["ts"].time() >= START_TIME), None)
    if start_i is None:
        return None

    for i in range(max(start_i, 15), len(bars) - 1):
        b = bars[i]
        if b["ts"].time() > LATEST_ENTRY:
            break
        atr = b.get("atr", 0.0)
        if atr <= 0:
            continue
        upper_wick, lower_wick, close_loc = wick_ratios(b)

        if side == "CALL":
            deviation = b["vwap"] - b["l"]
            stretched = deviation >= cfg["deviation_atr"] * atr
            exhausted = (
                b["c"] > b["o"]
                and lower_wick >= cfg["wick_ratio_min"]
                and close_loc >= 0.60
                and b["rsi"] <= cfg["rsi_extreme"]
            )
            continuity = all(x["c"] < x["vwap"] for x in bars[max(0, i - 2): i + 1])
        else:
            deviation = b["h"] - b["vwap"]
            stretched = deviation >= cfg["deviation_atr"] * atr
            exhausted = (
                b["c"] < b["o"]
                and upper_wick >= cfg["wick_ratio_min"]
                and close_loc <= 0.40
                and b["rsi"] >= 100.0 - cfg["rsi_extreme"]
            )
            continuity = all(x["c"] > x["vwap"] for x in bars[max(0, i - 2): i + 1])

        if not (stretched and exhausted and continuity):
            continue

        trigger_end = min(i + TRIGGER_WINDOW_BARS, len(bars) - 1)
        for k in range(i + 1, trigger_end + 1):
            t = bars[k]
            if t["ts"].time() > LATEST_ENTRY:
                break
            if side == "CALL":
                trigger = b["h"] + base.TICK
                if t["h"] < trigger:
                    continue
                entry = max(trigger, t["o"])
                stop = b["l"] - base.TICK - cfg["stop_pad_atr"] * atr
                risk = entry - stop
                if risk <= 0:
                    continue
                target = entry + cfg["target_r"] * risk
                # Natural mean-reversion constraint: fixed-R target must remain before VWAP.
                if target > t["vwap"]:
                    continue
            else:
                trigger = b["l"] - base.TICK
                if t["l"] > trigger:
                    continue
                entry = min(trigger, t["o"])
                stop = b["h"] + base.TICK + cfg["stop_pad_atr"] * atr
                risk = stop - entry
                if risk <= 0:
                    continue
                target = entry - cfg["target_r"] * risk
                if target < t["vwap"]:
                    continue

            sim = simulate_trade(bars, k, side, entry, stop, target)
            if sim is None:
                continue
            return {
                "date": str(day),
                "side": side,
                "signal_ts": b["ts"].isoformat(),
                "entry_ts": t["ts"].isoformat(),
                "entry": round(entry, 4),
                "stop": round(stop, 4),
                "target": round(target, 4),
                "vwap": round(t["vwap"], 4),
                "atr": round(atr, 4),
                "rsi": round(b["rsi"], 2),
                "deviation_atr": round(deviation / atr, 3),
                **sim,
            }
    return None


def evaluate(day_items, cfg):
    trades = []
    for day, bars in day_items:
        for side in ("CALL", "PUT"):
            trade = find_trade(day, bars, side, cfg)
            if trade:
                trades.append(trade)
    trades.sort(key=lambda t: t["entry_ts"])
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
    for deviation, wick, rsi, target_r, stop_pad in product(
        DEVIATION_ATR, WICK_RATIO_MIN, RSI_EXTREME, TARGET_R, STOP_PAD_ATR
    ):
        yield {
            "deviation_atr": deviation,
            "wick_ratio_min": wick,
            "rsi_extreme": rsi,
            "target_r": target_r,
            "stop_pad_atr": stop_pad,
        }


def robust_score(a, b):
    # Money first, weakest fold first, then expectancy and win rate.
    return (
        min(a["net_r"], b["net_r"]),
        min(a["expectancy_r"], b["expectancy_r"]),
        (a["net_r"] + b["net_r"]),
        (a["win_rate_pct"] + b["win_rate_pct"]) / 2.0,
        -(a["max_drawdown_r"] + b["max_drawdown_r"]),
    )


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    cfg = result["selected_config"]
    hold = result["holdout"]
    full = result["full_sample"]
    calls = result["direction_split_full"]["calls"]
    puts = result["direction_split_full"]["puts"]
    lines = [
        "# MarketPulse — Options Pattern 2: VWAP Stretch Reversal",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        "## Pattern",
        "- Underlying: **SPY**",
        "- Bars: **5 minute**",
        "- Distinct from Pattern 1: this is a **mean-reversion** setup, not a breakout continuation",
        "- CALL: stretched below VWAP + oversold/exhaustion reversal + snapback trigger",
        "- PUT: exact mirrored setup above VWAP",
        "- Fixed-R target must remain inside the path back toward VWAP",
        "",
        "## Selected Configuration",
        f"- Minimum stretch: **{cfg['deviation_atr']:.2f} ATR**",
        f"- Minimum exhaustion wick: **{cfg['wick_ratio_min']:.0%} of bar range**",
        f"- RSI extreme: **{cfg['rsi_extreme']} / {100-cfg['rsi_extreme']}**",
        f"- Target: **{cfg['target_r']:.2f}R**",
        f"- Stop padding: **{cfg['stop_pad_atr']:.2f} ATR**",
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
        f"- Expectancy: **{full['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        "",
        "## Direction Split",
        f"- CALLS: **{calls['trades']} trades | {calls['win_rate_pct']:.2f}% wins | ${calls['net_pl_dollars']:,.2f} P/L**",
        f"- PUTS: **{puts['trades']} trades | {puts['win_rate_pct']:.2f}% wins | ${puts['net_pl_dollars']:,.2f} P/L**",
        "",
        "## Validation",
        f"- Configurations tested: **{result['grid_tested']}**",
        f"- Profitable in both development folds: **{result['profitable_in_both_development_folds']}**",
        f"- Holdout profitable: **{'YES' if hold['net_pl_dollars'] > 0 else 'NO'}**",
        f"- Holdout 60–80% win-rate target: **{'YES' if 60 <= hold['win_rate_pct'] <= 80 else 'NO'}**",
        "",
        "Dollar P/L is risk-normalized underlying-pattern P/L at $25 per 1R, not yet actual option-premium P/L.",
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
        add_atr_rsi(bars)
        days.append((day, bars))

    split = int(len(days) * TRAIN_FRACTION)
    dev = days[:split]
    holdout_days = days[split:]
    fold_split = len(dev) // 2
    fold_a = dev[:fold_split]
    fold_b = dev[fold_split:]

    all_candidates = []
    qualified = []
    for cfg in grid():
        ta = evaluate(fold_a, cfg)
        tb = evaluate(fold_b, cfg)
        sa = base.summarize(ta)
        sb = base.summarize(tb)
        if sa["trades"] < MIN_FOLD_TRADES or sb["trades"] < MIN_FOLD_TRADES:
            continue
        item = (robust_score(sa, sb), cfg, sa, sb)
        all_candidates.append(item)
        if sa["net_r"] > 0 and sb["net_r"] > 0:
            qualified.append(item)

    if not all_candidates:
        raise RuntimeError("Pattern 2 produced too few development trades")

    pool = qualified if qualified else all_candidates
    pool.sort(key=lambda x: x[0], reverse=True)
    _, selected, sa, sb = pool[0]

    holdout_trades = evaluate(holdout_days, selected)
    full_trades = evaluate(days, selected)
    result = {
        "strategy": "options_pattern2_vwap_stretch_reversal",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "complete_sessions": len(days),
        "starting_balance": STARTING_BALANCE,
        "risk_dollars_per_1r": RISK_DOLLARS,
        "grid_tested": len(list(grid())),
        "eligible_configs": len(all_candidates),
        "profitable_in_both_development_folds": len(qualified),
        "robust_development_pass": bool(qualified),
        "selected_config": selected,
        "fold_a": dollarize(sa),
        "fold_b": dollarize(sb),
        "holdout": dollarize(base.summarize(holdout_trades)),
        "full_sample": dollarize(base.summarize(full_trades)),
        "direction_split_full": {
            "calls": dollarize(base.summarize([t for t in full_trades if t["side"] == "CALL"])),
            "puts": dollarize(base.summarize([t for t in full_trades if t["side"] == "PUT"])),
        },
        "trades": full_trades,
    }
    write_results(result)


if __name__ == "__main__":
    main()
