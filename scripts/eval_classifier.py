"""Run the eval scenarios with only the `classifier` role swapped (config files untouched). COSTS MONEY for the cloud roles.

    python -m uv run python scripts/eval_classifier.py ollama gemma4:e2b out.json
    python -m uv run python scripts/eval_classifier.py anthropic claude-haiku-5-5 base.json
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from personal_agent.evals import DEFAULT_SCENARIOS, load_scenarios, run_scenarios, summarize
from personal_agent.settings.settings import ModelSpec, load_settings

provider, model, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
s = load_settings()
extra = {"reasoning_effort": "none"} if provider == "ollama" else {}
s.models["classifier"] = ModelSpec(provider=provider, model=model, max_tokens=600, timeout_s=60, **extra)  # no fallbacks: failures stay visible
results = asyncio.run(run_scenarios(load_scenarios(DEFAULT_SCENARIOS), s))
run = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "classifier": f"{provider}/{model}", "summary": summarize(results), "results": results}
print(json.dumps(run["summary"], ensure_ascii=False))
out.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
