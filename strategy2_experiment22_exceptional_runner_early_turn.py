import json

import numpy as np
import pandas as pd

import strategy2_experiment20_exceptional_runner as e20
import strategy2_experiment18_volatile_leader_trendday as e18
import strategy2_experiment17b_volatile_5m_pattern_scan as base

EXPERIMENT = "S2-E22-EXCEPTIONAL-RUNNER-EARLY-TURN"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_2026_DEVELOPMENT = True

CANDIDATES = list(e20.CANDIDATES)
BLOCKS = dict(e20.BLOCKS)

# Experiment 20's exceptional-runner gate remains unchanged.
# The only research change is entry timing: detect the turn inside the pullback
# before an obvious breakout/re-ignition is complete.
SCAN_END = pd.Timestamp("11:00").time()
MIN_PULLBACK_FRACTION = 0.10
MAX_PULLBACK_FRACTION = 0.45
PULLBACK_VOLUME_CONTRACTION_MAX = 0.85
BUYER_VOLUME_REEXPANSION_MIN = 1.10
MIN_TURN_CLOSE_LOCATION = 0.70
VWAP_TOLERANCE = 0.0025


def session_vwap(g):
    px = g["vw"].astype(float) if "vw" in g.columns else g["c"].astype(float)
    vol = g["v"].astype(float)
    den = vol.cumsum().replace(0, np.nan)
    return (px * vol).cumsum() / den


def find_early_turn(g, original_sig):
    """Causal 5-minute early-turn detector after the 09:55 exceptional gate."""
    opening = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(e18.OPEN_END).time())]
    if len(opening) < 6:
        return None

    morning_open = float(opening.iloc[0]["o"])
    opening_high = float(opening["h"].max())
    impulse = opening_high - morning_open
    if impulse <= 0:
        return None

    x = g.copy()
    x["vwap_live"] = session_vwap(x)
    x["range"] = (x["h"] - x["l"]).astype(float)
    x["close_location"] = np.where(x["range"] > 0, (x["c"] - x["l"]) / x["range"], 0.0)

    after = x[(x.index > original_sig["signal_ts"]) & (x.index.time <= SCAN_END)]
    if len(after) < 4:
        return None

    first_two_volume = float(after.iloc[:2]["v"].mean())
    if not np.isfinite(first_two_volume) or first_two_volume <= 0:
        return None

    pullback_seen = False
    pullback_low = None
    pullback_fraction = None
    pullback_time = None

    for i in range(len(after)):
        r = after.iloc[i]
        lo = float(r["l"])
        frac = max(0.0, (opening_high - lo) / impulse)

        if not pullback_seen:
            if MIN_PULLBACK_FRACTION <= frac <= MAX_PULLBACK_FRACTION and lo > morning_open:
                pullback_seen = True
                pullback_low = lo
                pullback_fraction = frac
                pullback_time = after.index[i]
            continue

        pullback_low = min(float(pullback_low), lo)
        pullback_fraction = (opening_high - pullback_low) / impulse
        if pullback_fraction > MAX_PULLBACK_FRACTION or pullback_low <= morning_open:
            return None
        if i < 2:
            continue

        prev1 = after.iloc[i - 1]
        prev2 = after.iloc[i - 2]
        pullback_vol = float(np.mean([prev1["v"], prev2["v"]]))
        volume_contracted = pullback_vol <= first_two_volume * PULLBACK_VOLUME_CONTRACTION_MAX

        current_volume = float(r["v"])
        buyer_volume_returning = current_volume >= max(1.0, pullback_vol * BUYER_VOLUME_REEXPANSION_MIN)
        green = float(r["c"]) > float(r["o"])
        higher_low = float(r["l"]) > float(prev1["l"])
        higher_close = float(r["c"]) > float(prev1["c"])
        close_location = float(r["close_location"])
        vwap_live = float(r["vwap_live"])
        vwap_supported = float(r["c"]) >= vwap_live and float(r["l"]) >= vwap_live * (1.0 - VWAP_TOLERANCE)
        still_before_obvious_breakout = float(r["c"]) <= opening_high

        if (
            volume_contracted
            and buyer_volume_returning
            and green
            and higher_low
            and higher_close
            and close_location >= MIN_TURN_CLOSE_LOCATION
            and vwap_supported
            and still_before_obvious_breakout
        ):
            return {
                "signal_ts": after.index[i],
                "pullback_time": pullback_time,
                "pullback_low": float(pullback_low),
                "pullback_fraction": float(pullback_fraction),
                "turn_close": float(r["c"]),
                "turn_low": float(r["l"]),
                "turn_close_location": close_location,
                "turn_volume": current_volume,
                "pullback_volume_avg": pullback_vol,
                "volume_contraction_ratio": pullback_vol / first_two_volume,
                "buyer_reexpansion_ratio": current_volume / pullback_vol if pullback_vol > 0 else 0.0,
                "turn_vwap": vwap_live,
                "opening_high": opening_high,
            }
    return None


