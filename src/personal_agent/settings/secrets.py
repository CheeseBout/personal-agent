"""API keys: process env > .env file > Windows Credential Manager (keyring).

The .env file is read from PERSONAL_AGENT_ENV_FILE if set, otherwise `./.env` (current
directory) then `%APPDATA%\\personal-agent\\.env`; the first file defining a name wins.
"""

from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

import keyring
import keyring.errors
from dotenv import dotenv_values

from personal_agent.settings.settings import data_dir

SERVICE = "personal-agent"


def _env_file_values() -> dict[str, str]:
    explicit = os.environ.get("PERSONAL_AGENT_ENV_FILE")
    paths = [Path(explicit)] if explicit else [Path.cwd() / ".env", data_dir() / ".env"]
    out: dict[str, str] = {}
    for p in reversed(paths):  # reversed so earlier paths override later ones
        if p.is_file():
            out.update({k: v for k, v in dotenv_values(p).items() if v})
    return out


def _from_env(name: str) -> str | None:
    return os.environ.get(name) or _env_file_values().get(name) or None


def get_secret(name: str) -> str | None:
    if v := _from_env(name):
        return v
    try:
        return keyring.get_password(SERVICE, name) or None
    except keyring.errors.KeyringError:
        return None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_api_token() -> str:
    """Per-install token protecting the local API; created on first use."""
    if tok := _from_env("PERSONAL_AGENT_TOKEN"):
        return tok
    tok = get_secret("API_TOKEN")
    if not tok:
        tok = _secrets.token_urlsafe(32)
        set_secret("API_TOKEN", tok)
    return tok
