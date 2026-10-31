"""Sandbox tools: stage host files -> run code in Docker -> review the changeset -> apply to the host."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.sandbox.backup import new_apply_id
from personal_agent.sandbox.manager import DockerSandbox, SandboxUnavailable
from personal_agent.sandbox.staging import Change, Staging, describe
from personal_agent.settings.settings import data_dir
from personal_agent.tools.files import workspace
from personal_agent.tools.registry import cap, policy, register


@lru_cache(maxsize=1)
def staging() -> Staging:
    return Staging(data_dir() / "sandbox", policy(), workspace())


@lru_cache(maxsize=1)
def sandbox() -> DockerSandbox:
    return DockerSandbox(lambda: policy().cfg.get("sandbox", {}), staging().work)


def reset_singletons() -> None:
    sandbox.cache_clear()
    staging.cache_clear()


def _selected(files: list[str] | None) -> list[Change]:
    return [c for c in staging().changes() if files is None or c.rel in files]


@tool
def sandbox_stage(paths: list[str]) -> str:
    """Copy files/folders from the workspace into the isolated sandbox so code can work on copies.
    Sao chép tệp/thư mục vào sandbox để xử lý trên bản sao (bản gốc không bị đụng tới)."""
    try:
        staged = staging().stage(paths)
    except ValueError as e:
        return f"ERROR: {e}"
    return f"staged {len(staged)} file(s) into the sandbox at /work: " + ", ".join(staged[:50])


@tool
def sandbox_run(command: str, network: bool = False, timeout_s: int = 60) -> str:
    """Run a shell command inside the Docker sandbox (cwd /work, no network unless network=true, which needs approval).
    Chạy lệnh trong sandbox Docker (không có mạng trừ khi network=true và được duyệt)."""
    try:
        rc, out = sandbox().exec(command, timeout_s, network)
    except SandboxUnavailable as e:
        return f"ERROR: sandbox unavailable: {e}"
    return cap(f"exit={rc}\n{out}")


@tool
def sandbox_changes() -> str:
    """List what the sandbox changed compared with the original files (added / modified / deleted) with diffs.
    Liệt kê các thay đổi trong sandbox so với bản gốc."""
    try:
        return cap(describe(staging().changes()))
    except ValueError as e:
        return f"ERROR: {e}"


def _apply_tier(args: dict[str, Any]) -> str:
    try:
        ch = _selected(args.get("files"))
    except ValueError:
        return "T2"  # cannot even review it: never auto-approve
    only_new_in_output = all(c.status == "added" and staging()._in_output_dir(c.target) for c in ch)
    return "T1" if only_new_in_output else "T2"


def _apply_preview(args: dict[str, Any]) -> str:
    try:
        return describe(_selected(args.get("files")))[:20_000]
    except ValueError as e:
        return f"cannot review the changes: {e}"


@tool
def sandbox_apply(files: list[str] | None = None) -> str:
    """Apply sandbox changes to the real files (all, or only the listed names). New files in the output folder are
    auto-approved; changing or deleting existing files needs the user's approval. Undoable.
    Áp dụng thay đổi từ sandbox vào tệp thật (sửa/xóa tệp có sẵn cần người dùng duyệt)."""
    sandbox().stop()  # nothing in the container may touch the staged files while they are being applied
    aid = new_apply_id()
    try:
        done = staging().apply(files, aid)
    except (OSError, ValueError) as e:
        return f"ERROR: could not apply: {e}"
    return "\n".join(done or ["nothing to apply"]) + f"\nUndo with undo_change('{aid}')."


register(sandbox_stage, ToolMeta(tier="T1", risk="low"))
register(sandbox_run, ToolMeta(
    tier="T1", side_effects=True, needs_sandbox=True, untrusted_output=True,
    tier_fn=lambda a: "T2" if a.get("network") else "T1", risk="medium",
    preview_fn=lambda a: f"Run in the sandbox{' WITH NETWORK ACCESS' if a.get('network') else ''}:\n{a.get('command', '')}"))
register(sandbox_changes, ToolMeta(tier="T0"))
register(sandbox_apply, ToolMeta(tier="T2", side_effects=True, risk="high", tier_fn=_apply_tier, preview_fn=_apply_preview))
