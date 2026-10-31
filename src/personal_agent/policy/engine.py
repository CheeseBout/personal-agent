"""Policy engine (Phase 1 subset): tiers, path scope, command rules. Hot-reloads on file change.

HITL for T2 arrives in Phase 3, so until then T2/T3 are refused.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from personal_agent.settings.settings import config_dir

# Also blocks $ ~ % so env/home expansion cannot smuggle a path past the scope check.
_SHELL_META = re.compile(r"[;&|<>`\n\r$~%]")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class ToolMeta:
    tier: str  # T0..T3
    side_effects: bool = False
    needs_sandbox: bool = False
    network: bool = False


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.expandvars(str(p))))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


class PolicyEngine:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or config_dir() / "policy.yaml"
        self._mtime = 0.0
        self._cfg: dict[str, Any] = {}

    @property
    def cfg(self) -> dict[str, Any]:
        m = self._path.stat().st_mtime
        if m != self._mtime:
            self._cfg = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            self._mtime = m
        return self._cfg

    def authorize(self, tool: str | None, meta: ToolMeta | None) -> Decision:
        if tool is None or meta is None:
            return Decision(False, f"unknown tool '{tool}' is denied by default")
        if meta.tier in ("T0", "T1"):
            return Decision(True)
        return Decision(False, f"{meta.tier} tools need user approval, which is not available yet")

    def check_path(self, path: str | Path) -> Decision:
        raw = os.path.expandvars(str(path))
        if raw.startswith(("\\\\", "//")) or ":" in raw[2:]:
            return Decision(False, "UNC, device and alternate-data-stream paths are not allowed")
        if any(s not in (".", "..") and s != s.rstrip(". ") for s in re.split(r"[\\/]", raw)):
            return Decision(False, "path segments ending in a dot or space are not allowed")
        p = _norm(path)
        paths = self.cfg.get("paths", {})
        for d in paths.get("deny", []):
            if _inside(p, _norm(d)):
                return Decision(False, "path is in a denied location")
        if any(_inside(p, _norm(a)) for a in paths.get("allow", [])):
            return Decision(True)
        return Decision(False, "path is outside the allowed folders")

    def check_command(self, command: str) -> Decision:
        c = self.cfg.get("commands", {})
        if _SHELL_META.search(command):
            return Decision(False, "command chaining/redirection characters are not allowed")
        for pat in c.get("deny_patterns", []):
            if re.search(pat, command, re.IGNORECASE):
                return Decision(False, "command matches a deny pattern")
        if any(re.fullmatch(p, command.strip()) for p in c.get("allow_patterns", [])):
            return Decision(True)
        return Decision(False, "command is not on the allowlist (approval not available yet)")
