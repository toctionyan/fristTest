#!/usr/bin/env python3
from __future__ import annotations

"""Machine authority for governed repair RCA and exact write grants.

This module is deterministic: it never calls a model and never edits the
candidate workspace. Historical product repair keeps using the canonical
``validate_automatic_repair_paths`` API. M8.6 adds a separate exact
``CONTROL_PLANE_IMPLEMENTATION`` domain only after semantic Stage-1 routing has
proved a bounded engineering-verifier implementation failure. Tests/oracles,
workflows, governance, merge, deploy, and production authority remain protected.
"""

import hashlib
import json
from typing import Any, Iterable, Mapping

from governed_repair_contract import (
    CONTRACT_ID,
    PREWRITE_STATES,
    PROTECTED_AUTHORITY,
    contract_fingerprint,
)
from governed_repair_path_policy import (
    PATH_POLICY_ID,
    REPAIR_DOMAIN_CONTROL_PLANE,
    REPAIR_DOMAIN_PRODUCT,
    RepairPathPolicyError,
    normalize_repo_path as policy_normalize_repo_path,
    policy_fingerprint,
    validate_automatic_repair_paths,
    validate_repair_paths,
)

RCA_SCHEMA = "github-governed-repair-rca@1"
WRITE_GRANT_SCHEMA = "github-governed-repair-write-grant@1"
STATE_SCHEMA = "governed-repair-state@1"
REQUIRED_RCA_TEXT_FIELDS = (
    "failure_class",
    "violated_invariant",
    "authority_owner",
    "drifted_projection",
    "root_cause",
    "existing_gate_gap",
    "required_permanent_guard",
)
_BINDING_FIELDS = (
    "repository",
    "workflow_run_id",
    "workflow_run_attempt",
    "head_sha",
    "failure_signature",
)


