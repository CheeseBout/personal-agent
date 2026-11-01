"""Policy engine: tiers, path/command/domain rules, rate limits. Hot-reloads when policy.yaml changes.

Tiers: T0 read-only (auto) | T1 reversible, contained (auto unless tainted) | T2 needs human approval |
T3 denied unless explicitly enabled (and then needs a double confirmation).
"""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import structlog
import yaml

from personal_agent.settings.settings import policy_file

# Also blocks $ ~ % so env/home expansion cannot smuggle a path past the scope check.
_SHELL_META = re.compile(r"[;&|<>`\n\r$~%]")


log = structlog.get_logger()
REQUIRED_KEYS = ("default_tier", "paths", "commands", "approval")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class ToolMeta:
    tier: str  # T0..T3 (static default)
    side_effects: bool = False
    needs_sandbox: bool = False
    network: bool = False
    untrusted_output: bool = False  # result may contain attacker-controlled text -> taints the task
    risk: str = "medium"  # shown on the approval card: low | medium | high
    attended_only: bool = False  # never allowed in unattended (scheduled) runs, e.g. creating more schedules
    tier_fn: Callable[[dict[str, Any]], str] | None = None  # tier depends on the arguments
    preview_fn: Callable[[dict[str, Any]], str] | None = None  # read-only preview/diff for the approval card
    scope_fn: Callable[[dict[str, Any]], str | None] | None = None  # "approve and remember" scope; None = never remember


@dataclass(frozen=True)
class Gate:
    action: Literal["allow", "approve", "deny"]
    tier: str
    reason: str = ""
    risk: str = "low"
    confirm: bool = False  # T3: the user must confirm twice


def _norm_cmd(c: str) -> str:
    """Undo cheap obfuscation: PowerShell backtick escapes, cmd carets, quotes, runs of whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[`^'\"]", "", c)).strip()


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.expandvars(str(p))))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


class PolicyEngine:
    def __init__(self, path: Path | None = None) -> None:
        self._fixed = path  # tests / explicit path; otherwise the effective policy file is looked up on every reload check
        self._loaded: Path | None = None
        self._mtime = 0.0
        self._cfg: dict[str, Any] = {}
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    @property
    def cfg(self) -> dict[str, Any]:
        path = self._fixed or policy_file()  # your %APPDATA% copy takes over as soon as it exists
        m = path.stat().st_mtime
        if m != self._mtime or path != self._loaded:
            try:
                new = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if not isinstance(new, dict) or any(k not in new for k in REQUIRED_KEYS):
                    # an empty / truncated file (a half-finished save) is not "no rules": it is a broken policy
                    raise TypeError("policy must be a YAML mapping with " + ", ".join(REQUIRED_KEYS))
            except (yaml.YAMLError, TypeError, OSError) as e:
                if not self._cfg:  # never had a good policy: fail closed (callers get an error, nothing is allowed)
                    raise
                # A half-saved or broken edit must not switch the rules off or break every turn: keep the last good rules.
                log.warning("policy_reload_failed_keeping_previous", error=type(e).__name__)
                self._mtime, self._loaded = m, path
                return self._cfg
            self._cfg, self._mtime, self._loaded = new, m, path
        return self._cfg

    # ---- tool-level decision -------------------------------------------------------------------
    def evaluate(self, name: str, meta: ToolMeta | None, args: dict[str, Any], tainted: bool = False) -> Gate:
        if meta is None:
            return Gate("deny", "T3", f"unknown tool '{name}' is denied by default", "high")
        tier = meta.tier_fn(args) if meta.tier_fn else meta.tier
        risk = meta.risk
        if (r := self.rate_limited(name)) is not None:
            return Gate("deny", tier, r, risk)
        if tier == "T0":
            return Gate("allow", tier)
        if tier == "T1":
            auto = self.cfg.get("tiers", {}).get("T1_auto_approve", True)
            if tainted and meta.side_effects:
                return Gate("approve", "T2", "this action follows untrusted content (web/file/tool output)", risk)
            return Gate("allow", tier) if auto else Gate("approve", tier, "T1 auto-approval is off", risk)
        if tier == "T2":
            why = "needs your approval" + (" (follows untrusted content)" if tainted else "")
            return Gate("approve", tier, why, risk)
        if self.cfg.get("tiers", {}).get("T3_allow_with_confirmation", False):
            return Gate("approve", "T3", "dangerous action: explicit confirmation required", "critical", confirm=True)
        return Gate("deny", "T3", "dangerous (T3) actions are disabled by policy", "critical")

    def rate_limited(self, name: str) -> str | None:
        """Sliding-window limit per tool; counts the attempt when it is allowed."""
        limit = int(self.cfg.get("rate_limits", {}).get("per_tool_per_minute", 0))
        if limit <= 0:
            return None
        now, q = time.monotonic(), self._calls[name]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return f"rate limit: more than {limit} '{name}' calls in a minute"
        q.append(now)
        return None

    # ---- argument-level checks used by the tools ---------------------------------------------------
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
        """Strict allowlist used for commands that run WITHOUT approval."""
        c = self.cfg.get("commands", {})
        if _SHELL_META.search(command):
            return Decision(False, "command chaining/redirection characters are not allowed")
        if self.is_hard_denied_command(command):
            return Decision(False, "command matches a deny pattern")
        if any(re.fullmatch(p, command.strip()) for p in c.get("allow_patterns", [])):
            return Decision(True)
        return Decision(False, "command is not on the allowlist")

    def is_hard_denied_command(self, command: str) -> str | None:
        """Deny patterns apply even to commands a human could approve.

        Best-effort text matching on a normalised command (PowerShell has many spellings): the real boundary
        for anything not allow-listed is the human reading the exact command on the approval card."""
        norm = _norm_cmd(command)
        for pat in self.cfg.get("commands", {}).get("deny_patterns", []):
            if re.search(pat, command, re.IGNORECASE) or re.search(pat, norm, re.IGNORECASE):
                return "command matches a deny pattern (never allowed, even with approval)"
        return None

    def check_domain(self, url: str) -> Decision:
        host = (urlparse(url).hostname or "").lower()
        allow = [d.lower() for d in self.cfg.get("network", {}).get("domain_allowlist", [])]
        if host and any(host == d or host.endswith("." + d) for d in allow):
            return Decision(True)
        return Decision(False, "domain is not on the allowlist")
