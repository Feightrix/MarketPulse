import strategy2_experiment8_news_momentum_long as base


def collect_events_fast(start, end):
    out = []
    token = None
    while True:
        params = {
            "symbols": ",".join(base.UNIVERSE),
            "start": start,
            "end": end,
            "sort": "asc",
            "limit": 50,
            "include_content": "false",
        }
        if token:
            params["page_token"] = token
        payload = base.get_json(f"{base.DATA_BASE}/v1beta1/news", params)
        out.extend(payload.get("news") or [])
        token = payload.get("next_page_token")
        if not token:
            break

    events = []
    last_kept = {}
    universe = set(base.UNIVERSE)
    for item in out:
        item_symbols = universe.intersection(str(x).upper() for x in (item.get("symbols") or []))
        for symbol in sorted(item_symbols):
            ev = base.normalize_news_item(item, symbol)
            if not ev:
                continue
            prev = last_kept.get(symbol)
            if prev is not None and (ev["created_et"] - prev).total_seconds() < 60 * 60:
                continue
            events.append(ev)
            last_kept[symbol] = ev["created_et"]
    events.sort(key=lambda x: x["created_et"])
    return events


base.collect_events = collect_events_fast

if __name__ == "__main__":
    base.main()
