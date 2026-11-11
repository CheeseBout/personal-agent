"""A real daemon with a scripted model, for the Electron end-to-end tests: isolated config/data folders, no network, no keys.

    python fake_daemon.py               run it (E2E_HOME = scratch folder, E2E_PORT = port)
    python fake_daemon.py --connection  print how to reach it, like `agent-core --connection`

A message containing "write" makes the agent call file_write (which needs approval); anything else is answered directly."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HOME, PORT, TOKEN = Path(os.environ["E2E_HOME"]), int(os.environ["E2E_PORT"]), "test-token"

if "--connection" in sys.argv:
    print(json.dumps({"host": "127.0.0.1", "port": PORT, "token": TOKEN}))
    raise SystemExit

os.chdir(REPO)
sys.path.insert(0, str(REPO / "tests"))
import yaml

ws, cfg = HOME / "ws", HOME / "cfg"
ws.mkdir(parents=True, exist_ok=True)
cfg.mkdir(exist_ok=True)
shutil.copy("config/default.yaml", cfg / "default.yaml")
pol = yaml.safe_load(Path("config/policy.yaml").read_text(encoding="utf-8"))
pol["paths"]["allow"] = [str(ws)]
(cfg / "policy.yaml").write_text(yaml.safe_dump(pol), encoding="utf-8")
os.environ.update(PERSONAL_AGENT_CONFIG_DIR=str(cfg), PERSONAL_AGENT_HOME=str(HOME / "home"), PERSONAL_AGENT_TOKEN=TOKEN,
                  PERSONAL_AGENT_ENV_FILE=str(HOME / "no.env"))

import keyring

# The user's real Credential Manager and provider keys must never be read, overwritten or spent by a test.
_vault: dict[tuple[str, str], str] = {}
keyring.get_password = lambda service, name: _vault.get((service, name))  # type: ignore[assignment]
keyring.set_password = lambda service, name, value: _vault.__setitem__((service, name), value)  # type: ignore[assignment]
for _k in [k for k in os.environ if k.endswith(("_API_KEY", "_SECRET_KEY", "_PUBLIC_KEY"))]:
    del os.environ[_k]

import uvicorn
from langchain_core.runnables import RunnableLambda

from fakes import FakeBrain, HashEmbedder, answer, call
from personal_agent import services
from personal_agent.core import app as core_app
from personal_agent.core.app import create_app
from personal_agent.settings.secrets import get_secret


async def fake_test_role(settings: Any, role: str) -> dict[str, Any]:  # no provider is ever called: "works" means a key was saved
    ok = bool(get_secret("ANTHROPIC_API_KEY"))
    return {"ok": ok, "role": role} if ok else {"ok": False, "code": "no_api_key", "message": "no key"}


core_app.test_role = fake_test_role  # type: ignore[assignment]

import time

from PIL import Image

from personal_agent.perception import capture, hooks


def fake_capture(kind: str, cfg: Any, *, window: Any = None, rect: Any = None) -> Any:  # the user's real screen is never grabbed by a test
    return capture.Shot(Image.new("RGB", (64, 64), (200, 200, 200)), kind, f"{kind} {list(rect) if rect else ''}".strip(), "test.exe", (0, 0, 64, 64), time.time(), 1)


capture.capture = fake_capture  # type: ignore[assignment]

import io
import wave

from personal_agent.voice import stt, tts


def fake_transcribe(audio: bytes, mime: str, cfg: Any) -> tuple[str, int, int]:  # no speech provider is called
    stt.check_audio(audio, mime, cfg)  # still refuses what the real one refuses (not audio, too long)
    return "ping by voice", 0, 0


async def fake_synth(sentence: str, voice: str, rate: str) -> bytes:  # half a second of quiet audio instead of a cloud voice
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)
    return buf.getvalue()


stt.transcribe = fake_transcribe  # type: ignore[assignment]
tts.synth = fake_synth  # type: ignore[assignment]
emb = HashEmbedder()
services.embedder = lambda: emb  # type: ignore[assignment]  # never reach a real embedding provider


class Worker(RunnableLambda):  # type: ignore[type-arg]
    def __init__(self) -> None:
        super().__init__(self.reply)

    def bind_tools(self, tools: Any, **kw: Any) -> Worker:
        return self

    @staticmethod
    def reply(msgs: Any) -> Any:
        msgs = list(msgs.to_messages()) if hasattr(msgs, "to_messages") else list(msgs)
        last = max(i for i, m in enumerate(msgs) if m.type == "human")
        if "highlight" in str(msgs[last].content):  # what a screen-control tool does just before it acts
            hooks.emit({"type": "highlight", "rect": [200, 300, 500, 380], "label": "click: Save", "ms": 1500})
        if "write" in str(msgs[last].content) and not any(m.type == "tool" for m in msgs[last:]):
            return call("file_write", {"path": "out.txt", "content": "hello"}, id=f"w{len(msgs)}")
        return answer("pong **ok** <img src=x onerror=\"document.title='pwned'\">", tokens=15)


uvicorn.run(create_app(FakeBrain(Worker()), HOME / "c.db", TOKEN), host="127.0.0.1", port=PORT, log_level="error")
