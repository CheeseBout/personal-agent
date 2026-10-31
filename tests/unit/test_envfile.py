from pathlib import Path

import pytest

from personal_agent.settings import secrets


def test_env_file_is_used_and_process_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "x.env"
    f.write_text("OPENAI_API_KEY=from-file\nANTHROPIC_API_KEY=file-anthropic\nEMPTY=\n", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_AGENT_ENV_FILE", str(f))
    monkeypatch.setattr(secrets.keyring, "get_password", lambda s, n: "from-keyring")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert secrets.get_secret("OPENAI_API_KEY") == "from-file"  # .env beats keyring
    assert secrets.get_secret("ANTHROPIC_API_KEY") == "from-env"  # process env beats .env
    assert secrets.get_secret("EMPTY") == "from-keyring"  # blank values fall through
    assert secrets.get_secret("UNSET") == "from-keyring"


def test_token_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "t.env"
    f.write_text("PERSONAL_AGENT_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_AGENT_ENV_FILE", str(f))
    monkeypatch.delenv("PERSONAL_AGENT_TOKEN")
    assert secrets.get_api_token() == "file-token"
