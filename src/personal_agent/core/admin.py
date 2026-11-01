"""Settings / secrets / policy management behind the UI's Settings and Policy pages.

Every call validates its input; secret VALUES are write-only (never returned, never logged). Only a whitelist of settings
is editable from the UI: it cannot touch workers, MCP, RAG folders or budgets other than the daily caps.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from personal_agent.llm.errors import classify_provider_error
from personal_agent.llm.factory import KEY_NAMES, MissingSecretError, build_chat_model
from personal_agent.policy.engine import PolicyEngine
from personal_agent.settings.secrets import get_secret, set_secret
from personal_agent.settings.settings import (
    Settings,
    _merge,
    policy_file,
    save_overrides,
    user_policy_path,
)

PROVIDERS = {*KEY_NAMES, "ollama"}
SECRET_NAMES = {*KEY_NAMES.values(), "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"}
BUDGET_KEYS = {"daily_max_tokens", "daily_max_cost_usd", "max_cost_usd", "max_tokens"}
_PLAIN = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:/@ +")


def secrets_status() -> dict[str, bool]:
    return {n: bool(get_secret(n)) for n in sorted(SECRET_NAMES)}


def save_secret(name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ValueError("unknown secret name")
    value = value.strip()
    if not value or len(value) > 500 or "\n" in value:
        raise ValueError("invalid secret value")
    set_secret(name, value)


def public_settings(s: Settings) -> dict[str, Any]:
    return {
        "agent": {"name": s.agent.name, "language": s.agent.language},
        "models": {r: {"provider": m.provider, "model": m.model} for r, m in s.models.items()},
        "providers": sorted(PROVIDERS),
        "budgets": {k: getattr(s.budgets, k) for k in sorted(BUDGET_KEYS)},
        "voice": s.voice.model_dump(),
        "ui": s.ui.model_dump(),
        "screen": s.screen.model_dump(),
        "rag_embedding": s.rag.embedding.model_dump(),
        "observability": s.observability.model_dump(),
        "secrets": secrets_status(),
    }


def _check_model_ref(role: str, ref: Any, known_roles: set[str]) -> None:
    if role not in known_roles or not isinstance(ref, dict) or set(ref) - {"provider", "model"}:
        raise ValueError(f"cannot edit models.{role}")
    if "provider" in ref and ref["provider"] not in PROVIDERS:
        raise ValueError(f"unknown provider '{ref['provider']}'")
    if "model" in ref and not (isinstance(ref["model"], str) and 1 <= len(ref["model"]) <= 80 and set(ref["model"]) <= _PLAIN):
        raise ValueError("model id must be 1-80 plain characters")


def validate_patch(s: Settings, patch: dict[str, Any]) -> dict[str, Any]:
    """Return the whitelisted patch or raise ValueError. Unknown / protected keys are an error, not silently dropped."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("empty settings patch")
    out: dict[str, Any] = {}
    for key, val in patch.items():
        if key == "models" and isinstance(val, dict):
            for role, ref in val.items():
                _check_model_ref(role, ref, set(s.models))
            out["models"] = val
        elif key == "agent" and isinstance(val, dict) and set(val) == {"language"}:
            if val["language"] not in ("auto", "vi", "en"):
                raise ValueError("language must be auto, vi or en")
            out["agent"] = val
        elif key == "budgets" and isinstance(val, dict) and set(val) <= BUDGET_KEYS:
            for k, v in val.items():
                if isinstance(v, bool) or not isinstance(v, int | float) or v <= 0:
                    raise ValueError(f"budgets.{k} must be a positive number")
            out["budgets"] = val
        elif key in ("voice", "ui", "observability", "screen") and isinstance(val, dict):
            out[key] = val  # field names/types are validated by the pydantic model below
        else:
            raise ValueError(f"settings key '{key}' cannot be changed from the UI")
    # Full validation (types, unknown fields inside voice/ui/observability) on a copy before anything is saved.
    base = s.model_dump()
    for section in ("voice", "ui", "observability", "screen"):
        for k, v in out.get(section, {}).items():
            if k not in base[section] or (isinstance(base[section][k], dict) and not isinstance(v, dict)):
                raise ValueError(f"unknown setting {section}.{k}")
    Settings.model_validate(_merge(base, out))
    return out


