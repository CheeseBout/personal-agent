"""Typed settings: packaged defaults + optional user override in the data dir."""

from __future__ import annotations

import os
import re
import sys
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
    port: int = Field(default=8765, ge=1, le=65535)


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


class ObservabilityCfg(BaseModel):
    langfuse_enabled: bool = False  # still needs LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY; without them it stays off
    metrics_enabled: bool = True  # local SQLite latency / error / tool-success events (the "Usage" page)
    metrics_keep_days: int = 90


class SttCfg(BaseModel):
    provider: str = "google"  # google (Gemini audio) | openai (whisper) | local (faster-whisper, optional extra)
    model: str = "gemini-3.5-flash-lite"
    max_seconds: int = 120  # longer recordings are refused (cost / abuse guard)


class TtsCfg(BaseModel):
    enabled: bool = False  # speak answers aloud (edge-tts, runs in the UI process)
    voice_vi: str = "vi-VN-HoaiMyNeural"
    voice_en: str = "en-US-AriaNeural"
    rate: str = "+0%"
    max_chars: int = 600  # long answers are not read in full


class VoiceCfg(BaseModel):
    stt: SttCfg = Field(default_factory=SttCfg)
    tts: TtsCfg = Field(default_factory=TtsCfg)


class UiCfg(BaseModel):
    language: str = "auto"  # auto | vi | en  (follows the OS locale when auto)
    avatar_enabled: bool = True
    skin: str = "default"
    avatar_scale: float = 1.0
    avatar_always_on_top: bool = True
    avatar_wander: bool = False
    sleep_after_s: int = 300
    hotkeys: dict[str, str] = Field(default_factory=lambda: {
        "kill": "ctrl+alt+k", "talk": "ctrl+alt+space", "chat": "ctrl+alt+a",
        "capture": "ctrl+alt+s", "watch": "ctrl+alt+w"})


DEFAULT_BLOCK_TITLES = [  # case-insensitive substrings of the window title
    "keepass", "bitwarden", "1password", "lastpass", "dashlane", "nordpass", "enpass", "proton pass", "credential manager",
    "sign in to your account", "đăng nhập", "internet banking", "ngân hàng", "mbbank", "vietcombank", "techcombank", "vietinbank",
    "bidv", "agribank", "tpbank", "vpbank", "paypal", "wells fargo", "chase.com", "online banking", "wallet", "ví điện tử", "momo", "zalopay"]
DEFAULT_BLOCK_PROCESSES = ["keepass.exe", "keepassxc.exe", "bitwarden.exe", "1password.exe", "lastpass.exe", "dashlane.exe", "nordpass.exe",
                           "enpass.exe", "protonpass.exe", "credwiz.exe", "consent.exe", "lockapp.exe", "logonui.exe"]


class ScreenCfg(BaseModel):
    """Screen perception and control (docs/phase8.md). Privacy first: blocked windows are never captured, read or controlled."""

    agent_view: bool = True  # the agent may look at the screen (screen_describe / screen_ocr / ui_tree); each view is shown to you
    control: bool = True  # the agent may ask to click/type (every use still needs your approval)
    block_titles: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCK_TITLES))
    block_processes: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCK_PROCESSES))
    max_image_px: int = Field(default=1280, ge=256, le=4000)  # longest side of anything sent to a vision model
    watch_interval_s: float = Field(default=2.0, ge=0.5, le=60)  # continuous mode: seconds between low-FPS captures
    watch_change_threshold: int = Field(default=8, ge=1, le=64)  # perceptual-hash distance (0-64) that counts as a meaningful change
    watch_max_minutes: int = Field(default=30, ge=1, le=480)  # continuous mode switches itself off after this long
    max_actions: int = Field(default=8, ge=1, le=20)  # one approval covers at most this many control steps
    highlight_ms: int = Field(default=700, ge=0, le=5000)  # the target is outlined this long before every action
    abort_move_px: int = Field(default=25, ge=5, le=500)  # moving the mouse this far during a sequence aborts it


class Settings(BaseModel):
    screen: ScreenCfg = Field(default_factory=ScreenCfg)
    voice: VoiceCfg = Field(default_factory=VoiceCfg)
    ui: UiCfg = Field(default_factory=UiCfg)
    observability: ObservabilityCfg = Field(default_factory=ObservabilityCfg)
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


def app_root() -> Path:
    """The folder that holds `config/` and `docker/`: the repo root in development, the unpacked bundle in a PyInstaller build."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parents[3]


def config_dir() -> Path:
    env = os.environ.get("PERSONAL_AGENT_CONFIG_DIR")
    return Path(env) if env else app_root() / "config"


def user_policy_path() -> Path:
    return data_dir() / "policy.yaml"


def policy_file() -> Path:
    r"""The policy in force: your edited copy in %APPDATA%\personal-agent when it exists, else the shipped one (read-only when installed)."""
    mine = user_policy_path()
    return mine if mine.is_file() else config_dir() / "policy.yaml"


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _yaml(p: Path) -> dict[str, Any]:
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Shipped defaults + the user's override file. A broken override (bad YAML, wrong types, a half-written save) is moved aside as
    config.yaml.invalid-<time> and the defaults are used, so a typo can never stop the daemon from starting."""
    base = _yaml(config_dir() / "default.yaml")  # the shipped file: if THIS is broken the install is broken, so let it raise
    user_path = data_dir() / "config.yaml"
    try:
        user = _yaml(user_path)
        if not isinstance(user, dict):
            raise TypeError("config.yaml must be a mapping")
        return Settings.model_validate(_merge(base, user))
    except (yaml.YAMLError, TypeError, ValueError, AttributeError, OSError):  # ValueError covers pydantic's ValidationError
        from personal_agent.storage import quarantine

        quarantine(user_path, "invalid")
        return Settings.model_validate(base)


def save_overrides(patch: dict[str, Any]) -> None:
    """Deep-merge `patch` into %APPDATA%\\personal-agent\\config.yaml (the user override file)."""
    path = data_dir() / "config.yaml"
    path.write_text(yaml.safe_dump(_merge(_yaml(path), patch), allow_unicode=True), encoding="utf-8")


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
