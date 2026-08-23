import json
import strategy2_experiment14_microburst_long as m


def main():
    out = {"experiment": "S2-E14-SMOKE", "research_only": True}
    try:
        feed, quality = m.detect_feed()
        start, end = m.utc_window("2026-08-21", "10:00")
        q = m.fetch_pages("NVDA", "quotes", start, end, feed)
        t = m.fetch_pages("NVDA", "trades", start, end, feed)
        df = m.to_frames(q, t)
        feat = m.add_features(df) if not df.empty else df
        out.update({
            "status": "PASS", "feed": feed, "data_quality": quality,
            "quotes": len(q), "trades": len(t), "seconds": len(df),
            "max_score": float(feat["score"].max()) if len(feat) else 0.0,
            "candidate_count": len(m.candidate_indices(feat)) if len(feat) else 0,
        })
    except Exception as e:
        out.update({"status": "ERROR", "error_type": type(e).__name__, "error": str(e)[:2000]})
    with open("strategy2_experiment14_smoke.json", "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
