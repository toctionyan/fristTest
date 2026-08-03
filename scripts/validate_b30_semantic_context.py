#!/usr/bin/env python3
"""Fail-closed validator for B30 WP-02B semantic/context authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_ORDER = [
    "compile_provider_safe_conversation",
    "build_context_evidence_projection",
    "model_proposes_semantic_contract",
    "validate_current_turn_literal_evidence",
    "validate_goal_and_focus_revisions",
    "validate_typed_reference_scope_shape_ttl_and_lineage",
    "freeze_turn_semantic_contract",
    "derive_goal_declaration_projection",
    "discover_exact_capability_surface",
]
REQUIRED_TARGET_RELATIONS = {
    "EXPLICIT_ENTITY",
    "VISIBLE_RESULT_REF",
    "VISIBLE_RESULT_MEMBER",
    "LATEST_VISIBLE_SCOPE",
    "SET",
    "SUBSET",
    "UNION",
    "INTERSECTION",
    "DIFFERENCE",
    "FILTER",
    "SORT",
    "TAKE_POSITION",
    "CONTINUATION_OF_GOAL",
}
REQUIRED_GOAL_CHANGES = {"SET_GOAL_LIFECYCLE", "PATCH_GOAL", "SUPERSEDE_GOAL"}
REQUIRED_FOCUS_CHANGES = {"SET_GOAL_FOCUS", "SET_INTERACTION_FOCUS", "CLEAR_FOCUS"}
REQUIRED_ACCEPTANCE = {
    "current_user_span_is_literal_and_contiguous",
    "historical_text_cannot_masquerade_as_current_span",
    "direct_explicit_entity_reference",
    "single_visible_result_continuation",
    "multiple_latest_refs_same_source_effect_form_one_scope",
    "multiple_latest_refs_distinct_source_effects_are_ambiguous",
    "set_reference_preserves_all_members",
    "subset_and_difference_use_typed_lineage",
    "position_reference_uses_canonical_order",
    "correction_supersedes_or_revision_patches_exact_goal",
    "interruption_pauses_without_cancelling_unmentioned_goal",
    "resumption_uses_explicit_continuation_relation",
    "stale_expired_or_out_of_scope_ref_is_rejected",
    "referent_set_is_non_dispatchable",
    "focus_state_cannot_select_a_business_target",
    "tool_failure_cannot_rewrite_requested_effect",
    "unsupported_goal_is_preserved_for_matchproof_absence",
    "multi_goal_split_is_by_business_effect_not_tool_count",
    "clarification_has_no_execution_surface",
    "goal_projection_is_derived_only_from_frozen_contract",
    "legacy_goal_type_is_non_authoritative",
    "wp02b_cannot_create_turn_request_identity",
}
REQUIRED_LEGACY_FORBIDDEN = {
    "keyword routing",
    "intent-map authority",
    "tool-first semantic selection",
    "per-intent pronoun resolution",
    "per-tool memory target selection",
    "latest-object automatic target selection",
    "free-text history scan used as a business target",
    "capability failure rewriting requested_effect",
    "legacy goal_type affecting newly frozen semantic identity",
}
REQUIRED_HARD_FLAGS = {
    "runtime_auto_select_target": False,
    "runtime_auto_switch_target": False,
    "referent_sets_dispatchable": False,
    "audit_metadata_target_selector": False,
    "tool_failures_are_business_facts": False,
}


class SemanticContextContractError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticContextContractError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise SemanticContextContractError("contract_root_must_be_object")
    return payload


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticContextContractError(f"missing_or_empty:{label}")
    return value.strip()


def validate(contract_path: Path, doc_path: Path) -> None:
    contract = _read(contract_path)
    doc = doc_path.read_text(encoding="utf-8")
    if (
        contract.get("schema_version") != 1
        or contract.get("stage") != "B30"
        or contract.get("work_package") != "WP-02B"
        or contract.get("parent_work_package") != "WP-02"
    ):
        raise SemanticContextContractError("schema_stage_or_work_package_invalid")

    authority = contract.get("authority")
    if not isinstance(authority, dict):
        raise SemanticContextContractError("authority_missing")
    expected_owners = {
        "semantic_owner": "TurnSemanticContract",
        "typed_target_owner": "TurnSemanticContract.TypedTargetSet",
        "context_evidence_owner": "ContextEvidenceProjection",
        "model_role": "open_language_semantic_compiler_candidate",
        "runtime_role": "shape_scope_revision_literal_evidence_and_integrity_validator",
    }
    for key, expected in expected_owners.items():
        if authority.get(key) != expected:
            raise SemanticContextContractError(f"authority_field_invalid:{key}")
    if set(authority.get("reference_provenance_owners") or []) != {"VisibleResultRef", "SourceEffect"}:
        raise SemanticContextContractError("reference_provenance_owner_set_invalid")

    if list(contract.get("semantic_compile_order") or []) != REQUIRED_ORDER:
        raise SemanticContextContractError("semantic_compile_order_invalid")

    current = contract.get("current_user_authority")
    if not isinstance(current, dict):
        raise SemanticContextContractError("current_user_authority_missing")
    for field in ("source", "literal_span_rule", "history_rule", "history_reference_rule"):
        _text(current.get(field), f"current_user_authority.{field}")

    frozen = contract.get("frozen_semantic_contract")
    if not isinstance(frozen, dict):
        raise SemanticContextContractError("frozen_semantic_contract_missing")
    if frozen.get("version") != "frozen-turn-semantic-contract@1":
        raise SemanticContextContractError("semantic_contract_version_invalid")
    if frozen.get("authority_value") != "sole_formal_turn_semantics":
        raise SemanticContextContractError("semantic_contract_authority_invalid")
    if frozen.get("mutation_after_freeze") is not False:
        raise SemanticContextContractError("semantic_mutation_after_freeze_forbidden")
    if not isinstance(frozen.get("required_fields"), list) or len(frozen["required_fields"]) < 8:
        raise SemanticContextContractError("semantic_required_fields_incomplete")
    _text(frozen.get("failure_rule"), "frozen_semantic_contract.failure_rule")

    target = contract.get("typed_target_set")
    if not isinstance(target, dict):
        raise SemanticContextContractError("typed_target_set_missing")
    if set(target.get("allowed_relation_kinds") or []) != REQUIRED_TARGET_RELATIONS:
        raise SemanticContextContractError("typed_target_relation_set_invalid")
    for field in (
        "authority_location",
        "ambiguity_rule",
        "same_effect_scope_rule",
        "correction_rule",
        "requested_effect_change_rule",
    ):
        _text(target.get(field), f"typed_target_set.{field}")

    context = contract.get("context_evidence_projection")
    if not isinstance(context, dict):
        raise SemanticContextContractError("context_evidence_projection_missing")
    if context.get("hard_flags") != REQUIRED_HARD_FLAGS:
        raise SemanticContextContractError("context_hard_flags_invalid")
    if not isinstance(context.get("components"), list) or len(context["components"]) < 8:
        raise SemanticContextContractError("context_component_set_incomplete")
    _text(context.get("rebuild_rule"), "context_evidence_projection.rebuild_rule")
    _text(context.get("availability_rule"), "context_evidence_projection.availability_rule")

    visible = contract.get("visible_result_ref")
    if not isinstance(visible, dict):
        raise SemanticContextContractError("visible_result_ref_missing")
    if not isinstance(visible.get("not_meaning"), list) or "automatic target" not in visible["not_meaning"]:
        raise SemanticContextContractError("visible_result_ref_non_authority_incomplete")
    if not isinstance(visible.get("validation"), list) or len(visible["validation"]) < 7:
        raise SemanticContextContractError("visible_result_ref_validation_incomplete")

    referents = contract.get("visible_referent_sets")
    if not isinstance(referents, dict):
        raise SemanticContextContractError("visible_referent_sets_missing")
    if referents.get("authority") != "read_only_discourse_projection" or referents.get("dispatchable") is not False:
        raise SemanticContextContractError("referent_set_must_be_read_only_and_non_dispatchable")
    if referents.get("selection_policy") != "model_proposes_exact_refs_runtime_validates":
        raise SemanticContextContractError("referent_set_selection_policy_invalid")

    lifecycle = contract.get("goal_and_focus_lifecycle")
    if not isinstance(lifecycle, dict):
        raise SemanticContextContractError("goal_and_focus_lifecycle_missing")
    if set(lifecycle.get("allowed_goal_change_operations") or []) != REQUIRED_GOAL_CHANGES:
        raise SemanticContextContractError("goal_change_operation_set_invalid")
    if set(lifecycle.get("allowed_focus_operations") or []) != REQUIRED_FOCUS_CHANGES:
        raise SemanticContextContractError("focus_change_operation_set_invalid")
    _text(lifecycle.get("focus_rule"), "goal_and_focus_lifecycle.focus_rule")

    separation = contract.get("separation_boundaries")
    if not isinstance(separation, dict) or set(separation) != {"wp02a", "wp03", "wp04", "wp05", "wp06"}:
        raise SemanticContextContractError("separation_boundary_set_invalid")
    for key, value in separation.items():
        _text(value, f"separation_boundaries.{key}")

    legacy = contract.get("legacy_policy")
    if not isinstance(legacy, dict):
        raise SemanticContextContractError("legacy_policy_missing")
    if set(legacy.get("forbidden") or []) != REQUIRED_LEGACY_FORBIDDEN:
        raise SemanticContextContractError("legacy_forbidden_set_invalid")

    if set(contract.get("acceptance_tests") or []) != REQUIRED_ACCEPTANCE:
        raise SemanticContextContractError("acceptance_test_set_invalid")

    scope = contract.get("implementation_scope")
    if not isinstance(scope, dict):
        raise SemanticContextContractError("implementation_scope_missing")
    forbidden = set(scope.get("forbidden_paths") or [])
    if "services/agent-service/src/agent_core/persistence/turn_request_store.py" not in forbidden:
        raise SemanticContextContractError("wp02a_request_store_must_be_forbidden")
    if not isinstance(scope.get("allowed_paths"), list) or not scope["allowed_paths"]:
        raise SemanticContextContractError("implementation_allowed_paths_missing")

    for reference in (
        "ContextEvidenceProjection",
        "TurnSemanticContract",
        "TypedTargetSet",
        "VisibleResultRef",
        "SourceEffect",
        "WP-02A",
        "WP-02B",
        "MatchProof",
        "grounded_execution_plan" if False else "RuntimeOutcome",
    ):
        if reference not in doc:
            raise SemanticContextContractError(f"documentation_reference_missing:{reference}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("governance/architecture/b30-semantic-context-authority.json"),
    )
    parser.add_argument(
        "--doc",
        type=Path,
        default=Path("docs/architecture/B30_SEMANTIC_CONTEXT_AUTHORITY.md"),
    )
    args = parser.parse_args()
    try:
        validate(args.contract, args.doc)
    except (SemanticContextContractError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "stage": "B30", "work_package": "WP-02B"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
