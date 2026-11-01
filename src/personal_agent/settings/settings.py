"""Typed settings: packaged defaults + optional user override in the data dir."""

from __future__ import annotations

import os
import re
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
    max_steps: int = 25
    max_tool_calls: int = 40
    max_seconds: int = 600
    max_tokens: int = 150_000
    max_cost_usd: float = 0.50
    max_consecutive_failures: int = 4
    max_replans: int = 2
    max_verifications: int = 4
    daily_max_tokens: int = 1_000_000
    daily_max_cost_usd: float = 2.0


class Price(BaseModel):
    input: float
    output: float


class BrainCfg(BaseModel):
    min_confidence: float = 0.55


class ContextCfg(BaseModel):
    compact_tokens: int = 12_000
    keep_last: int = 6
    offload_chars: int = 6000


class EmbeddingCfg(BaseModel):
    provider: str = "google"  # google | openai | local (fastembed, optional extra)
    model: str = "gemini-embedding-001"
    dim: int = 768


class RagCfg(BaseModel):
    embedding: EmbeddingCfg = Field(default_factory=EmbeddingCfg)
    chunk_chars: int = 900
    overlap_chars: int = 120
    top_k: int = 5
    watch: list[str] = Field(default_factory=list)  # folders (must be inside the allowed paths)


class MemoryCfg(BaseModel):
    reflect: bool = True
    auto_k: int = 5
    half_life_days: float = 90.0
    min_auto_confidence: float = 0.7


class SchedulerCfg(BaseModel):
    max_jobs: int = 20
    min_interval_minutes: int = 5
    grace_minutes: int = 60  # a run missed while the daemon was down is still run if it is this recent


class WorkerBudget(BaseModel):
    max_steps: int = 8
    max_tool_calls: int = 12
    max_seconds: int = 180
    max_tokens: int = 40_000
    max_cost_usd: float = 0.15


class WorkerRole(BaseModel):
    model_role: str = "fast"  # which entry of `models:` runs this worker (Haiku by default)
    tools: list[str] = Field(default_factory=list)
    # True: the worker may touch private data, so reading untrusted content makes its later side effects need approval
    private_context: bool = True


class WorkersCfg(BaseModel):
    enabled: bool = True
    max_parallel: int = 3
    max_runs_per_task: int = 6
    max_total_steps: int = 30  # model calls ALL workers of one task may make together
    min_delegable: int = 2  # delegate only when the plan has at least this many independent sub-agent steps
    budget: WorkerBudget = Field(default_factory=WorkerBudget)
    roles: dict[str, WorkerRole] = Field(default_factory=dict)


class Settings(BaseModel):
    agent: AgentCfg = Field(default_factory=AgentCfg)
    workers: WorkersCfg = Field(default_factory=WorkersCfg)
    rag: RagCfg = Field(default_factory=RagCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    pricing: dict[str, Price] = Field(default_factory=dict)
    budgets: Budgets = Field(default_factory=Budgets)
    brain: BrainCfg = Field(default_factory=BrainCfg)
    context: ContextCfg = Field(default_factory=ContextCfg)


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


# Names go into the system prompt, so keep them to a short, plain, single-line shape.
_NAME_RE = re.compile(r"^\w[\w .'\-]{0,39}$")


def set_agent_name(settings: Settings, name: str) -> None:
    """Rename the agent: applies to the next turn (graph reads settings live) and persists."""
    name = name.strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError("agent name must be 1-40 letters/digits/space/.-' characters")
    path = data_dir() / "config.yaml"
    path.write_text(yaml.safe_dump(_merge(_yaml(path), {"agent": {"name": name}}), allow_unicode=True),
                    encoding="utf-8")
    settings.agent.name = name
