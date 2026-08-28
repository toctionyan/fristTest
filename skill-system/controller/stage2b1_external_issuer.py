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
import re
import subprocess
from typing import Any, Mapping


EXTERNAL_ISSUER_SCHEMA = "github-artifact-attestation-proof@1"
STAGE2B1_REPOSITORY = "toctionyan/fristTest"
STAGE2B1_SIGNER_WORKFLOW = (
    "toctionyan/fristTest/.github/workflows/p4-8-evidence-producer.yml"
)
STAGE2B1_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
STAGE2B1_SOURCE_REF = "refs/heads/main"
P48_PRODUCER_SCHEMA = "p4-8-evidence-producer@2"
_VERIFIER_TOKEN = object()
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RUNNER_INVOCATION = re.compile(
    r"https://github\.com/toctionyan/fristTest/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)\Z"
)
_EXPECTED_KEYS = frozenset(
    {
        "repository",
        "signer_workflow",
        "subject_digest",
        "predicate_type",
        "source_digest",
        "source_ref",
    }
)


class ExternalIssuerVerificationError(ValueError):
    """Raised when GitHub cannot provide a cryptographically verified proof."""


def _runner_invocation(source_run_id: int, source_run_attempt: int) -> str:
    if (
        isinstance(source_run_id, bool)
        or not isinstance(source_run_id, int)
        or source_run_id < 1
        or isinstance(source_run_attempt, bool)
        or not isinstance(source_run_attempt, int)
        or source_run_attempt < 1
    ):
        raise ExternalIssuerVerificationError("source_run_selector_invalid")
    return (
        f"https://github.com/{STAGE2B1_REPOSITORY}"
        f"/actions/runs/{source_run_id}/attempts/{source_run_attempt}"
    )


def _validate_runner_invocation(value: object) -> tuple[int, int]:
    if not isinstance(value, str):
        raise ExternalIssuerVerificationError("runner_invocation_invalid")
    match = _RUNNER_INVOCATION.fullmatch(value)
    if match is None:
        raise ExternalIssuerVerificationError("runner_invocation_invalid")
    return int(match.group(1)), int(match.group(2))


@dataclass(frozen=True, init=False)
class P48SourceArtifactProof:
    """Immutable witness for one explicitly selected P4.8 run and artifact."""

    source_run_id: int
    source_run_attempt: int
    source_head_sha: str
    artifact_id: str
    artifact_name: str
    artifact_digest: str
    metadata_digest: str
    _verifier_token: object = field(
        default=None, init=False, repr=False, compare=False
    )

    def __init__(self, *, _verifier_token: object, **values: Any) -> None:
        if _verifier_token is not _VERIFIER_TOKEN:
            raise TypeError("source artifact proofs are issued by the fixed verifier")
        expected = {
            name for name in self.__dataclass_fields__ if name != "_verifier_token"
        }
        if set(values) != expected:
            raise TypeError("source artifact proof fields are closed")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_verifier_token", _verifier_token)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self._verifier_token is not _VERIFIER_TOKEN:
            raise ExternalIssuerVerificationError("source_artifact_proof_constructor_is_private")
        for field_name in ("source_run_id", "source_run_attempt"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ExternalIssuerVerificationError("source_artifact_proof_run_invalid")
        if _SHA1.fullmatch(self.source_head_sha) is None:
            raise ExternalIssuerVerificationError("source_artifact_proof_head_sha_invalid")
        if (
            not isinstance(self.artifact_id, str)
            or re.fullmatch(r"[1-9][0-9]*\Z", self.artifact_id) is None
            or not isinstance(self.artifact_name, str)
            or not self.artifact_name
            or _SHA256.fullmatch(self.artifact_digest) is None
        ):
            raise ExternalIssuerVerificationError("source_artifact_proof_artifact_invalid")
        body = {
            "source_run_id": self.source_run_id,
            "source_run_attempt": self.source_run_attempt,
            "source_head_sha": self.source_head_sha,
            "artifact_id": self.artifact_id,
            "artifact_name": self.artifact_name,
            "artifact_digest": self.artifact_digest,
        }
        if self.metadata_digest != _digest(body):
            raise ExternalIssuerVerificationError("source_artifact_proof_digest_mismatch")

    @property
    def proof_ref(self) -> str:
        return "p4-8-source-artifact:" + self.metadata_digest


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


@dataclass(frozen=True, init=False)
class ExternalIssuerProof:
    """Sealed result of the fixed ``gh attestation verify`` invocation."""

    schema: str
    repository: str
    signer_workflow: str
    subject_digest: str
    predicate_type: str
    source_digest: str
    source_ref: str
    verification_digest: str
    verified_timestamp_count: int
    _proof_body_json: bytes = field(default=b"", repr=False, compare=False)
    _verifier_token: object = field(
        default=None, init=False, repr=False, compare=False
    )

    def __init__(self, *, _verifier_token: object, **values: Any) -> None:
        if _verifier_token is not _VERIFIER_TOKEN:
            raise TypeError("external issuer proofs are issued by the fixed verifier")
        expected = {
            name for name in self.__dataclass_fields__ if name != "_verifier_token"
        }
        if set(values) != expected:
            raise TypeError("external issuer proof fields are closed")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_verifier_token", _verifier_token)
        self.__post_init__()

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
            "source_digest": self.source_digest,
            "source_ref": self.source_ref,
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
    expected_runner_invocation: str,
) -> tuple[dict[str, Any], str, int]:
    if not isinstance(expected, Mapping) or set(expected) != set(_EXPECTED_KEYS):
        raise ExternalIssuerVerificationError("attestation_expected_fields_invalid")
    repository = _text(expected["repository"], field="repository")
    signer_workflow = _text(expected["signer_workflow"], field="signer_workflow")
    subject_digest = _text(expected["subject_digest"], field="subject_digest")
    predicate_type = _text(expected["predicate_type"], field="predicate_type")
    source_digest = _text(expected["source_digest"], field="source_digest")
    source_ref = _text(expected["source_ref"], field="source_ref")
    if not signer_workflow.startswith(repository + "/"):
        raise ExternalIssuerVerificationError("signer_workflow_must_include_repository")
    if not subject_digest.startswith("sha256:") or len(subject_digest) != len("sha256:") + 64:
        raise ExternalIssuerVerificationError("subject_digest_invalid")
    if len(source_digest) != 40 or any(char not in "0123456789abcdef" for char in source_digest):
        raise ExternalIssuerVerificationError("source_digest_invalid")
    if not isinstance(output, list) or not output:
        raise ExternalIssuerVerificationError("attestation_output_empty")

    _validate_runner_invocation(expected_runner_invocation)
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
        if certificate.get("runnerInvocationURI") != expected_runner_invocation:
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
        "source_digest": source_digest,
        "source_ref": source_ref,
        "verification": selected,
    }
    return proof_body, _digest(proof_body), timestamp_count


