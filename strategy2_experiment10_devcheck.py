import json
import strategy2_experiment10_entity_abnormal_news as e10


def main():
    stats = e10.evaluate_period("2024-01-01", "2024-12-31")
    checks = {
        "development_trades_at_least_5": stats["trades"] >= 5,
        "development_positive_expectancy": stats["avg_trade_pnl_dollars"] > 0,
        "development_profit_factor_at_least_1_20": stats["profit_factor"] >= 1.20,
        "development_winner_loser_at_least_1_25": stats["avg_winner_to_loser"] >= 1.25,
        "development_drawdown_at_most_5pct": stats["max_drawdown_pct"] <= 5.0,
    }
    result = {
        "experiment": "S2-E10-DEVELOPMENT-GATE-CHECK",
        "research_only": True,
        "period": ["2024-01-01", "2024-12-31"],
        "rules": "identical to Experiment 10",
        "stats": stats,
        "checks": checks,
        "development_gate": "PASS" if all(checks.values()) else "FAIL",
    }
    with open("strategy2_experiment10_devcheck.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
