import json

import numpy as np

import strategy2_experiment14_microburst_long as v1

EXPERIMENT = "S2-E16-MICROBURST-CORE-INTENSITY-V3"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False

# Untouched relative to v1 (Aug 19-21) and v2 (Aug 12-18).
TEST_DATES = ["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11"]
UNIVERSE = list(v1.UNIVERSE)
WINDOW_STARTS_ET = list(v1.WINDOW_STARTS_ET)

# Execution/outcome protocol unchanged from v1/v2.
FEATURE_SECONDS = v1.FEATURE_SECONDS
HORIZON_SECONDS = v1.HORIZON_SECONDS
PROFIT_BPS = v1.PROFIT_BPS
STOP_BPS = v1.STOP_BPS
IMPACT_BPS_PER_SIDE = v1.IMPACT_BPS_PER_SIDE
MAX_NOTIONAL_PCT = v1.MAX_NOTIONAL_PCT
COOLDOWN_SECONDS = v1.COOLDOWN_SECONDS
MAX_SPREAD_BPS = v1.MAX_SPREAD_BPS

# Mandatory directional core.
MIN_QUOTE_IMBALANCE = 0.35
MIN_MICRO_DISP_BPS = 0.15
MIN_BUY_IMBALANCE = 0.35
MIN_BID_VELOCITY_BPS = 0.0
CORE_MAX_SPREAD_BPS = 3.0

# At least one intensity confirmation required.
MIN_TRADE_ACCEL = 1.20
MIN_QUOTE_ACCEL = 1.00
MIN_ASK_DEPLETION = 0.20
MIN_INTENSITY_CONFIRMATIONS = 1


def finite_ge(x, threshold):
    return np.isfinite(x) and float(x) >= float(threshold)


def signal_pass(df, i):
    r = df.iloc[i]
    core = [
        finite_ge(r["imbalance"], MIN_QUOTE_IMBALANCE),
        finite_ge(r["micro_disp_bps"], MIN_MICRO_DISP_BPS),
        finite_ge(r["buy_imbalance"], MIN_BUY_IMBALANCE),
        finite_ge(r["bid_velocity_bps"], MIN_BID_VELOCITY_BPS),
        np.isfinite(r["spread_bps"]) and float(r["spread_bps"]) <= CORE_MAX_SPREAD_BPS,
    ]
    if not all(core):
        return False
    intensity = [
        finite_ge(r["trade_accel"], MIN_TRADE_ACCEL),
        finite_ge(r["quote_accel"], MIN_QUOTE_ACCEL),
        finite_ge(r["ask_depletion"], MIN_ASK_DEPLETION),
    ]
    return sum(bool(x) for x in intensity) >= MIN_INTENSITY_CONFIRMATIONS


def candidate_indices(df):
    out = []
    last = -10**9
    start = max(FEATURE_SECONDS, 20)
    for i in range(start, len(df) - HORIZON_SECONDS - 1):
        if not signal_pass(df, i):
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
    signal_trades, baseline = [], []
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
                    intensity_count = int(finite_ge(r["trade_accel"], MIN_TRADE_ACCEL)) + int(finite_ge(r["quote_accel"], MIN_QUOTE_ACCEL)) + int(finite_ge(r["ask_depletion"], MIN_ASK_DEPLETION))
                    o.update({
                        "symbol": symbol,
                        "day": day,
                        "time": str(df.index[i]),
                        "imbalance": float(r["imbalance"]),
                        "micro_disp_bps": float(r["micro_disp_bps"]),
                        "buy_imbalance": float(r["buy_imbalance"]),
                        "bid_velocity_bps": float(r["bid_velocity_bps"]),
                        "trade_accel": float(r["trade_accel"]) if np.isfinite(r["trade_accel"]) else 0.0,
                        "quote_accel": float(r["quote_accel"]) if np.isfinite(r["quote_accel"]) else 0.0,
                        "ask_depletion": float(r["ask_depletion"]),
                        "spread_bps": float(r["spread_bps"]),
                        "intensity_confirmations": intensity_count,
                    })
                    signal_trades.append(o)
                    if len(sample_signals) < 40:
                        sample_signals.append(o)

                for i in baseline_indices(df):
                    o = v1.outcome(df, i)
                    if o is not None:
                        baseline.append(o)

    signal_stats = v1.summarize(signal_trades)
    baseline_stats = v1.summarize(baseline)
    edge_pp = signal_stats["barrier_win_rate_pct"] - baseline_stats["barrier_win_rate_pct"]

    checks = {
        "sip_required_for_valid_pass": feed == "sip",
        "at_least_10_signal_trades": signal_stats["trades"] >= 10,
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
        "independent_test_dates": True,
        "excluded_prior_test_dates": list(v1.PILOT_DATES) + ["2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17", "2026-08-18"],
        "test_dates": TEST_DATES,
        "feed_used": feed,
        "data_quality": data_quality,
        "universe": UNIVERSE,
        "windows_et": WINDOW_STARTS_ET,
        "locked_rules": {
            "architecture": "mandatory directional core plus >=1 intensity confirmation",
            "feature_seconds": FEATURE_SECONDS,
            "horizon_seconds": HORIZON_SECONDS,
            "profit_barrier_bps": PROFIT_BPS,
            "stop_barrier_bps": STOP_BPS,
            "impact_bps_per_side": IMPACT_BPS_PER_SIDE,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100,
            "cooldown_seconds": COOLDOWN_SECONDS,
            "mandatory_core": {
                "quote_imbalance_ge": MIN_QUOTE_IMBALANCE,
                "microprice_displacement_bps_ge": MIN_MICRO_DISP_BPS,
                "signed_trade_buy_imbalance_ge": MIN_BUY_IMBALANCE,
                "bid_velocity_bps_ge": MIN_BID_VELOCITY_BPS,
                "spread_bps_le": CORE_MAX_SPREAD_BPS,
            },
            "intensity_any_of": {
                "trade_acceleration_ge": MIN_TRADE_ACCEL,
                "quote_acceleration_ge": MIN_QUOTE_ACCEL,
                "ask_depletion_ge": MIN_ASK_DEPLETION,
                "minimum_confirmations": MIN_INTENSITY_CONFIRMATIONS,
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

    with open("strategy2_experiment16_microburst_core_intensity_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 16 — MicroBurst Core Consensus + Intensity v3",
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
    lines += ["", "Activation remains OFF. A PASS only justifies a larger walk-forward SIP test."]
    with open("strategy2_experiment16_microburst_core_intensity_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
