from __future__ import annotations

"""Bounded, single-use final merge authority for autonomous engineering tasks.

This authority is intentionally separate from AutonomyGrant.  It cannot create
source/test write authority, change acceptance, expand scope, deploy, or reach
production.  It may only authorize Ready+merge after the existing G6 exact-head
contract has independently succeeded for the same governed TaskRun lineage.
"""

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from autonomy_grant import task_binding_fingerprint
from local_first_governance import scope_violations

MERGE_GRANT_SCHEMA = "engineering-merge-grant@1"
MERGE_GATE_SCHEMA = "engineering-merge-gate@1"
MERGE_REQUEST_SCHEMA = "engineering-merge-network-request@1"
MANDATORY_WORKFLOWS = ("skill-self-validation", "quality")
ALLOWED_MERGE_ACTIONS = ("mark_ready", "merge")
MERGE_METHOD = "merge"
SYSTEM_GENERATED_LANDING_PATHS = ("skill-system/registry/product-source-baseline.json",)


class EngineeringMergeGrantError(RuntimeError):
    pass


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object, *, name: str) -> str:
    result = _text(value).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        raise EngineeringMergeGrantError(f"{name} must be an exact 40-character SHA")
    return result


def _positive(value: object, *, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise EngineeringMergeGrantError(f"{name} must be an integer") from exc
    if result < 1:
        raise EngineeringMergeGrantError(f"{name} must be positive")
    return result


def _paths(values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(sorted({_text(value).replace("\\", "/") for value in values if _text(value)}))
    if not result:
        raise EngineeringMergeGrantError("merge grant requires exact non-empty TaskRun allowed_paths")
    return result


def create_merge_grant(
    *,
    task: Mapping[str, Any],
    repository: str,
    source_pr_number: int,
    issued_by: str,
    owner_authorization_ref: str,
    grant_id: str | None = None,
) -> dict[str, Any]:
    binding = task.get("binding")
    if not isinstance(binding, Mapping):
        raise EngineeringMergeGrantError("merge grant requires TaskRun immutable binding")
    task_id = _text(task.get("task_id"))
    repo = _text(repository)
    actor = _text(issued_by)
    auth_ref = _text(owner_authorization_ref)
    if not task_id or "/" not in repo or not actor or not auth_ref:
        raise EngineeringMergeGrantError("merge grant task/repository/actor/authorization is incomplete")
    origin_branch = _text(binding.get("branch"))
    if not origin_branch or origin_branch in {"main", "master"}:
        raise EngineeringMergeGrantError("merge grant requires a non-default TaskRun branch")
    initial_base_sha = _sha(binding.get("base_sha"), name="TaskRun base_sha")
    target_fingerprint = _text(binding.get("target_fingerprint"))
    if not target_fingerprint:
        raise EngineeringMergeGrantError("merge grant requires TaskRun target_fingerprint")
    allowed_paths = _paths(binding.get("allowed_paths") or [])
    source_pr = _positive(source_pr_number, name="source_pr_number")
    task_fp = task_binding_fingerprint(task)
    resolved_id = _text(grant_id) or f"merge:{task_id}:{task_fp[:16]}"
    payload: dict[str, Any] = {
        "schema": MERGE_GRANT_SCHEMA,
        "status": "ACTIVE",
        "grant_id": resolved_id,
        "task_id": task_id,
        "task_binding_fingerprint": task_fp,
        "repository": repo,
        "source_pr_number": source_pr,
        "origin_branch": origin_branch,
        "base_branch": "main",
        "initial_base_sha": initial_base_sha,
        "allowed_paths": list(allowed_paths),
        "system_generated_landing_paths": list(SYSTEM_GENERATED_LANDING_PATHS),
        "target_fingerprint": target_fingerprint,
        "required_workflows": list(MANDATORY_WORKFLOWS),
        "allowed_actions": list(ALLOWED_MERGE_ACTIONS),
        "merge_method": MERGE_METHOD,
        "single_use": True,
        "pr_lineage_policy": "same_task_g6_lineage",
        "review_policy": "preserve_repository_governance",
        "issued_by": actor,
        "owner_authorization_ref": auth_ref,
        "authority_effect": "conditional_final_merge_only",
        "write_authority_effect": False,
        "test_authority_effect": False,
        "acceptance_mutation_allowed": False,
        "scope_expansion_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    payload["grant_sha256"] = _digest(payload)
    return payload


def validate_merge_grant_document(grant: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(grant)
    if payload.get("schema") != MERGE_GRANT_SCHEMA:
        raise EngineeringMergeGrantError("unsupported engineering merge grant schema")
    expected = _text(payload.pop("grant_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or _digest(payload) != expected:
        raise EngineeringMergeGrantError("merge grant digest mismatch")
    payload["grant_sha256"] = expected
    if payload.get("status") != "ACTIVE" or payload.get("single_use") is not True:
        raise EngineeringMergeGrantError("merge grant is not active single-use authority")
    if _text(payload.get("base_branch")) != "main" or payload.get("merge_method") != MERGE_METHOD:
        raise EngineeringMergeGrantError("merge grant landing contract drifted")
    if tuple(payload.get("required_workflows") or ()) != MANDATORY_WORKFLOWS:
        raise EngineeringMergeGrantError("merge grant workflow contract drifted")
    if tuple(payload.get("allowed_actions") or ()) != ALLOWED_MERGE_ACTIONS:
        raise EngineeringMergeGrantError("merge grant action contract drifted")
    if tuple(payload.get("system_generated_landing_paths") or ()) != SYSTEM_GENERATED_LANDING_PATHS:
        raise EngineeringMergeGrantError("merge grant generated-path contract drifted")
    if payload.get("authority_effect") != "conditional_final_merge_only":
        raise EngineeringMergeGrantError("merge grant authority effect drifted")
    for field in (
        "write_authority_effect", "test_authority_effect", "acceptance_mutation_allowed",
        "scope_expansion_allowed", "deploy_allowed", "production_closed",
    ):
        if payload.get(field) is not False:
            raise EngineeringMergeGrantError(f"merge grant cannot enable {field}")
    _positive(payload.get("source_pr_number"), name="source_pr_number")
    _sha(payload.get("initial_base_sha"), name="initial_base_sha")
    _paths(payload.get("allowed_paths") or [])
    if not re.fullmatch(r"[0-9a-f]{64}", _text(payload.get("task_binding_fingerprint"))):
        raise EngineeringMergeGrantError("merge grant TaskRun fingerprint is malformed")
    return payload


def validate_merge_grant_for_task(grant: Mapping[str, Any], *, task: Mapping[str, Any]) -> dict[str, Any]:
    result = validate_merge_grant_document(grant)
    binding = task.get("binding")
    if not isinstance(binding, Mapping):
        raise EngineeringMergeGrantError("TaskRun immutable binding is missing")
    if _text(task.get("task_id")) != _text(result.get("task_id")):
        raise EngineeringMergeGrantError("merge grant TaskRun id mismatch")
    if task_binding_fingerprint(task) != _text(result.get("task_binding_fingerprint")):
        raise EngineeringMergeGrantError("merge grant TaskRun binding fingerprint mismatch")
    if _text(binding.get("target_fingerprint")) != _text(result.get("target_fingerprint")):
        raise EngineeringMergeGrantError("merge grant target fingerprint mismatch")
    if _paths(binding.get("allowed_paths") or []) != tuple(result.get("allowed_paths") or []):
        raise EngineeringMergeGrantError("merge grant allowed-path scope mismatch")
    return result


def _pr_number_from_url(value: object) -> int:
    match = re.search(r"/pull/(\d+)$", _text(value).rstrip("/"))
    return int(match.group(1)) if match else 0


def _latest_runs(rows: Iterable[Mapping[str, Any]], *, head_sha: str, pr_number: int) -> dict[tuple[str, str], Mapping[str, Any]]:
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or _text(row.get("head_sha")).lower() != head_sha:
            continue
        name, event = _text(row.get("name")), _text(row.get("event"))
        if name not in MANDATORY_WORKFLOWS or event not in {"pull_request", "push"}:
            continue
        if event == "pull_request":
            prs = row.get("pull_requests")
            if not isinstance(prs, list) or not any(isinstance(item, Mapping) and int(item.get("number") or 0) == pr_number for item in prs):
                continue
        key = (name, event)
        identity = (int(row.get("run_number") or 0), int(row.get("run_attempt") or 0), int(row.get("id") or 0))
        prior = latest.get(key)
        if prior is None or identity > (int(prior.get("run_number") or 0), int(prior.get("run_attempt") or 0), int(prior.get("id") or 0)):
            latest[key] = row
    return latest


def _active_change_requests(reviews: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    latest: dict[str, tuple[str, int, str]] = {}
    for row in reviews:
        if not isinstance(row, Mapping):
            continue
        user = row.get("user") if isinstance(row.get("user"), Mapping) else {}
        actor = _text(user.get("login"))
        if not actor:
            continue
        identity = (_text(row.get("submitted_at")), int(row.get("id") or 0), _text(row.get("state")).upper())
        if actor not in latest or identity[:2] > latest[actor][:2]:
            latest[actor] = identity
    return tuple(sorted(actor for actor, (_, _, state) in latest.items() if state == "CHANGES_REQUESTED"))


def evaluate_merge_gate(
    grant: Mapping[str, Any], *, task: Mapping[str, Any], pr: Mapping[str, Any],
    lineage_result: Mapping[str, Any], exact_head_result: Mapping[str, Any],
    exact_head_ci_state: Mapping[str, Any], workflow_runs: Iterable[Mapping[str, Any]],
    changed_paths: Iterable[str], reviews: Iterable[Mapping[str, Any]] = (),
    unresolved_review_threads: int = 0, human_gate_reasons: Iterable[str] = (),
) -> dict[str, Any]:
    """Fail closed unless the exact current PR is safe to land under this grant."""
    validated = validate_merge_grant_for_task(grant, task=task)
    blockers: list[str] = []
    number = int(pr.get("number") or 0)
    if number < 1 or _text(pr.get("state")).lower() != "open": blockers.append("pr_not_open")
    if pr.get("merged") is True or pr.get("merged_at"): blockers.append("pr_already_merged")
    if pr.get("mergeable") is not True: blockers.append("pr_not_mergeable")
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    base = pr.get("base") if isinstance(pr.get("base"), Mapping) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), Mapping) else {}
    if _text(head_repo.get("full_name")) != _text(validated["repository"]): blockers.append("head_repository_mismatch")
    if _text(base.get("ref")) != "main": blockers.append("base_branch_mismatch")
    try:
        head_sha, base_sha = _sha(head.get("sha"), name="PR head SHA"), _sha(base.get("sha"), name="PR base SHA")
    except EngineeringMergeGrantError as exc:
        blockers.append(str(exc)); head_sha = base_sha = ""

    lineage_schema = _text(lineage_result.get("schema"))
    if lineage_schema not in {"github-governed-repair-draft-publication@1", "governed-baseline-acceptance@1"}:
        blockers.append("governed_pr_lineage_missing")
    else:
        if _pr_number_from_url(lineage_result.get("draft_pr_url")) != number: blockers.append("lineage_pr_mismatch")
        if _text(lineage_result.get("repair_branch")) != _text(head.get("ref")): blockers.append("lineage_branch_mismatch")

    if exact_head_result.get("schema") != "governed-repair-exact-head@1":
        blockers.append("g6_exact_head_result_missing")
    else:
        if _text(exact_head_result.get("status")) != "READY_FOR_REVIEW" or exact_head_result.get("ready_for_review") is not True: blockers.append("g6_not_ready_for_review")
        if exact_head_result.get("governance_closed") is not True: blockers.append("g6_governance_not_closed")
        if exact_head_result.get("baseline_accepted") is not True: blockers.append("g6_baseline_not_accepted")
        if exact_head_result.get("exact_head_certified") is not True: blockers.append("g6_exact_head_not_certified")
        if exact_head_result.get("merge_allowed") is not False or exact_head_result.get("deploy_allowed") is not False: blockers.append("g6_authority_boundary_drift")
        if _pr_number_from_url(exact_head_result.get("draft_pr_url")) != number: blockers.append("g6_pr_mismatch")
        if head_sha and _text(exact_head_result.get("baseline_commit_sha")).lower() != head_sha: blockers.append("g6_head_mismatch")

    if exact_head_ci_state.get("schema") != "governed-repair-exact-head-ci-state@1" or _text(exact_head_ci_state.get("status")) != "EXACT_HEAD_CI_PASSED":
        blockers.append("exact_head_ci_not_passed")
    else:
        if head_sha and _text(exact_head_ci_state.get("head_sha")).lower() != head_sha: blockers.append("exact_head_ci_head_mismatch")
        if int(exact_head_ci_state.get("pr_number") or 0) != number: blockers.append("exact_head_ci_pr_mismatch")

    normalized_changed = tuple(sorted({_text(path) for path in changed_paths if _text(path)}))
    violations = scope_violations(normalized_changed, tuple(validated["allowed_paths"]) + SYSTEM_GENERATED_LANDING_PATHS)
    blockers.extend(f"scope_violation:{path}" for path in violations)

    if head_sha and number > 0:
        latest = _latest_runs(workflow_runs, head_sha=head_sha, pr_number=number)
        for name in MANDATORY_WORKFLOWS:
            pr_run = latest.get((name, "pull_request"))
            if pr_run is None or _text(pr_run.get("status")) != "completed" or _text(pr_run.get("conclusion")) != "success":
                blockers.append(f"required_pr_workflow_not_green:{name}")
            push_run = latest.get((name, "push"))
            if push_run is not None and (_text(push_run.get("status")) != "completed" or _text(push_run.get("conclusion")) != "success"):
                blockers.append(f"current_head_push_not_green:{name}")

    blockers.extend(f"active_request_changes:{actor}" for actor in _active_change_requests(reviews))
    try: unresolved = int(unresolved_review_threads)
    except (TypeError, ValueError): unresolved = -1
    if unresolved < 0: blockers.append("unresolved_review_thread_count_invalid")
    elif unresolved: blockers.append(f"unresolved_review_threads:{unresolved}")
    blockers.extend(f"human_gate:{_text(reason)}" for reason in human_gate_reasons if _text(reason))
    blockers = list(dict.fromkeys(blockers))
    ready = not blockers
    result: dict[str, Any] = {
        "schema": MERGE_GATE_SCHEMA, "status": "READY" if ready else "BLOCKED",
        "grant_id": validated["grant_id"], "grant_sha256": validated["grant_sha256"],
        "task_id": validated["task_id"], "task_binding_fingerprint": validated["task_binding_fingerprint"],
        "repository": validated["repository"], "pr_number": number,
        "expected_head_sha": head_sha or None, "expected_base_sha": base_sha or None,
        "mark_ready_allowed": bool(ready and pr.get("draft") is True), "merge_allowed": bool(ready),
        "merge_method": MERGE_METHOD, "blockers": blockers, "changed_paths": list(normalized_changed),
        "authority_effect": "conditional_final_merge_only" if ready else "none",
        "write_authority_effect": False, "test_authority_effect": False,
        "deploy_allowed": False, "production_closed": False,
    }
    result["decision_sha256"] = _digest(result)
    return result


def compile_merge_network_request(grant: Mapping[str, Any], decision: Mapping[str, Any], *, current_pr: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the sole mutation after a passing gate, with exact-head/base CAS."""
    validated = validate_merge_grant_document(grant)
    check = dict(decision); decision_digest = _text(check.pop("decision_sha256", ""))
    if decision.get("schema") != MERGE_GATE_SCHEMA or decision.get("status") != "READY" or decision.get("merge_allowed") is not True or _digest(check) != decision_digest:
        raise EngineeringMergeGrantError("merge request requires an untampered READY decision")
    if _text(decision.get("grant_sha256")) != _text(validated.get("grant_sha256")):
        raise EngineeringMergeGrantError("merge decision grant binding mismatch")
    number = int(current_pr.get("number") or 0)
    if number != int(decision.get("pr_number") or 0) or _text(current_pr.get("state")).lower() != "open":
        raise EngineeringMergeGrantError("current PR identity/state drifted")
    if current_pr.get("draft") is not False or current_pr.get("mergeable") is not True:
        raise EngineeringMergeGrantError("current PR must be Ready and mergeable")
    head = current_pr.get("head") if isinstance(current_pr.get("head"), Mapping) else {}
    base = current_pr.get("base") if isinstance(current_pr.get("base"), Mapping) else {}
    current_head, current_base = _sha(head.get("sha"), name="current PR head SHA"), _sha(base.get("sha"), name="current PR base SHA")
    if current_head != _text(decision.get("expected_head_sha")) or current_base != _text(decision.get("expected_base_sha")):
        raise EngineeringMergeGrantError("PR head/base drifted after final gate")
    request: dict[str, Any] = {
        "schema": MERGE_REQUEST_SCHEMA, "kind": "MERGE_PULL_REQUEST",
        "repository": validated["repository"], "pr_number": number, "method": "PUT",
        "path": f"repos/{validated['repository']}/pulls/{number}/merge",
        "body": {"sha": current_head, "merge_method": MERGE_METHOD},
        "grant_id": validated["grant_id"], "grant_sha256": validated["grant_sha256"],
        "decision_sha256": decision_digest, "authority_effect": "merge_only",
        "write_authority_effect": False, "test_authority_effect": False,
        "deploy_allowed": False, "production_closed": False,
    }
    request["request_sha256"] = _digest(request)
    return request
