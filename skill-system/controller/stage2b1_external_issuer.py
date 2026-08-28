"""Cryptographic external-issuer verification for Stage2B1 artifacts.

The reducer never trusts a caller-provided attestation dictionary.  This
module invokes the fixed GitHub CLI verifier with a fixed policy and returns a
sealed value only after the CLI has verified the artifact signature, subject,
predicate and transparency timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


EXTERNAL_ISSUER_SCHEMA = "github-artifact-attestation-proof@1"
_VERIFIER_TOKEN = object()
_EXPECTED_KEYS = frozenset(
    {"repository", "signer_workflow", "subject_digest", "predicate_type"}
)


class ExternalIssuerVerificationError(ValueError):
    """Raised when GitHub cannot provide a cryptographically verified proof."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ExternalIssuerVerificationError("attestation_output_not_canonicalizable") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExternalIssuerVerificationError(f"{field}_invalid")
    return value.strip()


@dataclass(frozen=True)
class ExternalIssuerProof:
    """Sealed result of the fixed ``gh attestation verify`` invocation."""

    schema: str
    repository: str
    signer_workflow: str
    subject_digest: str
    predicate_type: str
    verification_digest: str
    verified_timestamp_count: int
    _proof_body_json: bytes = field(default=b"", repr=False, compare=False)
    _verifier_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._verifier_token is not _VERIFIER_TOKEN:
            raise ExternalIssuerVerificationError("external_proof_constructor_is_private")
        if self.schema != EXTERNAL_ISSUER_SCHEMA:
            raise ExternalIssuerVerificationError("external_proof_schema_invalid")
        if not isinstance(self._proof_body_json, bytes) or not self._proof_body_json:
            raise ExternalIssuerVerificationError("external_proof_body_missing")
        try:
            body = json.loads(self._proof_body_json.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalIssuerVerificationError("external_proof_body_invalid") from exc
        if not isinstance(body, Mapping):
            raise ExternalIssuerVerificationError("external_proof_body_invalid")
        expected_body = {
            "schema": self.schema,
            "repository": self.repository,
            "signer_workflow": self.signer_workflow,
            "subject_digest": self.subject_digest,
            "predicate_type": self.predicate_type,
            "verification": body.get("verification"),
        }
        if dict(body) != expected_body:
            raise ExternalIssuerVerificationError("external_proof_body_mismatch")
        if self.verification_digest != _digest(body):
            raise ExternalIssuerVerificationError("external_proof_digest_mismatch")
        if (
            isinstance(self.verified_timestamp_count, bool)
            or not isinstance(self.verified_timestamp_count, int)
            or self.verified_timestamp_count < 1
        ):
            raise ExternalIssuerVerificationError("external_proof_timestamp_count_invalid")

    @property
    def proof_ref(self) -> str:
        return "external-issuer:" + self.verification_digest


def _validate_success_output(
    output: object,
    *,
    expected: Mapping[str, Any],
) -> tuple[dict[str, Any], str, int]:
    if not isinstance(expected, Mapping) or set(expected) != set(_EXPECTED_KEYS):
        raise ExternalIssuerVerificationError("attestation_expected_fields_invalid")
    repository = _text(expected["repository"], field="repository")
    signer_workflow = _text(expected["signer_workflow"], field="signer_workflow")
    subject_digest = _text(expected["subject_digest"], field="subject_digest")
    predicate_type = _text(expected["predicate_type"], field="predicate_type")
    if not subject_digest.startswith("sha256:") or len(subject_digest) != len("sha256:") + 64:
        raise ExternalIssuerVerificationError("subject_digest_invalid")
    if not isinstance(output, list) or not output:
        raise ExternalIssuerVerificationError("attestation_output_empty")

    matches: list[dict[str, Any]] = []
    timestamp_count = 0
    for row in output:
        if not isinstance(row, Mapping):
            continue
        verification = row.get("verificationResult")
        if not isinstance(verification, Mapping):
            continue
        statement = verification.get("statement")
        if not isinstance(statement, Mapping):
            continue
        if statement.get("predicateType") != predicate_type:
            continue
        subjects = statement.get("subject")
        if not isinstance(subjects, list):
            continue
        subject_match = any(
            isinstance(subject, Mapping)
            and isinstance(subject.get("digest"), Mapping)
            and subject["digest"].get("sha256") == subject_digest.removeprefix("sha256:")
            for subject in subjects
        )
        if not subject_match:
            continue
        signature = verification.get("signature")
        certificate = signature.get("certificate") if isinstance(signature, Mapping) else None
        if not isinstance(certificate, Mapping):
            continue
        timestamps = verification.get("verifiedTimestamps")
        if not isinstance(timestamps, list) or not timestamps:
            continue
        timestamp_count += len(timestamps)
        matches.append(dict(row))

    if len(matches) != 1:
        raise ExternalIssuerVerificationError("attestation_matching_proof_count_invalid")
    selected = matches[0]
    # The CLI flags enforce repository and signer identity.  Keep those exact
    # policy values in the digest so a proof cannot be replayed under another
    # identity or predicate.
    proof_body = {
        "schema": EXTERNAL_ISSUER_SCHEMA,
        "repository": repository,
        "signer_workflow": signer_workflow,
        "subject_digest": subject_digest,
        "predicate_type": predicate_type,
        "verification": selected,
    }
    return proof_body, _digest(proof_body), timestamp_count


def verify_github_artifact_attestation(
    artifact_path: str | Path,
    *,
    expected: Mapping[str, Any],
    timeout_seconds: int = 60,
) -> ExternalIssuerProof:
    """Run the fixed GitHub attestation verifier and seal its result."""

    path = Path(artifact_path)
    if path.is_symlink() or not path.is_file():
        raise ExternalIssuerVerificationError("attested_artifact_missing_or_unsafe")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
        raise ExternalIssuerVerificationError("attestation_timeout_invalid")
    if not isinstance(expected, Mapping) or set(expected) != set(_EXPECTED_KEYS):
        raise ExternalIssuerVerificationError("attestation_expected_fields_invalid")
    repository = _text(expected["repository"], field="repository")
    signer_workflow = _text(expected["signer_workflow"], field="signer_workflow")
    predicate_type = _text(expected["predicate_type"], field="predicate_type")
    command = [
        "gh",
        "attestation",
        "verify",
        str(path),
        "--repo",
        repository,
        "--signer-workflow",
        signer_workflow,
        "--predicate-type",
        predicate_type,
        "--cert-oidc-issuer",
        "https://token.actions.githubusercontent.com",
        "--format",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalIssuerVerificationError("github_attestation_verifier_unavailable") from exc
    if completed.returncode != 0:
        raise ExternalIssuerVerificationError("github_attestation_verification_failed")
    try:
        output = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExternalIssuerVerificationError("github_attestation_output_invalid") from exc
    proof_body, verification_digest, timestamp_count = _validate_success_output(
        output,
        expected=expected,
    )
    return ExternalIssuerProof(
        schema=EXTERNAL_ISSUER_SCHEMA,
        repository=proof_body["repository"],
        signer_workflow=proof_body["signer_workflow"],
        subject_digest=proof_body["subject_digest"],
        predicate_type=proof_body["predicate_type"],
        verification_digest=verification_digest,
        verified_timestamp_count=timestamp_count,
        _proof_body_json=_canonical(proof_body),
        _verifier_token=_VERIFIER_TOKEN,
    )


def validate_external_issuer_proof(value: object) -> ExternalIssuerProof:
    """Validate the sealed proof without re-running the external verifier."""

    if not isinstance(value, ExternalIssuerProof):
        raise ExternalIssuerVerificationError("external_proof_type_invalid")
    # Re-run the invariant checks so dataclasses.replace() cannot alter a
    # public field while retaining the private verifier token.
    value.__post_init__()
    return value


__all__ = [
    "EXTERNAL_ISSUER_SCHEMA",
    "ExternalIssuerProof",
    "ExternalIssuerVerificationError",
    "validate_external_issuer_proof",
    "verify_github_artifact_attestation",
]