def verify_github_artifact_attestation(
    artifact_path: str | Path,
    *,
    expected: Mapping[str, Any],
    expected_runner_invocation: str,
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
    source_digest = _text(expected["source_digest"], field="source_digest")
    source_ref = _text(expected["source_ref"], field="source_ref")
    if repository != STAGE2B1_REPOSITORY:
        raise ExternalIssuerVerificationError("repository_not_stage2b1_policy")
    if signer_workflow != STAGE2B1_SIGNER_WORKFLOW:
        raise ExternalIssuerVerificationError("signer_workflow_not_stage2b1_policy")
    if predicate_type != STAGE2B1_PREDICATE_TYPE:
        raise ExternalIssuerVerificationError("predicate_type_not_stage2b1_policy")
    if source_ref != STAGE2B1_SOURCE_REF:
        raise ExternalIssuerVerificationError("source_ref_not_stage2b1_policy")
    if not signer_workflow.startswith(repository + "/"):
        raise ExternalIssuerVerificationError("signer_workflow_must_include_repository")
    if len(source_digest) != 40 or any(char not in "0123456789abcdef" for char in source_digest):
        raise ExternalIssuerVerificationError("source_digest_invalid")
    _validate_runner_invocation(expected_runner_invocation)
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
        "--source-digest",
        source_digest,
        "--source-ref",
        source_ref,
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
        expected_runner_invocation=expected_runner_invocation,
    )
    return ExternalIssuerProof(
        schema=EXTERNAL_ISSUER_SCHEMA,
        repository=proof_body["repository"],
        signer_workflow=proof_body["signer_workflow"],
        subject_digest=proof_body["subject_digest"],
        predicate_type=proof_body["predicate_type"],
        source_digest=proof_body["source_digest"],
        source_ref=proof_body["source_ref"],
        verification_digest=verification_digest,
        verified_timestamp_count=timestamp_count,
        _proof_body_json=_canonical(proof_body),
        _verifier_token=_VERIFIER_TOKEN,
    )


