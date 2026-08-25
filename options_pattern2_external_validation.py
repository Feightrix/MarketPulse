import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2
import options_pattern2_trend_refinement as refined

RESULT_JSON = "options_pattern2_external_validation.json"
RESULT_MD = "options_pattern2_external_validation.md"
LOOKBACK_DAYS = 720
PRIOR_RESEARCH_DAYS = 360
STARTING_BALANCE = 2500.0
RISK_DOLLARS = 25.0

# Frozen after the 360-day research. No parameter selection occurs in this file.
FROZEN_CFG = {
    "max_efficiency": 0.65,
    "max_vwap_slope_atr": 0.50,
    "min_rsi_turn": 3.0,
}


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


def prepare_days():
    old = base.LOOKBACK_DAYS
    base.LOOKBACK_DAYS = LOOKBACK_DAYS
    try:
        raw = base.fetch_bars()
    finally:
        base.LOOKBACK_DAYS = old
    by_day = base.regular_session_bars(raw)
    days = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        p2.add_atr_rsi(bars)
        days.append((day, bars))
    return days


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    ext = result["external_unseen_block"]
    a = result["external_first_half"]
    b = result["external_second_half"]
    lines = [
        "# MarketPulse — Pattern 2 Fixed External Validation",
        "",
        "**Research only. Order submission remains disabled.**",
        "",
        "The Pattern 2 rules were frozen before this test. This script performs no optimization or parameter selection.",
        "",
        "## Additional Previously Unseen Historical Block",
        f"- Sessions: **{result['external_sessions']}**",
        f"- Trades: **{ext['trades']}**",
        f"- Win rate: **{ext['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${ext['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${ext['ending_balance_dollars']:,.2f}**",
        f"- Return: **{ext['return_pct']:.2f}%**",
        f"- Profit factor: **{ext['profit_factor']}**",
        f"- Expectancy: **{ext['expectancy_r']:.4f}R/trade**",
        f"- Max drawdown: **${ext['max_drawdown_dollars']:,.2f}**",
        "",
        "## Stability Inside External Block",
        f"- First half: **{a['trades']} trades | {a['win_rate_pct']:.2f}% wins | ${a['net_pl_dollars']:,.2f} P/L**",
        f"- Second half: **{b['trades']} trades | {b['win_rate_pct']:.2f}% wins | ${b['net_pl_dollars']:,.2f} P/L**",
        "",
        "## Decision",
        f"- External block profitable: **{'YES' if ext['net_pl_dollars'] > 0 else 'NO'}**",
        f"- External block 60–80% win-rate target: **{'YES' if 60 <= ext['win_rate_pct'] <= 80 else 'NO'}**",
        "",
        "Dollar P/L is risk-normalized underlying-pattern P/L at $25 per 1R, not actual option-premium P/L.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    days = prepare_days()
    cutoff = (datetime.now(base.EASTERN) - timedelta(days=PRIOR_RESEARCH_DAYS)).date()
    external_days = [(d, bars) for d, bars in days if d < cutoff]
    if len(external_days) < 50:
        raise RuntimeError("Not enough external historical sessions")

    split = len(external_days) // 2
    first = external_days[:split]
    second = external_days[split:]

    ext_trades = refined.evaluate(external_days, FROZEN_CFG)
    first_trades = refined.evaluate(first, FROZEN_CFG)
    second_trades = refined.evaluate(second, FROZEN_CFG)

    result = {
        "strategy": "options_pattern2_fixed_external_validation",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "lookback_days": LOOKBACK_DAYS,
        "prior_research_days": PRIOR_RESEARCH_DAYS,
        "cutoff_date": str(cutoff),
        "frozen_config": FROZEN_CFG,
        "external_sessions": len(external_days),
        "external_unseen_block": dollarize(base.summarize(ext_trades)),
        "external_first_half": dollarize(base.summarize(first_trades)),
        "external_second_half": dollarize(base.summarize(second_trades)),
        "direction_split_external": {
            "calls": dollarize(base.summarize([t for t in ext_trades if t["side"] == "CALL"])),
            "puts": dollarize(base.summarize([t for t in ext_trades if t["side"] == "PUT"])),
        },
    }
    write_results(result)


if __name__ == "__main__":
    main()
