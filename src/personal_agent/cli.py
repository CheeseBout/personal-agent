"""Minimal terminal chat client: `agent-chat [--thread ID]`. Commands: /usage, /name <new name>, /exit."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import load_settings


def show(ev: dict[str, Any]) -> bool:
    """Print one event; True when the turn is over."""
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
        return True
    elif t == "usage":
        print(f"  today: {ev['today']}  caps: {ev['caps']}")
        return True
    elif t == "agent_name":
        print(f"  agent renamed to {ev['name']}")
        return True
    elif t == "error":
        print(f"error ({ev['code']}): {ev['message']}")
        return True
    return False


async def exchange(ws: ClientConnection, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload))
    while not show(json.loads(await ws.recv())):
        pass


async def chat(thread: str) -> None:
    s = load_settings().server
    headers = {"Authorization": f"Bearer {get_api_token()}"}
    async with connect(f"ws://{s.host}:{s.port}/ws", additional_headers=headers) as ws:
        print(f"connected (thread '{thread}'). Commands: /usage, /name <name>, /exit")
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except EOFError:
                return
            if text in ("/exit", "/quit"):
                return
            if not text:
                continue
            if text == "/usage":
                await exchange(ws, {"type": "usage"})
            elif text.startswith("/name "):
                await exchange(ws, {"type": "set_agent_name", "name": text[6:]})
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
