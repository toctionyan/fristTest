from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import Iterator

from fastapi import Depends, HTTPException, Request

from agent_core.security.auth_provider import AuthenticatedActor as Actor
from agent_core.security.auth_provider import authenticate_request
from agent_core.business import business_actor_context
from agent_core.security.roles import normalize_role


def current_actor(request: Request) -> Actor:
    """Return the trusted authenticated actor for the current API request.

    Routers should depend on this object rather than reading X-User-Id or
    X-User-Role directly.  In production, configure AGENT_AUTH_PROVIDER to
    `jwt_hs256` or `remote_userinfo`; `dev_headers` is only a local fallback.
    """

    return authenticate_request(request)


def require_api_permission(permission: str) -> Callable[[Actor], Actor]:
    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        # Unit tests may call the dependency directly with a role string.
        if isinstance(actor, str):
            role = normalize_role(actor)
            from agent_core.security.roles import can
            if not can(role, permission):
                raise HTTPException(status_code=403, detail=f"role {role} cannot use {permission}")
            return actor  # type: ignore[return-value]
        if not actor.has_permission(permission):
            raise HTTPException(status_code=403, detail=f"role {actor.role} cannot use {permission}")
        return actor

    return dependency


def apply_actor_to_payload(payload, actor: Actor):
    """Return a copy of a Pydantic request whose identity comes from Actor.

    This enforces the API boundary: clients may still send user_id/role fields for
    backwards compatibility, but routers overwrite them with authenticated data.
    """
    data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    data["user_id"] = actor.user_id
    data["role"] = normalize_role(actor.role)
    if "tenant_id" in getattr(payload.__class__, "model_fields", {}):
        data["tenant_id"] = actor.tenant_id
    if "actor_permissions" in getattr(payload.__class__, "model_fields", {}):
        data["actor_permissions"] = list(actor.permissions or [])
    if "subject" in getattr(payload.__class__, "model_fields", {}):
        data["subject"] = actor.subject or actor.user_id
    return payload.__class__(**data)


def actor_can_debug(actor: Actor) -> bool:
    return actor.has_permission("debug:read")


def current_actor_id(actor: Actor = Depends(current_actor)) -> str:
    return actor.user_id


@contextmanager
def business_context_for_actor(actor: Actor) -> Iterator[None]:
    """Propagate the trusted API actor into outbound business-service calls.

    Every Agent API endpoint that calls the business service directly should use
    this helper.  Chat/resume do the same through AgentService.  This keeps the
    service token from becoming a master key and makes switching to RuoYi/Spring
    Security straightforward: the actor always comes from AuthProvider, never
    from client-supplied user_id/role fields.
    """
    with business_actor_context(
        user_id=actor.user_id,
        role=actor.role,
        tenant_id=actor.tenant_id,
        account_id=actor.subject or actor.user_id,
        permissions=actor.permissions,
    ):
        yield
