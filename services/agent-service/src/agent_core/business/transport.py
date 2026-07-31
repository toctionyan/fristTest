"""Domain-neutral outbound actor propagation and transport failures."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_BUSINESS_ACTOR: ContextVar[dict[str, Any] | None] = ContextVar("business_actor", default=None)


@contextmanager
def business_actor_context(
    *,
    user_id: str,
    role: str = "customer",
    tenant_id: str | None = None,
    account_id: str | None = None,
    permissions: list[str] | tuple[str, ...] | None = None,
    user_token: str | None = None,
) -> Iterator[None]:
    """Attach an authenticated actor to the current business-port call scope."""
    token = _BUSINESS_ACTOR.set(
        {
            "user_id": user_id,
            "role": role,
            "tenant_id": tenant_id,
            "account_id": account_id or user_id,
            "permissions": list(permissions or []),
            "user_token": user_token,
        }
    )
    try:
        yield
    finally:
        _BUSINESS_ACTOR.reset(token)


def current_business_actor() -> dict[str, Any] | None:
    return _BUSINESS_ACTOR.get()


class BusinessServiceError(Exception):
    """Business port failed before a verified RuntimeOutcome was produced."""

    def __init__(self, status_code: int, message: str, payload: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.payload = payload
