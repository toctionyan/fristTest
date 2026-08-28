"""Fixed verifier for the Stage2B1 protected human approval observation."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Mapping


PROTECTED_APPROVAL_OBSERVATION_SCHEMA = "stage2b1-protected-approval-observation@1"
VERIFIED_PROTECTED_APPROVAL_SCHEMA = "stage2b1-verified-protected-approval@1"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FIELDS = frozenset(
    {
        "schema",
        "stage_id",
        "accepted_state_id",
        "product_source_ref",
        "protected_snapshot_digest",
        "control_plane_ref",
        "execution_repo_ref",
        "repository",
        "workflow_path",
        "workflow_id",
        "run_id",
        "run_attempt",
        "environment",
        "environment_id",
        "ref",
        "head_sha",
        "approval_state",
        "review_id",
        "reviewer_id",
        "reviewer_login",
        "run_actor_login",
        "self_review_forbidden",
    }
)
_VERIFIER_TOKEN = object()


class Stage2B1ProtectedApprovalError(ValueError):
    """Raised when protected approval evidence is absent or inconsistent."""


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
        raise Stage2B1ProtectedApprovalError("approval_not_canonicalizable") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _closed(value: object, expected: frozenset[str], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Stage2B1ProtectedApprovalError(f"{field}_not_object")
    actual = set(value)
    if actual != set(expected):
        raise Stage2B1ProtectedApprovalError(
            f"{field}_fields_invalid:missing={sorted(expected - actual)}:unknown={sorted(actual - set(expected))}"
        )
    return dict(value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Stage2B1ProtectedApprovalError(f"{field}_invalid")
    return value.strip()


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Stage2B1ProtectedApprovalError(f"{field}_invalid")
    return value


@dataclass(frozen=True)
class VerifiedProtectedApproval:
    """An approval result produced by the fixed protected-environment verifier."""

    schema: str
    stage_id: str
    accepted_state_id: str
    product_source_ref: str
    protected_snapshot_digest: str
    control_plane_ref: str
    execution_repo_ref: str
    repository: str
    workflow_path: str
    workflow_id: int
    run_id: int
    run_attempt: int
    environment: str
    environment_id: int
    ref: str
    head_sha: str
    review_id: int
    reviewer_id: int
    reviewer_login: str
    approval_state: str
    run_actor_login: str
    self_review_forbidden: bool
    proof_digest: str
    _verifier_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._verifier_token is not _VERIFIER_TOKEN:
            raise Stage2B1ProtectedApprovalError("verified_approval_constructor_is_private")

    @property
    def proof_ref(self) -> str:
        return "protected-approval:" + self.proof_digest


def verify_protected_approval(
    observation: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
) -> VerifiedProtectedApproval:
    """Verify the explicit approval history observation for Stage2B1."""

    payload = _closed(observation, _FIELDS, field="protected_approval")
    if payload["schema"] != PROTECTED_APPROVAL_OBSERVATION_SCHEMA:
        raise Stage2B1ProtectedApprovalError("approval_schema_invalid")

    expected_payload = _closed(
        expected,
        frozenset(
            {
                "stage_id",
                "accepted_state_id",
                "product_source_ref",
                "protected_snapshot_digest",
                "control_plane_ref",
                "execution_repo_ref",
                "repository",
                "workflow_path",
                "workflow_id",
                "run_id",
                "run_attempt",
                "environment",
                "ref",
                "head_sha",
            }
        ),
        field="expected_approval",
    )

    text_fields = (
        "stage_id",
        "accepted_state_id",
        "product_source_ref",
        "protected_snapshot_digest",
        "control_plane_ref",
        "execution_repo_ref",
        "repository",
        "workflow_path",
        "environment",
        "ref",
        "head_sha",
        "reviewer_login",
        "run_actor_login",
    )
    values = {field: _text(payload[field], field=field) for field in text_fields}
    if _SHA1.fullmatch(values["head_sha"]) is None:
        raise Stage2B1ProtectedApprovalError("head_sha_invalid")
    if not values["protected_snapshot_digest"].startswith("sha256:"):
        raise Stage2B1ProtectedApprovalError("protected_snapshot_digest_invalid")
    workflow_id = _positive(payload["workflow_id"], field="workflow_id")
    run_id = _positive(payload["run_id"], field="run_id")
    run_attempt = _positive(payload["run_attempt"], field="run_attempt")
    environment_id = _positive(payload["environment_id"], field="environment_id")
    review_id = _positive(payload["review_id"], field="review_id")
    reviewer_id = _positive(payload["reviewer_id"], field="reviewer_id")

    if payload["approval_state"] != "approved":
        raise Stage2B1ProtectedApprovalError("approval_state_not_approved")
    if payload["self_review_forbidden"] is not True:
        raise Stage2B1ProtectedApprovalError("self_review_policy_not_enabled")
    if values["reviewer_login"] == values["run_actor_login"]:
        raise Stage2B1ProtectedApprovalError("self_review_rejected")

    for field, actual in {
        **values,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "environment": values["environment"],
        "ref": values["ref"],
        "head_sha": values["head_sha"],
    }.items():
        if field in expected_payload and expected_payload[field] != actual:
            raise Stage2B1ProtectedApprovalError(f"expected_approval_mismatch:{field}")

    proof_body = {
        "schema": VERIFIED_PROTECTED_APPROVAL_SCHEMA,
        **{field: values[field] for field in (
            "stage_id",
            "accepted_state_id",
            "product_source_ref",
            "protected_snapshot_digest",
            "control_plane_ref",
            "execution_repo_ref",
            "repository",
            "workflow_path",
            "environment",
            "ref",
            "head_sha",
        )},
        "workflow_id": workflow_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "environment_id": environment_id,
        "review_id": review_id,
        "reviewer_id": reviewer_id,
        "reviewer_login": values["reviewer_login"],
        "approval_state": payload["approval_state"],
        "run_actor_login": values["run_actor_login"],
        "self_review_forbidden": payload["self_review_forbidden"],
    }
    return VerifiedProtectedApproval(
        schema=VERIFIED_PROTECTED_APPROVAL_SCHEMA,
        stage_id=values["stage_id"],
        accepted_state_id=values["accepted_state_id"],
        product_source_ref=values["product_source_ref"],
        protected_snapshot_digest=values["protected_snapshot_digest"],
        control_plane_ref=values["control_plane_ref"],
        execution_repo_ref=values["execution_repo_ref"],
        repository=values["repository"],
        workflow_path=values["workflow_path"],
        workflow_id=workflow_id,
        run_id=run_id,
        run_attempt=run_attempt,
        environment=values["environment"],
        environment_id=environment_id,
        ref=values["ref"],
        head_sha=values["head_sha"],
        review_id=review_id,
        reviewer_id=reviewer_id,
        reviewer_login=values["reviewer_login"],
        approval_state=payload["approval_state"],
        run_actor_login=values["run_actor_login"],
        self_review_forbidden=payload["self_review_forbidden"],
        proof_digest=_digest(proof_body),
        _verifier_token=_VERIFIER_TOKEN,
    )


__all__ = [
    "PROTECTED_APPROVAL_OBSERVATION_SCHEMA",
    "Stage2B1ProtectedApprovalError",
    "VERIFIED_PROTECTED_APPROVAL_SCHEMA",
    "VerifiedProtectedApproval",
    "verify_protected_approval",
]
