#!/usr/bin/env python3
"""Fail-closed validator for the B30 WP-02A implementation plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_BASELINE = "f9d3f63ddf68ec1e12c0258187a971720383716b"
EXPECTED_CONTRACT_BLOB = "1aeb3a7ac6ce10d6e421d96c8b06b7e818db50a1"
EXPECTED_PHASES = [f"P{index}" for index in range(1, 9)]
REQUIRED_DIMENSIONS = {
    "focused", "counterexample", "negative_path", "concurrency",
    "crash_recovery", "http_sse_equivalence", "backend_parity",
    "complete_regression",
}
REQUIRED_SCOPE_AMENDMENT = {
    "services/agent-service/app/services/agent_service.py",
    "services/agent-service/src/agent_core/persistence/message_store.py",
}
REQUIRED_IMPLEMENTATION_PATHS = {
    "services/agent-service/app/schemas/chat_schema.py",
    "services/agent-service/app/services/agent_service.py",
    "services/agent-service/app/use_cases/conversation_turn.py",
    "services/agent-service/src/agent_core/storage/repositories/base.py",
    "services/agent-service/src/agent_core/persistence/turn_request_store.py",
    "services/agent-service/src/agent_core/persistence/message_store.py",
    "services/agent-service/src/agent_core/persistence/store_provider.py",
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    "services/agent-service/frontend/src/components/ChatPanel.jsx",
}
EXPECTED_SEPARATELY_GOVERNED = {"skill-system/registry/product-source-baseline.json"}
FORBIDDEN_PREFIXES = (
    "services/business-service/",
    "skill-system/controller/",
    "skill-system/hooks/",
    ".github/workflows/",
)
FORBIDDEN_SEMANTIC_PATHS = {
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/src/agent_core/lifecycle/context_runtime.py",
}
REQUIRED_MUST_NOT_OWN = {
    "user meaning", "typed goals", "dialogue reference resolution",
    "capability support", "business facts", "transaction lifecycle",
}


class PlanError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise PlanError("plan_root_must_be_object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"missing_or_empty:{label}")
    return value.strip()


def validate(plan_path: Path, doc_path: Path) -> None:
    plan = _load(plan_path)
    doc = doc_path.read_text(encoding="utf-8")
    if (
        plan.get("schema_version") != 1
        or plan.get("stage") != "B30"
        or plan.get("work_package") != "WP-02A"
        or plan.get("parent_work_package") != "WP-02"
    ):
        raise PlanError("schema_stage_or_work_package_invalid")
    if plan.get("status") != "PROPOSED_FOR_REVIEW":
        raise PlanError("plan_must_remain_proposed_for_review")
    if plan.get("baseline_commit") != EXPECTED_BASELINE:
        raise PlanError("baseline_binding_invalid")

    binding = plan.get("contract_binding")
    if not isinstance(binding, dict):
        raise PlanError("contract_binding_missing")
    if binding.get("merged_commit") != EXPECTED_BASELINE or binding.get("git_blob_sha") != EXPECTED_CONTRACT_BLOB:
        raise PlanError("contract_binding_invalid")
    _text(binding.get("rule"), "contract_binding.rule")

    boundary = plan.get("authority_boundary")
    if not isinstance(boundary, dict) or boundary.get("owner") != "TurnRequestLedger":
        raise PlanError("wp02a_authority_owner_invalid")
    if set(boundary.get("must_not_own") or []) != REQUIRED_MUST_NOT_OWN:
        raise PlanError("wp02a_authority_exclusion_invalid")
    sibling = _text(boundary.get("sibling_boundary"), "authority_boundary.sibling_boundary")
    if "WP-02B" not in sibling or "TurnSemanticContract" not in sibling:
        raise PlanError("wp02b_sibling_boundary_missing")

    root_cause = plan.get("root_cause")
    if not isinstance(root_cause, dict):
        raise PlanError("root_cause_missing")
    _text(root_cause.get("failure"), "root_cause.failure")
    if not isinstance(root_cause.get("proof"), list) or len(root_cause["proof"]) < 4:
        raise PlanError("root_cause_proof_incomplete")

    amendment = plan.get("scope_amendment")
    if not isinstance(amendment, dict):
        raise PlanError("scope_amendment_missing")
    if set(amendment.get("added_paths_requiring_review") or []) != REQUIRED_SCOPE_AMENDMENT:
        raise PlanError("scope_amendment_paths_invalid")
    _text(amendment.get("reason"), "scope_amendment.reason")
    _text(amendment.get("rule"), "scope_amendment.rule")

    implementation_paths = set(plan.get("implementation_paths") or [])
    if not REQUIRED_IMPLEMENTATION_PATHS.issubset(implementation_paths):
        raise PlanError("required_implementation_path_missing")
    separately_governed = set(plan.get("separately_governed_paths") or [])
    if separately_governed != EXPECTED_SEPARATELY_GOVERNED:
        raise PlanError("baseline_refresh_must_be_separately_governed")
    overlap = implementation_paths & separately_governed
    if overlap:
        raise PlanError(f"separately_governed_path_in_implementation_scope:{sorted(overlap)}")
    semantic_overlap = implementation_paths & FORBIDDEN_SEMANTIC_PATHS
    if semantic_overlap:
        raise PlanError(f"wp02b_path_in_wp02a_scope:{sorted(semantic_overlap)}")
    for path in implementation_paths:
        if any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            raise PlanError(f"forbidden_implementation_path:{path}")

    forbidden_paths = set(plan.get("forbidden_paths") or [])
    if not FORBIDDEN_SEMANTIC_PATHS.issubset(forbidden_paths):
        raise PlanError("wp02b_forbidden_paths_missing")

    phases = plan.get("phases")
    if not isinstance(phases, list) or [row.get("id") for row in phases if isinstance(row, dict)] != EXPECTED_PHASES:
        raise PlanError("phase_order_invalid")
    for phase in phases:
        if not isinstance(phase, dict):
            raise PlanError("phase_must_be_object")
        phase_id = _text(phase.get("id"), "phase.id")
        _text(phase.get("name"), f"{phase_id}.name")
        _text(phase.get("rollback"), f"{phase_id}.rollback")
        requirements = phase.get("requirements")
        tests = phase.get("tests")
        if not isinstance(requirements, list) or len(requirements) < 3:
            raise PlanError(f"phase_requirements_incomplete:{phase_id}")
        if not isinstance(tests, list) or len(tests) < 3:
            raise PlanError(f"phase_tests_incomplete:{phase_id}")
        for path in phase.get("writes") or []:
            if path not in implementation_paths:
                raise PlanError(f"phase_write_outside_scope:{phase_id}:{path}")

    if set(plan.get("mandatory_test_dimensions") or []) != REQUIRED_DIMENSIONS:
        raise PlanError("mandatory_test_dimensions_invalid")
    if not isinstance(plan.get("review_questions"), list) or len(plan["review_questions"]) < 8:
        raise PlanError("review_questions_incomplete")
    if not isinstance(plan.get("permit_preconditions"), list) or len(plan["permit_preconditions"]) < 7:
        raise PlanError("permit_preconditions_incomplete")

    for reference in (
        EXPECTED_BASELINE, EXPECTED_CONTRACT_BLOB, "WP-02A", "WP-02B",
        "AgentService", "MessageStore", "ConversationTurnService", "ChatPanel",
        "P8", "product-source baseline",
    ):
        if reference not in doc:
            raise PlanError(f"documentation_reference_missing:{reference}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=Path("governance/architecture/b30-wp02a-implementation-plan.json"))
    parser.add_argument("--doc", type=Path, default=Path("docs/architecture/B30_WP02A_IMPLEMENTATION_PLAN.md"))
    args = parser.parse_args()
    try:
        validate(args.plan, args.doc)
    except (PlanError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "stage": "B30", "work_package": "WP-02A", "artifact": "implementation_plan"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
