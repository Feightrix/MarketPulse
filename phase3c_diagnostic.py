import json
import numpy as np
import pandas as pd
import phase3c_regime_backtest as b


def main():
    data = b.load_data()
    regimes, selections = b.daily_regimes(data)
    dev = b.pack_period(data, regimes, selections, "2021-01-01", "2023-12-31")
    out = {
        "raw_regime_true_days": {}, "raw_selection_counts": {}, "mapping_samples": {},
        "regime_true_bars": {}, "regime_true_days": {}, "selection_counts": {},
        "signal_counts": {}, "intersections": {}
    }

    for name, series in regimes.items():
        out["raw_regime_true_days"][name] = int(series.fillna(False).sum())
    for lb, mapping in selections.items():
        counts = {s: sum(v == s for v in mapping.values()) for s in b.SYMBOLS}
        counts["none"] = sum(v is None for v in mapping.values())
        out["raw_selection_counts"][str(lb)] = counts

    first_regime_key = next(iter(regimes.values())).index[0]
    first_selection_key = next(iter(selections.values())).keys().__iter__().__next__()
    first_pack_ns = int(dev["day_ns"][0])
    out["mapping_samples"] = {
        "regime_key_str": str(first_regime_key),
        "regime_key_ns": int(pd.Timestamp(first_regime_key).value),
        "selection_key_str": str(first_selection_key),
        "selection_key_ns": int(pd.Timestamp(first_selection_key).value),
        "pack_day_ns": first_pack_ns,
        "pack_day_as_utc": str(pd.Timestamp(first_pack_ns, tz="UTC")),
        "regime_key_matches_pack_first": int(pd.Timestamp(first_regime_key).value) == first_pack_ns,
        "selection_key_matches_pack_first": int(pd.Timestamp(first_selection_key).value) == first_pack_ns,
    }

    unique_days = dev["day_ns"]
    day_first = np.r_[True, unique_days[1:] != unique_days[:-1]]

    for name, arr in dev["regimes"].items():
        out["regime_true_bars"][name] = int(arr.sum())
        out["regime_true_days"][name] = int((arr & day_first).sum())

    for lb, arr in dev["selections"].items():
        counts = {s: int(((arr == s) & day_first).sum()) for s in b.SYMBOLS}
        counts["none"] = int(((arr == None) & day_first).sum())  # noqa: E711
        out["selection_counts"][str(lb)] = counts

    for s in b.SYMBOLS:
        out["signal_counts"][s] = {}
        for entry, arr in dev["symbols"][s]["signals"].items():
            out["signal_counts"][s][entry] = int(arr.sum())

    for rname, rarr in dev["regimes"].items():
        out["intersections"][rname] = {}
        for lb, sel in dev["selections"].items():
            out["intersections"][rname][str(lb)] = {}
            for entry in dev["entry_names"]:
                n = 0
                for s in b.SYMBOLS:
                    sig = dev["symbols"][s]["signals"][entry]
                    n += int((rarr & (sel == s) & sig).sum())
                out["intersections"][rname][str(lb)][entry] = n

    with open("phase3c_diagnostics.json", "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2), flush=True)


if __name__ == "__main__":
    main()
