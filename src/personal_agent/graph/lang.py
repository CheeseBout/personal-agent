"""Cheap vi/en detection (no model call)."""

from __future__ import annotations

import re

_VI_DIACRITICS = re.compile(
    "[ăâđêôơưàáạảãầấậẩẫằắặẳẵèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.IGNORECASE
)
# Vietnamese typed without accents; needs two hits so English words like "ban" alone don't match.
_VI_WORDS = {"toi", "ban", "khong", "xin", "chao", "giup", "cua", "duoc", "nhung", "va", "cho", "minh", "nhe"}


def detect_lang(text: str, override: str = "auto") -> str:
    if override in ("vi", "en"):
        return override
    if _VI_DIACRITICS.search(text):
        return "vi"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "vi" if len(words & _VI_WORDS) >= 2 else "en"
