import json

import numpy as np
import pandas as pd

import strategy2_experiment20_exceptional_runner as e20
import strategy2_experiment18_volatile_leader_trendday as e18
import strategy2_experiment17b_volatile_5m_pattern_scan as base

EXPERIMENT = "S2-E21-EXCEPTIONAL-RUNNER-REIGNITION"
RESEARCH_ONLY = True
BROKER_ORDERS = False
LONG_ONLY = True
LEVERAGE = False
NONBLIND_FOLLOWUP = True

CANDIDATES = list(e20.CANDIDATES)
BLOCKS = dict(e20.BLOCKS)

# Experiment 20 exceptional-runner gate is unchanged.
# Only the entry is changed: wait for a shallow pullback, then re-ignition.
SCAN_END = pd.Timestamp("11:00").time()
MIN_PULLBACK_FRACTION = 0.10
MAX_PULLBACK_FRACTION = 0.45
MIN_REIGNITION_RELVOL = 1.15
REIGNITION_LOOKBACK_BARS = 2


def session_vwap(g):
    px = g["vw"].astype(float) if "vw" in g.columns else g["c"].astype(float)
    vol = g["v"].astype(float)
    den = vol.cumsum().replace(0, np.nan)
    return (px * vol).cumsum() / den


def find_reignition(g, original_sig):
    """Use only bars known sequentially after the 09:55 signal."""
    opening = g[(g.index.time >= pd.Timestamp("09:30").time()) & (g.index.time <= pd.Timestamp(e18.OPEN_END).time())]
    if len(opening) < 6:
        return None

    op = float(opening.iloc[0]["o"])
    opening_high = float(opening["h"].max())
    impulse = opening_high - op
    if impulse <= 0:
        return None

    x = g.copy()
    x["vwap_live"] = session_vwap(x)
    x["vol_med4"] = x["v"].rolling(4, min_periods=2).median().shift(1)
    x["relvol_live"] = x["v"] / x["vol_med4"].replace(0, np.nan)

    after = x[(x.index > original_sig["signal_ts"]) & (x.index.time <= SCAN_END)]
    if len(after) < REIGNITION_LOOKBACK_BARS + 2:
        return None

    pullback_seen = False
    pullback_low = None
    pullback_fraction = None
    pullback_time = None

    for i in range(len(after)):
        r = after.iloc[i]
        lo = float(r["l"])

        # Track the first valid shallow pullback using only information available
        # up to the current bar. The pullback may deepen, but must remain valid.
        frac = max(0.0, (opening_high - lo) / impulse)
        if not pullback_seen:
            if MIN_PULLBACK_FRACTION <= frac <= MAX_PULLBACK_FRACTION and lo > op:
                pullback_seen = True
                pullback_low = lo
                pullback_fraction = frac
                pullback_time = after.index[i]
            continue

        pullback_low = min(float(pullback_low), lo)
        pullback_fraction = (opening_high - pullback_low) / impulse
        if pullback_fraction > MAX_PULLBACK_FRACTION or pullback_low <= op:
            return None

        if i < REIGNITION_LOOKBACK_BARS:
            continue
        prev = after.iloc[i-REIGNITION_LOOKBACK_BARS:i]
        trigger_level = float(prev["h"].max())
        close = float(r["c"])
        live_vwap = float(r["vwap_live"])
        relvol = float(r["relvol_live"]) if np.isfinite(r["relvol_live"]) else 0.0

        if close > trigger_level and close > live_vwap and relvol >= MIN_REIGNITION_RELVOL:
            return {
                "signal_ts": after.index[i],
                "pullback_time": pullback_time,
                "pullback_low": float(pullback_low),
                "pullback_fraction": float(pullback_fraction),
                "reignition_close": close,
                "trigger_level": trigger_level,
                "reignition_relvol": relvol,
                "reignition_vwap": live_vwap,
            }
    return None


