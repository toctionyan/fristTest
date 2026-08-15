#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

RUNTIME_PATH = "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
HARNESS_PATH = "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
TEST_PATH = "services/agent-service/tests/runtime/test_release47_dependency_repair_projection.py"
TRIGGER_PATH = ".github/release-trigger"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_runtime_projection(root: Path) -> None:
    path = root / RUNTIME_PATH
    old_state = '''    spans: list[str] = []\n    reason_code = ""\n    field = "semantic_declaration"\n'''
    new_state = '''    spans: list[str] = []\n    reason_code = ""\n    field = "semantic_declaration"\n    dependency_delta_kind = ""\n'''
    replace_once(path, old_state, new_state, "dependency projection state")

    old_after_edges = '''    for edge in list(feedback.get("dependency_edges") or []):\n        if not isinstance(edge, dict):\n            continue\n        for key in ("basis_span", "dependent_span", "requires_result_of_span"):\n            span = str(edge.get(key) or "").strip()\n            if span and span not in spans:\n                spans.append(span)\n\n    if feedback or reason_code or spans:\n'''
    new_after_edges = '''    for edge in list(feedback.get("dependency_edges") or []):\n        if not isinstance(edge, dict):\n            continue\n        for key in ("basis_span", "dependent_span", "requires_result_of_span"):\n            span = str(edge.get(key) or "").strip()\n            if span and span not in spans:\n                spans.append(span)\n\n    if field == "depends_on" and reason_code == "goal_alignment_dependency_graph_mismatch":\n        # Keep the independently verified graph itself out of provider-facing\n        # repair messages, but do not collapse a directional graph delta into an\n        # ambiguous generic field error.  The writer may learn only whether its\n        # candidate omitted a grounded relation, asserted an unproved relation,\n        # or did both; it must still rederive the actual edge from current input.\n        verified_pairs = {\n            (\n                str(edge.get("dependent_goal_id") or "").strip(),\n                str(edge.get("requires_result_of_goal_id") or "").strip(),\n            )\n            for edge in list(feedback.get("dependency_edges") or [])\n            if isinstance(edge, dict)\n            and str(edge.get("dependent_goal_id") or "").strip()\n            and str(edge.get("requires_result_of_goal_id") or "").strip()\n        }\n        declared_pairs = {\n            (\n                str(edge.get("dependent_goal_id") or "").strip(),\n                str(edge.get("requires_result_of_goal_id") or "").strip(),\n            )\n            for edge in list(feedback.get("candidate_declared_dependency_edges") or [])\n            if isinstance(edge, dict)\n            and str(edge.get("dependent_goal_id") or "").strip()\n            and str(edge.get("requires_result_of_goal_id") or "").strip()\n        }\n        missing_pairs = verified_pairs - declared_pairs\n        unproved_pairs = declared_pairs - verified_pairs\n        if missing_pairs and unproved_pairs:\n            dependency_delta_kind = "mixed_relation_delta"\n        elif missing_pairs:\n            dependency_delta_kind = "missing_grounded_relation"\n        elif unproved_pairs:\n            dependency_delta_kind = "unproved_declared_relation"\n\n    if feedback or reason_code or spans:\n'''
    replace_once(path, old_after_edges, new_after_edges, "dependency delta derivation")

    old_constraints = '''        if field == "target_candidate.scope_constraints":\n            writer_constraints.insert(0,\n                "remove_only_listed_invalid_scope_constraint_entries_and_preserve_other_literal_population_narrowing_constraints"\n            )\n        elif field == "requested_effect.requested_outputs":\n            writer_constraints.insert(0,\n                "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists"\n            )\n        projected_data["independent_verifier_feedback"] = {\n            "authority": "read_only_violation_evidence",\n            "required_action": "redeclaration_from_current_user_input",\n            "violation": {\n                "field": field,\n                "reason_code": reason_code or str(payload.get("code") or "semantic_declaration_rejected"),\n                "evidence_spans": spans,\n            },\n            "constraints": writer_constraints,\n        }\n'''
    new_constraints = '''        if field == "target_candidate.scope_constraints":\n            writer_constraints.insert(0,\n                "remove_only_listed_invalid_scope_constraint_entries_and_preserve_other_literal_population_narrowing_constraints"\n            )\n        elif field == "requested_effect.requested_outputs":\n            writer_constraints.insert(0,\n                "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists"\n            )\n        elif field == "depends_on":\n            dependency_constraints = [\n                "rederive_complete_depends_on_graph_from_current_user_input_only",\n                "explicit_same_turn_result_reference_result_condition_or_result_value_input_requires_depends_on",\n                "sequence_shared_topic_zero_anaphora_and_execution_support_dataflow_do_not_create_depends_on",\n                "dependency_delta_kind_reports_only_candidate_graph_error_polarity_and_never_identifies_an_edge_to_copy",\n            ]\n            if dependency_delta_kind == "missing_grounded_relation":\n                dependency_constraints.insert(0,\n                    "candidate_is_missing_at_least_one_grounded_relation_rederive_and_add_only_relations_proved_by_current_user_input"\n                )\n            elif dependency_delta_kind == "unproved_declared_relation":\n                dependency_constraints.insert(0,\n                    "candidate_contains_at_least_one_unproved_relation_rederive_and_remove_only_relations_not_proved_by_current_user_input"\n                )\n            elif dependency_delta_kind == "mixed_relation_delta":\n                dependency_constraints.insert(0,\n                    "candidate_both_omits_and_asserts_dependency_relations_rederive_the_complete_graph_from_current_user_input"\n                )\n            writer_constraints = [*dependency_constraints, *writer_constraints]\n        violation = {\n            "field": field,\n            "reason_code": reason_code or str(payload.get("code") or "semantic_declaration_rejected"),\n            "evidence_spans": spans,\n        }\n        if dependency_delta_kind:\n            violation["dependency_delta_kind"] = dependency_delta_kind\n        projected_data["independent_verifier_feedback"] = {\n            "authority": "read_only_violation_evidence",\n            "required_action": "redeclaration_from_current_user_input",\n            "violation": violation,\n            "constraints": writer_constraints,\n        }\n'''
    replace_once(path, old_constraints, new_constraints, "field-specific dependency writer repair")


