from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from task_run import TaskRunStore


AUTONOMY_GRANT_SCHEMA = "engineering-autonomy-grant@1"
AUTONOMY_BINDING_SCHEMA = "engineering-autonomy-binding@1"
MAX_AUTONOMOUS_REPAIR_ROUNDS = 8
MAX_AUTONOMOUS_VALIDATION_RETRIES = 3

SAFE_AUTONOMOUS_ACTIONS = frozenset(
    {
        "analyze_failure",
        "edit_authorized_source",
        "add_authorized_counterexample_tests",
        "commit_current_branch",
        "push_current_branch",
        "dispatch_ci",
        "retry_transient_ci",
        "repair_meaningful_product_red",
        "advance_verified_milestone",
    }
)

ABSOLUTE_FORBIDDEN_ACTIONS = frozenset(
    {
        "change_user_goal",
        "weaken_acceptance",
        "weaken_protected_test",
        "change_oracle_to_make_green",
        "expand_write_scope_without_evidence",
        "merge",
        "deploy",
        "production",
    }
)

_TERMINAL_OR_HUMAN_STOP_TASK_STATUSES = {"BLOCKED", "COMPLETED", "CANCELLED"}
_RETRYABLE_FAILURE_CLASSES = {"ENVIRONMENT_FAILURE", "TRANSIENT_INFRA_FAILURE"}


class AutonomyGrantError(RuntimeError):
    """Fail-closed engineering autonomy contract error."""


