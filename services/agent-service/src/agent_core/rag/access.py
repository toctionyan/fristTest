from __future__ import annotations

"""Deterministic document access scopes shared by upload, listing and retrieval.

The access decision is made before ranking.  Unknown uploaded documents fail
closed; only explicitly public builtin/published documents are globally visible.
"""
from typing import Any


def normalize_scope(*, tenant_id: str | None, user_id: str | None, role: str | None = None) -> dict[str, str]:
    return {
        "tenant_id": str(tenant_id or "").strip(),
        "owner_id": str(user_id or "").strip(),
        "role": str(role or "").strip().lower(),
    }


def document_metadata_for_scope(scope: dict[str, str], *, visibility: str = "tenant", builtin: bool = False) -> dict[str, Any]:
    visibility = str(visibility or "tenant").strip().lower()
    if builtin:
        visibility = "public"
    return {
        "visibility": visibility,
        "tenant_id": str(scope.get("tenant_id") or ""),
        "owner_id": str(scope.get("owner_id") or ""),
        "status": "published",
        "builtin": bool(builtin),
    }


def is_visible(metadata: dict[str, Any] | None, scope: dict[str, str] | None) -> bool:
    meta = dict(metadata or {})
    actor = dict(scope or {})
    visibility = str(meta.get("visibility") or "").strip().lower()
    # Seed policies are deliberately public.  Any non-builtin content without
    # explicit scope is fail-closed rather than accidentally global.
    if visibility == "public" and (meta.get("builtin") or meta.get("status") == "published"):
        return True
    tenant_id = str(actor.get("tenant_id") or "")
    owner_id = str(actor.get("owner_id") or "")
    if visibility in {"tenant", "internal"}:
        return bool(tenant_id) and tenant_id == str(meta.get("tenant_id") or "")
    if visibility in {"private", "user"}:
        return bool(owner_id) and owner_id == str(meta.get("owner_id") or "") and tenant_id == str(meta.get("tenant_id") or "")
    return False


def scope_filter(scope: dict[str, str]) -> dict[str, Any]:
    """Opaque filter consumed by providers.  Providers must apply it before top-k."""
    return {"__access_scope__": dict(scope)}
