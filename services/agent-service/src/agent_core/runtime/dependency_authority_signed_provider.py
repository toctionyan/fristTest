from __future__ import annotations

"""Stage 4K1 signed-record provider contract for dependency-authority control.

This module is production-capable but deliberately not wired into ``AgentService``.
It owns neither model/environment configuration nor signing secrets.  A trusted
application composition must supply both the signed-record source and the
cryptographic verifier.  Until that later composition is explicitly authorized,
Stage 4I keeps the disabled provider active.
"""

from copy import deepcopy
from hashlib import sha256
import json
import math
from typing import Any, Callable, Protocol

from agent_core.runtime.dependency_authority_control import (
    StaticVerifiedDependencyAuthorityControlProvider,
    build_dependency_authority_control_snapshot,
)

DEPENDENCY_AUTHORITY_SIGNED_RECORD_VERSION = (
    "typed-dependency-authority-signed-record@1"
)
DEPENDENCY_AUTHORITY_SIGNED_RECORD_AUTHORITY = (
    "external_governance_signed_dependency_authority_control"
)

_SIGNED_RECORD_FIELDS = {
    "version",
    "authority",
    "immutable",
    "provider_id",
    "revision",
    "control_epoch",
    "signer_id",
    "signature_scheme",
    "issued_at",
    "expires_at",
    "control",
    "payload_digest",
    "signature",
    "record_digest",
}
_CONTROL_FIELDS = {
    "activation_preflight",
    "runtime_activation",
    "rollback_requested",
}


def _text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_digest(value: Any) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _record_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": record.get("version"),
        "authority": record.get("authority"),
        "immutable": record.get("immutable"),
        "provider_id": record.get("provider_id"),
        "revision": record.get("revision"),
        "control_epoch": record.get("control_epoch"),
        "signer_id": record.get("signer_id"),
        "signature_scheme": record.get("signature_scheme"),
        "issued_at": record.get("issued_at"),
        "expires_at": record.get("expires_at"),
        "control": deepcopy(record.get("control")),
    }


def build_dependency_authority_signed_record(
    *,
    provider_id: str,
    revision: str,
    control_epoch: int,
    signer_id: str,
    signature_scheme: str,
    issued_at: float,
    expires_at: float,
    signature: str,
    activation_preflight: dict[str, Any] | None = None,
    runtime_activation: dict[str, Any] | None = None,
    rollback_requested: bool = False,
) -> dict[str, Any]:
    """Build the deterministic signed-record envelope; signing stays external."""

    control = {
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
        "rollback_requested": rollback_requested is True,
    }
    record: dict[str, Any] = {
        "version": DEPENDENCY_AUTHORITY_SIGNED_RECORD_VERSION,
        "authority": DEPENDENCY_AUTHORITY_SIGNED_RECORD_AUTHORITY,
        "immutable": True,
        "provider_id": _text(provider_id, limit=500),
        "revision": _text(revision, limit=500),
        "control_epoch": control_epoch,
        "signer_id": _text(signer_id, limit=500),
        "signature_scheme": _text(signature_scheme, limit=200),
        "issued_at": float(issued_at),
        "expires_at": float(expires_at),
        "control": control,
    }
    record["payload_digest"] = _strict_digest(_record_payload(record))
    record["signature"] = _text(signature, limit=4096)
    record["record_digest"] = _strict_digest(
        {
            **_record_payload(record),
            "payload_digest": record["payload_digest"],
            "signature": record["signature"],
        }
    )
    return record


def dependency_authority_signed_record_signing_bytes(
    record: dict[str, Any] | None,
) -> bytes:
    """Return the exact canonical bytes an external signer/verifier must bind."""

    row = record if isinstance(record, dict) else {}
    return _canonical_bytes(_record_payload(row))


