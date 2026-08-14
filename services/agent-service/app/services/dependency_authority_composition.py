from __future__ import annotations

"""Application-owned Stage4K4 dependency-authority composition.

The customer-serving default is deliberately ``disabled``.  Selecting
``persistent`` is an explicit startup-time composition choice; it never
follows database contents automatically and environment changes after
process construction cannot switch authority in-place.

Production verification uses Ed25519 public keys only.  Signing private
keys remain outside the Agent process.  The activation governance key and
emergency rollback key must be distinct trust roots.
"""

import base64
import binascii
import os
from dataclasses import dataclass
from hashlib import sha256
from time import time
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_core.runtime.dependency_authority_control import (
    DependencyAuthorityControlProvider,
    DisabledDependencyAuthorityControlProvider,
)
from agent_core.runtime.dependency_authority_persistent_control import (
    PersistentDependencyAuthorityControlProvider,
    SqlAlchemyDependencyAuthorityRollbackDirectiveSource,
    SqlAlchemyDependencyAuthoritySignedRecordSource,
)


DEPENDENCY_AUTHORITY_CONTROL_MODE_ENV = "DEPENDENCY_AUTHORITY_CONTROL_MODE"
DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME = "ed25519-v1"
_CONTROL_MODES = {"disabled", "persistent"}


class DependencyAuthorityCompositionError(RuntimeError):
    """Startup configuration cannot safely compose the requested authority."""


class _Ed25519Verifier:
    def __init__(
        self,
        *,
        expected_identity: str,
        public_key: Ed25519PublicKey,
    ) -> None:
        self._expected_identity = expected_identity
        self._public_key = public_key

    def _verify(
        self,
        *,
        identity: str,
        signature_scheme: str,
        message: bytes,
        signature: str,
    ) -> bool:
        if identity != self._expected_identity:
            return False
        if signature_scheme != DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME:
            return False
        if not isinstance(message, (bytes, bytearray)):
            return False
        try:
            encoded = str(signature or "").encode("ascii")
            signature_bytes = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, ValueError, binascii.Error):
            return False
        if len(signature_bytes) != 64:
            return False
        try:
            self._public_key.verify(signature_bytes, bytes(message))
        except (InvalidSignature, ValueError):
            return False
        return True


class Ed25519DependencyAuthorityActivationVerifier(_Ed25519Verifier):
    def verify(
        self,
        *,
        signer_id: str,
        signature_scheme: str,
        message: bytes,
        signature: str,
    ) -> bool:
        return self._verify(
            identity=signer_id,
            signature_scheme=signature_scheme,
            message=message,
            signature=signature,
        )


class Ed25519DependencyAuthorityRollbackVerifier(_Ed25519Verifier):
    def verify(
        self,
        *,
        operator_id: str,
        signature_scheme: str,
        message: bytes,
        signature: str,
    ) -> bool:
        return self._verify(
            identity=operator_id,
            signature_scheme=signature_scheme,
            message=message,
            signature=signature,
        )


@dataclass(frozen=True)
class DependencyAuthorityControlComposition:
    mode: str
    provider: DependencyAuthorityControlProvider
    activation_public_key_fingerprint: str | None = None
    rollback_public_key_fingerprint: str | None = None

    def diagnostics(self) -> dict[str, Any]:
        persistent = self.mode == "persistent"
        return {
            "mode": self.mode,
            "provider": self.provider.__class__.__name__,
            "default_disabled": self.mode == "disabled",
            "persistent_provider_wired": persistent,
            "signature_scheme": (
                DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME
                if persistent
                else None
            ),
            "activation_public_key_fingerprint": (
                self.activation_public_key_fingerprint
            ),
            "rollback_public_key_fingerprint": (
                self.rollback_public_key_fingerprint
            ),
            "trust_roots_independent": bool(
                persistent
                and self.activation_public_key_fingerprint
                and self.rollback_public_key_fingerprint
                and self.activation_public_key_fingerprint
                != self.rollback_public_key_fingerprint
            ),
            "owns_model_configuration": False,
            "creates_permit": False,
            "dispatches_tools": False,
            "mutates_business_state": False,
        }


def resolve_dependency_authority_control_mode() -> str:
    raw = (os.getenv(DEPENDENCY_AUTHORITY_CONTROL_MODE_ENV) or "disabled").strip().lower()
    if raw not in _CONTROL_MODES:
        raise DependencyAuthorityCompositionError(
            f"{DEPENDENCY_AUTHORITY_CONTROL_MODE_ENV} must be disabled or persistent"
        )
    return raw


def _required_setting(name: str, *, limit: int) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise DependencyAuthorityCompositionError(f"{name} is required in persistent mode")
    if len(value) > limit:
        raise DependencyAuthorityCompositionError(f"{name} exceeds the allowed length")
    return value


def _ed25519_public_key(name: str) -> tuple[Ed25519PublicKey, str]:
    encoded = _required_setting(name, limit=512)
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise DependencyAuthorityCompositionError(
            f"{name} must be canonical base64 for a raw Ed25519 public key"
        ) from exc
    if len(raw) != 32:
        raise DependencyAuthorityCompositionError(
            f"{name} must decode to exactly 32 Ed25519 public-key bytes"
        )
    try:
        key = Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise DependencyAuthorityCompositionError(
            f"{name} is not a valid Ed25519 public key"
        ) from exc
    return key, sha256(raw).hexdigest()


