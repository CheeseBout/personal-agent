"""Eval harness: `agent-eval [--only id,id] [--lang vi|en] [--out run.json] [--compare old.json]`.

Runs scenario tasks (tests/evals/scenarios.yaml, vi + en) through the real graph with the configured models and records
pass rate, steps and cost per scenario, so prompt / model changes can be compared. THIS CALLS REAL MODELS AND COSTS MONEY
(a few cents for the default set); the daily cap in `budgets` still applies. Approvals are never granted: a scenario that
needs one ends "paused", which is itself checkable (`paused: true`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from personal_agent import services
from personal_agent.graph.brain import Brain
from personal_agent.graph.builder import build_graph
from personal_agent.graph.lang import detect_lang
from personal_agent.memory.store import MemoryStore
from personal_agent.observability.audit import AuditLog
from personal_agent.observability.metrics import Metrics
from personal_agent.observability.tracing import Tracing
from personal_agent.policy.approvals import ApprovalStore
from personal_agent.settings.settings import Settings, data_dir, load_settings

DEFAULT_SCENARIOS = Path("tests/evals/scenarios.yaml")
CHECKS = {"contains_any", "contains_all", "not_contains", "tools_any", "tools_none", "reply_lang", "paused",
          "max_steps", "max_cost_usd"}


def _fold(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def load_scenarios(path: Path, only: set[str] | None = None, lang: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    for it in items:
        if bad := set(it.get("expect", {})) - CHECKS:
            raise ValueError(f"scenario {it.get('id')}: unknown check(s) {sorted(bad)}")
    return [it for it in items if (not only or it["id"] in only) and (not lang or it["lang"] == lang)]


def check(expect: dict[str, Any], r: dict[str, Any]) -> list[str]:
    """Return the failed checks (empty = pass). `r` holds final / tools / paused / steps / cost."""
    text, fails = _fold(r["final"]), []
    if (a := expect.get("contains_any")) and not any(_fold(x) in text for x in a):
        fails.append(f"none of {a} in the answer")
    fails += [f"missing '{x}'" for x in expect.get("contains_all", []) if _fold(x) not in text]
    fails += [f"must not contain '{x}'" for x in expect.get("not_contains", []) if _fold(x) in text]
    if (a := expect.get("tools_any")) and not set(a) & set(r["tools"]):
        fails.append(f"expected one of tools {a}, used {r['tools']}")
    if bad := set(expect.get("tools_none", [])) & set(r["tools"]):
        fails.append(f"used forbidden tool(s) {sorted(bad)}")
    if (want := expect.get("reply_lang")) and r["final"] and detect_lang(r["final"]) != want:
        fails.append(f"reply is not {want}")
    if "paused" in expect and bool(expect["paused"]) != r["paused"]:
        fails.append(f"paused={r['paused']}, expected {expect['paused']}")
    if "max_steps" in expect and (r["steps"] or 0) > expect["max_steps"]:
        fails.append(f"{r['steps']} steps > {expect['max_steps']}")
    if "max_cost_usd" in expect and (r["cost"] or 0) > expect["max_cost_usd"]:
        fails.append(f"${r['cost']:.4f} > ${expect['max_cost_usd']}")
    return fails


async def run_one(graph: Any, sc: dict[str, Any], tracing: Tracing, limit: int) -> dict[str, Any]:
    tid = f"eval-{sc['id']}-{int(time.time())}"
    config = tracing.run_config(tid, limit, tags=("eval", sc["id"], sc["lang"]))
    final, tools, paused, err = "", [], False, ""
    t0 = time.perf_counter()
    try:
        async for chunk in graph.astream({"messages": [HumanMessage(sc["prompt"])]}, config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    paused = True
                    continue
                for m in (update or {}).get("messages", []):
                    if isinstance(m, AIMessage):
                        tools += [c["name"] for c in m.tool_calls]
                if node in ("respond", "clarify") and update:
                    final = update.get("final", "")
    except Exception as e:  # noqa: BLE001 - a crashing scenario is a failed scenario, not a crashed run
        err = f"{type(e).__name__}: {str(e)[:200]}"
    vals = (await graph.aget_state({"configurable": {"thread_id": tid}})).values
    u = vals.get("used", {})
    r = {"id": sc["id"], "lang": sc["lang"], "final": final, "tools": tools, "paused": paused,
         "steps": u.get("steps"), "tool_calls": u.get("tool_calls"), "tokens": u.get("tokens"),
         "cost": round(float(u.get("cost") or 0), 6), "seconds": round(time.perf_counter() - t0, 1),
         "stop_reason": vals.get("stop_reason") or None, "error": err or None}
    r["failures"] = [f"error: {err}"] if err else check(sc.get("expect", {}), r)
    r["passed"] = not r["failures"]
    r["final"] = final[:400]
    return r


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rs) or 1
        return {"n": len(rs), "pass_rate": round(sum(r["passed"] for r in rs) / n, 3),
                "avg_steps": round(sum(r["steps"] or 0 for r in rs) / n, 2),
                "cost_usd": round(sum(r["cost"] for r in rs), 4)}
    return {"all": agg(results), **{lg: agg([r for r in results if r["lang"] == lg]) for lg in sorted({r["lang"] for r in results})}}


async def run_scenarios(scenarios: list[dict[str, Any]], settings: Settings | None = None, brain: Brain | None = None) -> list[dict[str, Any]]:
    s = settings or load_settings()
    tracing = Tracing(s, Metrics(data_dir() / "usage.db", s.observability.metrics_keep_days))
    out: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
        # Throw-away audit / approvals / memory so evals never add pending approvals or memories to the real ones.
        # The real usage meter is kept on purpose: eval spend counts against the daily cap.
        graph = build_graph(InMemorySaver(), brain, s, services.meter(), AuditLog(Path(tmp) / "audit.db"),
                            ApprovalStore(Path(tmp) / "approvals.db"), None, MemoryStore(Path(tmp) / "memory.db", services.embedder()))
        for sc in scenarios:
            r = await run_one(graph, sc, tracing, s.budgets.max_steps * 8 + 40)
            print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id']:<22} steps={r['steps']} tools={len(r['tools'])} "
                  f"${r['cost']:.4f} {r['seconds']}s" + ("" if r["passed"] else f"  <- {'; '.join(r['failures'])}"))
            out.append(r)
    tracing.flush()
    return out


def compare(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    o = {r["id"]: r for r in old["results"]}
    a, b = old["summary"]["all"], new["summary"]["all"]
    lines = [f"pass_rate {a['pass_rate']:.0%} -> {b['pass_rate']:.0%}   cost ${a['cost_usd']:.4f} -> ${b['cost_usd']:.4f}"]
    for r in new["results"]:
        p = o.get(r["id"])
        if p is None:
            lines.append(f"  {r['id']}: new")
        elif p["passed"] != r["passed"] or abs((p["steps"] or 0) - (r["steps"] or 0)) > 2:
            lines.append(f"  {r['id']}: {'PASS' if p['passed'] else 'FAIL'}->{'PASS' if r['passed'] else 'FAIL'} "
                         f"steps {p['steps']}->{r['steps']} cost ${p['cost']:.4f}->${r['cost']:.4f}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the eval scenarios against the configured models (costs money).")
    ap.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--lang", choices=["vi", "en"])
    ap.add_argument("--out", type=Path, help="write the full results as JSON")
    ap.add_argument("--compare", type=Path, help="a previous --out file to diff against")
    a = ap.parse_args()
    scenarios = load_scenarios(a.scenarios, set(a.only.split(",")) if a.only else None, a.lang)
    if not scenarios:
        raise SystemExit("no scenarios selected")
    s = load_settings()
    print(f"{len(scenarios)} scenario(s); models: planner={s.models['planner'].model} worker={s.models['worker'].model}")
    results = asyncio.run(run_scenarios(scenarios, s))
    run: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": {k: v.model for k, v in s.models.items()},
           "summary": summarize(results), "results": results}
    print(json.dumps(run["summary"], ensure_ascii=False))
    if a.out:
        a.out.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.compare:
        print("\n".join(compare(json.loads(a.compare.read_text(encoding="utf-8")), run)))
    raise SystemExit(0 if run["summary"]["all"]["pass_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
