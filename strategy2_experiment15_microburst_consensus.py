import json
import math

import numpy as np

import strategy2_experiment14_microburst_long as v1

EXPERIMENT = "S2-E15-MICROBURST-CONSENSUS-V2"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
INDEPENDENT_FROM_V1_PILOT_DATES = True

# Untouched relative to Experiment 14's Aug 19-21 development pilot.
TEST_DATES = ["2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17", "2026-08-18"]
UNIVERSE = list(v1.UNIVERSE)
WINDOW_STARTS_ET = list(v1.WINDOW_STARTS_ET)

# Outcome / execution protocol intentionally unchanged from v1.
FEATURE_SECONDS = v1.FEATURE_SECONDS
HORIZON_SECONDS = v1.HORIZON_SECONDS
PROFIT_BPS = v1.PROFIT_BPS
STOP_BPS = v1.STOP_BPS
IMPACT_BPS_PER_SIDE = v1.IMPACT_BPS_PER_SIDE
MAX_NOTIONAL_PCT = v1.MAX_NOTIONAL_PCT
COOLDOWN_SECONDS = v1.COOLDOWN_SECONDS
MAX_SPREAD_BPS = v1.MAX_SPREAD_BPS

# Locked consensus requirements. Unlike v1, points cannot compensate for a
# contradictory directional signal.
MIN_QUOTE_IMBALANCE = 0.35
MIN_MICRO_DISP_BPS = 0.15
MIN_BUY_IMBALANCE = 0.35
MIN_BID_VELOCITY_BPS = 0.0
MIN_TRADE_ACCEL = 1.20
MIN_QUOTE_ACCEL = 1.00
CONSENSUS_MAX_SPREAD_BPS = 3.0

# Persistence guard: previous second must still be directionally bullish.
PREV_MIN_QUOTE_IMBALANCE = 0.0
PREV_MIN_BUY_IMBALANCE = 0.0
PREV_MIN_MICRO_DISP_BPS = 0.0


def finite_ge(x, threshold):
    return np.isfinite(x) and float(x) >= float(threshold)


def consensus_pass(df, i):
    if i <= 0:
        return False
    r = df.iloc[i]
    p = df.iloc[i - 1]
    current = [
        finite_ge(r["imbalance"], MIN_QUOTE_IMBALANCE),
        finite_ge(r["micro_disp_bps"], MIN_MICRO_DISP_BPS),
        finite_ge(r["buy_imbalance"], MIN_BUY_IMBALANCE),
        finite_ge(r["bid_velocity_bps"], MIN_BID_VELOCITY_BPS),
        finite_ge(r["trade_accel"], MIN_TRADE_ACCEL),
        finite_ge(r["quote_accel"], MIN_QUOTE_ACCEL),
        np.isfinite(r["spread_bps"]) and float(r["spread_bps"]) <= CONSENSUS_MAX_SPREAD_BPS,
    ]
    previous = [
        finite_ge(p["imbalance"], PREV_MIN_QUOTE_IMBALANCE),
        finite_ge(p["buy_imbalance"], PREV_MIN_BUY_IMBALANCE),
        finite_ge(p["micro_disp_bps"], PREV_MIN_MICRO_DISP_BPS),
    ]
    return all(current) and all(previous)


def candidate_indices(df):
    out = []
    last = -10**9
    start = max(FEATURE_SECONDS, 20)
    for i in range(start, len(df) - HORIZON_SECONDS - 1):
        if not consensus_pass(df, i):
            continue
        if i - last < COOLDOWN_SECONDS:
            continue
        out.append(i)
        last = i
    return out


def baseline_indices(df):
    start = max(FEATURE_SECONDS, 20)
    return [
        i for i in range(start, len(df) - HORIZON_SECONDS - 1, 10)
        if np.isfinite(df.iloc[i]["spread_bps"]) and float(df.iloc[i]["spread_bps"]) <= MAX_SPREAD_BPS
    ]


