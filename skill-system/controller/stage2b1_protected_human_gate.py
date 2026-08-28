"""Verify the Stage2B1 protected-environment approval from GitHub itself.

The verifier is intentionally Stage2B1-specific. It reads one exact workflow
attempt, the protected environment, pending deployments and review history
through ``gh api``. Caller-supplied human-decision JSON is not an input to this
trust boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
import os
import subprocess
from typing import Any, Mapping

from durable_human_gate import (  # type: ignore
    DurableHumanGateError,
    validate_gate_contract,
)


SCHEMA = "stage2b1-protected-approval-proof@3"
WORKFLOW_ID = "governed-stage2b1-acceptance"
WORKFLOW_PATH = ".github/workflows/governed-stage2b1-acceptance.yml"
REPOSITORY = "toctionyan/fristTest"
STEP_ID = "stage2b1-acceptance"
ENVIRONMENT = "stage2b1-acceptance"
GATE_ID = "gate-stage2b1-acceptance"
GATE_QUESTION = "Approve the verified Stage2B1 environment deployment?"
TASK_ID = "stage2b1-task"
REF = "refs/heads/main"
ACCEPT_OUTCOME = "ACCEPT_STAGE2B1"
WAITING_OUTCOME = "WAITING_FOR_PROTECTED_APPROVAL"
COMMON_BINDING_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
)
APPROVAL_EVIDENCE_FIELDS = frozenset(
    {"receipt_id", "artifact_id", "artifact_digest", "run_id", "run_attempt"}
)
APPROVAL_BINDING_FIELDS = COMMON_BINDING_FIELDS + ("evidence_bindings",)
_HEX40 = frozenset("0123456789abcdef")
_PROOF_ISSUER = object()


class Stage2B1ProtectedHumanGateError(DurableHumanGateError):
    """Raised when GitHub cannot prove one exact protected approval."""


def _fail(message: str) -> None:
    raise Stage2B1ProtectedHumanGateError(message)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} is missing")
    return value.strip()


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        _fail(f"{field} is invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        _fail(f"{field} is invalid")
    if parsed < 1 or str(parsed) != str(value).strip():
        _fail(f"{field} is invalid")
    return parsed


def _object(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{field} is missing or not an object")
    return value


def _array(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{field} is missing or not an array")
    return value


def normalize_approval_evidence_bindings(
    value: object,
) -> tuple[Mapping[str, Any], ...]:
    """Normalize the exact evidence scope covered by a protected approval."""

    rows = _array(value, field="approval evidence bindings")
    if not rows:
        _fail("Stage2B1 approval evidence bindings are empty")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        row = _object(raw, field=f"approval evidence binding {index}")
        _closed(row, set(APPROVAL_EVIDENCE_FIELDS), field="approval evidence binding")
        receipt_id = _text(row.get("receipt_id"), field="receipt_id")
        if receipt_id in seen:
            _fail(f"duplicate approval evidence receipt: {receipt_id}")
        seen.add(receipt_id)
        artifact_id = _text(row.get("artifact_id"), field="artifact_id")
        artifact_digest = _text(row.get("artifact_digest"), field="artifact_digest")
        if (
            not artifact_digest.startswith("sha256:")
            or len(artifact_digest) != len("sha256:") + 64
            or any(char not in _HEX40 for char in artifact_digest.removeprefix("sha256:"))
        ):
            _fail("artifact_digest is invalid")
        normalized.append(
            {
                "receipt_id": receipt_id,
                "artifact_id": artifact_id,
                "artifact_digest": artifact_digest,
                "run_id": _positive_int(row.get("run_id"), field="run_id"),
                "run_attempt": _positive_int(row.get("run_attempt"), field="run_attempt"),
            }
        )
    return tuple(sorted(normalized, key=lambda row: row["receipt_id"].encode("utf-8")))


def _closed(value: Mapping[str, Any], expected: set[str], *, field: str) -> None:
    missing = sorted(expected - set(value))
    unexpected = sorted(set(value) - expected)
    if missing or unexpected:
        _fail(f"{field} fields are not closed: missing={missing} unexpected={unexpected}")


def _sha(value: object, *, field: str, length: int) -> str:
    text = _text(value, field=field).lower()
    if len(text) != length or any(char not in _HEX40 for char in text):
        _fail(f"{field} is invalid")
    return text


def _timestamp(value: object, *, field: str) -> tuple[str, dt.datetime]:
    raw = _text(value, field=field)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail(f"{field} is invalid")
        raise AssertionError from exc
    if parsed.tzinfo is None:
        _fail(f"{field} has no timezone")
    return raw, parsed.astimezone(dt.timezone.utc)


def _gate() -> dict[str, Any]:
    """Return the fixed gate contract; callers cannot supply its identity."""

    gate: dict[str, Any] = {
        "schema": "durable-human-gate@1",
        "gate_id": GATE_ID,
        "task_id": TASK_ID,
        "workflow_id": WORKFLOW_ID,
        "step_id": STEP_ID,
        "question": GATE_QUESTION,
        "waiting_outcome": WAITING_OUTCOME,
        "options": [ACCEPT_OUTCOME, "REJECT_STAGE2B1"],
        "routes": {
            WAITING_OUTCOME: "HUMAN_GATE",
            ACCEPT_OUTCOME: "STAGE_ACCEPTANCE",
            "REJECT_STAGE2B1": "STAGE_REJECTION",
        },
        "authority_effect": False,
    }
    gate["gate_sha256"] = _digest(gate)
    try:
        gate = validate_gate_contract(gate)
    except (TypeError, ValueError, DurableHumanGateError) as exc:
        _fail("Stage2B1 Human Gate contract is invalid")
        raise AssertionError from exc
    if (
        gate["gate_id"] != GATE_ID
        or gate["question"] != GATE_QUESTION
        or gate["workflow_id"] != WORKFLOW_ID
        or gate["step_id"] != STEP_ID
        or gate["waiting_outcome"] != WAITING_OUTCOME
        or gate["options"] != [ACCEPT_OUTCOME, "REJECT_STAGE2B1"]
        or gate["routes"]
        != {
            WAITING_OUTCOME: "HUMAN_GATE",
            ACCEPT_OUTCOME: "STAGE_ACCEPTANCE",
            "REJECT_STAGE2B1": "STAGE_REJECTION",
        }
    ):
        _fail("Human Gate is not the fixed Stage2B1 gate")
    return gate


def _context(source: Mapping[str, str]) -> dict[str, Any]:
    if source.get("GITHUB_ACTIONS") != "true":
        _fail("Stage2B1 protected approval requires GitHub Actions")
    repository = _text(source.get("GITHUB_REPOSITORY"), field="GITHUB_REPOSITORY")
    if repository != REPOSITORY:
        _fail("GitHub repository is not the fixed Stage2B1 repository")
    ref = _text(source.get("GITHUB_REF"), field="GITHUB_REF")
    if ref != REF:
        _fail("GitHub ref is not protected main")
    if source.get("GITHUB_REF_PROTECTED") != "true":
        _fail("GitHub ref is not protected")
    run_id = _positive_int(source.get("GITHUB_RUN_ID"), field="run_id")
    run_attempt = _positive_int(source.get("GITHUB_RUN_ATTEMPT"), field="run_attempt")
    head_sha = _sha(source.get("GITHUB_SHA"), field="head_sha", length=40)
    workflow_ref = _text(source.get("GITHUB_WORKFLOW_REF"), field="GITHUB_WORKFLOW_REF")
    expected_workflow_ref = f"{REPOSITORY}/{WORKFLOW_PATH}@{REF}"
    if workflow_ref != expected_workflow_ref:
        _fail("GitHub workflow identity is not the fixed Stage2B1 workflow")
    return {
        "repository": repository,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "ref": ref,
        "head_sha": head_sha,
        "workflow_ref": workflow_ref,
    }


def _github_api(endpoint: str) -> object:
    env = os.environ.copy()
    try:
        completed = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            check=False,
            text=True,
            env=env,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(f"GitHub API read failed for {endpoint}: {exc}")
    if completed.returncode:
        _fail(
            f"GitHub API read failed for {endpoint}: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"GitHub API returned unreadable JSON for {endpoint}")
        raise AssertionError from exc


def _required_reviewer_ids(environment: Mapping[str, Any]) -> set[tuple[str, int]]:
    rules = _array(environment.get("protection_rules"), field="protection_rules")
    reviewer_rules = [
        rule for rule in rules
        if isinstance(rule, Mapping) and rule.get("type") == "required_reviewers"
    ]
    if len(reviewer_rules) != 1:
        _fail("Stage2B1 environment has unknown required-reviewer protection")
    rule = reviewer_rules[0]
    if rule.get("prevent_self_review") is not True:
        _fail("Stage2B1 environment does not prevent self-review")
    reviewers = _array(rule.get("reviewers"), field="required reviewers")
    identities: set[tuple[str, int]] = set()
    for item in reviewers:
        entry = _object(item, field="required reviewer")
        if entry.get("type") != "User":
            _fail("Stage2B1 required reviewer is not an explicit user")
        user = _object(entry.get("reviewer"), field="required reviewer user")
        identities.add(
            (
                _text(user.get("login"), field="required reviewer login"),
                _positive_int(user.get("id"), field="required reviewer id"),
            )
        )
    if not identities:
        _fail("Stage2B1 environment has no required reviewer")
    return identities


def _approval_environment(row: Mapping[str, Any], environment_id: int) -> list[Mapping[str, Any]]:
    environments = _array(row.get("environments"), field="approval environments")
    return [
        item for item in environments
        if isinstance(item, Mapping)
        and item.get("id") == environment_id
        and item.get("name") == ENVIRONMENT
    ]


def _approved_deployment_release(
    *,
    read: Any,
    repository: str,
    head_sha: str,
    run_started_at: dt.datetime,
) -> str:
    """Return the unique exact deployment release marker for this run.

    GitHub's workflow-approval response embeds the environment record, whose
    ``updated_at`` is the environment configuration timestamp rather than the
    approval timestamp.  The exact deployment's transition to ``in_progress``
    is the platform-recorded release marker after protected approval.
    """

    deployments = _array(
        read(f"repos/{repository}/deployments?environment={ENVIRONMENT}&per_page=100"),
        field="protected environment deployments",
    )
    candidates: list[tuple[str, dt.datetime]] = []
    for raw_deployment in deployments:
        deployment = _object(raw_deployment, field="protected environment deployment")
        if (
            deployment.get("environment") != ENVIRONMENT
            or str(deployment.get("sha", "")).lower() != head_sha
            or deployment.get("ref") != "main"
        ):
            continue
        deployment_id = _positive_int(
            deployment.get("id"), field="protected environment deployment id"
        )
        statuses = _array(
            read(
                f"repos/{repository}/deployments/{deployment_id}/statuses?per_page=100"
            ),
            field="protected environment deployment statuses",
        )
        for raw_status in statuses:
            status = _object(
                raw_status, field="protected environment deployment status"
            )
            if status.get("state") != "in_progress":
                continue
            marker, marker_dt = _timestamp(
                status.get("created_at"),
                field="protected deployment release timestamp",
            )
            if marker_dt >= run_started_at:
                candidates.append((marker, marker_dt))

    if len(candidates) != 1:
        _fail("exact protected deployment release is missing or ambiguous")
    return candidates[0][0]


@dataclass(frozen=True, init=False)
class Stage2B1ProtectedApprovalProof:
    """Sealed proof issued only by ``verify_stage2b1_protected_approval``."""

    schema: str
    stage_id: str
    accepted_state_id: str
    product_source_ref: str
    protected_snapshot_digest: str
    control_plane_ref: str
    execution_repo_ref: str
    evidence_bindings: tuple[Mapping[str, Any], ...]
    gate_id: str
    gate_sha256: str
    task_id: str
    repository: str
    workflow_id: str
    workflow_path: str
    workflow_ref: str
    ref: str
    head_sha: str
    run_id: int
    run_attempt: int
    environment: str
    environment_id: int
    reviewer_login: str
    reviewer_id: int
    run_actor_login: str
    run_actor_id: int
    approved_at: str
    environment_policy_sha256: str
    review_history: tuple[Mapping[str, Any], ...]
    status: str
    approval_sha256: str
    _issuer: object = field(default=None, init=False, repr=False, compare=False)

    def __init__(self, *, _issuer: object, **values: Any) -> None:
        if _issuer is not _PROOF_ISSUER:
            raise TypeError("Stage2B1 approval proofs are issued by the fixed verifier")
        expected = {name for name in self.__dataclass_fields__ if name != "_issuer"}
        if set(values) != expected:
            raise TypeError("Stage2B1 approval proof fields are closed")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _issuer)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema": SCHEMA,
            **{
                name: getattr(self, name)
                for name in self.__dataclass_fields__
                if name not in {"_issuer", "approval_sha256"}
            },
        }
        payload["review_history"] = [dict(item) for item in self.review_history]
        payload["evidence_bindings"] = [dict(item) for item in self.evidence_bindings]
        payload["approval_sha256"] = _digest(payload)
        if payload["approval_sha256"] != self.approval_sha256:
            _fail("Stage2B1 approval proof fingerprint drifted")
        return payload


def _verify_stage2b1_protected_approval(
    *,
    binding: Mapping[str, Any],
) -> Stage2B1ProtectedApprovalProof:
    """Read one exact approved environment deployment and issue sealed proof."""

    validated_gate = _gate()
    source = dict(os.environ)
    context = _context(source)
    if not isinstance(binding, Mapping):
        _fail("Stage2B1 common binding is missing")
    _closed(dict(binding), set(APPROVAL_BINDING_FIELDS), field="Stage2B1 approval binding")
    common_binding = {
        field: _text(binding.get(field), field=field) for field in COMMON_BINDING_FIELDS
    }
    if common_binding["stage_id"] != "stage2b1":
        _fail("Stage2B1 common binding stage mismatch")
    evidence_bindings = normalize_approval_evidence_bindings(binding.get("evidence_bindings"))

    read = _github_api
    repository = context["repository"]
    run_id = context["run_id"]
    attempt = context["run_attempt"]
    run = _object(
        read(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}"),
        field="exact workflow run attempt",
    )
    run_repository = _object(run.get("repository"), field="workflow run repository")
    run_path = _text(run.get("path"), field="workflow path")
    if run_path.endswith("@main"):
        run_path = run_path[:-5]
    if (
        _positive_int(run.get("id"), field="run_id") != run_id
        or _positive_int(run.get("run_attempt"), field="run_attempt") != attempt
        or run_repository.get("full_name") != repository
        or run.get("name") != WORKFLOW_ID
        or run_path != WORKFLOW_PATH
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != "main"
        or str(run.get("head_sha", "")).lower() != context["head_sha"]
    ):
        _fail("exact workflow run attempt identity mismatch")
    run_actor = _object(run.get("actor"), field="workflow run actor")
    run_actor_login = _text(run_actor.get("login"), field="workflow run actor login")
    run_actor_id = _positive_int(run_actor.get("id"), field="workflow run actor id")
    _, run_started_at = _timestamp(run.get("run_started_at"), field="run_started_at")

    environment = _object(
        read(f"repos/{repository}/environments/{ENVIRONMENT}"),
        field="protected environment",
    )
    environment_id = _positive_int(environment.get("id"), field="environment_id")
    if environment.get("name") != ENVIRONMENT:
        _fail("protected environment name mismatch")
    required_reviewers = _required_reviewer_ids(environment)
    policy_sha256 = _digest(
        {
            "id": environment_id,
            "name": ENVIRONMENT,
            "protection_rules": _array(environment.get("protection_rules"), field="protection_rules"),
        }
    )

    pending = _array(
        read(f"repos/{repository}/actions/runs/{run_id}/pending_deployments"),
        field="pending deployments after approval",
    )
    target_pending = [
        item for item in pending
        if isinstance(item, Mapping)
        and _object(item.get("environment"), field="pending deployment environment").get("id") == environment_id
    ]
    if target_pending:
        _fail("protected deployment approval is missing or delayed")

    raw_history = _array(
        read(f"repos/{repository}/actions/runs/{run_id}/approvals"),
        field="workflow approval history",
    )
    target_history: list[Mapping[str, Any]] = []
    for raw_row in raw_history:
        row = _object(raw_row, field="workflow approval history entry")
        matches = _approval_environment(row, environment_id)
        if len(matches) > 1:
            _fail("protected environment approval history is ambiguous")
        if matches:
            target_history.append(row)
    if len(target_history) != 1:
        _fail("protected environment approval history is missing or duplicate")
    approval_row = target_history[0]
    if approval_row.get("state") != "approved":
        _fail("protected environment approval is missing or not approved")
    reviewer = _object(approval_row.get("user"), field="approval reviewer")
    reviewer_login = _text(reviewer.get("login"), field="approval reviewer login")
    reviewer_id = _positive_int(reviewer.get("id"), field="approval reviewer id")
    if (reviewer_login, reviewer_id) not in required_reviewers:
        _fail("approval reviewer is not a configured required reviewer")
    if reviewer_login == run_actor_login or reviewer_id == run_actor_id:
        _fail("self-review is not an acceptable protected approval")
    approved_at = _approved_deployment_release(
        read=read,
        repository=repository,
        head_sha=context["head_sha"],
        run_started_at=run_started_at,
    )

    normalized_history: tuple[Mapping[str, Any], ...] = (
        {
            "state": "approved",
            "reviewer_login": reviewer_login,
            "reviewer_id": reviewer_id,
            "environment_id": environment_id,
            "approved_at": approved_at,
            "source_sha256": _digest(approval_row),
        },
    )
    proof_payload = {
        "schema": SCHEMA,
        **common_binding,
        "evidence_bindings": list(evidence_bindings),
        "gate_id": validated_gate["gate_id"],
        "gate_sha256": validated_gate["gate_sha256"],
        "task_id": validated_gate["task_id"],
        "repository": repository,
        "workflow_id": WORKFLOW_ID,
        "workflow_path": WORKFLOW_PATH,
        "workflow_ref": context["workflow_ref"],
        "ref": context["ref"],
        "head_sha": context["head_sha"],
        "run_id": run_id,
        "run_attempt": attempt,
        "environment": ENVIRONMENT,
        "environment_id": environment_id,
        "reviewer_login": reviewer_login,
        "reviewer_id": reviewer_id,
        "run_actor_login": run_actor_login,
        "run_actor_id": run_actor_id,
        "approved_at": approved_at,
        "environment_policy_sha256": policy_sha256,
        "review_history": list(normalized_history),
        "status": "approved",
    }
    return Stage2B1ProtectedApprovalProof(
        _issuer=_PROOF_ISSUER,
        **{**proof_payload, "approval_sha256": _digest(proof_payload)},
    )


def verify_stage2b1_protected_approval(
    *,
    binding: Mapping[str, Any],
) -> Stage2B1ProtectedApprovalProof:
    """Verify approval using the fixed gate and GitHub API reader."""

    return _verify_stage2b1_protected_approval(
        binding=binding,
    )


def reverify_stage2b1_protected_approval(
    proof: object,
    *,
    binding: Mapping[str, Any],
) -> Stage2B1ProtectedApprovalProof:
    """Re-read GitHub and compare a supplied proof byte-for-byte."""

    if not isinstance(proof, Stage2B1ProtectedApprovalProof):
        _fail("Stage2B1 approval proof type is invalid")
    supplied = proof.as_dict()
    fresh = verify_stage2b1_protected_approval(binding=binding)
    if supplied != fresh.as_dict():
        _fail("Stage2B1 approval proof does not match fixed verifier output")
    return fresh


__all__ = [
    "ACCEPT_OUTCOME",
    "APPROVAL_BINDING_FIELDS",
    "APPROVAL_EVIDENCE_FIELDS",
    "COMMON_BINDING_FIELDS",
    "ENVIRONMENT",
    "GATE_ID",
    "GATE_QUESTION",
    "TASK_ID",
    "REF",
    "SCHEMA",
    "Stage2B1ProtectedApprovalProof",
    "Stage2B1ProtectedHumanGateError",
    "normalize_approval_evidence_bindings",
    "verify_stage2b1_protected_approval",
    "reverify_stage2b1_protected_approval",
]
