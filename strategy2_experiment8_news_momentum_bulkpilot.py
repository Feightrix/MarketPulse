import json
from datetime import time as dtime

import pandas as pd

import strategy2_experiment8_news_momentum_long as base
import strategy2_experiment8_news_momentum_runner as fast

START = "2026-01-01"
END = "2026-07-31"
base.UNIVERSE = ["NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "NFLX", "GOOGL"]
base.collect_events = fast.collect_events_fast


def bulk_bars(symbol):
    rows = []
    token = None
    while True:
        params = {
            "timeframe": "1Min",
            "start": f"{START}T00:00:00Z",
            "end": f"{END}T23:59:59Z",
            "limit": 10000,
            "adjustment": "all",
            "feed": base.FEED,
            "sort": "asc",
        }
        if token:
            params["page_token"] = token
        payload = base.get_json(f"{base.DATA_BASE}/v2/stocks/{symbol}/bars", params)
        rows.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(base.ET)
    df = df.set_index("ts").sort_index()
    return df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))].copy()


def main():
    panels = {s: bulk_bars(s) for s in base.UNIVERSE + [base.BENCHMARK]}

    def day_slice(symbol, day):
        df = panels.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        target = pd.Timestamp(day).date()
        return df[df.index.date == target].copy()

    base.fetch_day_bars = day_slice
    stats = base.run_block("pilot_2026_ytd", START, END)
    checks = {
        "at_least_5_trades": stats["trades"] >= 5,
        "positive_after_costs": stats["total_return_pct"] > 0.0,
        "positive_expectancy": stats["avg_trade_pnl_dollars"] > 0.0,
        "profit_factor_at_least_1_25": stats["profit_factor"] >= 1.25,
        "winner_loser_ratio_at_least_1_5": stats["avg_winner_to_loser"] >= 1.5,
        "max_drawdown_at_most_8pct": stats["max_drawdown_pct"] <= 8.0,
    }
    passed = all(checks.values())
    compact = {k: v for k, v in stats.items() if k != "trade_details"}
    compact["top_10_trades"] = sorted(stats["trade_details"], key=lambda x: x["pnl_dollars"], reverse=True)[:10]
    compact["bottom_10_trades"] = sorted(stats["trade_details"], key=lambda x: x["pnl_dollars"])[:10]
    result = {
        "experiment": "S2-E8-NEWS-MOMENTUM-2026-YTD-BULK-PILOT",
        "research_only": True,
        "full_validation": False,
        "data_plumbing": "bulk historical IEX minute bars; consolidated historical news pull",
        "strategy_rules": "identical to locked Experiment 8",
        "universe": base.UNIVERSE,
        "period": [START, END],
        "stats": compact,
        "checks": checks,
        "pilot_gate": "PASS" if passed else "FAIL",
        "activate": False,
    }
    with open("strategy2_experiment8_news_momentum_quick_results.json", "w") as f:
        json.dump(result, f, indent=2)
    lines = [
        "# Experiment 8 — 2026 YTD News Momentum Pilot",
        "",
        f"**Pilot gate: {result['pilot_gate']}**",
        "",
        "Same locked trading rules as Experiment 8. Bulk data retrieval changes plumbing only.",
        "",
        f"- Eligible news events: {stats['eligible_news_events']}",
        f"- Confirmed events: {stats['confirmed_events']}",
        f"- Trades: {stats['trades']}",
        f"- Ending equity: ${stats['ending_equity']:.2f}",
        f"- Total return: {stats['total_return_pct']:+.3f}%",
        f"- Max drawdown: {stats['max_drawdown_pct']:.3f}%",
        f"- Win rate: {stats['win_rate_pct']:.2f}%",
        f"- Profit factor: {stats['profit_factor']:.3f}",
        f"- Avg trade P&L: ${stats['avg_trade_pnl_dollars']:+.2f}",
        f"- Avg winner / loser: ${stats['avg_winner_dollars']:.2f} / ${stats['avg_loser_dollars']:.2f}",
        f"- Winner/loser ratio: {stats['avg_winner_to_loser']:.2f}",
        f"- Best / worst trade: ${stats['best_trade_dollars']:+.2f} / ${stats['worst_trade_dollars']:+.2f}",
        f"- Avg R multiple: {stats['avg_r_multiple']:+.3f}",
        "",
        "## Pilot checks",
    ]
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "Activation remains OFF. This pilot is not a substitute for multi-period validation.", ""]
    with open("strategy2_experiment8_news_momentum_quick_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
