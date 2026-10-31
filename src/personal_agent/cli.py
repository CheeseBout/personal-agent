"""Terminal chat client: `agent-chat [--thread ID]`.

Commands: /usage, /name <new name>, /audit, /rules, /kill, /resume-kill, /exit.
When the agent needs approval, a card is shown: [y]es, [n]o (optionally with a reason), [e]dit, [r]emember (yes + never ask again for that scope).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import load_settings

TERMINAL = {"final", "error", "usage", "agent_name", "paused", "audit", "rules", "killed", "kill_released", "approvals",
            "memory", "memory_updated", "memory_deleted", "rag", "schedules", "schedule_cancel", "mcp", "mcp_set"}


def show(ev: dict[str, Any]) -> None:
    t = ev["type"]
    if t == "intent":
        print(f"  [intent] {ev['intent']} (confidence {ev['confidence']:.2f}, plan={ev['needs_plan']})")
    elif t == "plan":
        print(f"  [plan] {ev['objective']}")
        for c in ev["success_criteria"]:
            print(f"     done when: {c}")
    elif t == "verdict":
        print(f"  [verify] {ev['status']} {ev['notes']}")
    elif t == "tool_call":
        print(f"  [tool] {ev['name']} {json.dumps(ev['args'], ensure_ascii=False)}")
    elif t == "tool_result":
        print(f"  [{ev['status']}] {ev['content'][:200]!r}")
    elif t == "final":
        u = ev.get("used") or {}
        print(f"agent> {ev['text']}")
        print(f"  (steps={u.get('steps')}, tools={u.get('tool_calls')}, tokens={u.get('tokens')}, ~${(u.get('cost') or 0):.4f})")
    elif t == "usage":
        print(f"  today: {ev['today']}  caps: {ev['caps']}")
    elif t == "agent_name":
        print(f"  agent renamed to {ev['name']}")
    elif t in ("audit", "rules", "approvals"):
        for it in ev["items"]:
            print("  ", {k: v for k, v in it.items() if k != "preview"})
    elif t == "memory":
        for m in ev["items"]:
            print(f"  #{m['id']} [{m['kind']}/{m['status']}{' SENSITIVE' if m['sensitive'] else ''}] {m['text'][:110]}")
        print("  (none)" if not ev["items"] else "")
    elif t in ("memory_updated", "memory_deleted", "mcp_set", "schedule_cancel"):
        print(f"  ok={ev.get('ok')}" + ("  (run /mcp-reload to apply)" if t == "mcp_set" else ""))
    elif t == "rag":
        for d in ev["items"]:
            print(f"  {d['source']} ({d['kind']}, {d['chunks']} chunks)")
        print(f"  watching={ev['watching']} paused={ev['paused']}")
    elif t == "schedules":
        for j in ev["items"]:
            print(f"  #{j['id']} enabled={j['enabled']} every_s={j['every_s']} last={j['last_status']} | {j['prompt'][:70]}")
    elif t == "mcp":
        for n, sv in ev["servers"].items():
            print(f"  {n}: {sv}")
    elif t == "killed":
        print(f"  KILL SWITCH ENGAGED ({ev['threads']} thread(s) stopped). Use /resume-kill to re-enable.")
    elif t == "kill_released":
        print("  kill switch released")
    elif t == "error":
        print(f"error ({ev['code']}): {ev['message']}")


def card(a: dict[str, Any]) -> None:
    print(f"\n  APPROVAL NEEDED  [{a['tier']} / risk {a['risk']}]  {a['tool']}")
    print(f"  why: {a['reason']}{'  (UNTRUSTED CONTENT SEEN)' if a['tainted'] else ''}")
    print(f"  args: {a['args']}")
    for line in str(a["preview"]).splitlines()[:40]:
        print(f"  | {line}")


async def decide(approvals: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: dict[str, Any] = {}
    for a in approvals:
        card(a)
        ans = (await asyncio.to_thread(input, "  approve? [y]es / [n]o / [e]dit / [r]emember: ")).strip().lower()
        if ans in ("y", "yes", "r", "remember"):
            d: dict[str, Any] = {"action": "approve", "remember": ans.startswith("r")}
            if a["tier"] == "T3":
                d["confirm"] = (await asyncio.to_thread(input, "  type CONFIRM to proceed: ")).strip() == "CONFIRM"
            decisions[a["call_id"]] = d
        elif ans in ("e", "edit"):
            raw = await asyncio.to_thread(input, "  new args as JSON: ")
            try:
                decisions[a["call_id"]] = {"action": "edit", "args": json.loads(raw)}
            except json.JSONDecodeError:
                decisions[a["call_id"]] = {"action": "reject", "feedback": "invalid JSON for edited arguments"}
        else:
            why = (await asyncio.to_thread(input, "  reason (optional, the agent will see it): ")).strip()
            decisions[a["call_id"]] = {"action": "reject", "feedback": why}
    return decisions


async def exchange(ws: ClientConnection, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload))
    requests: list[dict[str, Any]] = []
    while True:
        ev = json.loads(await ws.recv())
        if ev["type"] == "approval_request":
            requests += ev["approvals"]
            continue
        show(ev)
        if ev["type"] == "paused":
            payload = {"type": "approve", "thread_id": ev["thread_id"], "decisions": await decide(requests)}
            requests = []
            await ws.send(json.dumps(payload))
        elif ev["type"] in TERMINAL:
            return


async def chat(thread: str) -> None:
    s = load_settings().server
    headers = {"Authorization": f"Bearer {get_api_token()}"}
    async with connect(f"ws://{s.host}:{s.port}/ws", additional_headers=headers) as ws:
        first = json.loads(await ws.recv())  # pending approvals from before (survive UI restarts)
        print(f"connected (thread '{thread}'). Commands: /usage /name <n> /audit /rules /kill /resume-kill /memory "
              "/memory-ok <id> /memory-del <id> /rag /schedules /schedule-cancel <id> /mcp /mcp-reload /exit")
        if first.get("items"):
            print(f"  {len(first['items'])} approval(s) are still pending - ask the agent about that thread or reject them.")
            show(first)
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except EOFError:
                return
            if text in ("/exit", "/quit"):
                return
            if not text:
                continue
            simple = {"/usage": "usage", "/audit": "audit", "/rules": "rules", "/kill": "kill", "/resume-kill": "release_kill"}
            if text in simple:
                await exchange(ws, {"type": simple[text]})
            elif text.startswith("/name "):
                await exchange(ws, {"type": "set_agent_name", "name": text[6:]})
            elif text in ("/memory", "/rag", "/schedules", "/mcp", "/mcp-reload"):
                kind = {"/memory": "memory_list", "/rag": "rag_list", "/schedules": "schedules", "/mcp": "mcp_list",
                        "/mcp-reload": "mcp_reload"}[text]
                await exchange(ws, {"type": kind})
            elif text.startswith(("/memory-ok ", "/memory-del ", "/schedule-cancel ")):
                cmd, _, arg = text.partition(" ")
                if not arg.strip().isdigit():
                    print("  usage: " + cmd + " <number>")
                    continue
                n = int(arg)
                payload = {"/memory-ok": {"type": "memory_update", "id": n, "status": "active"},
                           "/memory-del": {"type": "memory_delete", "id": n},
                           "/schedule-cancel": {"type": "schedule_cancel", "id": n}}[cmd]
                await exchange(ws, payload)
            else:
                await exchange(ws, {"type": "user", "thread_id": thread, "text": text})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", default="default")
    try:
        asyncio.run(chat(ap.parse_args().thread))
    except (ConnectionRefusedError, OSError):
        raise SystemExit("cannot reach the daemon - start it with: agent-core") from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
