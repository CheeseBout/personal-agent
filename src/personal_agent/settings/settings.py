"""Typed settings: packaged defaults + optional user override in the data dir."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelRef(BaseModel):
    provider: str
    model: str


class ModelSpec(ModelRef):
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_s: float = 60.0
    fallbacks: list[ModelRef] = Field(default_factory=list)


class AgentCfg(BaseModel):
    name: str = "personal-agent"
    language: str = "auto"


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class Budgets(BaseModel):
    max_steps: int = 40
    max_tool_calls: int = 80
    max_seconds: int = 1800
    max_tokens: int = 400_000
    max_cost_usd: float = 2.0
    max_consecutive_failures: int = 4


class Settings(BaseModel):
    agent: AgentCfg = Field(default_factory=AgentCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    budgets: Budgets = Field(default_factory=Budgets)


def data_dir() -> Path:
    r"""%APPDATA%\personal-agent (override with PERSONAL_AGENT_HOME)."""
    root = os.environ.get("PERSONAL_AGENT_HOME") or os.path.join(
        os.environ.get("APPDATA", str(Path.home())), "personal-agent"
    )
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    # shortcut: repo-relative config dir, switch to packaged resources when building the installer
    env = os.environ.get("PERSONAL_AGENT_CONFIG_DIR")
    return Path(env) if env else Path(__file__).parents[3] / "config"


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _yaml(p: Path) -> dict[str, Any]:
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    raw = _merge(_yaml(config_dir() / "default.yaml"), _yaml(data_dir() / "config.yaml"))
    return Settings.model_validate(raw)
