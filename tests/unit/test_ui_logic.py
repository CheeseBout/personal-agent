"""UI logic that needs no windows: session model, avatar state machine, decisions, hotkeys, voice helpers, i18n, autostart."""

from __future__ import annotations

import ast
import json
import uuid
import wave
from pathlib import Path
from typing import Any

import pytest

from personal_agent.core.admin import policy_add_path, policy_text
from personal_agent.settings.settings import TtsCfg
from personal_agent.ui import i18n
from personal_agent.ui.avatar_state import AvatarState
from personal_agent.ui.decisions import build_decisions
from personal_agent.ui.model import Session, normalize_approval
from personal_agent.ui.system import MOD, NOREPEAT, autostart_enabled, parse_hotkey, set_autostart
from personal_agent.ui.voice_io import pcm_to_wav, pick_voice, rms, split_sentences, strip_markdown

UI_SRC = Path("src/personal_agent/ui")


# ---- session model -----------------------------------------------------------------------------------------------------
def test_session_follows_a_planned_task_from_intent_to_final() -> None:
    s = Session("t", "Mimi")
    s.user_says("do it", ["a.png"])
    assert s.busy and s.transcript[0].meta["attachments"] == ["a.png"]
    s.apply({"type": "intent", "intent": "task", "confidence": 0.9, "needs_plan": True})
    ch = s.apply({"type": "plan", "objective": "O", "success_criteria": ["c1"], "steps": ["a", "b"], "roles": ["web", "main"]})
    assert "plan" in ch and [x["role"] for x in s.steps] == ["web", "main"]
    s.apply({"type": "worker", "step": "s1", "role": "web", "status": "done", "summary": "found it"})
    assert [x["status"] for x in s.steps] == ["done", "todo"]
    s.apply({"type": "worker", "step": "s9", "role": "web", "status": "done", "summary": "x"})  # unknown id is ignored
    s.apply({"type": "verdict", "status": "done", "notes": ""})
    assert all(x["status"] == "done" for x in s.steps)
    ch = s.apply({"type": "final", "text": "ok", "stopped": None, "used": {"steps": 2, "tokens": 10, "cost": 0.001}})
    assert not s.busy and s.transcript[-1].kind == "agent" and s.used["steps"] == 2 and "usage" in ch


def test_session_error_final_stop_and_kill_states() -> None:
    s = Session()
    s.user_says("x")
    s.apply({"type": "final", "text": "partial", "stopped": "budget", "used": {}})
    assert "stopped: budget" in s.transcript[-1].text
    s.user_says("y")
    s.apply({"type": "error", "code": "quota_exhausted", "message": "out of credit"})
    assert not s.busy and s.transcript[-1].kind == "error"
    s.apply({"type": "killed", "threads": 2})
    assert s.killed
    s.apply({"type": "kill_released"})
    assert not s.killed
    assert s.apply({"type": "agent_name", "name": "Bé Na"}) == {"name"} and s.agent_name == "Bé Na"


def test_approvals_normalise_json_args_and_survive_a_reconnect() -> None:
    row = {"call_id": "c1", "thread_id": "t", "tool": "shell", "args": json.dumps({"command": "dir"}), "tainted": 1, "tier": "T2"}
    s = Session()
    s.apply({"type": "approvals", "items": [row]})  # pushed by the daemon on every (re)connect
    assert s.approvals["c1"]["args"] == {"command": "dir"} and s.approvals["c1"]["tainted"] is True
    s.apply({"type": "approval_request", "thread_id": "t", "approvals": [{**row, "call_id": "c2", "args": {"a": 1}}]})
    assert set(s.approvals) == {"c1", "c2"} and set(s.pending_for_thread()) == {"t"}
    s.apply({"type": "approvals", "items": []})
    assert not s.approvals
    assert normalize_approval({"args": "not json"})["args"] == {"raw": "not json"}


