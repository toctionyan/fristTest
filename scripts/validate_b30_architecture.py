#!/usr/bin/env python3
"""Fail-closed validator for the B30 architecture authority contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_CHAIN = [
    "request_ledger",
    "context_evidence",
    "semantic_contract",
    "capability_match",
    "plan_run",
    "execution_authority",
    "runtime_outcome",
    "public_projection",
]
REQUIRED_OUTCOMES = {"EXECUTE", "CLARIFY", "UNSUPPORTED", "SUBMISSION_UNKNOWN"}
REQUIRED_BOUNDARIES = {
    "request_identity",
    "context_evidence",
    "semantic_meaning",
    "dialogue_reference",
    "capability_support",
    "executable_plan",
    "business_fact",
    "transaction_state",
    "public_outcome",
}
REQUIRED_BOUNDARY_OWNERS = {
    "request_identity": "TurnRequestLedger",
    "context_evidence": "ContextEvidenceProjection",
    "semantic_meaning": "TurnSemanticContract",
    "dialogue_reference": "TurnSemanticContract.TypedTargetSet",
    "capability_support": "CapabilitySurface+MatchProof",
    "executable_plan": "PlanRun",
    "business_fact": "BusinessService",
    "transaction_state": "TransactionRepository",
    "public_outcome": "RuntimeOutcome",
}
REQUIRED_WORK_PACKAGES = {f"WP-{index:02d}" for index in range(1, 9)}
REQUIRED_WP02_SUBPACKAGES = {"WP-02A", "WP-02B"}
REQUIRED_WP02_OWNERS = {
    "WP-02A": "TurnRequestLedger",
    "WP-02B": "ContextEvidenceProjection+TurnSemanticContract+TurnSemanticContract.TypedTargetSet",
}


class ContractError(ValueError):
    """Raised when the architecture contract is incomplete or contradictory."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"root_must_be_object:{path}")
    return payload


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"missing_or_empty:{label}")
    return value.strip()


def _validate_paths_and_exit_conditions(payload: dict[str, Any], identifier: str) -> None:
    allowed_paths = payload.get("allowed_paths")
    exit_conditions = payload.get("exit_conditions")
    if not isinstance(allowed_paths, list) or not allowed_paths or not all(isinstance(item, str) and item for item in allowed_paths):
        raise ContractError(f"work_package_allowed_paths_invalid:{identifier}")
    if not isinstance(exit_conditions, list) or len(exit_conditions) < 2 or not all(isinstance(item, str) and item for item in exit_conditions):
        raise ContractError(f"work_package_exit_conditions_invalid:{identifier}")


