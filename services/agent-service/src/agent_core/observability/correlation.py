from __future__ import annotations

from contextvars import ContextVar
import re
from uuid import uuid4

_CORRELATION: ContextVar[str | None] = ContextVar("agent_correlation_id", default=None)
_VALID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def normalize_correlation_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate and _VALID.fullmatch(candidate):
        return candidate
    return uuid4().hex


def set_correlation_id(value: str | None) -> str:
    correlation_id = normalize_correlation_id(value)
    _CORRELATION.set(correlation_id)
    return correlation_id


def get_correlation_id() -> str | None:
    return _CORRELATION.get()


def reset_correlation_id() -> None:
    _CORRELATION.set(None)