def build_dependency_authority_control_composition(
    *,
    store_provider: Any,
    evaluation_time_resolver: Callable[[], float] | None = None,
) -> DependencyAuthorityControlComposition:
    """Compose once at process startup; never auto-activate from stored rows."""

    mode = resolve_dependency_authority_control_mode()
    if mode == "disabled":
        return DependencyAuthorityControlComposition(
            mode="disabled",
            provider=DisabledDependencyAuthorityControlProvider(),
        )

    engine = getattr(store_provider, "engine", None)
    sa = getattr(store_provider, "sa", None)
    if engine is None or sa is None:
        raise DependencyAuthorityCompositionError(
            "persistent dependency authority requires the shared SQLAlchemy StoreProvider"
        )

    activation_signer_id = _required_setting(
        "DEPENDENCY_AUTHORITY_ACTIVATION_SIGNER_ID",
        limit=500,
    )
    rollback_operator_id = _required_setting(
        "DEPENDENCY_AUTHORITY_ROLLBACK_OPERATOR_ID",
        limit=500,
    )
    if activation_signer_id == rollback_operator_id:
        raise DependencyAuthorityCompositionError(
            "activation signer and rollback operator identities must be distinct"
        )

    activation_key, activation_fingerprint = _ed25519_public_key(
        "DEPENDENCY_AUTHORITY_ACTIVATION_PUBLIC_KEY_B64"
    )
    rollback_key, rollback_fingerprint = _ed25519_public_key(
        "DEPENDENCY_AUTHORITY_ROLLBACK_PUBLIC_KEY_B64"
    )
    if activation_fingerprint == rollback_fingerprint:
        raise DependencyAuthorityCompositionError(
            "activation and rollback Ed25519 trust roots must be distinct"
        )

    provider = PersistentDependencyAuthorityControlProvider(
        activation_source=SqlAlchemyDependencyAuthoritySignedRecordSource(
            engine=engine,
            sa=sa,
        ),
        activation_signature_verifier=(
            Ed25519DependencyAuthorityActivationVerifier(
                expected_identity=activation_signer_id,
                public_key=activation_key,
            )
        ),
        rollback_source=SqlAlchemyDependencyAuthorityRollbackDirectiveSource(
            engine=engine,
            sa=sa,
        ),
        rollback_signature_verifier=(
            Ed25519DependencyAuthorityRollbackVerifier(
                expected_identity=rollback_operator_id,
                public_key=rollback_key,
            )
        ),
        evaluation_time_resolver=evaluation_time_resolver or time,
    )
    return DependencyAuthorityControlComposition(
        mode="persistent",
        provider=provider,
        activation_public_key_fingerprint=activation_fingerprint,
        rollback_public_key_fingerprint=rollback_fingerprint,
    )


def dependency_authority_composition_readiness(
    composition: DependencyAuthorityControlComposition,
) -> dict[str, Any]:
    """Return a sanitized readiness view without exposing signed control records."""

    diagnostics = composition.diagnostics()
    if composition.mode == "disabled":
        return {
            **diagnostics,
            "ready": True,
            "status": "DISABLED_FAIL_CLOSED",
            "trusted_control_available": False,
        }

    try:
        resolved = composition.provider.resolve()
    except Exception:
        resolved = None
    if not isinstance(resolved, dict):
        return {
            **diagnostics,
            "ready": False,
            "status": "PERSISTENT_CONTROL_UNAVAILABLE_FAIL_CLOSED",
            "trusted_control_available": False,
        }

    rollback_requested = resolved.get("rollback_requested") is True
    return {
        **diagnostics,
        "ready": True,
        "status": (
            "PERSISTENT_ROLLBACK_ACTIVE"
            if rollback_requested
            else "PERSISTENT_CONTROL_VERIFIED"
        ),
        "trusted_control_available": True,
        "has_activation_preflight": isinstance(
            resolved.get("activation_preflight"), dict
        ),
        "has_runtime_activation": isinstance(
            resolved.get("runtime_activation"), dict
        ),
        "rollback_requested": rollback_requested,
        "control_head_identity": (
            dict(resolved["control_head_identity"])
            if isinstance(resolved.get("control_head_identity"), dict)
            else None
        ),
        "rollback_head_identity": (
            dict(resolved["rollback_head_identity"])
            if isinstance(resolved.get("rollback_head_identity"), dict)
            else None
        ),
    }


__all__ = [
    "DEPENDENCY_AUTHORITY_CONTROL_MODE_ENV",
    "DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME",
    "DependencyAuthorityCompositionError",
    "DependencyAuthorityControlComposition",
    "Ed25519DependencyAuthorityActivationVerifier",
    "Ed25519DependencyAuthorityRollbackVerifier",
    "build_dependency_authority_control_composition",
    "dependency_authority_composition_readiness",
    "resolve_dependency_authority_control_mode",
]
