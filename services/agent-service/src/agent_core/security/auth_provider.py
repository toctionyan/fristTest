from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from fastapi import HTTPException, Request

from agent_core.security.roles import ROLE_PERMISSIONS, can, normalize_role
from agent_core.config import is_local_dev


@dataclass(frozen=True)
class AuthenticatedActor:
    """Trusted identity after API authentication.

    The important design rule is: routers and AgentService consume this object,
    not user_id/role fields supplied by request bodies.  It is intentionally
    provider-neutral so the agent can switch from local JWT to a RuoYi/Spring
    Security `/auth/me` endpoint without touching graph nodes.
    """

    user_id: str
    role: str
    tenant_id: str | None = None
    permissions: tuple[str, ...] = field(default_factory=tuple)
    source: str = "unknown"
    subject: str | None = None

    def has_permission(self, permission: str) -> bool:
        return permission in set(self.permissions or ()) or can(self.role, permission)


class AuthProvider(Protocol):
    def authenticate(self, request: Request) -> AuthenticatedActor:
        ...


class AuthenticationError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def auth_required() -> bool:
    # Authentication is opt-out only in explicitly declared APP_PROFILE=local mode.
    return _truthy(os.getenv("AGENT_REQUIRE_AUTH", "true" if not is_local_dev() else "false"))


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthenticationError("invalid Authorization header; expected Bearer token")
    return parts[1].strip()


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _json_b64url_decode(value: str) -> dict[str, Any]:
    try:
        data = json.loads(_b64url_decode(value).decode("utf-8"))
    except Exception as exc:
        raise AuthenticationError("invalid jwt payload") from exc
    if not isinstance(data, dict):
        raise AuthenticationError("invalid jwt payload")
    return data


def _permissions_for_role(role: str) -> tuple[str, ...]:
    return tuple(sorted(ROLE_PERMISSIONS[normalize_role(role)]))


