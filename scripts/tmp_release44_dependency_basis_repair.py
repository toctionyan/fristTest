#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
TEST_PATH = "services/agent-service/tests/runtime/test_release44_dependency_basis_grounding.py"
TRIGGER_PATH = ".github/release-trigger"
BASELINE_PATH = "skill-system/registry/product-source-baseline.json"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATH

    anchor = '''def _model_alignment_dependency_proof(\n'''
    helper = '''def _requested_output_evidence_spans(goal: dict[str, Any]) -> tuple[str, ...]:\n    \"\"\"Return canonical requested-output evidence spans already frozen by the candidate.\n\n    These spans identify the user-visible outcome itself.  They are structural\n    evidence only; Runtime does not interpret their language or output IDs.\n    \"\"\"\n    effect = goal.get(\"requested_effect\") if isinstance(goal.get(\"requested_effect\"), dict) else {}\n    rows: list[str] = []\n    for raw in list(effect.get(\"requested_outputs\") or []):\n        if not isinstance(raw, dict):\n            continue\n        span = _clean_text(raw.get(\"evidence_span\"), limit=240)\n        if span and span not in rows:\n            rows.append(span)\n    return tuple(rows)\n\n\ndef _dependency_basis_overlaps_requested_output(goal: dict[str, Any], basis_span: str) -> bool:\n    \"\"\"Reject dependency proof text that is actually outcome/action evidence.\n\n    A dependency basis must isolate the relation that consumes an earlier Goal\n    result.  A span that is inside a requested-output span, or merely wraps that\n    output span with control/action wording, does not independently prove a\n    result-reference/condition/value relation.  This check is domain-neutral and\n    purely structural: it compares already-declared literal evidence spans.\n    \"\"\"\n    basis = _clean_text(basis_span, limit=240)\n    if not basis:\n        return False\n    return any(\n        basis in output_span or output_span in basis\n        for output_span in _requested_output_evidence_spans(goal)\n    )\n\n\n'''
    replace_once(path, anchor, helper + anchor, "insert requested-output dependency basis guard")

    first_old = '''        if (\n            not basis_span\n            or basis_span not in user_text\n            or not dependent_span\n            or basis_span not in dependent_span\n        ):\n            return base_details, f\"goal_alignment_dependency_basis_not_in_dependent_goal:{edge_index}\"\n        edge = (dependent, prerequisite)\n'''
    first_new = '''        if (\n            not basis_span\n            or basis_span not in user_text\n            or not dependent_span\n            or basis_span not in dependent_span\n        ):\n            return base_details, f\"goal_alignment_dependency_basis_not_in_dependent_goal:{edge_index}\"\n        if _dependency_basis_overlaps_requested_output(goal_by_id[dependent], basis_span):\n            return base_details, f\"goal_alignment_dependency_basis_overlaps_requested_output:{edge_index}\"\n        edge = (dependent, prerequisite)\n'''
    replace_once(path, first_old, first_new, "candidate-visible dependency basis guard")

    blind_old = '''            dependent_requested_effect = goal_by_id[dependent].get(\"requested_effect\")\n            dependent_requested_outputs = (dependent_requested_effect.get(\"requested_outputs\") if isinstance(dependent_requested_effect, dict) else [])\n            requested_output_spans = {\n                _clean_text(row.get(\"evidence_span\"), limit=240)\n                for row in list(dependent_requested_outputs or [])\n                if isinstance(row, dict) and _clean_text(row.get(\"evidence_span\"), limit=240)\n            }\n            if any(basis_span in output_span for output_span in requested_output_spans):\n                return base_details, f\"goal_alignment_dependency_basis_is_requested_output:{index}\"\n            edge = (dependent, prerequisite)\n'''
    blind_new = '''            if _dependency_basis_overlaps_requested_output(goal_by_id[dependent], basis_span):\n                return base_details, f\"goal_alignment_dependency_basis_overlaps_requested_output:{index}\"\n            edge = (dependent, prerequisite)\n'''
    replace_once(path, blind_old, blind_new, "candidate-blind dependency basis guard")

    rule_old = '''            \"dependency basis evidence must identify the result-reference, result-condition or result-value-input relation itself; \"\n            \"if a proposed basis_span is only or wholly inside the dependent Goal requested_outputs evidence_span, that phrase proves only the requested output and the pair must be independent\",\n'''
    rule_new = '''            \"dependency basis evidence must identify only the result-reference, result-condition or result-value-input relation itself; \"\n            \"it must be disjoint from the dependent Goal requested_outputs evidence spans. A basis inside requested-output evidence, or a broader phrase that wraps requested-output evidence with control/action wording, proves the requested outcome rather than a result dependency and must be rejected; use a relation-only literal basis when one exists, otherwise the pair is independent\",\n'''
    replace_once(path, rule_old, rule_new, "blind dependency evidence rule")

    repair_old = '''                    \"when a literal basis_span inside the dependent Goal proves result_reference, result_condition or result_value_input; \"\n                    \"otherwise return relation=independent. Return the complete dependency_decisions array and the strict JSON fields only.\"\n'''
    repair_new = '''                    \"when a relation-only literal basis_span inside the dependent Goal proves result_reference, result_condition or result_value_input. \"\n                    \"The basis must not be requested-output evidence and must not wrap a requested-output evidence span with action/control wording; \"\n                    \"if no disjoint relation-only basis exists, return relation=independent. Return the complete dependency_decisions array and the strict JSON fields only.\"\n'''
    replace_once(path, repair_old, repair_new, "dependency format repair grounding rule")


