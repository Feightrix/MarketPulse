import json
import math
import numpy as np
import pandas as pd

import phase5h_sector_neutral_ensemble as base

START_EQ = 2500.0
COST_BPS = 10.0
ASSETS = list(base.TREND_ASSETS)

# Locked before results: long-only, cash-funded, capped martingale.
INITIAL_FRACTION = 1.0 / 7.0
ADD1_FRACTION = 2.0 / 7.0
ADD2_FRACTION = 4.0 / 7.0
ADD_TRIGGER_PCT = 0.02
PROFIT_TARGET_PCT = 0.01
MAX_TRANCHES = 3

BLOCKS = {
    "development_2008_2014": (base.DEV_START, base.DEV_END),
    "validation_2015_2019": (base.VAL_START, base.VAL_END),
    "holdout1_2020_2023": (base.H1_START, base.H1_END),
    "holdout2_2024_2026": (base.H2_START, base.H2_END),
}


def trend_ok(c, i, sym):
    need = max(base.LOOKBACK, base.TREND_SMA, base.SPY_GATE_SMA)
    if i < need:
        return False
    px = float(c.iloc[i][sym])
    old = float(c.iloc[i - base.LOOKBACK][sym])
    sma = float(c[sym].iloc[i - base.TREND_SMA + 1:i + 1].mean())
    spy = float(c.iloc[i]["SPY"])
    spy_old = float(c.iloc[i - base.LOOKBACK]["SPY"])
    spy_sma = float(c["SPY"].iloc[i - base.SPY_GATE_SMA + 1:i + 1].mean())
    return bool(px > sma and px > old and spy > spy_sma and spy > spy_old)


def trade_cost(notional):
    return abs(float(notional)) * COST_BPS / 10000.0


def simulate_sleeve(o, c, sym, start, end, martingale=True):
    dates = list(c.index)
    sleeve0 = START_EQ / len(ASSETS)
    cash = sleeve0
    qty = 0.0
    avg_cost = 0.0
    last_fill = None
    tranches = 0
    cycle_start_equity = sleeve0
    pending = None
    curve = []
    cycles = 0
    winning_cycles = 0
    losing_cycles = 0
    add1 = 0
    add2 = 0
    total_fills = 0
    modeled_costs = 0.0
    max_deployed = 0.0

    for i in range(1, len(dates)):
        d = dates[i]
        if d < start:
            continue
        if d > end:
            break

        op = float(o.iloc[i][sym])
        cl = float(c.iloc[i][sym])

        if pending:
            action = pending
            pending = None
            if action == "BUY_INITIAL" and qty <= 1e-12:
                cycle_start_equity = cash
                frac = 1.0 if not martingale else INITIAL_FRACTION
                notional = min(cash, max(0.0, cycle_start_equity * frac))
                cost = trade_cost(notional)
                spend = max(0.0, min(notional, cash - cost))
                if spend > 0:
                    buy_qty = spend / op
                    cash -= spend + cost
                    qty += buy_qty
                    avg_cost = op
                    last_fill = op
                    tranches = 1
                    total_fills += 1
                    modeled_costs += cost
            elif action in ("BUY_ADD1", "BUY_ADD2") and martingale and qty > 1e-12:
                frac = ADD1_FRACTION if action == "BUY_ADD1" else ADD2_FRACTION
                requested = max(0.0, cycle_start_equity * frac)
                cost = trade_cost(requested)
                spend = max(0.0, min(requested, cash - cost))
                if spend > 0:
                    buy_qty = spend / op
                    old_cost_basis = qty * avg_cost
                    cash -= spend + trade_cost(spend)
                    modeled_costs += trade_cost(spend)
                    qty += buy_qty
                    avg_cost = (old_cost_basis + spend) / qty
                    last_fill = op
                    tranches += 1
                    total_fills += 1
                    if action == "BUY_ADD1":
                        add1 += 1
                    else:
                        add2 += 1
            elif action.startswith("SELL") and qty > 1e-12:
                proceeds = qty * op
                cost = trade_cost(proceeds)
                pnl = proceeds - cost + cash - cycle_start_equity
                cash += proceeds - cost
                modeled_costs += cost
                total_fills += 1
                cycles += 1
                if pnl > 0:
                    winning_cycles += 1
                else:
                    losing_cycles += 1
                qty = 0.0
                avg_cost = 0.0
                last_fill = None
                tranches = 0

        equity = cash + qty * cl
        max_deployed = max(max_deployed, max(0.0, equity - cash))
        curve.append((d, equity))

        sig = trend_ok(c, i, sym)
        if qty <= 1e-12:
            if sig:
                pending = "BUY_INITIAL"
            continue

        # Exit takes priority over adds. All decisions use today's close and execute next open.
        if not sig:
            pending = "SELL_TREND_BREAK"
            continue
        if martingale and avg_cost > 0 and cl >= avg_cost * (1.0 + PROFIT_TARGET_PCT):
            pending = "SELL_PROFIT_TARGET"
            continue
        if martingale and tranches < MAX_TRANCHES and last_fill:
            if cl <= last_fill * (1.0 - ADD_TRIGGER_PCT):
                pending = "BUY_ADD1" if tranches == 1 else "BUY_ADD2"

    # Mark final equity; no forced liquidation beyond the curve.
    series = pd.Series([x[1] for x in curve], index=pd.to_datetime([x[0] for x in curve]), dtype=float)
    return series, {
        "cycles": cycles,
        "winning_cycles": winning_cycles,
        "losing_cycles": losing_cycles,
        "win_rate_pct": float(winning_cycles / cycles * 100.0) if cycles else 0.0,
        "add1_count": add1,
        "add2_count": add2,
        "total_fills": total_fills,
        "modeled_costs_dollars": modeled_costs,
        "max_deployed_dollars": max_deployed,
    }


