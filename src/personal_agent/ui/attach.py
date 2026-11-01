"""Build the `attachments` payload of a chat message from files, clipboard images and recordings."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

MAX_BYTES = 5_000_000
MAX_TOTAL = 15_000_000  # per message; the daemon refuses more
_EXTRA = {".md": "text/markdown", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          ".webp": "image/webp", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


def from_bytes(name: str, data: bytes, mime: str | None = None) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise ValueError(f"{name} is larger than 5 MB")
    mime = mime or _EXTRA.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {"name": name, "mime": mime, "data": base64.b64encode(data).decode("ascii")}


def from_path(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"{p.name} is not a file")
    if p.stat().st_size > MAX_BYTES:
        raise ValueError(f"{p.name} is larger than 5 MB")
    return from_bytes(p.name, p.read_bytes())


def from_image(img: QImage, name: str = "pasted.png") -> dict[str, Any]:
    ba, buf = QByteArray(), QBuffer()
    buf.setBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")  # type: ignore[call-overload]
    return from_bytes(name, bytes(ba.data()), "image/png")