class RepairAuthorityError(RuntimeError):
    """Fail-closed error for governed repair authority validation."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def failure_case_fingerprint(failure_case: Mapping[str, Any]) -> str:
    return fingerprint(dict(failure_case))


def _without_digest(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(field, None)
    return result


def rca_fingerprint(rca: Mapping[str, Any]) -> str:
    return fingerprint(_without_digest(rca, "rca_sha256"))


def write_grant_fingerprint(grant: Mapping[str, Any]) -> str:
    return fingerprint(_without_digest(grant, "write_grant_sha256"))


def normalize_repo_path(raw: object) -> str:
    try:
        return policy_normalize_repo_path(raw)
    except RepairPathPolicyError as exc:
        raise RepairAuthorityError(str(exc)) from exc


def normalize_paths(paths: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in paths:
        path = normalize_repo_path(raw)
        if path in result:
            raise RepairAuthorityError(f"duplicate write path: {path}")
        result.append(path)
    return tuple(result)


def repair_domain(failure_case: Mapping[str, Any]) -> str:
    classification = str(failure_case.get("classification") or "").strip()
    supplied = str(failure_case.get("repair_domain") or "").strip()
    if classification == "code_or_contract":
        if supplied and supplied != REPAIR_DOMAIN_PRODUCT:
            raise RepairAuthorityError("product code/contract failure cannot switch repair domain")
        return REPAIR_DOMAIN_PRODUCT
    if classification != "control_plane_implementation":
        raise RepairAuthorityError(
            f"failure classification is outside automatic write authority: {classification!r}"
        )
    if supplied != REPAIR_DOMAIN_CONTROL_PLANE:
        raise RepairAuthorityError(
            "control-plane implementation failure requires its exact repair domain"
        )
    route = failure_case.get("repair_route")
    if not isinstance(route, Mapping):
        raise RepairAuthorityError("control-plane repair requires semantic route evidence")
    if route.get("repair_class") != "CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE":
        raise RepairAuthorityError("control-plane semantic route class is invalid")
    if route.get("repair_domain") != REPAIR_DOMAIN_CONTROL_PLANE:
        raise RepairAuthorityError("control-plane semantic route domain drift")
    if route.get("automatic_write_allowed") is not True:
        raise RepairAuthorityError("control-plane semantic route did not allow a write")
    protected_false = (
        "test_write_allowed",
        "acceptance_write_allowed",
        "oracle_write_allowed",
        "scope_expansion_allowed",
        "merge_allowed",
        "deploy_allowed",
        "production_closed",
    )
    if any(route.get(field) is not False for field in protected_false):
        raise RepairAuthorityError("control-plane route crossed a protected authority boundary")
    routed = normalize_paths(route.get("allowed_write_paths") or [])
    candidates = normalize_paths(failure_case.get("candidate_paths") or [])
    if not routed or routed != candidates:
        raise RepairAuthorityError("control-plane route scope differs from failure-case scope")
    return REPAIR_DOMAIN_CONTROL_PLANE


def _require_automatic_path_policy(
    paths: Iterable[object],
    *,
    failure_case: Mapping[str, Any],
) -> tuple[str, ...]:
    domain = repair_domain(failure_case)
    try:
        if domain == REPAIR_DOMAIN_PRODUCT:
            # Keep the historical product authority call explicit and executable.
            return validate_automatic_repair_paths(paths)
        return validate_repair_paths(paths, repair_domain=domain)
    except RepairPathPolicyError as exc:
        raise RepairAuthorityError(f"automatic repair path policy denied authority: {exc}") from exc


def failure_binding(failure_case: Mapping[str, Any]) -> dict[str, str]:
    return {
        "repository": str(failure_case.get("repository") or "").strip(),
        "workflow_run_id": str(failure_case.get("workflow_run_id") or "").strip(),
        "workflow_run_attempt": str(failure_case.get("workflow_run_attempt") or "1").strip(),
        "head_sha": str(failure_case.get("head_sha") or "").strip(),
        "failure_signature": str(failure_case.get("failure_signature") or "").strip(),
    }


def _require_binding(binding: Mapping[str, Any]) -> None:
    missing = [field for field in _BINDING_FIELDS if not str(binding.get(field) or "").strip()]
    if missing:
        raise RepairAuthorityError(f"failure binding is missing: {missing}")


def required_guard_ids(failure_case: Mapping[str, Any]) -> tuple[str, ...]:
    rows = failure_case.get("failed_gates")
    if not isinstance(rows, list):
        return ()
    result: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        gate_id = str(row.get("gate_id") or "").strip()
        if gate_id and "\n" not in gate_id and "\r" not in gate_id and gate_id not in result:
            result.append(gate_id)
    return tuple(result)


def validate_rca(
    rca: Mapping[str, Any],
    *,
    failure_case: Mapping[str, Any],
    candidate_paths: Iterable[object],
) -> tuple[str, ...]:
    if rca.get("schema") != RCA_SCHEMA:
        raise RepairAuthorityError("unsupported RCA schema")
    if rca.get("read_only") is not True:
        raise RepairAuthorityError("RCA is not marked read-only")
    if rca.get("workspace_mutated") is not False:
        raise RepairAuthorityError("RCA reports candidate workspace mutation")
    if rca.get("production_closed") is not False:
        raise RepairAuthorityError("RCA cannot close production")

    domain = repair_domain(failure_case)
    if str(rca.get("repair_domain") or domain) != domain:
        raise RepairAuthorityError("RCA repair domain drift")
    expected_binding = failure_binding(failure_case)
    _require_binding(expected_binding)
    actual_binding = rca.get("binding")
    if not isinstance(actual_binding, Mapping):
        raise RepairAuthorityError("RCA binding is missing")
    mismatched = [
        field for field in _BINDING_FIELDS
        if str(actual_binding.get(field) or "") != expected_binding[field]
    ]
    if mismatched:
        raise RepairAuthorityError(f"RCA failure binding mismatch: {mismatched}")
    if str(rca.get("failure_case_sha256") or "") != failure_case_fingerprint(failure_case):
        raise RepairAuthorityError("RCA failure-case fingerprint mismatch")

    expected_paths = normalize_paths(candidate_paths)
    if _require_automatic_path_policy(expected_paths, failure_case=failure_case) != expected_paths:
        raise RepairAuthorityError("automatic repair path policy normalization drift")
    evidence_paths = normalize_paths(rca.get("candidate_paths") or [])
    if evidence_paths != expected_paths:
        raise RepairAuthorityError(
            f"RCA candidate path binding mismatch: expected={list(expected_paths)} actual={list(evidence_paths)}"
        )
    for field in REQUIRED_RCA_TEXT_FIELDS:
        if not str(rca.get(field) or "").strip():
            raise RepairAuthorityError(f"RCA is missing {field}")
    plan = rca.get("repair_plan")
    if (
        not isinstance(plan, list)
        or not plan
        or len(plan) > 12
        or any(not isinstance(item, str) or not item.strip() for item in plan)
    ):
        raise RepairAuthorityError("RCA repair_plan must contain 1-12 non-empty steps")

    recommendation = rca.get("write_scope_recommendation")
    if not isinstance(recommendation, Mapping):
        raise RepairAuthorityError("RCA write_scope_recommendation is missing")
    decision = str(recommendation.get("decision") or "").upper()
    if decision not in {"GRANT", "DENY"}:
        raise RepairAuthorityError("RCA write decision must be GRANT or DENY")
    recommended_paths = normalize_paths(recommendation.get("paths") or [])
    if any(path not in expected_paths for path in recommended_paths):
        raise RepairAuthorityError("RCA attempted to expand writable scope")
    if decision == "GRANT" and not recommended_paths:
        raise RepairAuthorityError("RCA GRANT recommendation has empty write scope")
    if decision == "GRANT" and _require_automatic_path_policy(
        recommended_paths, failure_case=failure_case
    ) != recommended_paths:
        raise RepairAuthorityError("RCA recommended path policy normalization drift")
    if decision == "DENY" and recommended_paths:
        raise RepairAuthorityError("RCA DENY recommendation must not carry write paths")
    if str(rca.get("rca_sha256") or "") != rca_fingerprint(rca):
        raise RepairAuthorityError("RCA fingerprint mismatch")
    return recommended_paths


def compile_write_grant(
    *,
    failure_case: Mapping[str, Any],
    rca: Mapping[str, Any],
    candidate_paths: Iterable[object],
) -> dict[str, Any]:
    recommended = validate_rca(rca, failure_case=failure_case, candidate_paths=candidate_paths)
    recommendation = rca["write_scope_recommendation"]
    if str(recommendation.get("decision") or "").upper() != "GRANT":
        raise RepairAuthorityError("RCA denied write authority")
    guard_ids = required_guard_ids(failure_case)
    if not guard_ids:
        raise RepairAuthorityError(
            "write authority requires at least one existing machine guard from failed_gates"
        )
    lifecycle_sha = contract_fingerprint()
    path_policy_sha = policy_fingerprint()
    domain = repair_domain(failure_case)
    grant: dict[str, Any] = {
        "schema": WRITE_GRANT_SCHEMA,
        "state_schema": STATE_SCHEMA,
        "state": "WRITE_GRANTED",
        "state_history": list(PREWRITE_STATES),
        "lifecycle_contract_id": CONTRACT_ID,
        "lifecycle_contract_sha256": lifecycle_sha,
        "path_policy_id": PATH_POLICY_ID,
        "path_policy_sha256": path_policy_sha,
        "repair_domain": domain,
        "binding": failure_binding(failure_case),
        "failure_case_sha256": failure_case_fingerprint(failure_case),
        "rca_sha256": rca_fingerprint(rca),
        "allowed_paths": list(recommended),
        "required_guard_ids": list(guard_ids),
        "write_scope_mode": "exact_allowlist",
        "authority": {"write_authority": True, **PROTECTED_AUTHORITY},
        "invariant": str(rca["violated_invariant"]).strip(),
        "authority_owner": str(rca["authority_owner"]).strip(),
        "drifted_projection": str(rca["drifted_projection"]).strip(),
        "required_permanent_guard": str(rca["required_permanent_guard"]).strip(),
        "repair_plan": list(rca["repair_plan"]),
        "gates": {
            "G0_SCOPE_AUTHORITY": {
                "status": "PASS",
                "evidence": [
                    f"failure-case:{failure_case_fingerprint(failure_case)}",
                    f"rca:{rca_fingerprint(rca)}",
                    f"lifecycle-contract:{lifecycle_sha}",
                    f"path-policy:{path_policy_sha}",
                    f"repair-domain:{domain}",
                ],
            }
        },
        "production_closed": False,
    }
    grant["write_grant_sha256"] = write_grant_fingerprint(grant)
    return grant


def validate_write_grant(
    grant: Mapping[str, Any],
    *,
    failure_case: Mapping[str, Any],
    rca: Mapping[str, Any],
    candidate_paths: Iterable[object],
) -> tuple[str, ...]:
    recommended = validate_rca(rca, failure_case=failure_case, candidate_paths=candidate_paths)
    if grant.get("schema") != WRITE_GRANT_SCHEMA:
        raise RepairAuthorityError("unsupported write-grant schema")
    if grant.get("state_schema") != STATE_SCHEMA or grant.get("state") != "WRITE_GRANTED":
        raise RepairAuthorityError("write grant is not in WRITE_GRANTED state")
    if tuple(grant.get("state_history") or ()) != PREWRITE_STATES:
        raise RepairAuthorityError("write-grant state history drift")
    if str(grant.get("lifecycle_contract_id") or "") != CONTRACT_ID:
        raise RepairAuthorityError("write-grant lifecycle contract id drift")
    if str(grant.get("lifecycle_contract_sha256") or "") != contract_fingerprint():
        raise RepairAuthorityError("write-grant lifecycle contract fingerprint drift")
    if str(grant.get("path_policy_id") or "") != PATH_POLICY_ID:
        raise RepairAuthorityError("write-grant path policy id drift")
    if str(grant.get("path_policy_sha256") or "") != policy_fingerprint():
        raise RepairAuthorityError("write-grant path policy fingerprint drift")
    domain = repair_domain(failure_case)
    if str(grant.get("repair_domain") or "") != domain:
        raise RepairAuthorityError("write-grant repair domain drift")
    if grant.get("write_scope_mode") != "exact_allowlist":
        raise RepairAuthorityError("write-grant scope mode is not exact_allowlist")
    if grant.get("production_closed") is not False:
        raise RepairAuthorityError("write grant cannot close production")

    actual_binding = grant.get("binding")
    expected_binding = failure_binding(failure_case)
    if not isinstance(actual_binding, Mapping):
        raise RepairAuthorityError("write-grant binding is missing")
    mismatched = [
        field for field in _BINDING_FIELDS
        if str(actual_binding.get(field) or "") != expected_binding[field]
    ]
    if mismatched:
        raise RepairAuthorityError(f"write-grant binding mismatch: {mismatched}")
    if str(grant.get("failure_case_sha256") or "") != failure_case_fingerprint(failure_case):
        raise RepairAuthorityError("write-grant failure-case fingerprint mismatch")
    if str(grant.get("rca_sha256") or "") != rca_fingerprint(rca):
        raise RepairAuthorityError("write-grant RCA fingerprint mismatch")

    expected_guard_ids = required_guard_ids(failure_case)
    if not expected_guard_ids:
        raise RepairAuthorityError(
            "write authority requires at least one existing machine guard from failed_gates"
        )
    actual_guard_ids = tuple(str(item or "").strip() for item in grant.get("required_guard_ids") or [])
    if actual_guard_ids != expected_guard_ids:
        raise RepairAuthorityError(
            f"write-grant permanent guard binding mismatch: expected={list(expected_guard_ids)} actual={list(actual_guard_ids)}"
        )
    granted_paths = normalize_paths(grant.get("allowed_paths") or [])
    if _require_automatic_path_policy(granted_paths, failure_case=failure_case) != granted_paths:
        raise RepairAuthorityError("write-grant path policy normalization drift")
    if granted_paths != recommended:
        raise RepairAuthorityError(
            f"write-grant path mismatch: RCA={list(recommended)} grant={list(granted_paths)}"
        )
    authority = grant.get("authority")
    expected_authority = {"write_authority": True, **PROTECTED_AUTHORITY}
    if not isinstance(authority, Mapping) or dict(authority) != expected_authority:
        raise RepairAuthorityError("write grant protected authority drift")
    if str(grant.get("write_grant_sha256") or "") != write_grant_fingerprint(grant):
        raise RepairAuthorityError("write-grant fingerprint mismatch")
    return granted_paths


def revoke_write_grant(
    grant: Mapping[str, Any],
    *,
    reason: str,
    failure_signature: str,
) -> dict[str, Any]:
    receipt = {
        "schema": "github-governed-repair-write-revocation@1",
        "state": "RCA_READ_ONLY",
        "lifecycle_contract_id": CONTRACT_ID,
        "lifecycle_contract_sha256": contract_fingerprint(),
        "path_policy_id": PATH_POLICY_ID,
        "path_policy_sha256": policy_fingerprint(),
        "repair_domain": str(grant.get("repair_domain") or ""),
        "prior_write_grant_sha256": write_grant_fingerprint(grant),
        "failure_signature": str(failure_signature or "").strip(),
        "reason": str(reason or "repeated_failure_signature").strip(),
        "write_authority": False,
        "next_action": "ARCHITECTURE_REPLAN_AND_NEW_RCA",
        "production_closed": False,
    }
    receipt["revocation_sha256"] = fingerprint(receipt)
    return receipt
