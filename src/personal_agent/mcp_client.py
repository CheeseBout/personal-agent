"""MCP client: external tool servers from %APPDATA%\\personal-agent\\mcp.yaml (a file the agent cannot write).

Every MCP tool is namespaced `<server>_<tool>`, registered with policy metadata (default tier T2 = needs approval,
override per tool in the config) and its output is treated as untrusted. A broken server never blocks the others.

mcp.yaml:
  servers:
    files:                                   # stdio server
      command: npx
      args: [-y, "@modelcontextprotocol/server-filesystem", "C:/some/dir"]
      env: {TOKEN: "${MY_SECRET_NAME}"}      # ${NAME} is read from .env / Credential Manager
      enabled: true
      tools: {read_file: T0}                 # optional per-tool tier (T0|T1|T2); everything else stays T2
    remote:                                  # streamable-http server
      url: http://127.0.0.1:9000/mcp
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

import structlog
import yaml
from langchain_core.tools import BaseTool

from personal_agent.observability.logging import redact, register_secret
from personal_agent.policy.engine import ToolMeta
from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import data_dir
from personal_agent.tools import registry
from personal_agent.tools.registry import policy

log = structlog.get_logger()
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
_loaded: dict[str, str] = {}  # registered tool name -> server it came from
_details: dict[str, dict[str, Any]] = {}  # server -> {"tools": [{"name","tier"}], "skipped": [{"name","reason"}]} from the last load
pending: set[str] = set()  # servers whose config changed since the last load (their change needs a reload)
_TIERS = ("T0", "T1", "T2")


def config_path() -> Any:
    return data_dir() / "mcp.yaml"


def read_config() -> dict[str, Any]:
    """The servers section of mcp.yaml. Raises ValueError (a message for the user) when the file cannot be read or is not valid YAML."""
    p = config_path()
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        raise ValueError(f"mcp.yaml cannot be read: {type(e).__name__}") from e
    return cfg if isinstance(cfg, dict) and isinstance(cfg.get("servers"), dict) else {"servers": {}}


def _save(cfg: dict[str, Any]) -> None:
    """Write atomically: a crash or a full disk must not leave a half-written config that disables every server."""
    p = config_path()
    tmp = p.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        raise ValueError(f"mcp.yaml cannot be saved: {e.strerror or type(e).__name__}") from e


def set_enabled(name: str, enabled: bool) -> bool:
    cfg = read_config()
    if not isinstance(spec := cfg["servers"].get(name), dict):
        return False
    spec["enabled"] = bool(enabled)
    _save(cfg)
    pending.add(name)
    return True


def validate_spec(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Check a server entry typed in the UI and return the clean version to store. Raises ValueError with a user-facing message.
    Tool tiers cannot be set here: every tool of a new server needs approval (T0 is only possible by editing mcp.yaml by hand)."""
    if not _NAME.match(name):
        raise ValueError("the name must be 1-64 letters, digits, '_' or '-'")
    url, command = str(spec.get("url") or "").strip(), str(spec.get("command") or "").strip()
    if bool(url) == bool(command):
        raise ValueError("give either a command or a URL")
    out: dict[str, Any] = {"enabled": False}  # added disabled: nothing runs until the user reviews it and enables it
    if url:
        if not re.match(r"^https?://[^\s/]+", url):
            raise ValueError("the URL must start with http:// or https://")
        out["url"] = url
    else:
        args = spec.get("args") or []
        env = spec.get("env") or {}
        if not isinstance(args, list) or not isinstance(env, dict):
            raise ValueError("args must be a list and env a mapping")
        if any(not _NAME.match(str(k)) for k in env):
            raise ValueError("environment variable names may only use letters, digits, '_' or '-'")
        out["command"], out["args"] = command, [str(a) for a in args]
        if env:
            out["env"] = {str(k): str(v) for k, v in env.items()}
    return out


def add_server(name: str, spec: dict[str, Any]) -> None:
    clean = validate_spec(name, spec)
    cfg = read_config()
    if name in cfg["servers"]:
        raise ValueError(f"a server named '{name}' already exists")
    cfg["servers"][name] = clean
    _save(cfg)
    pending.add(name)


def remove_server(name: str) -> bool:
    cfg = read_config()
    if name not in cfg["servers"]:
        return False
    del cfg["servers"][name]
    _save(cfg)
    pending.add(name)
    return True


def describe(spec: Any) -> dict[str, Any]:
    """What a server entry would run or connect to (for the UI to show before the user enables it). Env values are never included, only names."""
    if not isinstance(spec, dict):
        return {"kind": "invalid", "target": "", "env": []}
    if "url" in spec:
        return {"kind": "http", "target": str(spec["url"]), "env": []}
    cmd = " ".join([str(spec.get("command", "")), *map(str, spec.get("args", []) or [])]).strip()
    return {"kind": "stdio", "target": cmd, "env": sorted(map(str, (spec.get("env") or {}).keys()))}


