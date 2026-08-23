import json
import math
import strategy2_experiment9_news_quality as e9
import strategy2_experiment9_news_quality_fast as fast

e9.collect_events = fast.collect_events_parallel
e9.evaluate_period = fast.evaluate_period_parallel

dev = e9.evaluate_period('2024-01-01', '2024-12-31')
eligibility = {}
for name, s in dev.items():
    checks = {
        'trades_at_least_5': s['trades'] >= 5,
        'positive_expectancy': s['avg_trade_pnl_dollars'] > 0,
        'profit_factor_at_least_1_20': s['profit_factor'] >= 1.20,
        'winner_loser_at_least_1_25': s['avg_winner_to_loser'] >= 1.25,
        'drawdown_at_most_5pct': s['max_drawdown_pct'] <= 5.0,
    }
    eligibility[name] = {
        'checks': checks,
        'eligible': all(checks.values()),
        'selection_score': s['avg_r_multiple'] * math.sqrt(max(s['trades'], 1)),
    }

out = {
    'experiment': 'S2-E9-DEV-GATE-CHECK',
    'research_only': True,
    'period': ['2024-01-01', '2024-12-31'],
    'rules': 'identical to Experiment 9',
    'development_2024': dev,
    'development_eligibility': eligibility,
    'any_profile_eligible': any(v['eligible'] for v in eligibility.values()),
}
with open('strategy2_experiment9_devcheck.json', 'w') as f:
    json.dump(out, f, indent=2)
print(json.dumps({k: {'trades': v['trades'], 'return': v['total_return_pct'], 'pf': v['profit_factor'], 'avg_trade': v['avg_trade_pnl_dollars'], 'wl': v['avg_winner_to_loser'], 'dd': v['max_drawdown_pct']} for k, v in dev.items()}, indent=2))
