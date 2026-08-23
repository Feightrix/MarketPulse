import json
import traceback

import strategy2_experiment17_volatile_pattern_scan as exp

try:
    exp.main()
except Exception as exc:
    payload = {
        "experiment": exp.EXPERIMENT,
        "status": "RUNTIME_ERROR",
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(),
    }
    with open("strategy2_experiment17_error.json", "w") as f:
        json.dump(payload, f, indent=2)
    raise