def aggregate(o, c, start, end, martingale):
    curves = []
    details = {}
    for sym in ASSETS:
        s, d = simulate_sleeve(o, c, sym, start, end, martingale=martingale)
        curves.append(s.rename(sym))
        details[sym] = d
    panel = pd.concat(curves, axis=1).dropna(how="all").ffill()
    total = panel.sum(axis=1)
    stats = base.summarize(total)
    diff = total.diff().dropna()
    stats.update({
        "avg_daily_pnl_dollars": float(diff.mean()) if len(diff) else 0.0,
        "median_daily_pnl_dollars": float(diff.median()) if len(diff) else 0.0,
        "positive_day_rate_pct": float((diff > 0).mean() * 100.0) if len(diff) else 0.0,
        "worst_day_pnl_dollars": float(diff.min()) if len(diff) else 0.0,
        "best_day_pnl_dollars": float(diff.max()) if len(diff) else 0.0,
        "trading_days": int(len(total)),
        "cycles": int(sum(x["cycles"] for x in details.values())),
        "winning_cycles": int(sum(x["winning_cycles"] for x in details.values())),
        "losing_cycles": int(sum(x["losing_cycles"] for x in details.values())),
        "cycle_win_rate_pct": float(sum(x["winning_cycles"] for x in details.values()) / max(sum(x["cycles"] for x in details.values()), 1) * 100.0),
        "add1_count": int(sum(x["add1_count"] for x in details.values())),
        "add2_count": int(sum(x["add2_count"] for x in details.values())),
        "total_fills": int(sum(x["total_fills"] for x in details.values())),
        "modeled_costs_dollars": float(sum(x["modeled_costs_dollars"] for x in details.values())),
        "per_symbol": details,
    })
    return stats


def main():
    o, c = base.download_panel()
    baseline = {}
    martingale = {}
    for name, (start, end) in BLOCKS.items():
        baseline[name] = aggregate(o, c, start, end, martingale=False)
        martingale[name] = aggregate(o, c, start, end, martingale=True)

    h1b, h1m = baseline["holdout1_2020_2023"], martingale["holdout1_2020_2023"]
    h2b, h2m = baseline["holdout2_2024_2026"], martingale["holdout2_2024_2026"]
    checks = {
        "recent_holdout_positive_after_costs": h2m["total_return_pct"] > 0,
        "recent_holdout_beats_long_only_trend_baseline": h2m["total_return_pct"] > h2b["total_return_pct"],
        "recent_holdout_avg_daily_pnl_beats_baseline": h2m["avg_daily_pnl_dollars"] > h2b["avg_daily_pnl_dollars"],
        "recent_holdout_drawdown_at_most_10pct": h2m["max_drawdown_pct"] <= 10.0,
        "prior_holdout_positive_after_costs": h1m["total_return_pct"] > 0,
        "prior_holdout_drawdown_at_most_10pct": h1m["max_drawdown_pct"] <= 10.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": "S2-E7-LONG-ONLY-CAPPED-MARTINGALE",
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "universe": ASSETS,
        "signal": "Strategy-1 style per-asset 252d momentum + 150d trend, plus SPY 252d/200d broad gate",
        "execution_timing": "signals/triggers at close; execute next open; no lookahead",
        "sizing_rule": {
            "initial_fraction_of_symbol_sleeve": INITIAL_FRACTION,
            "second_tranche_fraction": ADD1_FRACTION,
            "third_tranche_fraction": ADD2_FRACTION,
            "adverse_move_between_tranches_pct": ADD_TRIGGER_PCT * 100.0,
            "max_tranches": MAX_TRANCHES,
            "profit_exit_above_weighted_average_cost_pct": PROFIT_TARGET_PCT * 100.0,
            "trend_break_exit": True,
            "cash_funded_only": True,
        },
        "transaction_cost_stress_bps_per_fill": COST_BPS,
        "baseline": baseline,
        "martingale": martingale,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate": False,
    }
    with open("strategy2_experiment7_long_martingale_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# MarketPulse Strategy 2 — Experiment 7: Long-Only Capped Martingale",
        "",
        f"**Gate: {result['gate']}**",
        "",
        "Research only. No Alpaca orders. Long-only and cash-funded; no leverage.",
        "",
        "Locked rule: 1x / 2x / 4x tranches, each next tranche after a 2% adverse close, max three tranches, full exit at +1% over weighted average cost or trend break, 10 bps per fill.",
        "",
    ]
    for block in BLOCKS:
        b, m = baseline[block], martingale[block]
        lines += [
            f"## {block}",
            f"- Baseline long trend: ending ${b['final_equity']:.2f} | return {b['total_return_pct']:+.3f}% | DD {b['max_drawdown_pct']:.3f}% | avg day ${b['avg_daily_pnl_dollars']:+.3f}",
            f"- Martingale: ending ${m['final_equity']:.2f} | return {m['total_return_pct']:+.3f}% | DD {m['max_drawdown_pct']:.3f}% | avg day ${m['avg_daily_pnl_dollars']:+.3f}",
            f"- Martingale cycles {m['cycles']} | win rate {m['cycle_win_rate_pct']:.2f}% | add1 {m['add1_count']} | add2 {m['add2_count']} | fills {m['total_fills']} | modeled costs ${m['modeled_costs_dollars']:.2f}",
            f"- Martingale best/worst day ${m['best_day_pnl_dollars']:+.2f} / ${m['worst_day_pnl_dollars']:+.2f}",
            "",
        ]
    lines.append("## Predeclared checks")
    for k, v in checks.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", "**Activation remains OFF regardless of backtest result; paper/shadow validation would be a separate step.**", ""]
    with open("strategy2_experiment7_long_martingale_summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
