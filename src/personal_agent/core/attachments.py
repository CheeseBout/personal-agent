"""Turn a chat message's attachments (images, documents, text, audio) into one multimodal user message."""

from __future__ import annotations

import base64
import binascii
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

from personal_agent.rag.loaders import SUPPORTED as DOC_TYPES
from personal_agent.rag.loaders import load

MAX_FILES = 4
MAX_BYTES = 5_000_000
MAX_TEXT_CHARS = 60_000
IMAGES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py", ".log", ".ini", ".toml", ".xml", ".html"}


def _name(raw: Any) -> str:
    return PurePath(str(raw or "file")).name.replace("\n", " ")[:80] or "file"


def _decode(a: dict[str, Any]) -> bytes:
    try:
        data = base64.b64decode(str(a.get("data", "")), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"attachment '{_name(a.get('name'))}' is not valid base64") from e
    if not data or len(data) > MAX_BYTES:
        raise ValueError(f"attachment '{_name(a.get('name'))}' is empty or larger than 5 MB")
    return data


def build_content(text: str, attachments: Any, transcribe: Callable[[bytes, str], str]) -> str | list[str | dict[str, Any]]:
    """Plain text when there are no attachments, otherwise a content-block list (text first, then images).

    `transcribe(audio, mime)` is injected so this stays testable offline. Raises ValueError for anything invalid."""
    if not attachments:
        return text
    if not isinstance(attachments, list) or len(attachments) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} attachments")
    notes: list[str] = []
    images: list[dict[str, Any]] = []
    budget = MAX_TEXT_CHARS
    for a in attachments:
        if not isinstance(a, dict):
            raise ValueError("bad attachment")
        name, mime, data = _name(a.get("name")), str(a.get("mime", "")).lower(), _decode(a)
        ext = Path(name).suffix.lower()
        if mime in IMAGES:
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
            notes.append(f"[attached image: {name}]")
        elif mime.startswith("audio/"):
            heard = transcribe(data, mime)
            notes.append(f"[voice message: {name}] {heard}")
        elif ext in DOC_TYPES:
            with tempfile.TemporaryDirectory(prefix="agent-attach-") as d:
                p = Path(d) / f"doc{ext}"
                p.write_bytes(data)
                body = "\n\n".join(s.text for s in load(p))
            notes.append(f"[attached document: {name}]\n{body[:budget]}")
            budget = max(0, budget - len(body))
        elif ext in TEXT_EXT or mime.startswith("text/"):
            body = data.decode("utf-8", errors="replace")
            notes.append(f"[attached file: {name}]\n{body[:budget]}")
            budget = max(0, budget - len(body))
        else:
            raise ValueError(f"unsupported attachment type for '{name}'")
    full = "\n\n".join([t for t in (text.strip(), *notes) if t])
    return [{"type": "text", "text": full}, *images] if images else full
