"""Smoke test of the PyInstaller bundle: starts dist/personal-agent/agent-core.exe with a throw-away data dir on a spare port, checks
/health, the token, a WebSocket round trip (settings + usage) and that the shipped config/policy were found, then stops it.

Run after scripts/build.ps1:  python -m uv run python scripts/smoke_packaged.py [--chat]
--chat also sends one tiny real question (about $0.0005; needs API keys in ./.env) to prove the lazily imported provider SDKs are in the bundle,
and starts agent-chat.exe briefly to prove it launches.
The packaged desktop UI (ui/agent-ui.exe) is always started against that daemon and must connect to it.
"""

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from sys import argv

from websockets.asyncio.client import connect

BUNDLE = Path("dist/personal-agent")
exe = BUNDLE / "agent-core.exe"
if not exe.is_file():
    raise SystemExit(f"{exe} not found: run scripts/build.ps1 first")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
home = Path(tempfile.mkdtemp(prefix="pa-bundle-"))
(home / "config.yaml").write_text(f"server:\n  port: {port}\n", encoding="utf-8")
CHAT = "--chat" in argv
env = {**os.environ, "PERSONAL_AGENT_HOME": str(home), "PERSONAL_AGENT_TOKEN": "smoke",
       "PERSONAL_AGENT_ENV_FILE": str(Path(".env").resolve()) if CHAT else "nul"}
env.pop("PERSONAL_AGENT_CONFIG_DIR", None)  # prove the bundled config/ is found
ok = True


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


t0 = time.perf_counter()
p = subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
ready = None
while time.perf_counter() - t0 < 60 and p.poll() is None:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5).read()
        ready = time.perf_counter() - t0
        break
    except OSError:
        time.sleep(0.2)
check("packaged daemon answers /health", ready is not None, f"{ready:.1f}s" if ready else "")
if ready is not None:
    async def round_trip() -> dict:
        async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": "Bearer smoke"}) as ws:
            await ws.recv()
            await ws.send(json.dumps({"type": "settings_get"}))
            while (ev := json.loads(await ws.recv()))["type"] != "settings":
                pass
            await ws.send(json.dumps({"type": "policy_get"}))
            pol = None
            while (pol := json.loads(await ws.recv()))["type"] != "policy":
                pass
            return {"settings": ev, "policy": pol}

    r = asyncio.run(round_trip())
    check("bundled config/default.yaml was found", r["settings"]["models"]["planner"]["model"] != "")
    check("bundled config/policy.yaml was found", "default_tier" in r["policy"]["text"])
    try:
        async def bad() -> None:
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": "Bearer wrong"}) as ws:
                await ws.recv()
        asyncio.run(bad())
        check("a wrong token is refused", False)
    except Exception:  # noqa: BLE001
        check("a wrong token is refused", True)
    if CHAT:
        async def ask() -> dict:
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": "Bearer smoke"}, max_size=None) as ws:
                await ws.recv()
                await ws.send(json.dumps({"type": "user", "text": "What is 17 times 23? Just the number."}))
                while (ev := json.loads(await ws.recv()))["type"] not in ("final", "error"):
                    pass
                return ev

        ev = asyncio.run(ask())
        check("a real question is answered by the packaged daemon (provider SDKs bundled)", ev["type"] == "final" and "391" in ev.get("text", ""), str(ev)[:120])
        e2 = {**env, "PERSONAL_AGENT_ENV_FILE": "nul"}
        q = subprocess.Popen([str(BUNDLE / "agent-chat.exe")], env=e2, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(8)
        check("agent-chat.exe starts", True, f"exit code {q.poll()}")
        if q.poll() is None:
            q.terminate()
    ui = BUNDLE / "ui" / "agent-ui.exe"
    if ui.is_file():  # the packaged UI finds agent-core.exe next to its folder, asks it for the connection and registers with the daemon
        async def ui_connects() -> bool:
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": "Bearer smoke"}) as ws:
                await ws.recv()
                for _ in range(40):
                    await ws.send(json.dumps({"type": "about"}))
                    while (ev := json.loads(await ws.recv()))["type"] != "about":
                        pass
                    if ev.get("clients", 0) >= 2:  # this probe and the UI
                        return True
                    await asyncio.sleep(0.5)
                return False

        e3 = {**env, "PERSONAL_AGENT_NO_DAEMON": "1", "PERSONAL_AGENT_ENV_FILE": "nul"}
        u = subprocess.Popen([str(ui), f"--user-data-dir={home / 'electron'}"], env=e3, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            check("the packaged desktop UI starts and connects to the packaged daemon", asyncio.run(ui_connects()))
        finally:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(u.pid)], capture_output=True, check=False)
    else:
        check("the packaged desktop UI exists (ui/agent-ui.exe)", False, "run scripts/build.ps1")
    import psutil

    rss = max(x.memory_info().rss for x in [psutil.Process(p.pid), *psutil.Process(p.pid).children(recursive=True)]) / 1048576
    print(f"      packaged daemon idle RSS: {rss:.0f} MB")
else:
    print("--- output of the packaged daemon ---")
    print((p.stdout.read() if p.poll() is not None and p.stdout else "")[-3000:])
p.terminate()
print("\nsmoke test " + ("passed" if ok else "FAILED"))
raise SystemExit(0 if ok else 1)