def patch_harness_diagnostic(root: Path) -> None:
    path = root / HARNESS_PATH
    old = '''            "dependency_edges": [\n                {\n                    "dependent_span": str(row.get("dependent_span") or ""),\n                    "requires_result_of_span": str(row.get("requires_result_of_span") or ""),\n                }\n                for row in list(feedback.get("dependency_edges") or [])\n                if isinstance(row, dict)\n            ][:8],\n'''
    new = '''            "dependency_edges": [\n                {\n                    key: str(row.get(key) or "")\n                    for key in (\n                        "dependent_goal_id",\n                        "requires_result_of_goal_id",\n                        "basis_kind",\n                        "basis_span",\n                        "dependent_span",\n                        "requires_result_of_span",\n                    )\n                    if str(row.get(key) or "")\n                }\n                for row in list(feedback.get("dependency_edges") or [])\n                if isinstance(row, dict)\n            ][:8],\n'''
    replace_once(path, old, new, "dependency diagnostic edge shape")


def write_regression(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"regression already exists: {TEST_PATH}")
    path.write_text(r'''from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection
from agent_core.lifecycle.goal_planning import GoalAlignmentVerdict, _alignment_repair_feedback


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_preprod_conversation_smoke.py"
SPEC = importlib.util.spec_from_file_location("release47_preprod_semantic_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def _edge() -> dict[str, str]:
    return {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }


def _mismatch_result(*, verified: list[dict], declared: list[dict], user_text: str) -> dict:
    alignment = GoalAlignmentVerdict(
        "incomplete",
        (),
        (),
        "goal_alignment_dependency_graph_mismatch",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": False,
            "dependency_edges": verified,
            "declared_dependency_edges": declared,
            "verifier_repair_attempted": True,
            "verifier_repair_kind": "candidate_blind_dependency_reaudit",
        },
    )
    return {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": user_text,
            "repair_contract": {
                "authority": "current_user_input_only",
                "required_action": "redeclaration",
            },
        },
    }


def test_release47_missing_true_dependency_keeps_polarity_without_leaking_edge_ids():
    raw = _mismatch_result(
        verified=[_edge()],
        declared=[],
        user_text="查一下键盘订单，再看看它能不能退款",
    )
    projected = _semantic_writer_declaration_result_projection(raw)
    feedback = projected["data"]["independent_verifier_feedback"]

    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["required_action"] == "redeclaration_from_current_user_input"
    assert feedback["violation"] == {
        "field": "depends_on",
        "reason_code": "goal_alignment_dependency_graph_mismatch",
        "evidence_spans": ["它"],
        "dependency_delta_kind": "missing_grounded_relation",
    }
    assert (
        "candidate_is_missing_at_least_one_grounded_relation_rederive_and_add_only_relations_proved_by_current_user_input"
        in feedback["constraints"]
    )
    assert "explicit_same_turn_result_reference_result_condition_or_result_value_input_requires_depends_on" in feedback["constraints"]

    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    assert "dependent_goal_id" not in serialized
    assert "requires_result_of_goal_id" not in serialized
    assert "candidate_declared_dependency_edges" not in serialized
    assert '"g1"' not in serialized
    assert '"g2"' not in serialized


def test_release47_false_declared_dependency_keeps_opposite_polarity_without_replacement_graph():
    raw = _mismatch_result(
        verified=[],
        declared=[{"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}],
        user_text="查一下鼠标订单，然后帮我申请退款",
    )
    projected = _semantic_writer_declaration_result_projection(raw)
    feedback = projected["data"]["independent_verifier_feedback"]

    assert feedback["violation"] == {
        "field": "depends_on",
        "reason_code": "goal_alignment_dependency_graph_mismatch",
        "evidence_spans": [],
        "dependency_delta_kind": "unproved_declared_relation",
    }
    assert (
        "candidate_contains_at_least_one_unproved_relation_rederive_and_remove_only_relations_not_proved_by_current_user_input"
        in feedback["constraints"]
    )
    assert "sequence_shared_topic_zero_anaphora_and_execution_support_dataflow_do_not_create_depends_on" in feedback["constraints"]
    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    assert "dependent_goal_id" not in serialized
    assert "requires_result_of_goal_id" not in serialized
    assert '"g1"' not in serialized
    assert '"g2"' not in serialized


def test_release47_internal_certification_diagnostic_no_longer_renders_alignment_edges_as_blank_span_rows():
    raw = _mismatch_result(
        verified=[_edge()],
        declared=[],
        user_text="查一下键盘订单，再看看它能不能退款",
    )
    diagnostic = HARNESS._sanitized_goal_rejection_diagnostic(raw)

    assert diagnostic["independent_verifier_feedback"]["dependency_edges"] == [_edge()]
    assert diagnostic["alignment"]["dependency_edges"] == [_edge()]
''', encoding="utf-8")


def patch_release_trigger(root: Path) -> None:
    (root / TRIGGER_PATH).write_text(
        "release_request: 2026-08-15T20:05:00+08:00\n"
        "provider: deepseek\n"
        "model: deepseek-v4-flash\n"
        "embedding_model: text-embedding-v4\n"
        "embedding_dimension: 1024\n"
        "reason: rerun protected release after dependency graph repair polarity projection\n",
        encoding="utf-8",
    )


def patch(root: Path) -> None:
    patch_runtime_projection(root)
    patch_harness_diagnostic(root)
    write_regression(root)
    patch_release_trigger(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    patch(Path(args.workspace).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
