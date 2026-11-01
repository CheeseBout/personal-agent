"""Size limits that protect the daemon and keep the UI from losing a message to an oversized WebSocket frame."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from personal_agent.core import attachments
from personal_agent.core.attachments import MAX_TOTAL_BYTES, WS_MAX_BYTES, build_content


def att(n: int, name: str = "a.png") -> dict[str, Any]:
    return {"name": name, "mime": "image/png", "data": base64.b64encode(b"\x89PNG" + b"0" * (n - 4)).decode()}


def test_total_attachment_size_is_capped_but_several_large_files_fit() -> None:
    ok = build_content("look", [att(5_000_000, f"{i}.png") for i in range(3)], lambda a, m: "")
    assert isinstance(ok, list) and len(ok) == 4  # text + 3 images
    with pytest.raises(ValueError, match="15 MB together"):
        build_content("look", [att(5_000_000, f"{i}.png") for i in range(4)], lambda a, m: "")


def test_the_websocket_frame_limit_covers_the_largest_allowed_message() -> None:
    wire = MAX_TOTAL_BYTES * 4 // 3 + 4 * 1024  # base64 inflates by 4/3, plus JSON overhead
    assert WS_MAX_BYTES > wire > 16 * 1024 * 1024  # bigger than uvicorn's 16 MB default, which used to close the socket


def test_the_daemon_is_started_with_that_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_agent.core import main as core_main

    seen: dict[str, Any] = {}
    monkeypatch.setattr(core_main.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(core_main, "setup_logging", lambda: None)
    monkeypatch.setattr(core_main, "create_app", lambda: object())
    core_main.main()
    assert seen["ws_max_size"] == WS_MAX_BYTES and seen["host"] == "127.0.0.1"


def test_it_refuses_to_bind_to_a_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_agent.core import main as core_main
    from personal_agent.settings.settings import load_settings

    s = load_settings()
    monkeypatch.setattr(s.server, "host", "0.0.0.0")
    monkeypatch.setattr(core_main, "setup_logging", lambda: None)
    with pytest.raises(SystemExit, match="non-local"):
        core_main.main()
    assert attachments.MAX_FILES == 4
