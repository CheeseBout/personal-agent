"""Artifacts: big tool outputs live on disk; the model keeps a digest and reads slices on demand."""

from __future__ import annotations

import re
import uuid

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.settings.settings import data_dir
from personal_agent.tools.registry import cap, register

_ID = re.compile(r"^[0-9a-f]{16}$")


def store_artifact(text: str) -> str:
    d = data_dir() / "artifacts"
    d.mkdir(exist_ok=True)
    aid = uuid.uuid4().hex[:16]
    (d / f"{aid}.txt").write_text(text, encoding="utf-8")
    return aid


@tool
def artifact_read(artifact_id: str, offset: int = 0, length: int = 6000) -> str:
    """Read a slice of a stored large tool output by its artifact id.
    Đọc một đoạn của kết quả công cụ lớn đã lưu theo artifact id."""
    if not _ID.fullmatch(artifact_id):
        return "ERROR: invalid artifact id"
    p = data_dir() / "artifacts" / f"{artifact_id}.txt"
    if not p.is_file():
        return "ERROR: artifact not found"
    text = p.read_text(encoding="utf-8")
    return cap(text[max(0, offset): max(0, offset) + max(1, min(length, 20_000))])


register(artifact_read, ToolMeta(tier="T0", untrusted_output=True))
