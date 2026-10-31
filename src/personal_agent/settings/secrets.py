"""API keys live in Windows Credential Manager (keyring); env vars are a dev fallback."""

from __future__ import annotations

import os
import secrets as _secrets

import keyring
import keyring.errors

SERVICE = "personal-agent"


def get_secret(name: str) -> str | None:
    try:
        v = keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError:
        v = None
    return v or os.environ.get(name) or None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_api_token() -> str:
    """Per-install token protecting the local API; created on first use."""
    if env := os.environ.get("PERSONAL_AGENT_TOKEN"):
        return env
    tok = get_secret("API_TOKEN")
    if not tok:
        tok = _secrets.token_urlsafe(32)
        set_secret("API_TOKEN", tok)
    return tok