# ---- avatar state machine ---------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_avatar_follows_the_agent_and_falls_asleep() -> None:
    clk = Clock()
    a = AvatarState(sleep_after_s=60, clock=clk)
    assert a.current() == "idle" and a.next_change_in() == pytest.approx(60)
    a.user_sent()
    assert a.current() == "thinking"
    assert a.on_event({"type": "tool_call"}) == "working"
    assert a.on_event({"type": "plan"}) == "thinking"
    assert a.on_event({"type": "final", "text": "x"}) == "success"
    clk.t += 3
    assert a.current() == "idle"
    clk.t += 60
    assert a.current() == "sleeping"
    a.on_event({"type": "error", "code": "x"})
    assert a.current() == "error"


def test_avatar_priorities_approval_over_listening_over_speaking() -> None:
    a = AvatarState(clock=Clock())
    a.speaking = True
    assert a.current() == "speaking"
    a.listening = True
    assert a.current() == "listening"
    a.on_event({"type": "approval_request", "approvals": [{"call_id": "1"}]})
    assert a.current() == "awaiting_approval"
    a.pending_approvals = 0
    a.listening = a.speaking = False
    assert a.current() != "awaiting_approval"
    a.on_event({"type": "error", "code": "approval_pending"})  # a normal condition, not a failure animation
    assert a.current() != "error"


def test_disconnected_avatar_sleeps_and_a_stop_reason_shows_error() -> None:
    a = AvatarState(clock=Clock())
    a.connected = False
    assert a.current() == "sleeping"
    a.connected = True
    assert a.on_event({"type": "final", "text": "x", "stopped": "budget"}) == "error"


# ---- approval decisions -----------------------------------------------------------------------------------------------------
def card(**kw: Any) -> dict[str, Any]:
    base = {"call_id": "c1", "tool": "shell", "tier": "T2", "choice": "approve", "args": {"command": "dir"},
            "args_text": json.dumps({"command": "dir"}), "reason": "", "confirm": ""}
    return {**base, **kw}


def test_decisions_cover_approve_remember_edit_reject() -> None:
    d = build_decisions([card(), card(call_id="c2", choice="remember"),
                         card(call_id="c3", args_text='{"command": "whoami"}'),
                         card(call_id="c4", choice="reject", reason=" too risky ")])
    assert d["c1"] == {"action": "approve", "remember": False}
    assert d["c2"] == {"action": "approve", "remember": True}
    assert d["c3"] == {"action": "edit", "args": {"command": "whoami"}}
    assert d["c4"] == {"action": "reject", "feedback": "too risky"}


@pytest.mark.parametrize(("kw", "msg"), [
    ({"choice": None}, "choose an answer"),
    ({"args_text": "{nope"}, "not valid JSON"),
    ({"args_text": "[1]"}, "JSON object"),
    ({"tier": "T3"}, "CONFIRM"),
])
def test_decisions_reject_incomplete_answers(kw: dict[str, Any], msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        build_decisions([card(**kw)])


def test_t3_needs_the_confirm_word() -> None:
    assert build_decisions([card(tier="T3", confirm="CONFIRM")])["c1"] == {"action": "approve", "remember": False, "confirm": True}


# ---- hotkeys / autostart / voice helpers -----------------------------------------------------------------------------------
def test_parse_hotkey() -> None:
    assert parse_hotkey("ctrl+alt+k") == (MOD["ctrl"] | MOD["alt"] | NOREPEAT, ord("K"))
    assert parse_hotkey("Ctrl+Shift+F5")[1] == 0x74 and parse_hotkey("win+space")[1] == 0x20
    for bad in ("k", "ctrl+", "ctrl+alt+nope", "hyper+k", "", "ctrl+f99"):
        with pytest.raises(ValueError, match="hotkey|modifier|key"):
            parse_hotkey(bad)


def test_autostart_roundtrip_in_a_throwaway_registry_key() -> None:
    import sys
    import winreg

    if sys.platform != "win32":
        pytest.skip("Windows only")
    key = rf"Software\personal-agent-test-{uuid.uuid4().hex}"
    try:
        assert not autostart_enabled(key, "x")
        set_autostart(True, key, "x")
        assert autostart_enabled(key, "x")
        set_autostart(False, key, "x")
        assert not autostart_enabled(key, "x")
        set_autostart(False, key, "x")  # disabling twice is fine
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def test_voice_text_helpers() -> None:
    md = "# Title\n\nSee [docs](http://x.io/a) and `code`.\n\n```py\nprint(1)\n```\n- **bold** item"
    assert strip_markdown(md) == "Title See docs and code. bold item"
    assert split_sentences("Hello there. How are you? Fine!") == ["Hello there.", "How are you?", "Fine!"]
    long = "word " * 120
    assert all(len(p) <= 220 for p in split_sentences(long)) and "".join(split_sentences(long)).replace(" ", "") == long.replace(" ", "")
    cfg = TtsCfg()
    assert pick_voice("Xin chào bạn", cfg) == cfg.voice_vi and pick_voice("Hello there", cfg) == cfg.voice_en


def test_pcm_to_wav_and_level_meter(tmp_path: Path) -> None:
    pcm = (b"\x00\x10" * 8000)
    p = tmp_path / "a.wav"
    p.write_bytes(pcm_to_wav(pcm))
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 8000)
    assert rms(b"") == 0.0 and rms(b"\x00\x00" * 10) == 0.0 and rms(pcm) == pytest.approx(0x1000)


