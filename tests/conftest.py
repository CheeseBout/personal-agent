import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # UI tests never open real windows
import pytest
import yaml

from fakes import HashEmbedder
from personal_agent import services
from personal_agent.services import reset_services
from personal_agent.settings.settings import load_settings
from personal_agent.tools.registry import policy
from personal_agent.tools.sandbox_tools import reset_singletons


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated config/data dirs; returns the (allowed) workspace folder."""
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    shutil.copy("config/default.yaml", cfg / "default.yaml")
    pol = yaml.safe_load(Path("config/policy.yaml").read_text(encoding="utf-8"))
    pol["paths"]["allow"] = [str(ws)]
    (cfg / "policy.yaml").write_text(yaml.safe_dump(pol), encoding="utf-8")
    monkeypatch.setenv("PERSONAL_AGENT_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("PERSONAL_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PERSONAL_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("PERSONAL_AGENT_ENV_FILE", str(tmp_path / "no.env"))  # never read a real .env
    load_settings.cache_clear()
    policy.cache_clear()
    reset_singletons()
    reset_services()
    emb = HashEmbedder()  # tests never reach a real embedding provider
    monkeypatch.setattr(services, "embedder", lambda: emb)
    monkeypatch.setattr(services, "FAKE_EMBEDDER", emb, raising=False)
    return ws
