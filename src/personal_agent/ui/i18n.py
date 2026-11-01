"""Vietnamese + English UI strings. Usage: tr("Send", "Gửi"); placeholders via tr("{n} items", "{n} mục", n=3).

The English text is also the fallback, so a missing Vietnamese string never shows an empty label."""

from __future__ import annotations

import locale
import os

_lang = "en"


def resolve(setting: str) -> str:
    """'auto' follows the OS UI language; anything but vi falls back to English."""
    if setting in ("vi", "en"):
        return setting
    try:
        name = (os.environ.get("LANG") or locale.getlocale()[0] or "").lower()
        if not name:
            from PySide6.QtCore import QLocale

            name = QLocale.system().name().lower()
    except Exception:  # noqa: BLE001 - locale lookup must never stop the UI from starting
        name = ""
    return "vi" if name.startswith("vi") else "en"


def set_language(setting: str) -> str:
    global _lang
    _lang = resolve(setting)
    return _lang


def language() -> str:
    return _lang


def tr(en: str, vi: str | None = None, **fmt: object) -> str:
    text = vi if (_lang == "vi" and vi) else en
    return text.format(**fmt) if fmt else text
