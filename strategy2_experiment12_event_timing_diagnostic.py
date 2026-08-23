import json
from collections import Counter

import pandas as pd

import strategy2_experiment10_entity_abnormal_news as base
import strategy2_experiment12_primary_event_news as exp


def bucket(t):
    m = t.hour * 60 + t.minute
    if 4*60 <= m < 9*60+30:
        return "premarket_0400_0930"
    if 9*60+30 <= m < 10*60:
        return "opening_0930_1000"
    if 10*60 <= m < 15*60:
        return "intraday_1000_1500"
    if 15*60 <= m < 16*60:
        return "late_1500_1600"
    if 16*60 <= m < 20*60:
        return "afterhours_1600_2000"
    return "other"


def main():
    items = []
    for a, b in base.month_chunks("2024-01-01", "2024-12-31"):
        items.extend(base.fetch_news_chunk(a, b))

    aliases = base.COMPANY_ALIASES
    universe = set(base.UNIVERSE)
    counts = Counter()
    event_types = Counter()
    examples = {k: [] for k in ["premarket_0400_0930","opening_0930_1000","intraday_1000_1500","late_1500_1600","afterhours_1600_2000","other"]}
    seen = set()
    for item in sorted(items, key=lambda x: str(x.get("created_at") or x.get("updated_at") or "")):
        created_raw = item.get("created_at") or item.get("updated_at")
        if not created_raw:
            continue
        ts = pd.Timestamp(created_raw)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert(base.ET)
        headline = str(item.get("headline") or "")
        syms = universe.intersection(str(x).upper() for x in (item.get("symbols") or []))
        for sym in syms:
            if not base.headline_names_company(headline, sym):
                continue
            event_type, _ = exp.classify_primary_event(headline)
            if event_type is None:
                continue
            key = (sym, str(item.get("id") or ""), event_type)
            if key in seen:
                continue
            seen.add(key)
            bkt = bucket(ts)
            counts[bkt] += 1
            event_types[event_type] += 1
            if len(examples[bkt]) < 8:
                examples[bkt].append({"symbol": sym, "time_et": ts.isoformat(), "event_type": event_type, "headline": headline})

    result = {
        "diagnostic": "S2-E12-PRIMARY-EVENT-TIMING-2024",
        "research_only": True,
        "total_primary_events": int(sum(counts.values())),
        "time_buckets": dict(counts),
        "event_types": dict(event_types),
        "examples": examples,
    }
    with open("strategy2_experiment12_event_timing_diagnostic.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
