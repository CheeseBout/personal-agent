"""Map provider SDK exceptions to stable client-facing error codes (never echoes raw provider text)."""

from __future__ import annotations

_QUOTA = ("credit balance", "insufficient_quota", "exceeded your current quota", "billing", "payment required")


def classify_provider_error(e: BaseException) -> tuple[str, str] | None:
    """Return (code, safe message) for provider failures worth telling the user about, else None."""
    name = type(e).__name__
    msg = str(e).lower()
    if any(s in msg for s in _QUOTA):
        return "quota_exhausted", "The provider reports the account is out of credit/quota. Top it up or add a fallback key."
    if name == "RateLimitError" or "rate limit" in msg or "429" in msg:
        return "rate_limited", "The provider is rate-limiting requests. Try again in a minute."
    if name in ("AuthenticationError", "PermissionDeniedError") or "invalid x-api-key" in msg:
        return "auth_failed", "The provider rejected the API key. Check the key in .env / Credential Manager."
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout"):
        return "provider_unreachable", "Cannot reach the provider (network or outage)."
    return None
