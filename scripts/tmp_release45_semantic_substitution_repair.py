#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
TEST_PATH = "services/agent-service/tests/runtime/test_requested_output_exactness_redeclaration_feedback.py"
TRIGGER_PATH = ".github/release-trigger"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATH
    old = '''    requested_output_mismatch = (\n        "requested_effect" in normalized_reason\n        and any(marker in normalized_reason for marker in ("fidelity", "faithful", "business_effect", "semantic"))\n    )\n'''
    new = '''    requested_output_mismatch = (\n        (\n            "requested_effect" in normalized_reason\n            and any(marker in normalized_reason for marker in ("fidelity", "faithful", "business_effect", "semantic"))\n        )\n        # The verifier prompt reserves this exact reason for a registered\n        # requested_outputs identity that semantically substitutes a different\n        # user-visible outcome. Release #45 proved the provider may use the\n        # concise reserved reason instead of the longer requested_effect_* form.\n        or normalized_reason == "semantic_substitution"\n    )\n'''
    replace_once(path, old, new, "requested output mismatch reason normalization")


def patch_regressions(root: Path) -> None:
    path = root / TEST_PATH
    text = path.read_text(encoding="utf-8")
    marker = "test_release45_semantic_substitution_reason_routes_to_requested_output_redeclaration"
    if marker in text:
        raise SystemExit("Release 45 regression already present")
    addition = r'''


def _release45_semantic_substitution_mismatch() -> GoalAlignmentVerdict:
    return GoalAlignmentVerdict(
        "incomplete",
        ("鼠标订单的退款什么时候到账",),
        ("鼠标订单的退款什么时候到账",),
        "semantic_substitution",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
            "verifier_repair_attempted": False,
            "verifier_repair_kind": "",
        },
    )


def test_release45_semantic_substitution_reason_routes_to_requested_output_redeclaration():
    alignment = _release45_semantic_substitution_mismatch()
    row = _alignment_repair_feedback(alignment)["independent_verifier_feedback"]
    assert row["required_action"] == "redeclaration_rederiving_requested_outputs"
    assert row["violation_field"] == "requested_effect.requested_outputs"
    assert row["invalid_requested_output_spans"] == ["鼠标订单的退款什么时候到账"]

    result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": "鼠标订单的退款什么时候到账",
            "repair_contract": {"authority": "current_user_input_only", "required_action": "redeclaration"},
        },
    }
    projected = _semantic_writer_declaration_result_projection(result)
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"] == {
        "field": "requested_effect.requested_outputs",
        "reason_code": "semantic_substitution",
        "evidence_spans": ["鼠标订单的退款什么时候到账"],
    }
    assert "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists" in feedback["constraints"]
    assert "refund.status" not in str(projected)


def test_release45_unrelated_semantic_reason_is_not_reclassified_as_requested_output_mismatch():
    alignment = GoalAlignmentVerdict(
        "incomplete",
        ("鼠标订单",),
        ("鼠标订单",),
        "semantic_scope_mismatch",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
        },
    )
    assert _alignment_repair_feedback(alignment) == {}
'''
    path.write_text(text + addition, encoding="utf-8")


def patch_release_trigger(root: Path) -> None:
    (root / TRIGGER_PATH).write_text(
        "release_request: 2026-08-15T18:20:00+08:00\n"
        "provider: deepseek\n"
        "model: deepseek-v4-flash\n"
        "embedding_model: text-embedding-v4\n"
        "embedding_dimension: 1024\n"
        "reason: rerun protected release after semantic-substitution requested-output repair feedback normalization\n",
        encoding="utf-8",
    )


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_regressions(root)
    patch_release_trigger(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    patch(Path(args.workspace).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
