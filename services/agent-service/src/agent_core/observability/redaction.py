from __future__ import annotations

"""Persistence-time trace redaction.

Observability is valuable only when it does not turn the debug database into an
unbounded copy of credentials and sensitive customer fields.  This module is
intentionally used at the persistence boundary; business execution still sees
its original values.
"""

import os
from typing import Any

_SECRET_KEYS = {
    "authorization", "token", "access_token", "refresh_token", "api_key",
    "secret", "password", "cookie", "set_cookie", "signature",
    "business_service_token", "actor_signature", "idempotency_key",
}
_PERSONAL_KEYS = {
    "address", "address_detail", "phone", "mobile", "email", "email_address",
    "recipient", "receiver", "invoice_title", "tax_number", "id_card",
}


def redaction_mode() -> str:
    mode = (os.getenv("TRACE_REDACTION_MODE") or "standard").strip().lower()
    if mode not in {"standard", "off"}:
        raise ValueError("TRACE_REDACTION_MODE must be standard or off")
    return mode


def _mask(value: Any, *, secret: bool) -> str:
    text = str(value or "")
    if secret:
        return "[REDACTED]"
    if len(text) <= 4:
        return "[REDACTED]"
    return f"{text[:2]}***{text[-2:]}"


def redact_for_persistence(value: Any) -> Any:
    """Return a non-mutating redacted projection for trace/audit storage."""
    if redaction_mode() == "off":
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in _SECRET_KEYS or any(token in normalized for token in ("password", "secret", "token", "authorization", "api_key", "signature", "cookie")):
                result[key] = _mask(raw_value, secret=True)
            elif normalized in _PERSONAL_KEYS or any(token in normalized for token in ("phone", "mobile", "email", "address", "tax_number", "id_card")):
                result[key] = _mask(raw_value, secret=False)
            else:
                result[key] = redact_for_persistence(raw_value)
        return result
    if isinstance(value, list):
        return [redact_for_persistence(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_persistence(item) for item in value]
    return value
