"""Every host write goes through here so it can be undone: old content is backed up first."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from personal_agent.settings.settings import data_dir

_ID = re.compile(r"^[0-9a-f]{12}$")


def new_apply_id() -> str:
    return uuid.uuid4().hex[:12]


def _dir(apply_id: str) -> Path:
    if not _ID.fullmatch(apply_id):
        raise ValueError("invalid apply id")
    return data_dir() / "backups" / apply_id


def _journal(apply_id: str) -> list[dict[str, object]]:
    p = _dir(apply_id) / "journal.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _record(apply_id: str, target: Path) -> None:
    d = _dir(apply_id)
    d.mkdir(parents=True, exist_ok=True)
    entries = _journal(apply_id)
    existed = target.is_file()
    backup = None
    if existed:
        backup = f"{len(entries)}.bak"
        shutil.copy2(target, d / backup)
    entries.append({"path": str(target), "existed": existed, "backup": backup})
    (d / "journal.json").write_text(json.dumps(entries), encoding="utf-8")  # journal first, then the change


def write_with_backup(target: Path, data: bytes, apply_id: str) -> None:
    _record(apply_id, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)  # atomic: readers never see a half-written file


def delete_with_backup(target: Path, apply_id: str) -> None:
    _record(apply_id, target)
    target.unlink(missing_ok=True)


def undo(apply_id: str, allow: Callable[[Path], bool] | None = None) -> list[str]:
    """Restore every file touched under `apply_id` (newest first). Can only be done once.

    `allow` re-validates each journal path (a journal on disk is not trusted to name arbitrary files)."""
    d = _dir(apply_id)
    j = d / "journal.json"
    if not j.exists():
        raise FileNotFoundError("no such change (or it was already undone)")
    entries = _journal(apply_id)
    if allow is not None and not all(allow(Path(str(e["path"]))) for e in entries):
        raise ValueError("the change journal points outside the allowed folders; refusing to undo")
    restored = []
    for e in reversed(entries):
        target = Path(str(e["path"]))
        if e["existed"]:
            shutil.copy2(d / str(e["backup"]), target)
        else:
            target.unlink(missing_ok=True)
        restored.append(str(target))
    j.rename(d / "journal.undone.json")
    return restored