def evaluate(start, end, panels, dstats, ostats, otables):
    trades, gate_days, turn_setups = [], [], []
    all_days = [d for d in dstats["SPY"].index if pd.Timestamp(start).date() <= d <= pd.Timestamp(end).date()]

    for day in all_days:
        snap = e20.exceptional_snapshot(day, panels, dstats, otables)
        if snap is None or not snap["passes"]:
            continue
        gate_days.append({"day": str(day), **snap})

        sym = snap["leader"]["symbol"]
        original_sig = e18.candidate_signal(sym, day, panels, dstats, ostats)
        if original_sig is None:
            continue
        g = base.day_slice(panels[sym], day)
        turn = find_early_turn(g, original_sig)
        if turn is None:
            continue

        turn_setups.append({
            "day": str(day),
            "symbol": sym,
            "turn_time": turn["signal_ts"].isoformat(),
            "pullback_fraction": turn["pullback_fraction"],
            "volume_contraction_ratio": turn["volume_contraction_ratio"],
            "buyer_reexpansion_ratio": turn["buyer_reexpansion_ratio"],
        })

        sig = dict(original_sig)
        sig["signal_ts"] = turn["signal_ts"]
        equity = e18.START_EQ + sum(float(t["pnl"]) for t in trades)
        tr = e18.simulate(sig, g, equity)
        if tr:
            tr["exceptional_gate"] = {
                "return_percentile": snap["leader"]["return_percentile"],
                "volume_percentile": snap["leader"]["volume_percentile"],
                "range_atr_percentile": snap["leader"]["range_atr_percentile"],
                "leader_separation_pct": snap["leader_separation_pct"],
                "relative_strength_pct": snap["leader"]["relative_strength"] * 100.0,
            }
            tr["early_turn"] = {
                "pullback_fraction": turn["pullback_fraction"],
                "pullback_low": turn["pullback_low"],
                "pullback_time": turn["pullback_time"].isoformat(),
                "turn_close": turn["turn_close"],
                "turn_close_location": turn["turn_close_location"],
                "volume_contraction_ratio": turn["volume_contraction_ratio"],
                "buyer_reexpansion_ratio": turn["buyer_reexpansion_ratio"],
                "turn_vwap": turn["turn_vwap"],
                "opening_high": turn["opening_high"],
            }
            trades.append(tr)

    stats = e18.summarize(trades, start, end)
    stats["gate_days"] = len(gate_days)
    stats["early_turn_setups"] = len(turn_setups)
    stats["business_days"] = len(all_days)
    stats["turn_from_gate_pct"] = len(turn_setups) / len(gate_days) * 100.0 if gate_days else 0.0
    return trades, gate_days, turn_setups, stats


def main():
    panels = base.load_panels()
    missing = [s for s in base.CANDIDATES + base.BENCHMARKS if s not in panels or panels[s].empty]
    if missing:
        raise RuntimeError(f"Missing SIP data: {missing}")

    dstats = {s: base.daily_stats(panels[s]) for s in base.CANDIDATES + base.BENCHMARKS}
    ostats = {s: e18.opening_stats(panels[s]) for s in CANDIDATES}
    otables = {s: e20.opening_table(panels[s], dstats[s]) for s in CANDIDATES}

    blocks = {}
    for name, (a, b) in BLOCKS.items():
        trades, gate_days, setups, stats = evaluate(a, b, panels, dstats, ostats, otables)
        blocks[name] = {"stats": stats, "trades": trades, "gate_days": gate_days, "early_turn_setups": setups}

    result = {
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "nonblind_2026_development": True,
        "exceptional_gate": "Experiment 20 unchanged",
        "locked_entry": {
            "scan_end_et": "11:00",
            "min_pullback_fraction": MIN_PULLBACK_FRACTION,
            "max_pullback_fraction": MAX_PULLBACK_FRACTION,
            "pullback_volume_contraction_max": PULLBACK_VOLUME_CONTRACTION_MAX,
            "buyer_volume_reexpansion_min": BUYER_VOLUME_REEXPANSION_MIN,
            "min_turn_close_location": MIN_TURN_CLOSE_LOCATION,
            "vwap_tolerance": VWAP_TOLERANCE,
            "requires_green_turn_bar": True,
            "requires_higher_low": True,
            "requires_higher_close": True,
            "requires_close_below_or_at_opening_high": True,
            "entry": "next 5-minute bar open after early-turn signal",
        },
        "execution": {
            "risk_per_trade_pct": e18.RISK_PER_TRADE * 100.0,
            "max_notional_pct": e18.MAX_NOTIONAL_PCT * 100.0,
            "cost_bps_per_fill": e18.COST_BPS_PER_FILL,
            "leverage": False,
            "stop_and_trailing": "Experiment 18 unchanged at early-turn entry",
        },
        "blocks": blocks,
        "activate": False,
        "promotion_rule": "2026 results are development/debug only; promotion requires new forward shadow evidence",
    }
    with open("strategy2_experiment22_exceptional_runner_early_turn_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 22 — Exceptional Runner Early-Turn Detector",
        "",
        "Research-only, nonblind 2026 development/debug. Experiment 20's exceptional-runner gate is unchanged; entry moves earlier into the pullback turn rather than waiting for an obvious breakout.",
        "",
        "Entry: 10–45% pullback that holds above the morning open; pullback volume contracts; then a green 5-minute bar makes a higher low and higher close, finishes in the upper 30% of its range, holds/reclaims live VWAP, buyer volume re-expands, and the close is still at/below the opening high. Enter next bar.",
        "",
    ]
    for name in BLOCKS:
        s = blocks[name]["stats"]
        lines.append(
            f"- {name}: gate days {s['gate_days']} | early turns {s['early_turn_setups']} ({s['turn_from_gate_pct']:.1f}% of gates) | "
            f"trades {s['trades']} | win {s['win_rate_pct']:.1f}% | PF {s['profit_factor']:.2f} | return {s['return_pct']:+.2f}% | "
            f"DD {s['max_drawdown_pct']:.2f}% | avg trade-day {s['avg_return_on_trade_days_pct']:+.2f}% | >=1% days {s['days_ge_1pct']} | >=3% days {s['days_ge_3pct']}"
        )
    lines += ["", "Activation OFF. 2026 is not independent evidence. The next decision is based on forward shadow sessions only."]
    with open("strategy2_experiment22_exceptional_runner_early_turn_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
