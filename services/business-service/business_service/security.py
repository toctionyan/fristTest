from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Iterable

from fastapi import Header, HTTPException, Request, status

from .config import BusinessSettings
from .database import BusinessDatabase, as_dict, utcnow


@dataclass(frozen=True)
class Actor:
    """Trusted request identity for all business handlers.

    The transport can carry a signed delegated identity from the Agent or an
    upstream API gateway, but tenant, role and permissions are canonicalized
    from the business account table when the account exists. Request bodies
    never define identity.
    """

    user_id: str
    role: str
    tenant_id: str
    account_id: str
    permissions: frozenset[str]
    request_id: str | None = None

    def can(self, permission: str) -> bool:
        # Admin remains the explicit break-glass role. A developer can only use
        # permissions actually carried by a trusted identity; it is never an
        # implicit business administrator.
        if self.role == "admin":
            return True
        return permission in self.permissions


def _actor_permissions(value: str | None) -> frozenset[str]:
    return frozenset(item.strip() for item in (value or "").split(",") if item.strip())


def _verify_service_token(
    authorization: str | None, settings: BusinessSettings
) -> None:
    expected = f"Bearer {settings.service_token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid business service token",
        )


def _verify_signature(
    *,
    settings: BusinessSettings,
    database: BusinessDatabase,
    user_id: str,
    role: str,
    tenant_id: str,
    account_id: str,
    permissions_raw: str,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
) -> None:
    if not settings.require_actor_signature:
        return
    if not settings.actor_signing_secret:
        raise HTTPException(
            status_code=503,
            detail="actor signature required but signing secret not configured",
        )
    try:
        ts = int(timestamp or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="invalid actor signature timestamp"
        ) from exc
    now = int(time.time())
    if abs(now - ts) > settings.actor_signature_ttl_seconds:
        raise HTTPException(status_code=401, detail="actor signature expired")
    if not nonce or not signature:
        raise HTTPException(status_code=401, detail="actor signature missing")
    canonical = "\n".join(
        [user_id, role, tenant_id, account_id, permissions_raw, str(ts), nonce]
    )
    expected = hmac.new(
        settings.actor_signing_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="actor signature invalid")
    with database.transaction() as conn:
        conn.execute("DELETE FROM actor_nonces WHERE expires_at_epoch < ?", (now,))
        try:
            conn.execute(
                "INSERT INTO actor_nonces(nonce, expires_at_epoch, created_at) VALUES(?,?,?)",
                (nonce, now + settings.actor_signature_ttl_seconds, utcnow()),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=401, detail="actor signature replayed"
            ) from exc


def build_actor_dependency(settings: BusinessSettings, database: BusinessDatabase):
    def current_actor(
        request: Request,
        authorization: str | None = Header(default=None),
        x_actor_user_id: str | None = Header(default=None),
        x_actor_role: str | None = Header(default=None),
        x_actor_tenant_id: str | None = Header(default=None),
        x_actor_account_id: str | None = Header(default=None),
        x_actor_permissions: str | None = Header(default=None),
        x_actor_timestamp: str | None = Header(default=None),
        x_actor_nonce: str | None = Header(default=None),
        x_actor_signature: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
    ) -> Actor:
        _verify_service_token(authorization, settings)
        user_id = str(x_actor_user_id or "").strip()
        if not user_id:
            raise HTTPException(status_code=401, detail="business actor missing")
        supplied_role = str(x_actor_role or "customer").strip().lower() or "customer"
        supplied_tenant = str(x_actor_tenant_id or "default").strip() or "default"
        account_id = str(x_actor_account_id or user_id).strip() or user_id
        permissions_raw = str(x_actor_permissions or "")
        _verify_signature(
            settings=settings,
            database=database,
            user_id=user_id,
            role=supplied_role,
            tenant_id=supplied_tenant,
            account_id=account_id,
            permissions_raw=permissions_raw,
            timestamp=x_actor_timestamp,
            nonce=x_actor_nonce,
            signature=x_actor_signature,
        )
        # Canonicalize identity from the business service's account source. In a
        # RuoYi/Spring version this block is replaced by trusted JWT/SecurityContext
        # lookup, not by accepting arbitrary role headers.
        with database.read() as conn:
            account = as_dict(
                conn.execute(
                    "SELECT * FROM accounts WHERE user_id=?", (user_id,)
                ).fetchone()
            )
        if account:
            tenant_id = str(account["tenant_id"])
            role = str(account["role"])
            try:
                permissions = frozenset(
                    str(item)
                    for item in json.loads(str(account["permissions_json"] or "[]"))
                )
            except Exception:
                permissions = frozenset()
        else:
            # Demo development mode allows an explicitly signed external actor.
            # Production integrations should provision an account or replace this
            # branch with the enterprise identity provider.
            if settings.is_protected:
                raise HTTPException(
                    status_code=401, detail="business actor account not provisioned"
                )
            tenant_id = supplied_tenant
            role = supplied_role
            permissions = _actor_permissions(permissions_raw)
        return Actor(
            user_id=user_id,
            role=role,
            tenant_id=tenant_id,
            account_id=account_id,
            permissions=permissions,
            request_id=str(x_request_id or "").strip() or None,
        )

    return current_actor


def require_any(actor: Actor, permissions: Iterable[str]) -> None:
    if not any(actor.can(permission) for permission in permissions):
        raise HTTPException(
            status_code=403, detail="business actor lacks required permission"
        )
