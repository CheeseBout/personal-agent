"""Time the real `vision.describe` path on the current screen with local Ollama models (nothing leaves the machine).

    python -m uv run python scripts/vision_bench.py gemma4:e2b gemma4:e4b
"""
import sys
import time

from PIL import ImageGrab

from personal_agent.perception import vision
from personal_agent.settings.settings import ModelSpec, load_settings

QUESTIONS = ["Describe what is on the screen in 3 sentences.",
             "List the visible window titles and the 5 most prominent texts you can read."]

img = ImageGrab.grab(all_screens=True)
for model in sys.argv[1:] or ["gemma4:e2b", "gemma4:e4b"]:
    s = load_settings()
    s.models["vision"] = ModelSpec(provider="ollama", model=model, max_tokens=2000, timeout_s=180, reasoning_effort="none")
    for q in QUESTIONS:
        t = time.time()
        try:
            out = vision.describe(img, q, s)
        except Exception as e:  # noqa: BLE001 - benchmark: report and go on
            out = f"ERROR {type(e).__name__}: {e}"
        print(f"\n=== {model} | {time.time() - t:.1f}s | {q}\n{out}", flush=True)