class HmacJwtAuthProvider:
    """Minimal HS256 JWT verifier using stdlib only.

    This intentionally supports the common production boundary without adding a
    heavy dependency.  For RuoYi/Spring Security, prefer RemoteUserInfoAuthProvider
    if tokens are signed/validated by the Java service.
    """

    def __init__(self, *, secret: str | None = None, issuer: str | None = None, audience: str | None = None):
        self.secret = secret or os.getenv("AGENT_JWT_SECRET", "")
        self.issuer = issuer if issuer is not None else os.getenv("AGENT_JWT_ISSUER")
        self.audience = audience if audience is not None else os.getenv("AGENT_JWT_AUDIENCE")
        if not self.secret:
            raise AuthenticationError("AGENT_JWT_SECRET is required for jwt_hs256 auth provider")

    def _verify(self, token: str) -> dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("invalid jwt format")
        header_b64, payload_b64, signature_b64 = parts
        header = _json_b64url_decode(header_b64)
        if header.get("alg") != "HS256":
            raise AuthenticationError("unsupported jwt alg")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = hmac.new(self.secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        try:
            supplied = _b64url_decode(signature_b64)
        except Exception as exc:
            raise AuthenticationError("invalid jwt signature") from exc
        if not hmac.compare_digest(expected, supplied):
            raise AuthenticationError("invalid jwt signature")
        payload = _json_b64url_decode(payload_b64)
        now = int(time.time())
        exp = payload.get("exp")
        if exp is not None and int(exp) < now:
            raise AuthenticationError("jwt expired")
        nbf = payload.get("nbf")
        if nbf is not None and int(nbf) > now:
            raise AuthenticationError("jwt not active yet")
        if self.issuer and payload.get("iss") != self.issuer:
            raise AuthenticationError("jwt issuer mismatch")
        if self.audience:
            aud = payload.get("aud")
            valid_aud = self.audience in aud if isinstance(aud, list) else aud == self.audience
            if not valid_aud:
                raise AuthenticationError("jwt audience mismatch")
        return payload

    def authenticate(self, request: Request) -> AuthenticatedActor:
        token = _bearer_token(request)
        if not token:
            raise AuthenticationError("missing Authorization bearer token")
        payload = self._verify(token)
        user_id = str(payload.get("user_id") or payload.get("uid") or payload.get("sub") or "").strip()
        if not user_id:
            raise AuthenticationError("jwt missing user identity")
        role_value = payload.get("role") or payload.get("roles") or "customer"
        if isinstance(role_value, list):
            role_value = role_value[0] if role_value else "customer"
        role = normalize_role(str(role_value))
        permissions_raw = payload.get("permissions") or payload.get("perms") or []
        permissions = tuple(str(p) for p in permissions_raw if p) if isinstance(permissions_raw, list) else ()
        if not permissions:
            permissions = _permissions_for_role(role)
        return AuthenticatedActor(
            user_id=user_id,
            role=role,
            tenant_id=str(payload.get("tenant_id") or payload.get("tenantId") or "") or None,
            permissions=permissions,
            source="jwt_hs256",
            subject=str(payload.get("subject_user_id") or payload.get("subjectUserId") or payload.get("account_id") or payload.get("accountId") or payload.get("sub") or user_id),
        )


class RemoteUserInfoAuthProvider:
    """Validate token through a business auth service such as RuoYi/Spring Boot.

    The endpoint should return either `{user_id, role, permissions, tenant_id}` or
    `{success: true, data: {...}}`.  This keeps Agent auth pluggable: switching to
    RuoYi mostly means changing AGENT_AUTH_PROVIDER and AGENT_AUTH_USERINFO_URL.
    """

    def __init__(self, *, userinfo_url: str | None = None, timeout: float | None = None):
        # The business-service bearer token is an internal service credential,
        # not an end-user credential.  Do not silently point remote user-info at
        # its internal /auth/me route: that would either fail or blur the trust
        # boundary.  A RuoYi/Spring/OIDC user-info endpoint must be explicit.
        configured = userinfo_url or os.getenv("AGENT_AUTH_USERINFO_URL")
        if not configured:
            raise AuthenticationError("AGENT_AUTH_USERINFO_URL is required for remote_userinfo authentication", status_code=500)
        self.userinfo_url = configured
        self.timeout = timeout if timeout is not None else float(os.getenv("AGENT_AUTH_TIMEOUT", "5"))

    def authenticate(self, request: Request) -> AuthenticatedActor:
        token = _bearer_token(request)
        if not token:
            raise AuthenticationError("missing Authorization bearer token")
        try:
            response = httpx.get(self.userinfo_url, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise AuthenticationError("auth service timeout", status_code=503) from exc
        except httpx.HTTPError as exc:
            raise AuthenticationError(f"auth service unavailable: {exc}", status_code=503) from exc
        if response.status_code in {401, 403}:
            raise AuthenticationError("token rejected by auth service", status_code=response.status_code)
        if response.status_code >= 400:
            raise AuthenticationError(f"auth service error: {response.status_code}", status_code=503)
        try:
            payload = response.json()
        except Exception as exc:
            raise AuthenticationError("auth service returned invalid json", status_code=503) from exc
        if not isinstance(payload, dict):
            raise AuthenticationError("auth service returned invalid user info", status_code=503)

        # Support both this demo service (`{success:true,data:{...}}`) and common
        # RuoYi/Spring Security `/getInfo` shapes (`{code:200,user:{...},roles:[],permissions:[]}`).
        code = payload.get("code")
        if code is not None and str(code) not in {"0", "200"}:
            raise AuthenticationError(f"auth service rejected token: code={code}", status_code=401)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        user_obj = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        if not isinstance(data, dict):
            data = {}
        merged: dict[str, Any] = {}
        merged.update(user_obj)
        merged.update(data)

        user_id = str(
            merged.get("user_id") or merged.get("userId") or merged.get("id")
            or merged.get("sub") or merged.get("userName") or merged.get("username") or ""
        ).strip()
        if not user_id:
            raise AuthenticationError("auth service user info missing user_id", status_code=503)
        role_value = merged.get("role") or merged.get("roles") or payload.get("roles") or "customer"
        if isinstance(role_value, list):
            role_value = role_value[0] if role_value else "customer"
        role = normalize_role(str(role_value))
        permissions_raw = merged.get("permissions") or merged.get("perms") or payload.get("permissions") or payload.get("perms") or []
        permissions = tuple(str(p) for p in permissions_raw if p) if isinstance(permissions_raw, list) else ()
        if not permissions:
            permissions = _permissions_for_role(role)
        tenant_id = str(merged.get("tenant_id") or merged.get("tenantId") or payload.get("tenant_id") or payload.get("tenantId") or "") or None
        return AuthenticatedActor(
            user_id=user_id,
            role=role,
            tenant_id=tenant_id,
            permissions=permissions,
            source="remote_userinfo",
            subject=str(merged.get("subject_user_id") or merged.get("subjectUserId") or merged.get("account_id") or merged.get("accountId") or merged.get("sub") or user_id),
        )


class DevHeaderAuthProvider:
    """Development-only compatibility provider.

    It is deliberately refused when AGENT_REQUIRE_AUTH=true unless
    AGENT_ALLOW_INSECURE_DEV_HEADERS=true is explicitly set.  This prevents an
    accidental production deployment where X-User-Role can be spoofed.
    """

    def authenticate(self, request: Request) -> AuthenticatedActor:
        if auth_required() and not _truthy(os.getenv("AGENT_ALLOW_INSECURE_DEV_HEADERS", "false")):
            raise AuthenticationError(
                "dev header auth is disabled when AGENT_REQUIRE_AUTH=true; use jwt_hs256 or remote_userinfo",
                status_code=500,
            )
        user_id = (request.headers.get("X-User-Id") or "system").strip() or "system"
        role = normalize_role(request.headers.get("X-User-Role") or "customer")
        return AuthenticatedActor(
            user_id=user_id,
            role=role,
            tenant_id=(request.headers.get("X-Tenant-Id") or "").strip() or None,
            permissions=_permissions_for_role(role),
            source="dev_headers",
            subject=user_id,
        )


class DevBearerTokenAuthProvider:
    """Safer local-development provider that avoids user/role spoofing headers.

    Token format: `Bearer dev:<user_id>:<role>[:tenant_id]`.
    Only use locally; production should use jwt_hs256 or remote_userinfo.
    """

    def authenticate(self, request: Request) -> AuthenticatedActor:
        token = _bearer_token(request)
        if not token:
            if auth_required():
                raise AuthenticationError("missing Authorization bearer token")
            return DevHeaderAuthProvider().authenticate(request)
        if not token.startswith("dev:"):
            raise AuthenticationError("invalid dev bearer token")
        parts = token.split(":")
        if len(parts) < 3:
            raise AuthenticationError("invalid dev token format")
        user_id = parts[1].strip()
        role = normalize_role(parts[2])
        tenant_id = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
        if not user_id:
            raise AuthenticationError("invalid dev token user")
        return AuthenticatedActor(user_id=user_id, role=role, tenant_id=tenant_id, permissions=_permissions_for_role(role), source="dev_token", subject=user_id)




def _console_dev_login_enabled() -> bool:
    """Whether the built-in test-console login is allowed.

    This deliberately stays development-only. Production uses the existing JWT
    or remote-userinfo providers; no browser-side demo credential is accepted.
    """
    if not is_local_dev():
        return False
    # Local login is explicit even in local mode; it must not appear because a
    # browser merely hit an undeclared development default.
    return _truthy(os.getenv("WEB_CONSOLE_DEV_LOGIN", "false"))


def console_dev_login_enabled() -> bool:
    """Public read-only helper for the web-console router."""
    return _console_dev_login_enabled()


def _console_secret() -> str:
    configured = (os.getenv("WEB_CONSOLE_SESSION_SECRET") or "").strip()
    if configured:
        return configured
    # This fallback is only reachable when dev console login is enabled. It is
    # intentionally not valid in production because _console_dev_login_enabled
    # rejects production before any token is issued or accepted.
    return "ecom-v63-local-console-session-secret"


def console_dev_accounts() -> list[dict[str, str]]:
    """Small local-only account set used to exercise the restored V4 console.

    Identity in production must come from the normal authentication provider.
    These entries do not grant business access by themselves: the independent
    Business Service still canonicalizes actor identity from its account source.
    """
    default = [
        {"username": "customer_u001", "user_id": "u001", "display_name": "张三", "role": "customer", "tenant_id": "default"},
        {"username": "customer_u002", "user_id": "u002", "display_name": "李四", "role": "customer", "tenant_id": "default"},
        {"username": "customer_u003", "user_id": "u003", "display_name": "王五", "role": "customer", "tenant_id": "tenant-B"},
        {"username": "operator_001", "user_id": "operator001", "display_name": "客服一号", "role": "operator", "tenant_id": "default"},
        {"username": "developer_001", "user_id": "developer001", "display_name": "开发调试", "role": "developer", "tenant_id": "default"},
        {"username": "admin_001", "user_id": "admin001", "display_name": "管理员", "role": "admin", "tenant_id": "default"},
    ]
    raw = (os.getenv("WEB_CONSOLE_DEV_ACCOUNTS_JSON") or "").strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except Exception:
        return default
    if not isinstance(parsed, list):
        return default
    normalized: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("user_id") or item.get("username") or "").strip()
        username = str(item.get("username") or user_id).strip()
        if not user_id or not username:
            continue
        normalized.append({
            "username": username,
            "user_id": user_id,
            "display_name": str(item.get("display_name") or user_id),
            "role": normalize_role(str(item.get("role") or "customer")),
            "tenant_id": str(item.get("tenant_id") or "default"),
        })
    return normalized or default


def issue_console_dev_token(*, user_id: str, role: str, tenant_id: str | None, subject: str | None = None) -> str:
    if not _console_dev_login_enabled():
        raise AuthenticationError("local web console login is disabled", status_code=404)
    expires_at = int(time.time()) + int(os.getenv("WEB_CONSOLE_SESSION_TTL_SECONDS", "28800"))
    payload = {
        "user_id": user_id,
        "role": normalize_role(role),
        "tenant_id": tenant_id or None,
        "subject": subject or user_id,
        "exp": expires_at,
        "kind": "local_console",
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(_console_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"console.{body}.{signature}"


def _authenticate_console_dev_token(token: str) -> AuthenticatedActor:
    if not _console_dev_login_enabled():
        raise AuthenticationError("local web console login is disabled", status_code=404)
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "console":
        raise AuthenticationError("invalid console session token")
    body, signature = parts[1], parts[2]
    expected = hmac.new(_console_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AuthenticationError("invalid console session signature")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise AuthenticationError("invalid console session payload") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "local_console":
        raise AuthenticationError("invalid console session payload")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise AuthenticationError("console session expired")
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise AuthenticationError("console session missing user identity")
    role = normalize_role(str(payload.get("role") or "customer"))
    return AuthenticatedActor(
        user_id=user_id,
        role=role,
        tenant_id=str(payload.get("tenant_id") or "") or None,
        permissions=_permissions_for_role(role),
        source="local_console",
        subject=str(payload.get("subject") or user_id),
    )

def _default_auth_provider() -> str:
    return "dev_headers" if is_local_dev() else "remote_userinfo"


def build_auth_provider() -> AuthProvider:
    mode = os.getenv("AGENT_AUTH_PROVIDER", _default_auth_provider()).strip().lower()
    if mode in {"jwt", "jwt_hs256", "hs256"}:
        return HmacJwtAuthProvider()
    if mode in {"remote", "remote_userinfo", "business", "ruoyi"}:
        return RemoteUserInfoAuthProvider()
    if mode in {"dev_token", "local_token"}:
        return DevBearerTokenAuthProvider()
    if mode in {"dev_headers", "headers", "mock"}:
        return DevHeaderAuthProvider()
    raise AuthenticationError(f"unknown AGENT_AUTH_PROVIDER: {mode}", status_code=500)


def authenticate_request(request: Request) -> AuthenticatedActor:
    try:
        token = _bearer_token(request)
        if token and token.startswith("console."):
            return _authenticate_console_dev_token(token)
        return build_auth_provider().authenticate(request)
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