def dependency_authority_signed_record_integrity(
    record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate the deterministic envelope before trusted signature verification."""

    row = deepcopy(record) if isinstance(record, dict) else {}
    errors: list[str] = []
    unknown = sorted(set(row) - _SIGNED_RECORD_FIELDS)
    if unknown:
        errors.append("SIGNED_RECORD_UNKNOWN_FIELDS_FORBIDDEN")
    if row.get("version") != DEPENDENCY_AUTHORITY_SIGNED_RECORD_VERSION:
        errors.append("SIGNED_RECORD_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_AUTHORITY_SIGNED_RECORD_AUTHORITY:
        errors.append("SIGNED_RECORD_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("SIGNED_RECORD_IMMUTABLE_REQUIRED")
    if not _text(row.get("provider_id"), limit=500):
        errors.append("SIGNED_RECORD_PROVIDER_ID_REQUIRED")
    if not _text(row.get("revision"), limit=500):
        errors.append("SIGNED_RECORD_REVISION_REQUIRED")
    epoch = row.get("control_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
        errors.append("SIGNED_RECORD_CONTROL_EPOCH_REQUIRED")
    if not _text(row.get("signer_id"), limit=500):
        errors.append("SIGNED_RECORD_SIGNER_REQUIRED")
    if not _text(row.get("signature_scheme"), limit=200):
        errors.append("SIGNED_RECORD_SIGNATURE_SCHEME_REQUIRED")

    issued_at = _positive_float(row.get("issued_at"))
    expires_at = _positive_float(row.get("expires_at"))
    if issued_at is None:
        errors.append("SIGNED_RECORD_ISSUED_AT_REQUIRED")
    if expires_at is None:
        errors.append("SIGNED_RECORD_EXPIRES_AT_REQUIRED")
    if issued_at is not None and expires_at is not None and expires_at <= issued_at:
        errors.append("SIGNED_RECORD_EXPIRY_ORDER_INVALID")

    control = row.get("control") if isinstance(row.get("control"), dict) else {}
    if not isinstance(row.get("control"), dict):
        errors.append("SIGNED_RECORD_CONTROL_REQUIRED")
    else:
        if set(control) - _CONTROL_FIELDS:
            errors.append("SIGNED_RECORD_CONTROL_UNKNOWN_FIELDS_FORBIDDEN")
        if control.get("activation_preflight") is not None and not isinstance(
            control.get("activation_preflight"), dict
        ):
            errors.append("SIGNED_RECORD_PREFLIGHT_INVALID")
        if control.get("runtime_activation") is not None and not isinstance(
            control.get("runtime_activation"), dict
        ):
            errors.append("SIGNED_RECORD_RUNTIME_ACTIVATION_INVALID")
        if not isinstance(control.get("rollback_requested"), bool):
            errors.append("SIGNED_RECORD_ROLLBACK_FLAG_REQUIRED")

    stored_payload_digest = _text(row.get("payload_digest"), limit=128)
    if not stored_payload_digest:
        errors.append("SIGNED_RECORD_PAYLOAD_DIGEST_REQUIRED")
    else:
        try:
            expected_payload_digest = _strict_digest(_record_payload(row))
        except (TypeError, ValueError):
            expected_payload_digest = ""
            errors.append("SIGNED_RECORD_PAYLOAD_NOT_CANONICAL_JSON")
        if expected_payload_digest and stored_payload_digest != expected_payload_digest:
            errors.append("SIGNED_RECORD_PAYLOAD_DIGEST_INVALID")

    signature = _text(row.get("signature"), limit=4096)
    if not signature:
        errors.append("SIGNED_RECORD_SIGNATURE_REQUIRED")

    stored_record_digest = _text(row.get("record_digest"), limit=128)
    if not stored_record_digest:
        errors.append("SIGNED_RECORD_DIGEST_REQUIRED")
    else:
        try:
            expected_record_digest = _strict_digest(
                {
                    **_record_payload(row),
                    "payload_digest": stored_payload_digest,
                    "signature": signature,
                }
            )
        except (TypeError, ValueError):
            expected_record_digest = ""
            errors.append("SIGNED_RECORD_NOT_CANONICAL_JSON")
        if expected_record_digest and stored_record_digest != expected_record_digest:
            errors.append("SIGNED_RECORD_DIGEST_INVALID")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "record_digest": stored_record_digest or None,
        "payload_digest": stored_payload_digest or None,
        "provider_id": _text(row.get("provider_id"), limit=500) or None,
        "revision": _text(row.get("revision"), limit=500) or None,
        "control_epoch": epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None,
    }


class DependencyAuthoritySignedRecordSource(Protocol):
    """Trusted transport/storage adapter; it must return the current sealed record."""

    def load_signed_record(self) -> dict[str, Any] | None: ...


class DependencyAuthoritySignatureVerifier(Protocol):
    """Cryptographic trust-root adapter supplied by application composition.

    Implementations must reject untrusted signer IDs and unsupported schemes and
    verify ``signature`` over the exact ``message`` bytes.
    """

    def verify(
        self,
        *,
        signer_id: str,
        signature_scheme: str,
        message: bytes,
        signature: str,
    ) -> bool: ...


class SignedRecordDependencyAuthorityControlProvider:
    """Verify a signed external record before exposing Stage 4F control fields."""

    def __init__(
        self,
        *,
        source: DependencyAuthoritySignedRecordSource,
        signature_verifier: DependencyAuthoritySignatureVerifier,
        evaluation_time_resolver: Callable[[], float],
    ) -> None:
        self._source = source
        self._signature_verifier = signature_verifier
        self._evaluation_time_resolver = evaluation_time_resolver

    def resolve(self) -> dict[str, Any] | None:
        try:
            record = self._source.load_signed_record()
        except Exception:
            return None
        integrity = dependency_authority_signed_record_integrity(record)
        if not integrity.get("ok") or not isinstance(record, dict):
            return None

        try:
            verified = self._signature_verifier.verify(
                signer_id=str(record.get("signer_id") or ""),
                signature_scheme=str(record.get("signature_scheme") or ""),
                message=dependency_authority_signed_record_signing_bytes(record),
                signature=str(record.get("signature") or ""),
            )
        except Exception:
            return None
        if verified is not True:
            return None

        try:
            now = float(self._evaluation_time_resolver())
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(now) or now <= 0:
            return None
        issued_at = _positive_float(record.get("issued_at"))
        expires_at = _positive_float(record.get("expires_at"))
        if issued_at is None or expires_at is None or now < issued_at or now >= expires_at:
            return None

        control = record.get("control") if isinstance(record.get("control"), dict) else {}
        snapshot = build_dependency_authority_control_snapshot(
            provider_id=str(record.get("provider_id") or ""),
            revision=str(record.get("revision") or ""),
            signer_id=str(record.get("signer_id") or ""),
            signature_scheme=str(record.get("signature_scheme") or ""),
            signed_record_digest=str(record.get("record_digest") or ""),
            signature_verified=True,
            activation_preflight=control.get("activation_preflight"),
            runtime_activation=control.get("runtime_activation"),
            evaluation_time=now,
            rollback_requested=control.get("rollback_requested") is True,
        )
        return StaticVerifiedDependencyAuthorityControlProvider(snapshot).resolve()


__all__ = [
    "DEPENDENCY_AUTHORITY_SIGNED_RECORD_AUTHORITY",
    "DEPENDENCY_AUTHORITY_SIGNED_RECORD_VERSION",
    "DependencyAuthoritySignatureVerifier",
    "DependencyAuthoritySignedRecordSource",
    "SignedRecordDependencyAuthorityControlProvider",
    "build_dependency_authority_signed_record",
    "dependency_authority_signed_record_integrity",
    "dependency_authority_signed_record_signing_bytes",
]
