from __future__ import annotations

"""Dependency-authority provider contract with a default-disabled control seam.

This core module deliberately owns no environment, model, database, or trust-root
configuration. Stage4K4 application composition may select the persistent signed
provider at process startup, but the customer-serving default remains the explicit
fail-closed disabled provider.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Protocol


DEPENDENCY_AUTHORITY_CONTROL_SNAPSHOT_VERSION = (
    "typed-dependency-authority-control-snapshot@1"
)
DEPENDENCY_AUTHORITY_CONTROL_PROVIDER_AUTHORITY = (
    "external_trusted_dependency_authority_control_provider"
)


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def build_dependency_authority_control_snapshot(
    *,
    provider_id: str,
    revision: str,
    signer_id: str,
    signature_scheme: str,
    signed_record_digest: str,
    signature_verified: bool,
    activation_preflight: dict[str, Any] | None = None,
    runtime_activation: dict[str, Any] | None = None,
    evaluation_time: float | None = None,
    rollback_requested: bool = False,
) -> dict[str, Any]:
    """Build a sealed provider snapshot without touching runtime configuration."""

    payload: dict[str, Any] = {
        "version": DEPENDENCY_AUTHORITY_CONTROL_SNAPSHOT_VERSION,
        "authority": DEPENDENCY_AUTHORITY_CONTROL_PROVIDER_AUTHORITY,
        "immutable": True,
        "provider_id": _text(provider_id, limit=500),
        "revision": _text(revision, limit=500),
        "signer_id": _text(signer_id, limit=500),
        "signature_scheme": _text(signature_scheme, limit=200),
        "signed_record_digest": _text(signed_record_digest, limit=128),
        "signature_verified": signature_verified is True,
        "activation_preflight": (
            deepcopy(activation_preflight)
            if isinstance(activation_preflight, dict)
            else None
        ),
        "runtime_activation": (
            deepcopy(runtime_activation)
            if isinstance(runtime_activation, dict)
            else None
        ),
        "evaluation_time": (
            float(evaluation_time) if evaluation_time is not None else None
        ),
        "rollback_requested": rollback_requested is True,
        "owns_environment_configuration": False,
        "owns_model_configuration": False,
        "creates_permit": False,
        "dispatches_tools": False,
        "mutates_business_state": False,
    }
    payload["snapshot_digest"] = _digest(payload)
    return payload


def dependency_authority_control_snapshot_integrity(
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate provider provenance and the sealed control snapshot."""

    row = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_AUTHORITY_CONTROL_SNAPSHOT_VERSION:
        errors.append("CONTROL_SNAPSHOT_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_AUTHORITY_CONTROL_PROVIDER_AUTHORITY:
        errors.append("CONTROL_SNAPSHOT_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("CONTROL_SNAPSHOT_IMMUTABLE_REQUIRED")
    if not _text(row.get("provider_id"), limit=500):
        errors.append("CONTROL_PROVIDER_ID_REQUIRED")
    if not _text(row.get("revision"), limit=500):
        errors.append("CONTROL_PROVIDER_REVISION_REQUIRED")
    if not _text(row.get("signer_id"), limit=500):
        errors.append("CONTROL_PROVIDER_SIGNER_REQUIRED")
    if not _text(row.get("signature_scheme"), limit=200):
        errors.append("CONTROL_PROVIDER_SIGNATURE_SCHEME_REQUIRED")
    if not _text(row.get("signed_record_digest"), limit=128):
        errors.append("CONTROL_PROVIDER_SIGNED_RECORD_DIGEST_REQUIRED")
    if row.get("signature_verified") is not True:
        errors.append("CONTROL_PROVIDER_SIGNATURE_VERIFICATION_REQUIRED")
    if row.get("owns_environment_configuration") is not False:
        errors.append("CONTROL_PROVIDER_ENVIRONMENT_OWNERSHIP_FORBIDDEN")
    if row.get("owns_model_configuration") is not False:
        errors.append("CONTROL_PROVIDER_MODEL_OWNERSHIP_FORBIDDEN")
    for field in ("creates_permit", "dispatches_tools", "mutates_business_state"):
        if bool(row.get(field)):
            errors.append(f"CONTROL_PROVIDER_{field.upper()}_MUST_BE_FALSE")

    has_preflight = isinstance(row.get("activation_preflight"), dict)
    has_activation = isinstance(row.get("runtime_activation"), dict)
    if has_preflight or has_activation:
        try:
            evaluation_time = float(row.get("evaluation_time"))
        except (TypeError, ValueError):
            evaluation_time = 0.0
        if evaluation_time <= 0:
            errors.append("CONTROL_PROVIDER_EVALUATION_TIME_REQUIRED")

    stored = _text(row.get("snapshot_digest"), limit=128)
    if not stored:
        errors.append("CONTROL_SNAPSHOT_DIGEST_REQUIRED")
    else:
        payload = deepcopy(row)
        payload.pop("snapshot_digest", None)
        if stored != _digest(payload):
            errors.append("CONTROL_SNAPSHOT_DIGEST_INVALID")
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "snapshot_digest": stored or None,
    }


class DependencyAuthorityControlProvider(Protocol):
    """Application-owned provider interface; storage/transport is external."""

    def resolve(self) -> dict[str, Any] | None: ...


class DisabledDependencyAuthorityControlProvider:
    """Production composition default: explicit fail-closed, no activation control."""

    def resolve(self) -> None:
        return None


class StaticVerifiedDependencyAuthorityControlProvider:
    """Reference provider for tests and deterministic replay, not a trust root."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = deepcopy(snapshot)

    def resolve(self) -> dict[str, Any] | None:
        integrity = dependency_authority_control_snapshot_integrity(self._snapshot)
        if not integrity.get("ok"):
            return None
        row = deepcopy(self._snapshot)
        return {
            "activation_preflight": deepcopy(row.get("activation_preflight")),
            "runtime_activation": deepcopy(row.get("runtime_activation")),
            "evaluation_time": row.get("evaluation_time"),
            "rollback_requested": row.get("rollback_requested") is True,
        }


def dependency_authority_control_resolver(
    provider: DependencyAuthorityControlProvider,
):
    """Adapt an application-owned provider to the Stage 4F zero-arg seam."""

    return provider.resolve


__all__ = [
    "DEPENDENCY_AUTHORITY_CONTROL_PROVIDER_AUTHORITY",
    "DEPENDENCY_AUTHORITY_CONTROL_SNAPSHOT_VERSION",
    "DependencyAuthorityControlProvider",
    "DisabledDependencyAuthorityControlProvider",
    "StaticVerifiedDependencyAuthorityControlProvider",
    "build_dependency_authority_control_snapshot",
    "dependency_authority_control_resolver",
    "dependency_authority_control_snapshot_integrity",
]