def apply_patch(s: Settings, patch: dict[str, Any]) -> set[str]:
    """Validate, persist to the user override file and update the live settings object. Returns the sections changed."""
    clean = validate_patch(s, patch)
    new = Settings.model_validate(_merge(s.model_dump(), clean))
    save_overrides(clean)
    for section in clean:
        setattr(s, section, getattr(new, section))
    return set(clean)


async def test_role(s: Settings, role: str) -> dict[str, Any]:
    """One tiny real call to prove a role's provider/key/model work (used by the first-run wizard and Settings)."""
    if role not in s.models:
        return {"ok": False, "code": "bad_request", "message": f"unknown role '{role}'"}
    try:
        model = build_chat_model(s.models[role], s.models[role], retries=0)
        await asyncio.wait_for(model.ainvoke("Reply with the single word: ok"), 30)
    except MissingSecretError as e:
        return {"ok": False, "code": "no_api_key", "message": str(e)}
    except TimeoutError:
        return {"ok": False, "code": "timeout", "message": "no answer within 30 s"}
    except Exception as e:  # noqa: BLE001 - any provider failure is a test result, never a daemon crash
        code, message = classify_provider_error(e) or ("failed", type(e).__name__)
        return {"ok": False, "code": code, "message": message}
    return {"ok": True, "role": role, "model": s.models[role].model}


# ---- policy editor -------------------------------------------------------------------------------------------------
def policy_text() -> str:
    return policy_file().read_text(encoding="utf-8")


def policy_add_path(path: str) -> None:
    """Add a folder to `paths.allow` by editing the one-line list in place (comments in policy.yaml are kept)."""
    p = Path(path)
    if not p.is_dir() or any(c in path for c in "\r\n"):
        raise ValueError("not an existing folder")
    text = policy_text()
    quoted = "'" + str(p).replace("'", "''") + "'"
    if (m := re.search(r"^(\s*allow:\s*)\[(.*)\][ \t]*$", text, re.MULTILINE)) is not None:  # allow: [a, b]
        if quoted in m.group(2):
            return
        policy_save(text[:m.end(2)] + (", " if m.group(2).strip() else "") + quoted + text[m.end(2):])
    elif (b := re.search(r"^([ \t]*)allow:[ \t]*\n((?:[ \t]*-[ \t].*\n?)+)", text, re.MULTILINE)) is not None:  # block list
        items = b.group(2)
        if quoted in items:
            return
        first = re.match(r"[ \t]*", items)
        indent = first.group(0) if first else b.group(1)
        sep = "" if items.endswith("\n") else "\n"
        policy_save(text[:b.end(2)] + sep + f"{indent}- {quoted}\n" + text[b.end(2):])
    else:
        raise ValueError("paths.allow has an unsupported shape: edit the policy by hand")


def policy_save(text: str) -> None:
    """Validate then replace policy.yaml (keeps policy.yaml.bak). The engine hot-reloads it."""
    if len(text) > 200_000:
        raise ValueError("policy file too large")
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {str(e)[:200]}") from e
    if not isinstance(cfg, dict):
        raise ValueError("policy must be a YAML mapping")
    for key in ("paths", "commands", "approval", "default_tier"):
        if key not in cfg:
            raise ValueError(f"policy is missing '{key}' (a policy without it would fail open or crash)")
    if cfg["default_tier"] not in ("T0", "T1", "T2", "T3"):
        raise ValueError("default_tier must be T0..T3")
    if not isinstance(cfg["paths"].get("allow"), list) or not isinstance(cfg["paths"].get("deny", []), list):
        raise ValueError("paths.allow and paths.deny must be lists")
    target = user_policy_path()  # edits always go to your copy in %APPDATA%: the shipped file may sit in a read-only install folder
    tmp = target.with_suffix(".yaml.new")
    tmp.write_text(text, encoding="utf-8")
    try:
        eng = PolicyEngine(tmp)  # load and exercise the file with the real engine (paths, regexes, domains)
        eng.check_path(os.getcwd())
        eng.check_command("whoami")
        eng.check_domain("https://example.com")
        shutil.copyfile(policy_file(), target.with_suffix(".yaml.bak"))  # what was in force until now
        os.replace(tmp, target)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"policy rejected by the engine: {type(e).__name__}") from e
