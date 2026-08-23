import strategy2_experiment13_opening_news_continuation as exp


def simulate_fixed(event, f, bars, equity):
    conf = {
        "entry_ts": f["entry_ts"],
        "entry_open": f["entry_open"],
        "initial_stop": f["initial_stop"],
        "score": f["score"],
        "reaction_pct": f["gap_atr"],
        "relative_strength_pct": f["relative_strength_pct"],
        "move_z": f["gap_atr"],
        "volume_z": f["opening_relvol"],
        "rs_z": f["relative_strength_pct"],
        "retrace_fraction": 1.0 - f["gap_retention"],
    }
    ev = dict(event)
    ev["groups"] = exp.base.catalyst_groups(event.get("headline", ""))
    ev["hard_catalyst"] = bool(set(ev["groups"]) & exp.base.HARD_GROUPS)
    tr = exp.base.simulate_trade(ev, conf, bars, equity)
    if tr:
        tr["event_type"] = event["event_type"]
        tr["gap_atr"] = f["gap_atr"]
        tr["opening_relvol"] = f["opening_relvol"]
        tr["gap_retention"] = f["gap_retention"]
    return tr


exp.simulate = simulate_fixed

if __name__ == "__main__":
    exp.main()
