"""Fixed verifier for GitHub-sourced Stage2B1 artifact provenance.

This module validates an explicit observation collected by a trusted workflow.
It does not discover runs, select the latest artifact, or mutate governance
state.  The returned value is an immutable runtime object; callers cannot
inject a verifier callback into the acceptance reducer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Mapping


PROVENANCE_OBSERVATION_SCHEMA = "stage2b1-github-provenance@1"
VERIFIED_PROVENANCE_SCHEMA = "stage2b1-verified-provenance@1"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "repository",
        "workflow_path",
        "workflow_id",
        "event",
        "ref",
        "head_sha",
        "run_id",
        "run_attempt",
        "artifact",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {
        "id",
        "name",
        "digest",
        "archive_digest",
        "content_digest",
        "source_run_id",
        "source_run_attempt",
    }
)
_VERIFIER_TOKEN = object()


class Stage2B1ProvenanceError(ValueError):
    """Raised when explicit provenance evidence is absent or inconsistent."""


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
        raise Stage2B1ProvenanceError("provenance_not_canonicalizable") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _closed(value: object, expected: frozenset[str], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Stage2B1ProvenanceError(f"{field}_not_object")
    actual = set(value)
    if actual != set(expected):
        missing = sorted(expected - actual)
        unknown = sorted(actual - set(expected))
        raise Stage2B1ProvenanceError(
            f"{field}_fields_invalid:missing={missing}:unknown={unknown}"
        )
    return dict(value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Stage2B1ProvenanceError(f"{field}_invalid")
    return value.strip()


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Stage2B1ProvenanceError(f"{field}_invalid")
    return value


def _sha256(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if _SHA256.fullmatch(text) is None:
        raise Stage2B1ProvenanceError(f"{field}_invalid")
    return text


@dataclass(frozen=True)
class VerifiedArtifactProvenance:
    """An artifact provenance result created only by the fixed verifier."""

    schema: str
    receipt_id: str
    artifact_id: str
    artifact_name: str
    artifact_digest: str
    content_digest: str
    repository: str
    workflow_path: str
    event: str
    ref: str
    head_sha: str
    run_id: int
    run_attempt: int
    proof_digest: str
    _verifier_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._verifier_token is not _VERIFIER_TOKEN:
            raise Stage2B1ProvenanceError("verified_provenance_constructor_is_private")

    @property
    def proof_ref(self) -> str:
        return "provenance:" + self.proof_digest


def verify_artifact_provenance(
    observation: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
) -> VerifiedArtifactProvenance:
    """Verify one explicit GitHub artifact observation against its contract."""

    payload = _closed(observation, _OBSERVATION_FIELDS, field="provenance")
    if payload["schema"] != PROVENANCE_OBSERVATION_SCHEMA:
        raise Stage2B1ProvenanceError("provenance_schema_invalid")

    repository = _text(payload["repository"], field="repository")
    workflow_path = _text(payload["workflow_path"], field="workflow_path")
    workflow_id = _positive(payload["workflow_id"], field="workflow_id")
    event = _text(payload["event"], field="event")
    ref = _text(payload["ref"], field="ref")
    head_sha = _text(payload["head_sha"], field="head_sha")
    if _SHA1.fullmatch(head_sha) is None:
        raise Stage2B1ProvenanceError("head_sha_invalid")
    run_id = _positive(payload["run_id"], field="run_id")
    run_attempt = _positive(payload["run_attempt"], field="run_attempt")
    artifact = _closed(payload["artifact"], _ARTIFACT_FIELDS, field="artifact")
    artifact_id = _text(artifact["id"], field="artifact_id")
    artifact_name = _text(artifact["name"], field="artifact_name")
    artifact_digest = _sha256(artifact["digest"], field="artifact_digest")
    archive_digest = _sha256(artifact["archive_digest"], field="archive_digest")
    content_digest = _sha256(artifact["content_digest"], field="content_digest")
    source_run_id = _positive(artifact["source_run_id"], field="source_run_id")
    source_run_attempt = _positive(
        artifact["source_run_attempt"], field="source_run_attempt"
    )

    if artifact_digest != archive_digest:
        raise Stage2B1ProvenanceError("artifact_archive_digest_mismatch")
    if source_run_id != run_id or source_run_attempt != run_attempt:
        raise Stage2B1ProvenanceError("artifact_source_run_mismatch")

    expected_payload = _closed(
        expected,
        frozenset(
            {
                "receipt_id",
                "artifact_id",
                "artifact_name",
                "artifact_digest",
                "content_digest",
                "repository",
                "workflow_path",
                "workflow_id",
                "event",
                "ref",
                "head_sha",
                "run_id",
                "run_attempt",
            }
        ),
        field="expected_provenance",
    )
    receipt_id = _text(expected_payload["receipt_id"], field="receipt_id")
    expected_values = {
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_digest": artifact_digest,
        "content_digest": content_digest,
        "repository": repository,
        "workflow_path": workflow_path,
        "workflow_id": workflow_id,
        "event": event,
        "ref": ref,
        "head_sha": head_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }
    for field, actual in expected_values.items():
        if expected_payload[field] != actual:
            raise Stage2B1ProvenanceError(f"expected_provenance_mismatch:{field}")

    proof_body = {
        "schema": VERIFIED_PROVENANCE_SCHEMA,
        "receipt_id": receipt_id,
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_digest": artifact_digest,
        "content_digest": content_digest,
        "repository": repository,
        "workflow_path": workflow_path,
        "workflow_id": workflow_id,
        "event": event,
        "ref": ref,
        "head_sha": head_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }
    return VerifiedArtifactProvenance(
        schema=VERIFIED_PROVENANCE_SCHEMA,
        receipt_id=receipt_id,
        artifact_id=artifact_id,
        artifact_name=artifact_name,
        artifact_digest=artifact_digest,
        content_digest=content_digest,
        repository=repository,
        workflow_path=workflow_path,
        event=event,
        ref=ref,
        head_sha=head_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        proof_digest=_digest(proof_body),
        _verifier_token=_VERIFIER_TOKEN,
    )


__all__ = [
    "PROVENANCE_OBSERVATION_SCHEMA",
    "Stage2B1ProvenanceError",
    "VERIFIED_PROVENANCE_SCHEMA",
    "VerifiedArtifactProvenance",
    "verify_artifact_provenance",
]
