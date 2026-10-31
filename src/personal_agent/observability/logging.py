"""Structured JSON logs with rotation and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from logging.handlers import RotatingFileHandler
from typing import Any

import structlog

from personal_agent.settings.settings import data_dir

_SENSITIVE_KEY = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)
# Best-effort: known credential formats only. Unknown formats are NOT caught.
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+\S+|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$))"
)


def redact(text: str) -> str:
    return _SECRET_VALUE.sub("***", text)


def redact_obj(v: Any) -> Any:
    """Recursively redact strings and values under sensitive-looking keys."""
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: "***" if _SENSITIVE_KEY.search(str(k)) else redact_obj(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [redact_obj(x) for x in v]
    return v


def redact_processor(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    return {k: "***" if _SENSITIVE_KEY.search(k) else redact_obj(v) for k, v in event.items()}


def setup_logging(level: int = logging.INFO) -> None:
    logs = data_dir() / "logs"
    logs.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs / "agent.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    logging.basicConfig(level=level, handlers=handlers, format="%(message)s", force=True)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
