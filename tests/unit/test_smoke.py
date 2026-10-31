from pathlib import Path

import yaml

import personal_agent


def test_version() -> None:
    assert personal_agent.__version__


def test_default_config_binds_localhost() -> None:
    cfg = yaml.safe_load(Path("config/default.yaml").read_text(encoding="utf-8"))
    assert cfg["server"]["host"] == "127.0.0.1"
    assert cfg["agent"]["name"] == "personal-agent"


def test_policy_parses_and_denies_by_default() -> None:
    pol = yaml.safe_load(Path("config/policy.yaml").read_text(encoding="utf-8"))
    assert pol["default_tier"] == "T3"
    assert pol["paths"]["deny"][0] == r"C:\Windows"