# ---- i18n ---------------------------------------------------------------------------------------------------------------------
def test_i18n_switches_language_and_falls_back_to_english() -> None:
    i18n.set_language("vi")
    assert i18n.tr("Send", "Gửi") == "Gửi" and i18n.tr("Only English") == "Only English"
    assert i18n.tr("{n} items", "{n} mục", n=3) == "3 mục"
    i18n.set_language("en")
    assert i18n.tr("Send", "Gửi") == "Send"
    assert i18n.resolve("vi") == "vi" and i18n.resolve("fr") in ("en", "vi")


def test_every_visible_string_has_a_vietnamese_translation() -> None:
    missing: list[str] = []
    for f in sorted(UI_SRC.glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            is_tr = isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr" and f.name != "i18n.py"
            if is_tr and len(node.args) < 2:  # type: ignore[attr-defined]
                missing.append(f"{f.name}:{node.lineno}")  # type: ignore[attr-defined]
    assert not missing, f"tr() without a Vietnamese string: {missing}"


# ---- workspace folder helper used by the wizard ------------------------------------------------------------------------------
def test_policy_add_path_edits_the_list_in_place_and_is_idempotent(tmp_path: Path) -> None:
    folder = tmp_path / "my work"
    folder.mkdir()
    before = policy_text()
    policy_add_path(str(folder))
    after = policy_text()
    assert str(folder) in after and after.count(str(folder)) == 1
    assert "# Default-deny posture" not in after or "# Default-deny posture" in before  # comments are never dropped
    policy_add_path(str(folder))
    assert policy_text() == after
    with pytest.raises(ValueError, match="existing folder"):
        policy_add_path(str(tmp_path / "nope"))
    assert before != after


def test_policy_add_path_also_handles_the_one_line_form_and_keeps_comments(tmp_path: Path) -> None:
    import os

    import yaml

    pol = Path(os.environ["PERSONAL_AGENT_CONFIG_DIR"]) / "policy.yaml"
    pol.write_text("# keep me\ndefault_tier: T3\napproval: {timeout_s: 5}\ncommands: {allow_patterns: []}\n"
                   "paths:\n  allow: ['C:\\a']   \n  deny: []\n", encoding="utf-8")
    folder = tmp_path / "it's"
    folder.mkdir()
    policy_add_path(str(folder))
    text = pol.read_text(encoding="utf-8")
    assert text.startswith("# keep me")
    allowed = yaml.safe_load(text)["paths"]["allow"]
    assert allowed[0] == "C:\\a" and str(folder) in allowed  # the quote in the folder name survived

