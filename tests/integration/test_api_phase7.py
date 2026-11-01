"""Phase 7 backend: settings / secrets / policy editor, attachments and speech-to-text over the WebSocket."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from fakes import FakeBrain, ScriptedModel, answer
from personal_agent.core.app import create_app
from personal_agent.settings.settings import data_dir

AUTH = {"Authorization": "Bearer test-token"}
END = {"final", "error", "settings", "secrets", "policy", "transcript", "test_role", "about", "usage"}
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class Rig:
    def __init__(self, tmp: Path, brain: FakeBrain | None = None) -> None:
        self.brain = brain or FakeBrain(ScriptedModel(responses=[answer("seen")]))
        self.client = TestClient(create_app(self.brain, tmp / "c.db", "test-token"))

    def __enter__(self) -> Rig:
        self.client.__enter__()
        self.ws = self.client.websocket_connect("/ws", headers=AUTH).__enter__()
        assert self.ws.receive_json()["type"] == "approvals"
        return self

    def __exit__(self, *a: object) -> None:
        self.ws.__exit__(*a)
        self.client.__exit__(*a)

    def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ws.send_json(payload)
        while True:
            ev: dict[str, Any] = self.ws.receive_json()
            if ev["type"] in END:
                return ev


def test_settings_roundtrip_persists_and_never_returns_secret_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[str, str] = {}
    monkeypatch.setattr("personal_agent.core.admin.set_secret", store.__setitem__)
    monkeypatch.setattr("personal_agent.core.admin.get_secret", lambda n: store.get(n))
    with Rig(tmp_path) as r:
        s = r.ask({"type": "settings_get"})
        assert s["models"]["planner"]["model"] == "claude-haiku-5-5" and "ollama" in s["providers"]
        assert s["secrets"]["ANTHROPIC_API_KEY"] is False
        out = r.ask({"type": "secret_set", "name": "ANTHROPIC_API_KEY", "value": "sk-ant-verysecretvalue123"})
        assert out["secrets"]["ANTHROPIC_API_KEY"] is True and "verysecret" not in str(out)
        assert "verysecret" not in str(r.ask({"type": "settings_get"}))
        changed = r.ask({"type": "settings_set", "patch": {"models": {"fast": {"model": "gemini-3.5-flash"}},
                                                          "agent": {"language": "vi"}, "ui": {"avatar_scale": 1.5}}})
        assert set(changed["changed"]) == {"models", "agent", "ui"} and changed["models"]["fast"]["model"] == "gemini-3.5-flash"
    saved = yaml.safe_load((data_dir() / "config.yaml").read_text(encoding="utf-8"))
    assert saved["models"]["fast"] == {"model": "gemini-3.5-flash"} and saved["ui"]["avatar_scale"] == 1.5


@pytest.mark.parametrize("patch", [
    {"workers": {"enabled": False}},                       # not whitelisted
    {"models": {"nope": {"model": "x"}}},                  # unknown role
    {"models": {"fast": {"provider": "evil"}}},            # unknown provider
    {"models": {"fast": {"model": "bad\nmodel"}}},         # control characters
    {"models": {"fast": {"fallbacks": []}}},               # only provider/model are editable
    {"agent": {"name": "x"}},                              # rename has its own message
    {"budgets": {"daily_max_tokens": -1}},
    {"budgets": {"max_steps": 99}},                        # per-task step cap is not editable
    {"ui": {"no_such_field": 1}},
    {"ui": {"avatar_scale": "huge"}},
    {},
])
def test_settings_set_rejects_protected_or_invalid_patches(tmp_path: Path, patch: dict[str, Any]) -> None:
    with Rig(tmp_path) as r:
        ev = r.ask({"type": "settings_set", "patch": patch})
        assert ev["type"] == "error" and ev["code"] == "bad_request"
    assert not (data_dir() / "config.yaml").exists() or "workers" not in (data_dir() / "config.yaml").read_text()


def test_unknown_secret_name_is_rejected(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        assert r.ask({"type": "secret_set", "name": "PATH", "value": "x"})["code"] == "bad_request"
        assert r.ask({"type": "secret_set", "name": "OPENAI_API_KEY", "value": "  "})["code"] == "bad_request"


def test_policy_editor_validates_before_saving_and_keeps_a_backup(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        text = r.ask({"type": "policy_get"})["text"]
        assert "default_tier" in text
        for bad in ("{{{", "- a\n- b\n", "paths: {allow: []}\n", text.replace("default_tier: T3", "default_tier: T9"),
                    text.replace("allow_patterns:", "allow_patterns: [", 1)):
            assert r.ask({"type": "policy_set", "text": bad})["code"] == "bad_request"
        assert r.ask({"type": "policy_get"})["text"] == text  # nothing was written
        ok = r.ask({"type": "policy_set", "text": text.replace("timeout_s: 600", "timeout_s: 300")})
        assert ok["saved"] and "timeout_s: 300" in ok["text"]
        assert "timeout_s: 600" in (data_dir() / "policy.yaml.bak").read_text(encoding="utf-8")  # what was in force before
        assert "timeout_s: 300" in (data_dir() / "policy.yaml").read_text(encoding="utf-8")  # your copy lives in %APPDATA%
        assert "timeout_s: 600" in (Path(__import__("os").environ["PERSONAL_AGENT_CONFIG_DIR"]) / "policy.yaml").read_text(encoding="utf-8")  # the shipped file is untouched


def test_image_and_text_attachments_reach_the_model_as_one_message(tmp_path: Path) -> None:
    seen: list[Any] = []

    class Spy(ScriptedModel):
        def _generate(self, messages: Any, *a: Any, **k: Any) -> Any:
            seen.append([m for m in messages if isinstance(m, HumanMessage)][-1].content)
            return super()._generate(messages, *a, **k)

    with Rig(tmp_path, FakeBrain(Spy(responses=[answer("seen")]))) as r:
        r.ws.send_json({"type": "user", "text": "what is this?", "attachments": [
            {"name": "a.png", "mime": "image/png", "data": PNG},
            {"name": "notes.txt", "mime": "text/plain", "data": b64("xin chào".encode())}]})
        while (ev := r.ws.receive_json())["type"] != "final":
            assert ev["type"] != "error", ev
    content = seen[-1]
    assert content[0]["type"] == "text" and "what is this?" in content[0]["text"] and "xin chào" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("files", [
    [{"name": "a.exe", "mime": "application/x-msdownload", "data": b64(b"MZ")}],
    [{"name": "a.txt", "mime": "text/plain", "data": "not base64!!"}],
    [{"name": "a.txt", "mime": "text/plain", "data": ""}],
    [{"name": "a.txt", "mime": "text/plain", "data": b64(b"x")}] * 5,
    "nope",
])
def test_bad_attachments_are_a_clean_bad_request(tmp_path: Path, files: Any) -> None:
    with Rig(tmp_path) as r:
        r.ws.send_json({"type": "user", "text": "hi", "attachments": files})
        ev = r.ws.receive_json()
        assert ev["type"] == "error" and ev["code"] == "bad_request"


def test_attachment_only_message_is_allowed_and_empty_message_is_not(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        r.ws.send_json({"type": "user", "text": "", "attachments": [{"name": "a.txt", "mime": "text/plain", "data": b64(b"hello")}]})
        while (ev := r.ws.receive_json())["type"] not in ("final", "error"):
            pass
        assert ev["type"] == "final"
        r.ws.send_json({"type": "user", "text": "  "})
        assert r.ws.receive_json()["code"] == "bad_request"


def test_voice_message_is_transcribed_metered_and_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(audio: bytes, mime: str, cfg: Any) -> tuple[str, int, int]:
        calls.append(mime)
        return "xin chào", 40, 5

    monkeypatch.setattr("personal_agent.voice.stt.transcribe", fake)
    with Rig(tmp_path) as r:
        t = r.ask({"type": "transcribe", "data": b64(b"RIFFxxxx"), "mime": "audio/wav"})
        assert t["text"] == "xin chào" and calls == ["audio/wav"]
        assert r.ask({"type": "usage"})["today"]["tokens"] >= 45  # the cloud call was metered
        assert r.ask({"type": "transcribe", "data": "!!", "mime": "audio/wav"})["code"] == "bad_request"
        # an audio attachment becomes text inside the message
        r.ws.send_json({"type": "user", "text": "", "attachments": [{"name": "v.wav", "mime": "audio/wav", "data": b64(b"RIFFxxxx")}]})
        while (ev := r.ws.receive_json())["type"] not in ("final", "error"):
            pass
        assert ev["type"] == "final" and len(calls) == 2


def test_stt_refuses_non_audio_and_oversized_audio() -> None:
    from personal_agent.settings.settings import SttCfg
    from personal_agent.voice import stt

    with pytest.raises(stt.SttError, match="not an audio"):
        stt.check_audio(b"x", "text/plain", SttCfg())
    with pytest.raises(stt.SttError, match="10 MB"):
        stt.check_audio(b"x" * (stt.MAX_BYTES + 1), "audio/wav", SttCfg())


def test_about_reports_the_daemon_pid(tmp_path: Path) -> None:
    import os

    with Rig(tmp_path) as r:
        assert r.ask({"type": "about"})["pid"] == os.getpid()