def main():
    feed, data_quality = v1.detect_feed()
    signal_trades = []
    baseline = []
    windows_loaded = 0
    data_counts = {"quotes": 0, "trades": 0}
    sample_signals = []

    for day in TEST_DATES:
        for symbol in UNIVERSE:
            for hhmm in WINDOW_STARTS_ET:
                start, end = v1.utc_window(day, hhmm)
                quotes = v1.fetch_pages(symbol, "quotes", start, end, feed)
                trades = v1.fetch_pages(symbol, "trades", start, end, feed)
                data_counts["quotes"] += len(quotes)
                data_counts["trades"] += len(trades)
                df = v1.to_frames(quotes, trades)
                if len(df) < 55:
                    continue
                df = v1.add_features(df)
                windows_loaded += 1

                for i in candidate_indices(df):
                    o = v1.outcome(df, i)
                    if o is None:
                        continue
                    r = df.iloc[i]
                    p = df.iloc[i - 1]
                    o.update({
                        "symbol": symbol,
                        "day": day,
                        "time": str(df.index[i]),
                        "imbalance": float(r["imbalance"]),
                        "micro_disp_bps": float(r["micro_disp_bps"]),
                        "buy_imbalance": float(r["buy_imbalance"]),
                        "bid_velocity_bps": float(r["bid_velocity_bps"]),
                        "trade_accel": float(r["trade_accel"]),
                        "quote_accel": float(r["quote_accel"]),
                        "spread_bps": float(r["spread_bps"]),
                        "ask_depletion": float(r["ask_depletion"]),
                        "prev_imbalance": float(p["imbalance"]),
                        "prev_buy_imbalance": float(p["buy_imbalance"]),
                        "prev_micro_disp_bps": float(p["micro_disp_bps"]),
                    })
                    signal_trades.append(o)
                    if len(sample_signals) < 30:
                        sample_signals.append(o)

                # Baseline remains ordinary executable seconds in the same windows.
                for i in baseline_indices(df):
                    o = v1.outcome(df, i)
                    if o is not None:
                        baseline.append(o)

    signal_stats = v1.summarize(signal_trades)
    baseline_stats = v1.summarize(baseline)
    edge_pp = signal_stats["barrier_win_rate_pct"] - baseline_stats["barrier_win_rate_pct"]

    checks = {
        "sip_required_for_valid_pass": feed == "sip",
        "at_least_10_consensus_trades": signal_stats["trades"] >= 10,
        "barrier_win_rate_beats_baseline_by_8pp": edge_pp >= 8.0,
        "positive_avg_trade_after_spread_and_impact": signal_stats["avg_trade_pnl"] > 0,
        "profit_factor_at_least_1_25": signal_stats["profit_factor"] >= 1.25,
        "max_drawdown_at_most_3pct": signal_stats["max_drawdown_pct"] <= 3.0,
    }
    passed = all(checks.values())

    result = {
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "independent_from_v1_pilot_dates": True,
        "v1_development_dates_excluded": list(v1.PILOT_DATES),
        "test_dates": TEST_DATES,
        "feed_used": feed,
        "data_quality": data_quality,
        "universe": UNIVERSE,
        "windows_et": WINDOW_STARTS_ET,
        "locked_rules": {
            "architecture": "hard consensus; no additive-score compensation",
            "feature_seconds": FEATURE_SECONDS,
            "horizon_seconds": HORIZON_SECONDS,
            "profit_barrier_bps": PROFIT_BPS,
            "stop_barrier_bps": STOP_BPS,
            "impact_bps_per_side": IMPACT_BPS_PER_SIDE,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100,
            "cooldown_seconds": COOLDOWN_SECONDS,
            "requirements": {
                "quote_imbalance_ge": MIN_QUOTE_IMBALANCE,
                "microprice_displacement_bps_ge": MIN_MICRO_DISP_BPS,
                "signed_trade_buy_imbalance_ge": MIN_BUY_IMBALANCE,
                "bid_velocity_bps_ge": MIN_BID_VELOCITY_BPS,
                "trade_acceleration_ge": MIN_TRADE_ACCEL,
                "quote_acceleration_ge": MIN_QUOTE_ACCEL,
                "spread_bps_le": CONSENSUS_MAX_SPREAD_BPS,
                "prior_second_quote_imbalance_ge": PREV_MIN_QUOTE_IMBALANCE,
                "prior_second_buy_imbalance_ge": PREV_MIN_BUY_IMBALANCE,
                "prior_second_microprice_displacement_bps_ge": PREV_MIN_MICRO_DISP_BPS,
            },
        },
        "data_counts": data_counts,
        "windows_loaded": windows_loaded,
        "signal": signal_stats,
        "baseline": baseline_stats,
        "signal_minus_baseline_win_rate_pp": edge_pp,
        "checks": checks,
        "gate": "PASS" if passed else "FAIL",
        "activate": False,
        "sample_signal_trades": sample_signals,
    }

    with open("strategy2_experiment15_microburst_consensus_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 15 — MicroBurst Consensus v2",
        "",
        f"**Gate: {result['gate']}**",
        "",
        f"Feed: **{feed}** ({data_quality}) | untouched test dates: {', '.join(TEST_DATES)}",
        f"Windows loaded: {windows_loaded} | quotes/trades: {data_counts['quotes']:,} / {data_counts['trades']:,}",
        "",
        "## Signal",
        f"- Trades {signal_stats['trades']} | barrier wins {signal_stats['wins']} | losses {signal_stats['losses']} | timeouts {signal_stats['timeouts']}",
        f"- Barrier win rate {signal_stats['barrier_win_rate_pct']:.2f}% vs baseline {baseline_stats['barrier_win_rate_pct']:.2f}% | edge {edge_pp:+.2f}pp",
        f"- Ending equity ${signal_stats['ending_equity']:.2f} | return {signal_stats['return_pct']:+.3f}% | avg trade ${signal_stats['avg_trade_pnl']:+.3f}",
        f"- PF {signal_stats['profit_factor']:.3f} | DD {signal_stats['max_drawdown_pct']:.3f}% | avg return {signal_stats['avg_ret_bps']:+.2f}bps | avg hold {signal_stats['avg_hold_seconds']:.1f}s",
        "",
        "## Checks",
    ]
    for k, ok in checks.items():
        lines.append(f"- {'PASS' if ok else 'FAIL'} — {k}")
    lines += ["", "Activation remains OFF. A PASS would justify a larger walk-forward SIP test, not live trading."]
    with open("strategy2_experiment15_microburst_consensus_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