def details(name: str) -> dict[str, Any]:
    return _details.get(name, {"tools": [], "skipped": []})


def _connection(spec: dict[str, Any]) -> dict[str, Any]:
    if "url" in spec:
        return {"transport": "streamable_http", "url": str(spec["url"])}
    def resolve(m: re.Match[str]) -> str:
        value = get_secret(m.group(1)) or ""
        register_secret(value)  # whatever the config calls it, a value injected into a server's environment is a secret
        return value

    env = {k: _VAR.sub(resolve, str(v)) for k, v in (spec.get("env") or {}).items()}
    return {"transport": "stdio", "command": str(spec["command"]), "args": [str(a) for a in spec.get("args", [])], "env": env or None}


def _preview(tool_name: str) -> Callable[[dict[str, Any]], str]:
    return lambda a: f"External MCP tool {tool_name}\nArguments: {str(a)[:1500]}"


def unload() -> None:
    for n in list(_loaded):
        registry._TOOLS.pop(n, None)
    _loaded.clear()
    _details.clear()


async def load_tools() -> dict[str, str]:
    """(Re)load tools of all enabled servers and register them. Returns {server: 'ok (n tools)' | 'error: ...'}.

    New tools are collected first and swapped in at the end, so a server that fails to connect keeps the tools it had before
    and a broken mcp.yaml (ValueError, raised before anything changes) never tears down the working ones."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    servers = read_config()["servers"]
    status: dict[str, str] = {}
    staged: list[tuple[BaseTool, ToolMeta, str]] = []
    new_details: dict[str, dict[str, Any]] = {}
    failed: set[str] = set()
    # T0 (no approval) is only ever a deliberate per-tool choice in mcp.yaml, never the default for a whole server
    default_tier = str(policy().cfg.get("mcp_default_tier", "T2"))
    if default_tier not in ("T1", "T2"):
        default_tier = "T2"
    for name, spec in servers.items():
        if not _NAME.match(name):
            status[name] = "error: invalid server name"
        elif not isinstance(spec, dict):
            status[name] = "error: the entry must be a mapping"
        elif not spec.get("enabled", True):
            status[name] = "disabled"
            new_details[name] = {"tools": [], "skipped": []}
        else:
            try:
                conns: dict[str, Any] = {name: _connection(spec)}
                tools: list[BaseTool] = await MultiServerMCPClient(conns, tool_name_prefix=True).get_tools(server_name=name)
            except Exception as e:  # noqa: BLE001  one broken server must not stop the others
                log.warning("mcp_server_failed", server=name, error=type(e).__name__)
                failed.add(name)
                kept = sum(1 for srv in _loaded.values() if srv == name)
                status[name] = f"error: {type(e).__name__}: {redact(str(e))[:200]}" + (f" (kept {kept} previous tools)" if kept else "")
                continue
            ok: list[dict[str, str]] = []
            skipped: list[dict[str, str]] = []
            taken = {t.name for t, _m, _s in staged}
            for t in tools:
                raw = t.name.removeprefix(f"{name}_").removeprefix(f"{name}__")
                tier = str((spec.get("tools") or {}).get(raw, default_tier))
                if not _NAME.match(t.name):
                    reason = "invalid tool name"
                elif t.name in taken or (t.name in registry._TOOLS and t.name not in _loaded):
                    reason = "the name is already used by another tool"
                elif tier not in _TIERS:
                    reason = f"invalid tier '{tier}'"
                else:
                    staged.append((t, ToolMeta(tier=tier, side_effects=tier != "T0", network=True, untrusted_output=True, risk="medium",
                                               preview_fn=_preview(t.name)), name))
                    taken.add(t.name)
                    ok.append({"name": t.name, "tier": tier})
                    continue
                skipped.append({"name": t.name, "reason": reason})
            new_details[name] = {"tools": ok, "skipped": skipped}
            status[name] = f"ok ({len(ok)} tools" + (f", {len(skipped)} skipped" if skipped else "") + ")"
    for n, srv in list(_loaded.items()):  # swap: drop the old tools of every server that was (re)loaded or removed; failed ones keep theirs
        if srv not in failed:
            registry._TOOLS.pop(n, None)
            del _loaded[n]
    for t, meta, srv in staged:
        registry.register(t, meta)
        _loaded[t.name] = srv
    for srv in [k for k in _details if k not in failed]:
        del _details[srv]
    _details.update(new_details)
    pending.clear()
    return status
