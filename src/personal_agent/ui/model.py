"""UI-side view of one conversation, built from the daemon's event stream. No Qt here, so it is unit-testable.

The windows only render this model; they never interpret raw events themselves.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_STEP = re.compile(r"^s(\d+)$")


@dataclass
class Entry:
    kind: str  # user | agent | event | error
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_approval(a: dict[str, Any]) -> dict[str, Any]:
    """Approval records arrive with args as a JSON string (DB row) or a dict (live request): always return a dict."""
    out = dict(a)
    args = out.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    out["args"] = args if isinstance(args, dict) else {}
    out["tainted"] = bool(out.get("tainted"))
    return out


class Session:
    def __init__(self, thread_id: str = "default", agent_name: str = "personal-agent") -> None:
        self.thread_id, self.agent_name = thread_id, agent_name
        self.transcript: list[Entry] = []
        self.plan: dict[str, Any] | None = None
        self.steps: list[dict[str, Any]] = []  # {"text", "role", "status": todo|done|failed|blocked}
        self.verdict = ""
        self.intent: dict[str, Any] | None = None
        self.approvals: dict[str, dict[str, Any]] = {}  # call_id -> approval (pending only)
        self.busy = False
        self.used: dict[str, Any] = {}
        self.usage: dict[str, Any] = {}
        self.killed = False

    # ---- local actions --------------------------------------------------------------------------
    def user_says(self, text: str, attachments: list[str] | None = None) -> None:
        self.transcript.append(Entry("user", text, {"attachments": attachments or []}))
        self.busy, self.plan, self.steps, self.verdict, self.intent = True, None, [], "", None

    def pending_for_thread(self) -> dict[str, list[dict[str, Any]]]:
        by: dict[str, list[dict[str, Any]]] = {}
        for a in self.approvals.values():
            by.setdefault(a.get("thread_id", self.thread_id), []).append(a)
        return by

    # ---- daemon events ----------------------------------------------------------------------------
    def apply(self, ev: dict[str, Any]) -> set[str]:
        """Update the model from one event; returns which parts changed (transcript, plan, approvals, busy, usage, name)."""
        t = ev.get("type")
        ch: set[str] = set()
        if t == "intent":
            self.intent = {k: ev.get(k) for k in ("intent", "complexity", "needs_plan", "risk_level", "confidence")}
            self.transcript.append(Entry("event", f"intent: {ev.get('intent')} ({float(ev.get('confidence') or 0):.0%})"))
            ch |= {"transcript"}
        elif t == "plan":
            roles = ev.get("roles") or []
            self.plan = {"objective": ev.get("objective", ""), "criteria": list(ev.get("success_criteria") or [])}
            self.steps = [{"text": s, "role": roles[i] if i < len(roles) else "main", "status": "todo"}
                          for i, s in enumerate(ev.get("steps") or [])]
            self.transcript.append(Entry("event", f"plan: {ev.get('objective', '')}"))
            ch |= {"transcript", "plan"}
        elif t == "worker":
            m = _STEP.match(str(ev.get("step", "")))
            if m and 0 < int(m.group(1)) <= len(self.steps):
                self.steps[int(m.group(1)) - 1]["status"] = {"done": "done", "ok": "done"}.get(str(ev.get("status")), "failed")
            self.transcript.append(Entry("event", f"[{ev.get('role')}] {ev.get('status')}: {ev.get('summary', '')}"))
            ch |= {"transcript", "plan"}
        elif t == "tool_call":
            self.transcript.append(Entry("event", f"tool: {ev.get('name')} {json.dumps(ev.get('args', {}), ensure_ascii=False)[:160]}"))
            ch.add("transcript")
        elif t == "tool_result":
            self.transcript.append(Entry("event", f"  -> {ev.get('status')}: {str(ev.get('content', ''))[:200]}"))
            ch.add("transcript")
        elif t == "verdict":
            self.verdict = str(ev.get("status") or "")
            if self.verdict == "done":
                for s in self.steps:
                    s["status"] = "done" if s["status"] == "todo" else s["status"]
            self.transcript.append(Entry("event", f"verify: {self.verdict} {ev.get('notes') or ''}".strip()))
            ch |= {"transcript", "plan"}
        elif t == "approvals":
            self.approvals = {a["call_id"]: normalize_approval(a) for a in ev.get("items", [])}
            ch.add("approvals")
        elif t == "approval_request":
            for a in ev.get("approvals", []):
                self.approvals[a["call_id"]] = normalize_approval({**a, "thread_id": a.get("thread_id", ev.get("thread_id"))})
            ch.add("approvals")
        elif t == "paused":
            self.busy = False
            ch |= {"busy", "approvals"}
        elif t == "final":
            self.busy, self.used = False, dict(ev.get("used") or {})
            note = f" (stopped: {ev['stopped']})" if ev.get("stopped") else ""
            self.transcript.append(Entry("agent", str(ev.get("text", "")) + note, {"used": self.used}))
            ch |= {"transcript", "busy", "usage"}
        elif t == "error":
            self.busy = False
            self.transcript.append(Entry("error", f"{ev.get('code')}: {ev.get('message')}"))
            ch |= {"transcript", "busy"}
        elif t == "usage":
            self.usage = {k: ev.get(k) for k in ("today", "caps", "report", "langfuse")}
            ch.add("usage")
        elif t == "agent_name":
            self.agent_name = str(ev.get("name", self.agent_name))
            ch.add("name")
        elif t == "killed":
            self.busy, self.killed = False, True
            self.transcript.append(Entry("error", f"kill switch engaged ({ev.get('threads', 0)} task(s) stopped)"))
            ch |= {"transcript", "busy", "killed"}
        elif t == "kill_released":
            self.killed = False
            ch.add("killed")
        return ch
