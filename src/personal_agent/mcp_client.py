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

import re
from collections.abc import Callable
from typing import Any

import structlog
import yaml
from langchain_core.tools import BaseTool

from personal_agent.policy.engine import ToolMeta
from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import data_dir
from personal_agent.tools import registry
from personal_agent.tools.registry import policy

log = structlog.get_logger()
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
_loaded: set[str] = set()


def config_path() -> Any:
    return data_dir() / "mcp.yaml"


def read_config() -> dict[str, Any]:
    p = config_path()
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    return cfg if isinstance(cfg, dict) and isinstance(cfg.get("servers"), dict) else {"servers": {}}


def set_enabled(name: str, enabled: bool) -> bool:
    cfg = read_config()
    if name not in cfg["servers"]:
        return False
    cfg["servers"][name]["enabled"] = bool(enabled)
    config_path().write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return True


def _connection(spec: dict[str, Any]) -> dict[str, Any]:
    if "url" in spec:
        return {"transport": "streamable_http", "url": str(spec["url"])}
    env = {k: _VAR.sub(lambda m: get_secret(m.group(1)) or "", str(v)) for k, v in (spec.get("env") or {}).items()}
    return {"transport": "stdio", "command": str(spec["command"]), "args": [str(a) for a in spec.get("args", [])], "env": env or None}


def _preview(tool_name: str) -> Callable[[dict[str, Any]], str]:
    return lambda a: f"External MCP tool {tool_name}\nArguments: {str(a)[:1500]}"


def unload() -> None:
    for n in list(_loaded):
        registry._TOOLS.pop(n, None)
    _loaded.clear()


async def load_tools() -> dict[str, str]:
    """(Re)load tools of all enabled servers and register them. Returns {server: 'ok (n tools)' | 'error: ...'}."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    unload()
    status: dict[str, str] = {}
    default_tier = str(policy().cfg.get("mcp_default_tier", "T2"))
    for name, spec in read_config()["servers"].items():
        if not _NAME.match(name):
            status[name] = "error: invalid server name"
        elif not spec.get("enabled", True):
            status[name] = "disabled"
        else:
            conns: dict[str, Any] = {name: _connection(spec)}
            try:
                tools: list[BaseTool] = await MultiServerMCPClient(
                    conns, tool_name_prefix=True).get_tools(server_name=name)
            except Exception as e:  # noqa: BLE001  one broken server must not stop the others
                log.warning("mcp_server_failed", server=name, error=type(e).__name__)
                status[name] = f"error: {type(e).__name__}"
                continue
            n = 0
            for t in tools:
                raw = t.name.removeprefix(f"{name}_").removeprefix(f"{name}__")
                tier = str((spec.get("tools") or {}).get(raw, default_tier))
                if not _NAME.match(t.name) or t.name in registry._TOOLS or tier not in ("T0", "T1", "T2"):
                    continue
                registry.register(t, ToolMeta(
                    tier=tier, side_effects=tier != "T0", network=True, untrusted_output=True, risk="medium",
                    preview_fn=_preview(t.name)))
                _loaded.add(t.name)
                n += 1
            status[name] = f"ok ({n} tools)"
    return status