def validate(authority_path: Path, retirement_path: Path, doc_path: Path) -> None:
    authority = _read_json(authority_path)
    retirement = _read_json(retirement_path)
    documentation = doc_path.read_text(encoding="utf-8")

    if authority.get("schema_version") != 1 or authority.get("stage") != "B30":
        raise ContractError("authority_schema_or_stage_invalid")
    _require_nonempty_string(authority.get("baseline_commit"), "baseline_commit")

    chain = authority.get("authoritative_chain")
    if not isinstance(chain, list):
        raise ContractError("authoritative_chain_must_be_list")
    chain_ids = [_require_nonempty_string(item.get("id"), "chain.id") for item in chain if isinstance(item, dict)]
    if chain_ids != REQUIRED_CHAIN:
        raise ContractError(f"authoritative_chain_order_invalid:{chain_ids}")
    if len(chain_ids) != len(set(chain_ids)):
        raise ContractError("duplicate_chain_stage")
    for item in chain:
        if not isinstance(item, dict):
            raise ContractError("chain_entry_must_be_object")
        _require_nonempty_string(item.get("owner"), f"{item.get('id')}.owner")
        _require_nonempty_string(item.get("responsibility"), f"{item.get('id')}.responsibility")
        _require_nonempty_string(item.get("output"), f"{item.get('id')}.output")

    boundaries = authority.get("authority_boundaries")
    if not isinstance(boundaries, dict) or set(boundaries) != REQUIRED_BOUNDARIES:
        raise ContractError("authority_boundary_set_invalid")
    boundary_owners: list[str] = []
    for name, boundary in boundaries.items():
        if not isinstance(boundary, dict):
            raise ContractError(f"boundary_must_be_object:{name}")
        owner = _require_nonempty_string(boundary.get("owner"), f"{name}.owner")
        if owner != REQUIRED_BOUNDARY_OWNERS[name]:
            raise ContractError(f"authority_boundary_owner_invalid:{name}:{owner}")
        boundary_owners.append(owner)
        forbidden = boundary.get("must_not_be_owned_by")
        if not isinstance(forbidden, list) or not forbidden:
            raise ContractError(f"boundary_forbidden_owners_missing:{name}")
    if len(boundary_owners) != len(set(boundary_owners)):
        raise ContractError("duplicate_authority_owner")

    outcomes = authority.get("allowed_terminal_outcomes")
    if not isinstance(outcomes, list) or not REQUIRED_OUTCOMES.issubset(set(outcomes)):
        raise ContractError("required_terminal_outcome_missing")
    invariants = authority.get("hard_invariants")
    if not isinstance(invariants, list) or len(invariants) < 10 or len(invariants) != len(set(invariants)):
        raise ContractError("hard_invariants_incomplete_or_duplicate")
    if "context_evidence_is_projected_before_semantic_freeze_and_never_selects_a_target" not in invariants:
        raise ContractError("context_before_semantics_invariant_missing")

    if retirement.get("schema_version") != 1 or retirement.get("stage") != "B30":
        raise ContractError("retirement_schema_or_stage_invalid")
    targets = retirement.get("retirement_targets")
    if not isinstance(targets, list) or not targets:
        raise ContractError("retirement_targets_missing")
    target_ids: list[str] = []
    for target in targets:
        if not isinstance(target, dict):
            raise ContractError("retirement_target_must_be_object")
        target_id = _require_nonempty_string(target.get("id"), "retirement_target.id")
        target_ids.append(target_id)
        for field in ("target", "replacement", "discovery", "deletion_condition", "rollback"):
            _require_nonempty_string(target.get(field), f"{target_id}.{field}")
    if len(target_ids) != len(set(target_ids)):
        raise ContractError("duplicate_retirement_target")

    work_packages = retirement.get("work_packages")
    if not isinstance(work_packages, list):
        raise ContractError("work_packages_must_be_list")
    work_package_ids = {item.get("id") for item in work_packages if isinstance(item, dict)}
    if work_package_ids != REQUIRED_WORK_PACKAGES:
        raise ContractError(f"work_package_set_invalid:{sorted(str(item) for item in work_package_ids)}")

    wp02: dict[str, Any] | None = None
    for package in work_packages:
        if not isinstance(package, dict):
            raise ContractError("work_package_must_be_object")
        package_id = _require_nonempty_string(package.get("id"), "work_package.id")
        _require_nonempty_string(package.get("name"), f"{package_id}.name")
        _validate_paths_and_exit_conditions(package, package_id)
        if package_id == "WP-02":
            wp02 = package
        elif "sub_work_packages" in package:
            raise ContractError(f"sub_work_packages_only_allowed_on_wp02:{package_id}")

    if wp02 is None:
        raise ContractError("wp02_missing")
    subpackages = wp02.get("sub_work_packages")
    if not isinstance(subpackages, list):
        raise ContractError("wp02_sub_work_packages_missing")
    subpackage_ids = {item.get("id") for item in subpackages if isinstance(item, dict)}
    if subpackage_ids != REQUIRED_WP02_SUBPACKAGES:
        raise ContractError(f"wp02_sub_work_package_set_invalid:{sorted(str(item) for item in subpackage_ids)}")
    if len(subpackages) != len(subpackage_ids):
        raise ContractError("duplicate_wp02_sub_work_package")
    for subpackage in subpackages:
        if not isinstance(subpackage, dict):
            raise ContractError("wp02_sub_work_package_must_be_object")
        subpackage_id = _require_nonempty_string(subpackage.get("id"), "wp02_sub_work_package.id")
        if subpackage.get("parent") != "WP-02":
            raise ContractError(f"wp02_sub_work_package_parent_invalid:{subpackage_id}")
        _require_nonempty_string(subpackage.get("name"), f"{subpackage_id}.name")
        if subpackage.get("authority_owner") != REQUIRED_WP02_OWNERS[subpackage_id]:
            raise ContractError(f"wp02_sub_work_package_owner_invalid:{subpackage_id}")
        _validate_paths_and_exit_conditions(subpackage, subpackage_id)

    for required_reference in (
        "TurnRequestLedger",
        "ContextEvidenceProjection",
        "TurnSemanticContract",
        "CapabilitySurface",
        "MatchProof",
        "PlanRun",
        "BusinessService",
        "TransactionRepository",
        "RuntimeOutcome",
        "b30-authority-map.json",
        "b30-legacy-retirement.json",
        "WP-02A",
        "WP-02B",
    ):
        if required_reference not in documentation:
            raise ContractError(f"documentation_reference_missing:{required_reference}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path, default=Path("governance/architecture/b30-authority-map.json"))
    parser.add_argument("--retirement", type=Path, default=Path("governance/architecture/b30-legacy-retirement.json"))
    parser.add_argument("--doc", type=Path, default=Path("docs/architecture/B30_AUTHORITATIVE_RUNTIME.md"))
    args = parser.parse_args()
    try:
        validate(args.authority, args.retirement, args.doc)
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "stage": "B30"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