def _run_github_json(endpoint: str, *, timeout_seconds: int) -> object:
    if not endpoint.startswith("repos/" + STAGE2B1_REPOSITORY + "/"):
        raise ExternalIssuerVerificationError("github_endpoint_not_stage2b1_policy")
    try:
        completed = subprocess.run(
            ["gh", "api", endpoint],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalIssuerVerificationError("github_source_metadata_unavailable") from exc
    if completed.returncode != 0:
        raise ExternalIssuerVerificationError("github_source_metadata_verification_failed")
    try:
        return json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExternalIssuerVerificationError("github_source_metadata_invalid") from exc


def verify_p4_8_source_artifact(
    *,
    source_run_id: int,
    source_run_attempt: int,
    artifact_id: str,
    expected_head_sha: str,
    expected_artifact_digest: str,
    timeout_seconds: int = 60,
) -> P48SourceArtifactProof:
    """Verify one exact completed P4.8 run and its owned artifact metadata.

    The identifiers are selectors only.  Every accepted value is read back
    from the fixed GitHub API endpoints and compared with the producer bundle.
    This function never searches runs or artifacts and never accepts a caller
    supplied metadata object as proof.
    """

    if (
        isinstance(source_run_id, bool)
        or not isinstance(source_run_id, int)
        or source_run_id < 1
        or isinstance(source_run_attempt, bool)
        or not isinstance(source_run_attempt, int)
        or source_run_attempt < 1
    ):
        raise ExternalIssuerVerificationError("source_run_selector_invalid")
    artifact_id = _text(artifact_id, field="artifact_id")
    if not artifact_id.isdigit() or artifact_id.startswith("0"):
        raise ExternalIssuerVerificationError("artifact_id_invalid")
    if _SHA1.fullmatch(expected_head_sha) is None:
        raise ExternalIssuerVerificationError("source_head_sha_invalid")
    if _SHA256.fullmatch(expected_artifact_digest) is None:
        raise ExternalIssuerVerificationError("artifact_digest_invalid")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ExternalIssuerVerificationError("metadata_timeout_invalid")

    run = _run_github_json(
        f"repos/{STAGE2B1_REPOSITORY}/actions/runs/{source_run_id}/attempts/{source_run_attempt}",
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(run, Mapping):
        raise ExternalIssuerVerificationError("source_run_response_invalid")
    repository = run.get("repository")
    head_repository = run.get("head_repository")
    if (
        run.get("id") != source_run_id
        or run.get("run_attempt") != source_run_attempt
        or run.get("name") != "p4-8-evidence-producer"
        or run.get("path") != ".github/workflows/p4-8-evidence-producer.yml"
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != "main"
        or run.get("head_sha") != expected_head_sha
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not isinstance(repository, Mapping)
        or repository.get("full_name") != STAGE2B1_REPOSITORY
        or not isinstance(head_repository, Mapping)
        or head_repository.get("full_name") != STAGE2B1_REPOSITORY
    ):
        raise ExternalIssuerVerificationError("source_run_identity_mismatch")

    artifact = _run_github_json(
        f"repos/{STAGE2B1_REPOSITORY}/actions/artifacts/{artifact_id}",
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(artifact, Mapping):
        raise ExternalIssuerVerificationError("artifact_metadata_invalid")
    workflow_run = artifact.get("workflow_run")
    expected_name = f"p4-8-evidence-payload-{source_run_id}-{source_run_attempt}"
    if (
        str(artifact.get("id")) != artifact_id
        or artifact.get("name") != expected_name
        or artifact.get("digest") != expected_artifact_digest
        or artifact.get("expired") is not False
        or not isinstance(workflow_run, Mapping)
        or workflow_run.get("id") != source_run_id
        or (
            workflow_run.get("run_attempt") is not None
            and workflow_run.get("run_attempt") != source_run_attempt
        )
        or workflow_run.get("head_branch") != "main"
        or workflow_run.get("head_sha") != expected_head_sha
    ):
        raise ExternalIssuerVerificationError("artifact_identity_mismatch")

    body = {
        "source_run_id": source_run_id,
        "source_run_attempt": source_run_attempt,
        "source_head_sha": expected_head_sha,
        "artifact_id": artifact_id,
        "artifact_name": expected_name,
        "artifact_digest": expected_artifact_digest,
    }
    return P48SourceArtifactProof(
        **body,
        metadata_digest=_digest(body),
        _verifier_token=_VERIFIER_TOKEN,
    )


def verify_p4_8_payload_attestation(
    artifact_archive_path: str | Path,
    *,
    source_run_id: int,
    source_run_attempt: int,
    source_digest: str,
    subject_digest: str,
    timeout_seconds: int = 60,
) -> ExternalIssuerProof:
    """Verify the platform attestation for the exact downloaded archive.

    GitHub's artifact digest identifies the uploaded archive, not an extracted
    member.  Hashing the supplied bytes before invoking ``gh attestation``
    prevents an extracted payload or a different archive from being accepted
    under the producer's attestation.
    """

    path = Path(artifact_archive_path)
    if path.is_symlink() or not path.is_file():
        raise ExternalIssuerVerificationError("artifact_archive_missing_or_unsafe")
    if _SHA1.fullmatch(source_digest) is None:
        raise ExternalIssuerVerificationError("source_digest_invalid")
    if (
        isinstance(source_run_id, bool)
        or not isinstance(source_run_id, int)
        or source_run_id < 1
        or isinstance(source_run_attempt, bool)
        or not isinstance(source_run_attempt, int)
        or source_run_attempt < 1
    ):
        raise ExternalIssuerVerificationError("source_run_selector_invalid")
    expected_runner_invocation = _runner_invocation(source_run_id, source_run_attempt)
    if _SHA256.fullmatch(subject_digest) is None:
        raise ExternalIssuerVerificationError("subject_digest_invalid")
    try:
        actual_digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ExternalIssuerVerificationError("artifact_archive_unreadable") from exc
    if actual_digest != subject_digest:
        raise ExternalIssuerVerificationError("artifact_archive_digest_mismatch")
    return verify_github_artifact_attestation(
        path,
        expected={
            "repository": STAGE2B1_REPOSITORY,
            "signer_workflow": STAGE2B1_SIGNER_WORKFLOW,
            "subject_digest": subject_digest,
            "predicate_type": STAGE2B1_PREDICATE_TYPE,
            "source_digest": source_digest,
            "source_ref": STAGE2B1_SOURCE_REF,
        },
        expected_runner_invocation=expected_runner_invocation,
        timeout_seconds=timeout_seconds,
    )


def validate_external_issuer_proof(value: object) -> ExternalIssuerProof:
    """Validate the sealed proof without re-running the external verifier."""

    if not isinstance(value, ExternalIssuerProof):
        raise ExternalIssuerVerificationError("external_proof_type_invalid")
    # Re-run the invariant checks so dataclasses.replace() cannot alter a
    # public field while retaining the private verifier token.
    value.__post_init__()
    return value


def validate_p4_8_source_artifact_proof(value: object) -> P48SourceArtifactProof:
    """Validate a source proof before using it as a re-verification selector."""

    if not isinstance(value, P48SourceArtifactProof):
        raise ExternalIssuerVerificationError("source_artifact_proof_type_invalid")
    value.__post_init__()
    return value


def reverify_external_issuer_proof(
    proof: object,
    artifact_path: str | Path,
    *,
    source_artifact_proof: object,
    timeout_seconds: int = 60,
) -> ExternalIssuerProof:
    """Re-verify one exact server-owned run, attempt and artifact.

    The source artifact proof is only a selector.  Its run and artifact
    metadata are read again from fixed GitHub API endpoints before the
    attestation is verified.  No caller-supplied policy mapping is accepted.
    """

    validate_external_issuer_proof(proof)
    selected = validate_p4_8_source_artifact_proof(source_artifact_proof)
    fresh_source = verify_p4_8_source_artifact(
        source_run_id=selected.source_run_id,
        source_run_attempt=selected.source_run_attempt,
        artifact_id=selected.artifact_id,
        expected_head_sha=selected.source_head_sha,
        expected_artifact_digest=selected.artifact_digest,
        timeout_seconds=timeout_seconds,
    )
    if fresh_source != selected:
        raise ExternalIssuerVerificationError(
            "source_artifact_proof_does_not_match_fixed_metadata"
        )
    fresh = verify_p4_8_payload_attestation(
        artifact_path,
        source_run_id=selected.source_run_id,
        source_run_attempt=selected.source_run_attempt,
        source_digest=selected.source_head_sha,
        subject_digest=selected.artifact_digest,
        timeout_seconds=timeout_seconds,
    )
    if proof.__dict__ != fresh.__dict__:
        raise ExternalIssuerVerificationError(
            "external_proof_does_not_match_fixed_verifier_output"
        )
    return fresh


__all__ = [
    "EXTERNAL_ISSUER_SCHEMA",
    "STAGE2B1_PREDICATE_TYPE",
    "STAGE2B1_REPOSITORY",
    "STAGE2B1_SIGNER_WORKFLOW",
    "STAGE2B1_SOURCE_REF",
    "ExternalIssuerProof",
    "P48SourceArtifactProof",
    "ExternalIssuerVerificationError",
    "validate_external_issuer_proof",
    "validate_p4_8_source_artifact_proof",
    "reverify_external_issuer_proof",
    "verify_p4_8_payload_attestation",
    "verify_p4_8_source_artifact",
    "verify_github_artifact_attestation",
]
