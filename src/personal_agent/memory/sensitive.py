"""Sensitive-content detection: such memories are never stored without the user's explicit approval."""

from __future__ import annotations

import re

from personal_agent.observability.logging import redact

_WORDS = re.compile(
    r"\b(password|passwd|passcode|otp|cvv|cvc|pin\s*code|ssn|iban|credit\s*card|card\s*number|api[\s_-]?key|secret|token|"
    r"diagnos\w*|prescription|medication|salary|bank\s*account|"
    r"mật\s*khẩu|mã\s*otp|số\s*thẻ|thẻ\s*tín\s*dụng|tài\s*khoản\s*ngân\s*hàng|chẩn\s*đoán|bệnh|thuốc|"
    r"lương|cmnd|cccd|căn\s*cước)\b",
    re.IGNORECASE,
)
_LONG_DIGITS = re.compile(r"(?<!\d)\d(?:[ -]?\d){11,18}(?!\d)")  # card / account number shaped


def is_sensitive(text: str) -> bool:
    return bool(_WORDS.search(text) or _LONG_DIGITS.search(text) or redact(text) != text)
