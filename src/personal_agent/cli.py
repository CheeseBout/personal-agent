"""Minimal terminal chat client: `agent-chat [--thread ID]`."""

from __future__ import annotations

import argparse
import asyncio
import json

from websockets.asyncio.client import connect

from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import load_settings


async def chat(thread: str) -> None:
    s = load_settings().server
    headers = {"Authorization": f"Bearer {get_api_token()}"}
    async with connect(f"ws://{s.host}:{s.port}/ws", additional_headers=headers) as ws:
        print(f"connected (thread '{thread}'). Type /exit to quit.")
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except EOFError:
                return
            if text in ("/exit", "/quit"):
                return
            if not text:
                continue
            await ws.send(json.dumps({"type": "user", "thread_id": thread, "text": text}))
            while True:
                ev = json.loads(await ws.recv())
                if ev["type"] == "tool_call":
                    print(f"  [tool] {ev['name']} {json.dumps(ev['args'], ensure_ascii=False)}")
                elif ev["type"] == "tool_result":
                    print(f"  [{ev['status']}] {ev['content'][:200]!r}")
                elif ev["type"] == "final":
                    print(f"agent> {ev['text']}")
                    break
                elif ev["type"] == "error":
                    print(f"error ({ev['code']}): {ev['message']}")
                    break


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