def write_regression(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"regression file already exists: {TEST_PATH}")
    path.write_text(r'''from __future__ import annotations

from agent_core.lifecycle.goal_planning import (
    _model_alignment_dependency_proof,
    _model_alignment_pairwise_dependency_proof,
)


def _goal(
    goal_id: str,
    span: str,
    depends_on: list[str],
    *,
    output_span: str | None = None,
) -> dict:
    requested_effect = {
        "domain": "open",
        "operation": "open",
        "object_type": "order",
        "raw_description": span,
    }
    if output_span is not None:
        requested_effect["requested_outputs"] = [{
            "output_id": "open",
            "evidence_span": output_span,
            "open_description": "domain-neutral requested outcome",
        }]
    return {
        "goal_id": goal_id,
        "evidence_span": span,
        "depends_on": depends_on,
        "requested_effect": requested_effect,
    }


def test_release44_action_phrase_cannot_self_certify_as_result_dependency_basis() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal("g2", "帮我申请退款", ["g1"], output_span="申请退款"),
    ]
    edge = [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "帮我申请退款",
    }]
    details, error = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=edge,
    )
    assert details["dependency_proof_complete"] is False
    assert error == "goal_alignment_dependency_basis_overlaps_requested_output:0"


def test_release44_blind_action_phrase_cannot_self_certify_as_result_dependency_basis() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal("g2", "帮我申请退款", ["g1"], output_span="申请退款"),
    ]
    decisions = [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_value_input",
        "basis_span": "帮我申请退款",
    }]
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=goals,
        values=decisions,
    )
    assert details["dependency_proof_complete"] is False
    assert error == "goal_alignment_dependency_basis_overlaps_requested_output:0"


def test_relation_only_reference_stays_valid_when_disjoint_from_requested_output() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [
        _goal("g1", "查一下键盘订单", []),
        _goal("g2", "再看看它能不能退款", ["g1"], output_span="能不能退款"),
    ]
    edge = [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }]
    details, error = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=edge,
    )
    assert error is None
    assert details["dependency_proof_complete"] is True
    assert details["dependency_graph_match"] is True

    blind, blind_error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
    )
    assert blind_error is None
    assert blind["dependency_proof_complete"] is True
    assert blind["dependency_graph_match"] is True


def test_broad_true_relation_basis_must_be_narrowed_instead_of_wrapping_output() -> None:
    text = "Inspect record A, then use that result to open a service request"
    goals = [
        _goal("g1", "Inspect record A", []),
        _goal(
            "g2",
            "use that result to open a service request",
            ["g1"],
            output_span="open a service request",
        ),
    ]
    details, error = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "use that result to open a service request",
        }],
    )
    assert details["dependency_proof_complete"] is False
    assert error == "goal_alignment_dependency_basis_overlaps_requested_output:0"
''', encoding="utf-8")


def patch_release_trigger(root: Path) -> None:
    (root / TRIGGER_PATH).write_text(
        "release_request: 2026-08-15T18:05:00+08:00\n"
        "provider: deepseek\n"
        "model: deepseek-v4-flash\n"
        "embedding_model: text-embedding-v4\n"
        "embedding_dimension: 1024\n"
        "reason: rerun protected release after relation-only dependency basis grounding repair\n",
        encoding="utf-8",
    )


def patch(root: Path) -> None:
    patch_goal_planning(root)
    write_regression(root)
    patch_release_trigger(root)


def baseline(root: Path, product_sha: str) -> None:
    path = root / BASELINE_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict):
        raise SystemExit("protected baseline files map is missing")
    updated: list[str] = []
    for rel in (SOURCE_PATH, TEST_PATH):
        if rel in files:
            files[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
            updated.append(rel)
    if SOURCE_PATH not in updated:
        raise SystemExit(f"protected baseline does not own required source {SOURCE_PATH}")
    payload["file_count"] = len(files)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = "git:" + product_sha
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"updated": updated, "file_count": len(files)}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_patch = sub.add_parser("patch")
    p_patch.add_argument("--workspace", required=True)
    p_baseline = sub.add_parser("baseline")
    p_baseline.add_argument("--workspace", required=True)
    p_baseline.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        baseline(root, str(args.product_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
