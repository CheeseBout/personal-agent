"""More file tools: list / search (T0) and edit / move / delete (T2, always undoable via backups)."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.sandbox.backup import delete_with_backup, new_apply_id, write_with_backup
from personal_agent.tools.files import _parent_scope, folder_scope, path_args, resolve
from personal_agent.tools.registry import cap, policy, register

MAX_MOVE_BYTES = 20_000_000
MAX_SCAN_BYTES = 2_000_000


def _ok(p: Path) -> str | None:
    d = policy().check_path(p)
    return None if d.allowed else f"ERROR: access denied: {d.reason}"


@tool
def file_list(path: str = ".", pattern: str = "*", recursive: bool = False, max_items: int = 200) -> str:
    """List files in a workspace folder (glob pattern, optional recursion). Liệt kê tệp trong thư mục workspace."""
    p = resolve(path)
    if err := _ok(p):
        return err
    if not p.is_dir():
        return "ERROR: not a folder"
    it = p.rglob("*") if recursive else p.iterdir()
    rows = []
    for f in sorted(it):
        if fnmatch.fnmatch(f.name, pattern) and not f.is_symlink() and policy().check_path(f).allowed:
            rows.append(f"{'d' if f.is_dir() else '-'} {f.relative_to(p)}" + ("" if f.is_dir() else f"  ({f.stat().st_size} B)"))
        if len(rows) >= max(1, min(max_items, 1000)):
            rows.append("...[more not shown]")
            break
    return cap("\n".join(rows) or "(empty)")


@tool
def file_search(query: str, path: str = ".", glob: str = "*", max_results: int = 50) -> str:
    """Find lines containing text (case-insensitive) in workspace files. Tìm dòng chứa văn bản trong các tệp."""
    root = resolve(path)
    if err := _ok(root):
        return err
    q, out = query.lower(), []
    for f in sorted(root.rglob("*") if root.is_dir() else [root]):
        if not f.is_file() or f.is_symlink() or not fnmatch.fnmatch(f.name, glob) or not policy().check_path(f).allowed:
            continue
        if f.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if q in line.lower():
                    out.append(f"{f.relative_to(root) if root.is_dir() else f.name}:{n}: {line.strip()[:200]}")
                    if len(out) >= max(1, min(max_results, 200)):
                        return cap("\n".join(out) + "\n...[limit reached]")
        except (UnicodeDecodeError, OSError):
            continue
    return cap("\n".join(out) or "no matches")


@tool
def file_edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """Replace exact text in a file (must match exactly once unless replace_all). Needs approval; undoable.
    Thay thế đoạn văn bản chính xác trong tệp (cần duyệt, hoàn tác được)."""
    p = resolve(path)
    if err := _ok(p):
        return err
    if not old:
        return "ERROR: 'old' must not be empty"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return f"ERROR: cannot read file: {type(e).__name__}"
    n = text.count(old)
    if n == 0 or (n > 1 and not replace_all):
        return f"ERROR: 'old' matched {n} times; make it unique or set replace_all"
    aid = new_apply_id()
    write_with_backup(Path(os.path.realpath(p)), text.replace(old, new).encode("utf-8"), aid)
    return f"edited {p} ({n} replacement(s)). Undo with undo_change('{aid}')."


@tool
def file_move(source: str, destination: str) -> str:
    """Move or rename a single file inside the workspace. Needs approval; undoable. Di chuyển/đổi tên một tệp."""
    s, d = resolve(source), resolve(destination)
    if (err := _ok(s)) or (err := _ok(d)):
        return err
    if not s.is_file() or s.is_symlink():
        return "ERROR: source must be a regular file"
    if d.exists():
        return "ERROR: destination already exists"
    if s.stat().st_size > MAX_MOVE_BYTES:
        return "ERROR: file is too large to move safely"
    aid = new_apply_id()
    write_with_backup(Path(os.path.realpath(d)), s.read_bytes(), aid)
    delete_with_backup(Path(os.path.realpath(s)), aid)
    return f"moved {s} -> {d}. Undo with undo_change('{aid}')."


@tool
def file_delete(path: str) -> str:
    """Delete ONE file (not folders). Needs approval; the content is backed up so it can be restored.
    Xóa một tệp (không xóa thư mục); có sao lưu để khôi phục."""
    p = resolve(path)
    if err := _ok(p):
        return err
    if not p.is_file() or p.is_symlink():
        return "ERROR: only a single regular file can be deleted"
    aid = new_apply_id()
    delete_with_backup(Path(os.path.realpath(p)), aid)
    return f"deleted {p}. Undo with undo_change('{aid}')."


def _edit_preview(a: dict[str, Any]) -> str:
    return f"Edit {resolve(str(a.get('path', '')))}\n--- replace ---\n{a.get('old', '')[:2000]}\n--- with ---\n{a.get('new', '')[:2000]}"


register(file_list, ToolMeta(tier="T0", paths_fn=path_args("path"), scope_fn=folder_scope))
register(file_search, ToolMeta(tier="T0", untrusted_output=True, paths_fn=path_args("path"), scope_fn=folder_scope))
register(file_edit, ToolMeta(tier="T2", side_effects=True, preview_fn=_edit_preview, scope_fn=_parent_scope,
                             paths_fn=path_args("path")))
register(file_move, ToolMeta(tier="T2", side_effects=True, risk="high",
                             preview_fn=lambda a: f"Move\n  {resolve(str(a.get('source', '')))}\n  -> {resolve(str(a.get('destination', '')))}",
                             paths_fn=path_args("source", "destination")))
register(file_delete, ToolMeta(tier="T2", side_effects=True, risk="high",
                               preview_fn=lambda a: f"DELETE {resolve(str(a.get('path', '')))} (backed up, undoable)",
                               paths_fn=path_args("path")))
