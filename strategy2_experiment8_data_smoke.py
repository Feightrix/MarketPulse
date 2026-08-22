import json
import traceback
import strategy2_experiment8_news_momentum_long as base

out = {"research_only": True, "symbol": "NVDA", "period": ["2026-07-20", "2026-07-24"]}
try:
    news = base.fetch_news("NVDA", "2026-07-20", "2026-07-24")
    bars = base.fetch_day_bars("NVDA", "2026-07-22")
    out.update({
        "status": "PASS",
        "news_items": len(news),
        "bar_rows": int(len(bars)),
        "sample_headlines": [str(x.get("headline") or "") for x in news[:3]],
    })
except Exception as e:
    out.update({"status": "ERROR", "error_type": type(e).__name__, "error": str(e), "trace": traceback.format_exc()[-3000:]})

with open("strategy2_experiment8_data_smoke.json", "w") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
