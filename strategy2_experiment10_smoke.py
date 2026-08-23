import json
import traceback
import strategy2_experiment10_entity_abnormal_news as e10


def main():
    out = {
        "experiment": "S2-E10-RUNTIME-DIAGNOSTIC",
        "research_only": True,
        "period": ["2024-01-01", "2024-01-31"],
    }
    try:
        stats = e10.evaluate_period("2024-01-01", "2024-01-31")
        out.update({"status": "PASS", "stats": stats})
    except Exception as exc:
        out.update({"status": "ERROR", "error": repr(exc), "traceback": traceback.format_exc()})
    with open("strategy2_experiment10_smoke.json", "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