def evaluate(start, end, panels, dstats, ostats, otables):
    trades, gate_days, reignition_setups = [], [], []
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
        re = find_reignition(g, original_sig)
        if re is None:
            continue

        reignition_setups.append({
            "day": str(day), "symbol": sym,
            "pullback_fraction": re["pullback_fraction"],
            "reignition_time": re["signal_ts"].isoformat(),
            "reignition_relvol": re["reignition_relvol"],
        })

        sig = dict(original_sig)
        sig["signal_ts"] = re["signal_ts"]
        # Let the unchanged E18 simulator use its recent-three-bar structural
        # stop at the later re-ignition entry. Preserve the original opening low.
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
            tr["reignition"] = {
                "pullback_fraction": re["pullback_fraction"],
                "pullback_low": re["pullback_low"],
                "pullback_time": re["pullback_time"].isoformat(),
                "trigger_level": re["trigger_level"],
                "reignition_close": re["reignition_close"],
                "reignition_relvol": re["reignition_relvol"],
            }
            trades.append(tr)

    stats = e18.summarize(trades, start, end)
    stats["gate_days"] = len(gate_days)
    stats["reignition_setups"] = len(reignition_setups)
    stats["business_days"] = len(all_days)
    stats["gate_frequency_pct"] = len(gate_days) / len(all_days) * 100.0 if all_days else 0.0
    stats["reignition_from_gate_pct"] = len(reignition_setups) / len(gate_days) * 100.0 if gate_days else 0.0
    return trades, gate_days, reignition_setups, stats


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
        blocks[name] = {"stats": stats, "trades": trades, "gate_days": gate_days, "reignition_setups": setups}

    result = {
        "experiment": EXPERIMENT,
        "research_only": True,
        "broker_orders": False,
        "long_only": True,
        "leverage": False,
        "nonblind_followup": True,
        "exceptional_gate": "Experiment 20 unchanged",
        "locked_entry": {
            "scan_end_et": "11:00",
            "min_pullback_fraction": MIN_PULLBACK_FRACTION,
            "max_pullback_fraction": MAX_PULLBACK_FRACTION,
            "must_hold_above_morning_open": True,
            "reignition_close_above_prior_bars": REIGNITION_LOOKBACK_BARS,
            "min_reignition_relvol": MIN_REIGNITION_RELVOL,
            "must_close_above_live_vwap": True,
            "entry": "next 5-minute bar open after re-ignition",
        },
        "execution": {
            "risk_per_trade_pct": e18.RISK_PER_TRADE * 100.0,
            "max_notional_pct": e18.MAX_NOTIONAL_PCT * 100.0,
            "cost_bps_per_fill": e18.COST_BPS_PER_FILL,
            "leverage": False,
            "stop_and_trailing": "Experiment 18 unchanged at later entry",
        },
        "blocks": blocks,
        "activate": False,
    }

    with open("strategy2_experiment21_exceptional_runner_reignition_results.json", "w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Experiment 21 — Exceptional Runner + Shallow Pullback + Re-ignition",
        "",
        "Nonblind research-only follow-up. Experiment 20's rare exceptional-runner gate is unchanged; only the entry waits for a shallow pullback and renewed strength.",
        "",
        "Entry: pull back 10–45% of the opening impulse while staying above the morning open; then a 5-minute close above the prior two bars' highs, above live VWAP, with >=1.15x recent volume; enter next bar.",
        "",
    ]
    for name in BLOCKS:
        s = blocks[name]["stats"]
        lines.append(
            f"- {name}: gate days {s['gate_days']} | re-ignitions {s['reignition_setups']} ({s['reignition_from_gate_pct']:.1f}% of gates) | "
            f"trades {s['trades']} | win {s['win_rate_pct']:.1f}% | PF {s['profit_factor']:.2f} | return {s['return_pct']:+.2f}% | "
            f"DD {s['max_drawdown_pct']:.2f}% | avg trade-day {s['avg_return_on_trade_days_pct']:+.2f}% | >=1% days {s['days_ge_1pct']} | >=3% days {s['days_ge_3pct']}"
        )
    lines += ["", "Activation OFF. 2026 is nonblind development evidence; forward shadow confirmation is required before any aggressive sizing."]
    with open("strategy2_experiment21_exceptional_runner_reignition_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
