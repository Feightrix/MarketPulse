import json
import traceback
import strategy2_experiment9_news_quality as e9

out = {"research_only": True, "period": ["2024-01-01", "2024-01-31"]}
try:
    r = e9.evaluate_period("2024-01-01", "2024-01-31")
    out["status"] = "PASS"
    out["profiles"] = {k: {"trades": v["trades"], "return_pct": v["total_return_pct"], "funnel": v["funnel"]} for k, v in r.items()}
except Exception as exc:
    out["status"] = "ERROR"
    out["error_type"] = type(exc).__name__
    out["error"] = str(exc)
    out["traceback"] = traceback.format_exc()[-4000:]

with open("strategy2_experiment9_smoke.json", "w") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
