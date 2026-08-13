import numpy as np
import pandas as pd

import phase3c_regime_backtest as b


def pack_period_fixed(data, regimes, selections, start, end):
    """Use YYYYMMDD integer keys so pandas datetime storage units cannot break joins."""
    st = pd.Timestamp(start, tz=b.TZ)
    en = pd.Timestamp(end, tz=b.TZ) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    frames = {s: d[(d.index >= st) & (d.index <= en)].copy() for s, d in data.items()}
    idx = frames[b.SYMBOLS[0]].index
    for s in b.SYMBOLS[1:]:
        idx = idx.union(frames[s].index)
    idx = idx.sort_values()

    day_ids = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy(dtype=np.int32)
    months = (idx.year * 100 + idx.month).to_numpy(dtype=np.int32)
    pack = {
        "ts_ns": np.arange(len(idx), dtype=np.int64),
        "day_ns": day_ids,
        "month": months,
        "minute": (idx.hour * 60 + idx.minute).to_numpy(dtype=np.int16),
        "symbols": {}, "regimes": {}, "selections": {},
    }

    for name, series in regimes.items():
        mapping = {
            int(pd.Timestamp(k).year * 10000 + pd.Timestamp(k).month * 100 + pd.Timestamp(k).day): bool(v)
            for k, v in series.items()
        }
        pack["regimes"][name] = np.array([mapping.get(int(x), False) for x in day_ids], dtype=bool)

    for lb, mapping0 in selections.items():
        mapping = {
            int(pd.Timestamp(k).year * 10000 + pd.Timestamp(k).month * 100 + pd.Timestamp(k).day): v
            for k, v in mapping0.items()
        }
        pack["selections"][lb] = np.array([mapping.get(int(x), None) for x in day_ids], dtype=object)

    for s in b.SYMBOLS:
        d = frames[s]
        a = d.reindex(idx)
        sig = b.entry_signals(d)
        pack["symbols"][s] = {
            "valid": a.open.notna().to_numpy(),
            "open": a.open.to_numpy(float),
            "high": a.high.to_numpy(float),
            "low": a.low.to_numpy(float),
            "close": a.close.to_numpy(float),
            "atr_pct": a.atr_pct.to_numpy(float),
            "quality": b.quality_score(d).reindex(idx).to_numpy(float),
            "signals": {k: v.reindex(idx, fill_value=False).to_numpy(bool) for k, v in sig.items()},
        }
    pack["entry_names"] = ["breakout6", "vwap_reclaim", "pullback9"]
    pack["months_all"] = sorted(set(int(x) for x in months))
    return pack


# Apply the mapping fix before the corrected Phase 3C evaluator runs.
b.pack_period = pack_period_fixed

import phase3c_regime_backtest_v2 as evaluator  # noqa: E402


if __name__ == "__main__":
    evaluator.main()