@dataclass(frozen=True)
class AutonomousActionDecision:
    allowed: bool
    action: str
    reason: str
    human_required: bool
    grant_id: str | None = None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _task_binding(task: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = task.get("binding")
    if not isinstance(binding, Mapping) or not binding:
        raise AutonomyGrantError("autonomy grant requires a TaskRun immutable binding")
    return binding


def task_binding_fingerprint(task: Mapping[str, Any]) -> str:
    return _digest(dict(_task_binding(task)))


def _validate_commit_like(value: object, *, name: str) -> str:
    text = _text(value).lower()
    if not re.fullmatch(r"[0-9a-f]{7,64}", text):
        raise AutonomyGrantError(f"{name} must be a hexadecimal commit-like identifier")
    return text


def _validate_task_identity(
    task: Mapping[str, Any],
    *,
    branch: str,
    base_sha: str,
) -> None:
    task_id = _text(task.get("task_id"))
    if not task_id:
        raise AutonomyGrantError("autonomy grant requires task_id")
    binding = _task_binding(task)
    if _text(binding.get("branch")) != branch:
        raise AutonomyGrantError("autonomy grant branch does not match TaskRun binding")
    if _validate_commit_like(binding.get("base_sha"), name="TaskRun base_sha") != base_sha:
        raise AutonomyGrantError("autonomy grant base_sha does not match TaskRun binding")


def _normalized_actions(actions: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({_text(action) for action in actions if _text(action)}))
    if not normalized:
        raise AutonomyGrantError("autonomy grant requires at least one allowed action")
    forbidden = sorted(set(normalized) & ABSOLUTE_FORBIDDEN_ACTIONS)
    if forbidden:
        raise AutonomyGrantError(f"autonomy grant cannot authorize forbidden actions: {forbidden}")
    unknown = sorted(set(normalized) - SAFE_AUTONOMOUS_ACTIONS)
    if unknown:
        raise AutonomyGrantError(f"autonomy grant contains unknown actions: {unknown}")
    return normalized


def _validate_budgets(max_repair_rounds: int, max_validation_retries: int) -> tuple[int, int]:
    repair = int(max_repair_rounds)
    retries = int(max_validation_retries)
    if not 1 <= repair <= MAX_AUTONOMOUS_REPAIR_ROUNDS:
        raise AutonomyGrantError(
            f"max_repair_rounds must be within 1..{MAX_AUTONOMOUS_REPAIR_ROUNDS}"
        )
    if not 0 <= retries <= MAX_AUTONOMOUS_VALIDATION_RETRIES:
        raise AutonomyGrantError(
            f"max_validation_retries must be within 0..{MAX_AUTONOMOUS_VALIDATION_RETRIES}"
        )
    return repair, retries


def create_autonomy_grant(
    *,
    task: Mapping[str, Any],
    repository: str,
    branch: str,
    base_sha: str,
    issued_by: str,
    allowed_actions: Iterable[str],
    max_repair_rounds: int = MAX_AUTONOMOUS_REPAIR_ROUNDS,
    max_validation_retries: int = MAX_AUTONOMOUS_VALIDATION_RETRIES,
    grant_id: str | None = None,
) -> dict[str, Any]:
    """Build a bounded grant document; this function does not prove human authorization.

    A trusted M3 entrypoint must bind this document to its owner-authorization
    evidence before any autonomous action may consume it. The grant itself never
    creates product/test write authority and never carries merge/deploy/production
    authority.
    """

    repo = _text(repository)
    branch_name = _text(branch)
    actor = _text(issued_by)
    if not repo or "/" not in repo:
        raise AutonomyGrantError("repository must be owner/name")
    if not branch_name or branch_name in {"main", "master"}:
        raise AutonomyGrantError("autonomy grant requires a non-default working branch")
    if not actor:
        raise AutonomyGrantError("issued_by is required")
    base = _validate_commit_like(base_sha, name="base_sha")
    _validate_task_identity(task, branch=branch_name, base_sha=base)
    actions = _normalized_actions(allowed_actions)
    repair_budget, retry_budget = _validate_budgets(max_repair_rounds, max_validation_retries)
    task_id = _text(task.get("task_id"))
    resolved_grant_id = _text(grant_id) or f"autonomy:{task_id}:{task_binding_fingerprint(task)[:16]}"

    payload: dict[str, Any] = {
        "schema": AUTONOMY_GRANT_SCHEMA,
        "grant_id": resolved_grant_id,
        "status": "ACTIVE",
        "task_id": task_id,
        "task_binding_fingerprint": task_binding_fingerprint(task),
        "repository": repo,
        "branch": branch_name,
        "base_sha": base,
        "issued_by": actor,
        "issued_at": _now(),
        "allowed_actions": list(actions),
        "forbidden_actions": sorted(ABSOLUTE_FORBIDDEN_ACTIONS),
        "budgets": {
            "max_repair_rounds": repair_budget,
            "max_validation_retries": retry_budget,
        },
        "authority_effect": "automation_continuation_only",
        "write_authority_effect": False,
        "test_authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    payload["grant_sha256"] = _digest(payload)
    return payload


def validate_autonomy_grant(
    grant: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    repository: str,
    branch: str,
    base_sha: str,
) -> dict[str, Any]:
    payload = dict(grant)
    if payload.get("schema") != AUTONOMY_GRANT_SCHEMA:
        raise AutonomyGrantError("unsupported autonomy grant schema")
    if payload.get("status") != "ACTIVE":
        raise AutonomyGrantError("autonomy grant is not active")
    expected_digest = _text(payload.pop("grant_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise AutonomyGrantError("autonomy grant digest is missing or invalid")
    if _digest(payload) != expected_digest:
        raise AutonomyGrantError("autonomy grant digest mismatch")
    payload["grant_sha256"] = expected_digest

    repo = _text(repository)
    branch_name = _text(branch)
    base = _validate_commit_like(base_sha, name="base_sha")
    _validate_task_identity(task, branch=branch_name, base_sha=base)
    expected = {
        "task_id": _text(task.get("task_id")),
        "task_binding_fingerprint": task_binding_fingerprint(task),
        "repository": repo,
        "branch": branch_name,
        "base_sha": base,
    }
    for key, value in expected.items():
        if _text(payload.get(key)) != _text(value):
            raise AutonomyGrantError(f"autonomy grant binding mismatch: {key}")

    actions = _normalized_actions(payload.get("allowed_actions") or [])
    if list(actions) != list(payload.get("allowed_actions") or []):
        raise AutonomyGrantError("autonomy grant actions are not canonical")
    if set(payload.get("forbidden_actions") or []) != set(ABSOLUTE_FORBIDDEN_ACTIONS):
        raise AutonomyGrantError("autonomy grant forbidden-action boundary drifted")
    budgets = payload.get("budgets") if isinstance(payload.get("budgets"), Mapping) else {}
    _validate_budgets(
        int(budgets.get("max_repair_rounds") or 0),
        int(budgets.get("max_validation_retries") or 0),
    )
    if payload.get("authority_effect") != "automation_continuation_only":
        raise AutonomyGrantError("autonomy grant authority effect drifted")
    for field in (
        "write_authority_effect",
        "test_authority_effect",
        "merge_allowed",
        "deploy_allowed",
        "production_closed",
    ):
        if payload.get(field) is not False:
            raise AutonomyGrantError(f"autonomy grant cannot enable {field}")
    return payload


def bind_autonomy_grant(
    store: TaskRunStore,
    grant: Mapping[str, Any],
    *,
    repository: str,
    owner_authorization_ref: str,
) -> dict[str, Any]:
    """Bind one pre-authorized grant to one TaskRun without changing task phase/status."""

    authorization_ref = _text(owner_authorization_ref)
    if not authorization_ref:
        raise AutonomyGrantError("owner authorization evidence is required")
    binding = _task_binding(store.payload)
    validated = validate_autonomy_grant(
        grant,
        task=store.payload,
        repository=repository,
        branch=_text(binding.get("branch")),
        base_sha=_text(binding.get("base_sha")),
    )
    metadata = store.payload.get("metadata") if isinstance(store.payload.get("metadata"), dict) else {}
    existing = metadata.get("engineering_autonomy")
    if isinstance(existing, Mapping):
        same = (
            _text(existing.get("grant_id")) == _text(validated.get("grant_id"))
            and _text(existing.get("grant_sha256")) == _text(validated.get("grant_sha256"))
            and existing.get("status") == "ACTIVE"
        )
        if same:
            return dict(existing)
        raise AutonomyGrantError("TaskRun already has an autonomy grant binding")

    record = {
        "schema": AUTONOMY_BINDING_SCHEMA,
        "status": "ACTIVE",
        "grant_id": validated["grant_id"],
        "grant_sha256": validated["grant_sha256"],
        "task_binding_fingerprint": validated["task_binding_fingerprint"],
        "repository": validated["repository"],
        "owner_authorization_ref": authorization_ref,
        "bound_at": _now(),
    }
    store.set_metadata(engineering_autonomy=record)
    return record


def revoke_autonomy_grant(
    store: TaskRunStore,
    *,
    reason: str,
    evidence_ref: str,
) -> dict[str, Any]:
    metadata = store.payload.get("metadata") if isinstance(store.payload.get("metadata"), dict) else {}
    existing = metadata.get("engineering_autonomy")
    if not isinstance(existing, Mapping) or existing.get("status") != "ACTIVE":
        raise AutonomyGrantError("TaskRun has no active autonomy grant to revoke")
    if not _text(reason) or not _text(evidence_ref):
        raise AutonomyGrantError("revocation requires reason and evidence")
    record = dict(existing)
    record.update(
        {
            "status": "REVOKED",
            "revoked_at": _now(),
            "revocation_reason": _text(reason),
            "revocation_evidence_ref": _text(evidence_ref),
        }
    )
    store.set_metadata(engineering_autonomy=record)
    return record


def _deny(action: str, reason: str, *, grant_id: str | None = None, human: bool = False) -> AutonomousActionDecision:
    return AutonomousActionDecision(
        allowed=False,
        action=action,
        reason=reason,
        human_required=human,
        grant_id=grant_id,
    )


def _context_counter(facts: Mapping[str, Any], name: str) -> int:
    value = facts.get(name, 0)
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise AutonomyGrantError(f"{name} must be a non-negative integer") from exc
    if result < 0:
        raise AutonomyGrantError(f"{name} must be a non-negative integer")
    return result


def authorize_autonomous_action(
    store: TaskRunStore,
    grant: Mapping[str, Any],
    *,
    repository: str,
    action: str,
    context: Mapping[str, Any] | None = None,
) -> AutonomousActionDecision:
    """Evaluate continuation only; existing repair/test/write authorities remain mandatory."""

    requested = _text(action)
    if requested in ABSOLUTE_FORBIDDEN_ACTIONS:
        return _deny(requested, "action requires human/high-authority boundary", human=True)
    if requested not in SAFE_AUTONOMOUS_ACTIONS:
        return _deny(requested, "action is not part of the autonomous action contract", human=True)

    binding = _task_binding(store.payload)
    try:
        validated = validate_autonomy_grant(
            grant,
            task=store.payload,
            repository=repository,
            branch=_text(binding.get("branch")),
            base_sha=_text(binding.get("base_sha")),
        )
    except AutonomyGrantError as exc:
        return _deny(requested, str(exc), human=True)

    metadata = store.payload.get("metadata") if isinstance(store.payload.get("metadata"), dict) else {}
    bound = metadata.get("engineering_autonomy")
    grant_id = _text(validated.get("grant_id"))
    if not isinstance(bound, Mapping):
        return _deny(requested, "TaskRun has no bound autonomy grant", grant_id=grant_id, human=True)
    if bound.get("status") != "ACTIVE":
        return _deny(requested, "TaskRun autonomy grant is revoked or inactive", grant_id=grant_id, human=True)
    if (
        _text(bound.get("grant_id")) != grant_id
        or _text(bound.get("grant_sha256")) != _text(validated.get("grant_sha256"))
        or _text(bound.get("task_binding_fingerprint")) != task_binding_fingerprint(store.payload)
    ):
        return _deny(requested, "TaskRun autonomy grant binding mismatch", grant_id=grant_id, human=True)
    if requested not in set(validated.get("allowed_actions") or []):
        return _deny(requested, "action was not granted for this TaskRun", grant_id=grant_id, human=True)
    if _text(store.payload.get("status")) in _TERMINAL_OR_HUMAN_STOP_TASK_STATUSES:
        return _deny(
            requested,
            f"TaskRun status {_text(store.payload.get('status'))} requires explicit resume or a new task",
            grant_id=grant_id,
            human=True,
        )

    facts = dict(context or {})
    budgets = validated["budgets"]
    try:
        repair_round = _context_counter(facts, "repair_round")
        validation_retry = _context_counter(facts, "validation_retry")
    except AutonomyGrantError as exc:
        return _deny(requested, str(exc), grant_id=grant_id, human=True)
    if repair_round > int(budgets["max_repair_rounds"]):
        return _deny(requested, "autonomous repair budget exhausted", grant_id=grant_id, human=True)
    if validation_retry > int(budgets["max_validation_retries"]):
        return _deny(requested, "autonomous validation retry budget exhausted", grant_id=grant_id, human=True)

    if requested in {
        "edit_authorized_source",
        "commit_current_branch",
        "push_current_branch",
        "repair_meaningful_product_red",
    }:
        if facts.get("underlying_write_authority") is not True or facts.get("exact_write_scope") is not True:
            return _deny(
                requested,
                "AutonomyGrant cannot substitute for existing exact write authority",
                grant_id=grant_id,
                human=True,
            )
    if requested == "add_authorized_counterexample_tests":
        if facts.get("underlying_test_write_authority") is not True or facts.get("exact_test_scope") is not True:
            return _deny(
                requested,
                "AutonomyGrant cannot create test/oracle write authority",
                grant_id=grant_id,
                human=True,
            )
    if requested == "repair_meaningful_product_red":
        if repair_round < 1:
            return _deny(
                requested,
                "product repair requires an exact positive repair_round",
                grant_id=grant_id,
                human=True,
            )
        if facts.get("failure_class") != "PRODUCT_SOURCE_FAILURE":
            return _deny(
                requested,
                "product repair requires a classified meaningful product RED",
                grant_id=grant_id,
                human=False,
            )
    if requested == "retry_transient_ci":
        if facts.get("failure_class") not in _RETRYABLE_FAILURE_CLASSES or facts.get("same_candidate") is not True:
            return _deny(
                requested,
                "CI retry requires a transient/environment failure on the same candidate",
                grant_id=grant_id,
                human=False,
            )
    if requested in {"push_current_branch", "dispatch_ci"} and facts.get("head_bound") is not True:
        return _deny(requested, "action requires exact current-head binding", grant_id=grant_id, human=True)
    if requested == "advance_verified_milestone":
        if facts.get("verification_status") != "PASS" or facts.get("required_gates_terminal") is not True:
            return _deny(
                requested,
                "milestone cannot advance without terminal required-gate PASS evidence",
                grant_id=grant_id,
                human=False,
            )

    return AutonomousActionDecision(
        allowed=True,
        action=requested,
        reason="bounded autonomy grant permits continuation; underlying authorities remain unchanged",
        human_required=False,
        grant_id=grant_id,
    )
