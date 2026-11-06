"""File tools (scoped by policy path rules). Writes are T2 (approval) and always undoable."""

from __future__ import annotations

import difflib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.sandbox.backup import new_apply_id, undo, write_with_backup
from personal_agent.tools.registry import cap, policy, register

MAX_WRITE_CHARS = 1_000_000


def workspace() -> Path:
    allow = policy().cfg.get("paths", {}).get("allow", [])
    return Path(os.path.expandvars(allow[0])) if allow else Path.cwd()


def resolve(path: str) -> Path:
    p = Path(os.path.expandvars(path))
    return p if p.is_absolute() else workspace() / p


def _opened_path(f: Any) -> str | None:
    """Final path of an already-open file (Windows GetFinalPathNameByHandle), or None if it cannot be determined."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import msvcrt

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(f.fileno()), buf, 1024, 0)
        if not 0 < n < 1024:
            return None
        value = buf.value
        return value if value.startswith("\\\\?\\UNC\\") else value.removeprefix("\\\\?\\")  # a UNC path keeps its prefix (and is then denied)
    except (OSError, ValueError, AttributeError):
        return None


@tool
def file_read(path: str, max_chars: int = 20_000) -> str:
    """Read a UTF-8 text file. Relative paths are resolved inside the workspace folder.
    Đọc nội dung một tệp văn bản (đường dẫn tương đối tính từ thư mục workspace)."""
    p = resolve(path)
    d = policy().check_path(p)
    if not d.allowed:
        return f"ERROR: access denied: {d.reason}"
    p = Path(os.path.realpath(p))  # open exactly what was checked
    try:
        if not p.is_file():
            return "ERROR: file not found"
        with p.open("rb") as f:
            if (opened := _opened_path(f)) is not None and not policy().check_path(opened).allowed:
                return "ERROR: access denied: the file changed to a location outside the allowed folders"
            data = f.read(max_chars * 4 + 1)  # bound memory on huge files
    except OSError as e:
        return f"ERROR: cannot read file: {e.strerror or type(e).__name__}"
    return cap(data.decode("utf-8", errors="replace"), min(max_chars, 20_000))


def _write_preview(args: dict[str, Any]) -> str:
    p, new = resolve(str(args.get("path", ""))), str(args.get("content", ""))
    if not (d := policy().check_path(p)).allowed:
        return f"Target {p}\nBLOCKED: {d.reason}"
    if p.is_file():
        try:
            old = p.read_text(encoding="utf-8").splitlines(keepends=True)
        except (UnicodeDecodeError, OSError):
            return f"Target {p}\nOVERWRITES an existing binary/unreadable file ({len(new)} chars of new text)."
        diff = "".join(difflib.unified_diff(old, new.splitlines(keepends=True), "current", "new"))
        return f"Target {p}\nOVERWRITES existing file:\n{diff[:8000] or '(identical content)'}"
    return f"Target {p}\nNEW FILE ({len(new)} chars):\n{new[:4000]}"


@tool
def file_write(path: str, content: str) -> str:
    """Create or overwrite a UTF-8 text file in the workspace (needs the user's approval; undoable).
    Tạo hoặc ghi đè một tệp văn bản trong workspace (cần người dùng duyệt, có thể hoàn tác)."""
    p = resolve(path)
    if not (d := policy().check_path(p)).allowed:
        return f"ERROR: access denied: {d.reason}"
    if len(content) > MAX_WRITE_CHARS:
        return f"ERROR: content is larger than {MAX_WRITE_CHARS} characters"
    aid = new_apply_id()
    try:
        write_with_backup(Path(os.path.realpath(p)), content.encode("utf-8"), aid)
    except OSError as e:
        return f"ERROR: cannot write file: {e.strerror or type(e).__name__}"
    return f"written {len(content)} chars to {p}. Undo with undo_change('{aid}')."


@tool
def undo_change(apply_id: str) -> str:
    """Undo a previous file_write / sandbox_apply by its id (restores the backed-up files).
    Hoàn tác một thay đổi trước đó theo id."""
    try:
        restored = undo(apply_id, allow=lambda p: policy().check_path(p).allowed)
    except (ValueError, FileNotFoundError) as e:
        return f"ERROR: {e}"
    except OSError as e:
        return f"ERROR: could not restore: {e.strerror or type(e).__name__}"
    return "restored:\n" + "\n".join(restored)


def path_args(*keys: str) -> Callable[[dict[str, Any]], list[str]]:
    """Which resolved paths a call touches (the gate asks a human when any is outside the allowed folders)."""
    return lambda args: [str(resolve(str(args.get(k) or "."))) for k in keys]


def folder_scope(args: dict[str, Any]) -> str | None:
    """'Approve and remember' for READS outside the allowed folders: per folder, never for denied locations."""
    p = resolve(str(args.get("path") or "."))
    if policy().path_zone(p) != "ask":
        return None
    return os.path.normcase(os.path.realpath(p if p.is_dir() else p.parent))


def _parent_scope(args: dict[str, Any]) -> str | None:
    p = resolve(str(args.get("path", "")))
    return os.path.normcase(os.path.realpath(p.parent)) if policy().check_path(p).allowed else None


register(file_read, ToolMeta(tier="T0", untrusted_output=True, paths_fn=path_args("path"), scope_fn=folder_scope))
register(file_write, ToolMeta(tier="T2", side_effects=True, risk="medium", preview_fn=_write_preview, scope_fn=_parent_scope,
                               paths_fn=path_args("path")))
register(undo_change, ToolMeta(tier="T2", side_effects=True, risk="medium",
                               preview_fn=lambda a: f"Restore all files changed under change id {a.get('apply_id')}"))
