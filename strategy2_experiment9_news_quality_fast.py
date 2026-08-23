from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

import strategy2_experiment9_news_quality as e9


def month_ranges(start, end):
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    out = []
    cur = s
    while cur <= e:
        nxt = min((cur + pd.offsets.MonthEnd(0)), e)
        out.append((cur.strftime('%Y-%m-%d'), nxt.strftime('%Y-%m-%d')))
        cur = nxt + pd.Timedelta(days=1)
    return out


def fetch_news_chunk(start, end):
    out, token = [], None
    while True:
        params = {
            'symbols': ','.join(e9.UNIVERSE),
            'start': start,
            'end': end,
            'sort': 'asc',
            'limit': 50,
            'include_content': 'false',
        }
        if token:
            params['page_token'] = token
        p = e9.get_json(f'{e9.DATA_BASE}/v1beta1/news', params)
        out.extend(p.get('news') or [])
        token = p.get('next_page_token')
        if not token:
            return out


def collect_events_parallel(start, end):
    items = []
    ranges = month_ranges(start, end)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(fetch_news_chunk, a, b) for a, b in ranges]
        for f in as_completed(futs):
            items.extend(f.result())

    normalized = []
    universe = set(e9.UNIVERSE)
    for item in items:
        created = pd.Timestamp(item.get('created_at') or item.get('updated_at'))
        if created.tzinfo is None:
            created = created.tz_localize('UTC')
        created = created.tz_convert(e9.ET)
        if not (e9.NEWS_START_ET <= created.time() <= e9.NEWS_END_ET):
            continue
        text = ' '.join([str(item.get('headline') or ''), str(item.get('summary') or '')])
        groups = e9.catalyst_groups(text)
        if not groups:
            continue
        syms = universe.intersection(str(x).upper() for x in (item.get('symbols') or []))
        for sym in syms:
            normalized.append({
                'symbol': sym,
                'created_et': created,
                'headline': str(item.get('headline') or ''),
                'source': str(item.get('source') or ''),
                'groups': groups,
            })

    normalized.sort(key=lambda x: (x['created_et'], x['symbol']))
    events, last_kept = [], {}
    for ev in normalized:
        prev = last_kept.get(ev['symbol'])
        if prev is not None and (ev['created_et'] - prev).total_seconds() < 3600:
            continue
        events.append(ev)
        last_kept[ev['symbol']] = ev['created_et']
    return events


def evaluate_period_parallel(start, end):
    events = collect_events_parallel(start, end)
    panels = {}
    symbols = e9.UNIVERSE + [e9.BENCHMARK]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(e9.fetch_symbol_bars, s, start, end): s for s in symbols}
        for f in as_completed(futs):
            panels[futs[f]] = f.result()

    results = {}
    for profile in e9.PROFILES:
        trades, used_days = [], set()
        funnel = {'eligible_news': len(events), 'hard_catalyst_reject': 0, 'reaction_reject': 0, 'relative_strength_reject': 0,
                  'relative_volume_reject': 0, 'vwap_reject': 0, 'score_reject': 0, 'structure_reject': 0, 'stop_reject': 0, 'qualified': 0}
        for event in events:
            day = event['created_et'].date().isoformat()
            if day in used_days:
                continue
            bars = e9.day_slice(panels[event['symbol']], day)
            spy = e9.day_slice(panels[e9.BENCHMARK], day)
            feat = e9.initial_features(event, bars, spy)
            if feat is None:
                continue
            conf, reason = e9.make_entry(event, feat, bars, profile)
            if conf is None:
                key = {'hard_catalyst':'hard_catalyst_reject', 'reaction':'reaction_reject', 'relative_strength':'relative_strength_reject',
                       'relative_volume':'relative_volume_reject', 'vwap':'vwap_reject', 'score':'score_reject', 'structure':'structure_reject', 'stop':'stop_reject'}.get(reason)
                if key:
                    funnel[key] += 1
                continue
            funnel['qualified'] += 1
            equity = e9.START_EQ + sum(t['pnl_dollars'] for t in trades)
            tr = e9.simulate_trade(event, conf, bars, equity)
            if tr:
                trades.append(tr)
                used_days.add(day)
        stats = e9.summarize(trades)
        stats['funnel'] = funnel
        stats['top_trades'] = sorted(trades, key=lambda x: x['pnl_dollars'], reverse=True)[:10]
        stats['bottom_trades'] = sorted(trades, key=lambda x: x['pnl_dollars'])[:10]
        results[profile] = stats
    return results


e9.collect_events = collect_events_parallel
e9.evaluate_period = evaluate_period_parallel

if __name__ == '__main__':
    e9.main()
