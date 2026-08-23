import json

import strategy2_experiment12_primary_event_news as exp


def main():
    dev = exp.base.evaluate_period("2024-01-01", "2024-12-31")
    checks = exp.checks_for(dev, "development", 1.20)
    result = {
        "experiment": "S2-E12-DEVELOPMENT-GATE-CHECK",
        "research_only": True,
        "rules": "identical to Experiment 12",
        "period": ["2024-01-01", "2024-12-31"],
        "stats": dev,
        "checks": checks,
        "development_gate": "PASS" if all(checks.values()) else "FAIL",
    }
    with open("strategy2_experiment12_devcheck.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
