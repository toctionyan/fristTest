from __future__ import annotations

"""Stage 4K2 persistent dependency-authority control contracts.

This module is deliberately production-capable but not production-wired.
It adds an append-only persistent activation source, an independent signed
emergency-rollback source, and a fail-closed composite provider.  AgentService
continues to use DisabledDependencyAuthorityControlProvider until a later,
separately authorized production-activation change.

The runtime process owns no write API for either control table.  Production
operator/governance tooling is expected to append immutable rows under separate
database privileges.
"""

from copy import deepcopy
from hashlib import sha256
import json
import math
from typing import Any, Callable, Protocol

from agent_core.runtime.dependency_authority_signed_provider import (
    DependencyAuthoritySignatureVerifier,
    DependencyAuthoritySignedRecordSource,
    SignedRecordDependencyAuthorityControlProvider,
    dependency_authority_signed_record_integrity,
)

DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_VERSION = (
    "typed-dependency-authority-rollback-directive@1"
)
DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_AUTHORITY = (
    "independent_operator_dependency_authority_rollback"
)

_ROLLBACK_DIRECTIVE_FIELDS = {
    "version",
    "authority",
    "immutable",
    "rollback_epoch",
    "revision",
    "operator_id",
    "signature_scheme",
    "issued_at",
    "rollback_requested",
    "reason_code",
    "payload_digest",
    "signature",
    "directive_digest",
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


def _rollback_payload(directive: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": directive.get("version"),
        "authority": directive.get("authority"),
        "immutable": directive.get("immutable"),
        "rollback_epoch": directive.get("rollback_epoch"),
        "revision": directive.get("revision"),
        "operator_id": directive.get("operator_id"),
        "signature_scheme": directive.get("signature_scheme"),
        "issued_at": directive.get("issued_at"),
        "rollback_requested": directive.get("rollback_requested"),
        "reason_code": directive.get("reason_code"),
    }


def build_dependency_authority_rollback_directive(
    *,
    rollback_epoch: int,
    revision: str,
    operator_id: str,
    signature_scheme: str,
    issued_at: float,
    rollback_requested: bool,
    reason_code: str,
    signature: str,
) -> dict[str, Any]:
    """Build the deterministic rollback envelope; signing remains external."""

    directive: dict[str, Any] = {
        "version": DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_VERSION,
        "authority": DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_AUTHORITY,
        "immutable": True,
        "rollback_epoch": rollback_epoch,
        "revision": _text(revision, limit=500),
        "operator_id": _text(operator_id, limit=500),
        "signature_scheme": _text(signature_scheme, limit=200),
        "issued_at": float(issued_at),
        "rollback_requested": rollback_requested is True,
        "reason_code": _text(reason_code, limit=500),
    }
    directive["payload_digest"] = _strict_digest(_rollback_payload(directive))
    directive["signature"] = _text(signature, limit=4096)
    directive["directive_digest"] = _strict_digest(
        {
            **_rollback_payload(directive),
            "payload_digest": directive["payload_digest"],
            "signature": directive["signature"],
        }
    )
    return directive


def dependency_authority_rollback_directive_signing_bytes(
    directive: dict[str, Any] | None,
) -> bytes:
    row = directive if isinstance(directive, dict) else {}
    return _canonical_bytes(_rollback_payload(row))


def dependency_authority_rollback_directive_integrity(
    directive: dict[str, Any] | None,
) -> dict[str, Any]:
    row = deepcopy(directive) if isinstance(directive, dict) else {}
    errors: list[str] = []
    unknown = sorted(set(row) - _ROLLBACK_DIRECTIVE_FIELDS)
    if unknown:
        errors.append("ROLLBACK_DIRECTIVE_UNKNOWN_FIELDS_FORBIDDEN")
    if row.get("version") != DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_VERSION:
        errors.append("ROLLBACK_DIRECTIVE_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_AUTHORITY:
        errors.append("ROLLBACK_DIRECTIVE_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("ROLLBACK_DIRECTIVE_IMMUTABLE_REQUIRED")

    epoch = row.get("rollback_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
        errors.append("ROLLBACK_DIRECTIVE_EPOCH_REQUIRED")
    if not _text(row.get("revision"), limit=500):
        errors.append("ROLLBACK_DIRECTIVE_REVISION_REQUIRED")
    if not _text(row.get("operator_id"), limit=500):
        errors.append("ROLLBACK_DIRECTIVE_OPERATOR_REQUIRED")
    if not _text(row.get("signature_scheme"), limit=200):
        errors.append("ROLLBACK_DIRECTIVE_SIGNATURE_SCHEME_REQUIRED")
    if _positive_float(row.get("issued_at")) is None:
        errors.append("ROLLBACK_DIRECTIVE_ISSUED_AT_REQUIRED")
    if not isinstance(row.get("rollback_requested"), bool):
        errors.append("ROLLBACK_DIRECTIVE_FLAG_REQUIRED")
    if row.get("rollback_requested") is True and not _text(
        row.get("reason_code"), limit=500
    ):
        errors.append("ROLLBACK_DIRECTIVE_REASON_REQUIRED")

    stored_payload_digest = _text(row.get("payload_digest"), limit=128)
    if not stored_payload_digest:
        errors.append("ROLLBACK_DIRECTIVE_PAYLOAD_DIGEST_REQUIRED")
    else:
        try:
            expected_payload_digest = _strict_digest(_rollback_payload(row))
        except (TypeError, ValueError):
            expected_payload_digest = ""
            errors.append("ROLLBACK_DIRECTIVE_PAYLOAD_NOT_CANONICAL_JSON")
        if expected_payload_digest and stored_payload_digest != expected_payload_digest:
            errors.append("ROLLBACK_DIRECTIVE_PAYLOAD_DIGEST_INVALID")

    signature = _text(row.get("signature"), limit=4096)
    if not signature:
        errors.append("ROLLBACK_DIRECTIVE_SIGNATURE_REQUIRED")

    stored_directive_digest = _text(row.get("directive_digest"), limit=128)
    if not stored_directive_digest:
        errors.append("ROLLBACK_DIRECTIVE_DIGEST_REQUIRED")
    else:
        try:
            expected_directive_digest = _strict_digest(
                {
                    **_rollback_payload(row),
                    "payload_digest": stored_payload_digest,
                    "signature": signature,
                }
            )
        except (TypeError, ValueError):
            expected_directive_digest = ""
            errors.append("ROLLBACK_DIRECTIVE_NOT_CANONICAL_JSON")
        if (
            expected_directive_digest
            and stored_directive_digest != expected_directive_digest
        ):
            errors.append("ROLLBACK_DIRECTIVE_DIGEST_INVALID")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "rollback_epoch": (
            epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None
        ),
        "revision": _text(row.get("revision"), limit=500) or None,
        "snapshot_digest": stored_directive_digest or None,
    }


class DependencyAuthorityRollbackDirectiveSource(Protocol):
    """Independent operator-controlled source for the latest rollback directive."""

    def load_current_rollback_directive(self) -> dict[str, Any] | None: ...


class DependencyAuthorityRollbackSignatureVerifier(Protocol):
    """Independent rollback trust-root adapter supplied by composition."""

    def verify(
        self,
        *,
        operator_id: str,
        signature_scheme: str,
        message: bytes,
        signature: str,
    ) -> bool: ...


class SqlAlchemyDependencyAuthoritySignedRecordSource:
    """Read the highest immutable control epoch from the shared production DB."""

    def __init__(self, *, engine: Any, sa: Any) -> None:
        self._engine = engine
        self._sa = sa

    def _load_head(self) -> dict[str, Any] | None:
        statement = self._sa.text(
            """
            SELECT control_epoch, revision, snapshot_digest, record_json
            FROM agent_dependency_authority_control_records
            ORDER BY control_epoch DESC
            LIMIT 1
            """
        )
        with self._engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def load_head_identity(self) -> dict[str, Any] | None:
        row = self._load_head()
        if row is None:
            return None
        return {
            "control_epoch": int(row["control_epoch"]),
            "revision": str(row["revision"]),
            "snapshot_digest": str(row["snapshot_digest"]),
        }

    def load_signed_record(self) -> dict[str, Any] | None:
        row = self._load_head()
        if row is None:
            return None
        record = json.loads(str(row["record_json"]))
        if not isinstance(record, dict):
            raise ValueError("dependency-authority control row is not an object")
        integrity = dependency_authority_signed_record_integrity(record)
        if not integrity.get("ok"):
            raise ValueError("dependency-authority control row failed structural integrity")
        if int(row["control_epoch"]) != int(record.get("control_epoch") or 0):
            raise ValueError("dependency-authority control epoch mismatch")
        if str(row["revision"]) != str(record.get("revision") or ""):
            raise ValueError("dependency-authority control revision mismatch")
        if str(row["snapshot_digest"]) != str(record.get("record_digest") or ""):
            raise ValueError("dependency-authority control snapshot digest mismatch")
        return deepcopy(record)


class SqlAlchemyDependencyAuthorityRollbackDirectiveSource:
    """Read rollback independently from the activation-record source."""

    def __init__(self, *, engine: Any, sa: Any) -> None:
        self._engine = engine
        self._sa = sa

    def _load_head(self) -> dict[str, Any] | None:
        statement = self._sa.text(
            """
            SELECT rollback_epoch, revision, snapshot_digest, directive_json
            FROM agent_dependency_authority_rollback_directives
            ORDER BY rollback_epoch DESC
            LIMIT 1
            """
        )
        with self._engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def load_head_identity(self) -> dict[str, Any] | None:
        row = self._load_head()
        if row is None:
            return None
        return {
            "rollback_epoch": int(row["rollback_epoch"]),
            "revision": str(row["revision"]),
            "snapshot_digest": str(row["snapshot_digest"]),
        }

    def load_current_rollback_directive(self) -> dict[str, Any] | None:
        row = self._load_head()
        if row is None:
            return None
        directive = json.loads(str(row["directive_json"]))
        if not isinstance(directive, dict):
            raise ValueError("dependency-authority rollback row is not an object")
        integrity = dependency_authority_rollback_directive_integrity(directive)
        if not integrity.get("ok"):
            raise ValueError("dependency-authority rollback row failed structural integrity")
        if int(row["rollback_epoch"]) != int(directive.get("rollback_epoch") or 0):
            raise ValueError("dependency-authority rollback epoch mismatch")
        if str(row["revision"]) != str(directive.get("revision") or ""):
            raise ValueError("dependency-authority rollback revision mismatch")
        if str(row["snapshot_digest"]) != str(
            directive.get("directive_digest") or ""
        ):
            raise ValueError("dependency-authority rollback snapshot digest mismatch")
        return deepcopy(directive)


class PersistentDependencyAuthorityControlProvider:
    """Compose persistent activation with an independent fail-closed rollback path.

    Rollback is evaluated first.  A verified ``rollback_requested=true`` directive
    returns legacy-forcing control without consulting the activation source, so
    rollback still works when activation storage or verification is impaired.

    Any rollback-source read/validation/verification error fails closed by
    returning ``None``.  The existing Stage 4F ingress interprets ``None`` as no
    trusted activation control, which preserves legacy authority.
    """

    def __init__(
        self,
        *,
        activation_source: DependencyAuthoritySignedRecordSource,
        activation_signature_verifier: DependencyAuthoritySignatureVerifier,
        rollback_source: DependencyAuthorityRollbackDirectiveSource,
        rollback_signature_verifier: DependencyAuthorityRollbackSignatureVerifier,
        evaluation_time_resolver: Callable[[], float],
    ) -> None:
        self._activation_provider = SignedRecordDependencyAuthorityControlProvider(
            source=activation_source,
            signature_verifier=activation_signature_verifier,
            evaluation_time_resolver=evaluation_time_resolver,
        )
        self._rollback_source = rollback_source
        self._rollback_signature_verifier = rollback_signature_verifier

    def _verified_rollback(self) -> dict[str, Any] | None:
        directive = self._rollback_source.load_current_rollback_directive()
        if directive is None:
            return {}
        integrity = dependency_authority_rollback_directive_integrity(directive)
        if not integrity.get("ok"):
            raise ValueError("rollback directive failed structural integrity")
        verified = self._rollback_signature_verifier.verify(
            operator_id=str(directive.get("operator_id") or ""),
            signature_scheme=str(directive.get("signature_scheme") or ""),
            message=dependency_authority_rollback_directive_signing_bytes(directive),
            signature=str(directive.get("signature") or ""),
        )
        if verified is not True:
            raise ValueError("rollback directive signature rejected")
        return directive

    def resolve(self) -> dict[str, Any] | None:
        try:
            rollback = self._verified_rollback()
        except Exception:
            return None
        if rollback is None:
            return None
        if rollback.get("rollback_requested") is True:
            return {
                "activation_preflight": None,
                "runtime_activation": None,
                "evaluation_time": None,
                "rollback_requested": True,
            }
        return self._activation_provider.resolve()


__all__ = [
    "DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_AUTHORITY",
    "DEPENDENCY_AUTHORITY_ROLLBACK_DIRECTIVE_VERSION",
    "DependencyAuthorityRollbackDirectiveSource",
    "DependencyAuthorityRollbackSignatureVerifier",
    "PersistentDependencyAuthorityControlProvider",
    "SqlAlchemyDependencyAuthorityRollbackDirectiveSource",
    "SqlAlchemyDependencyAuthoritySignedRecordSource",
    "build_dependency_authority_rollback_directive",
    "dependency_authority_rollback_directive_integrity",
    "dependency_authority_rollback_directive_signing_bytes",
]
