#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
TEST_PATH = "services/agent-service/tests/runtime/test_wp08_attempt5_dependency_authority.py"
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
    old = '''                if (
                    raw_verdict == "incomplete"
                    and dependency_error == "goal_alignment_dependency_graph_mismatch"
                ):
                    evidence = _literal_spans(user_text, parsed.get("evidence_spans"))
                    if evidence:
                        verdict = GoalAlignmentVerdict(
                            "incomplete",
                            evidence,
                            _literal_spans(user_text, parsed.get("missing_spans")),
                            "goal_alignment_dependency_graph_mismatch",
                            "model",
                            True,
                            dependency_details,
                        )
                    else:
                        verdict = GoalAlignmentVerdict(
                            "indeterminate",
                            (),
                            (),
                            "goal_alignment_dependency_mismatch_without_literal_evidence",
                            "model",
                            True,
                            dependency_details,
                        )
                elif raw_verdict == "exact" and dependency_error == "goal_alignment_dependency_graph_mismatch":
                    evidence = _literal_spans(user_text, parsed.get("evidence_spans"))
                    if blind_dependency_audit and evidence:
                        verdict = GoalAlignmentVerdict(
                            "incomplete",
                            evidence,
                            (),
                            "goal_alignment_dependency_graph_mismatch",
                            "model",
                            True,
                            {**dependency_details, "candidate_blind_dependency_reaudit": True},
                        )
                    elif blind_dependency_audit:
                        verdict = GoalAlignmentVerdict(
                            "indeterminate",
                            (),
                            (),
                            "goal_alignment_dependency_mismatch_without_literal_evidence",
                            "model",
                            True,
                            {**dependency_details, "candidate_blind_dependency_reaudit": True},
                        )
                    else:
                        verdict = GoalAlignmentVerdict(
                            "indeterminate",
                            evidence,
                            (),
                            "goal_alignment_dependency_exact_contradiction",
                            "model",
                            True,
                            dependency_details,
                        )
'''
    new = '''                if (
                    raw_verdict == "incomplete"
                    and dependency_error == "goal_alignment_dependency_graph_mismatch"
                ):
                    # A complete, structurally validated dependency proof can disprove
                    # the candidate graph without restating outcome-level evidence_spans.
                    # This remains fail-closed: the declaration is rejected as repairable
                    # incomplete, never accepted as exact, and Runtime still does not
                    # infer or rewrite any dependency edge itself.
                    evidence = _literal_spans(user_text, parsed.get("evidence_spans"))
                    verdict = GoalAlignmentVerdict(
                        "incomplete",
                        evidence,
                        _literal_spans(user_text, parsed.get("missing_spans")),
                        "goal_alignment_dependency_graph_mismatch",
                        "model",
                        True,
                        {
                            **dependency_details,
                            "dependency_mismatch_grounding": "machine_dependency_proof",
                        },
                    )
                elif raw_verdict == "exact" and dependency_error == "goal_alignment_dependency_graph_mismatch":
                    evidence = _literal_spans(user_text, parsed.get("evidence_spans"))
                    if blind_dependency_audit:
                        # The candidate-blind proof is itself the authority that the
                        # declared graph is wrong. Missing redundant top-level evidence
                        # must not turn a proven mismatch into an unrepairable format
                        # failure. Empty evidence still cannot certify an exact plan.
                        verdict = GoalAlignmentVerdict(
                            "incomplete",
                            evidence,
                            (),
                            "goal_alignment_dependency_graph_mismatch",
                            "model",
                            True,
                            {
                                **dependency_details,
                                "candidate_blind_dependency_reaudit": True,
                                "dependency_mismatch_grounding": "machine_dependency_proof",
                            },
                        )
                    else:
                        verdict = GoalAlignmentVerdict(
                            "indeterminate",
                            evidence,
                            (),
                            "goal_alignment_dependency_exact_contradiction",
                            "model",
                            True,
                            dependency_details,
                        )
'''
    replace_once(path, old, new, "dependency mismatch classification")

    old_comment = '''                # Both entry paths have already passed literal evidence grounding: either the
                # first verdict is exact, or its only authoritative disagreement is a
                # structurally valid dependency graph that introduces a new edge. Keep that
                # grounded outcome evidence available while the blind audit repairs graph format.
                initial_grounded_alignment = verdict
'''
    new_comment = '''                # Both entry paths are safe to send to the candidate-blind graph audit.
                # An exact verdict already carries literal outcome evidence; a dependency-only
                # mismatch may instead be grounded solely by the machine-validated graph proof.
                # Preserve whatever outcome evidence exists, but never use an empty tuple to
                # certify exactness later: the outer normalizer still fails exact-without-evidence closed.
                initial_grounded_alignment = verdict
'''
    replace_once(path, old_comment, new_comment, "dependency blind-audit grounding comment")


def patch_regressions(root: Path) -> None:
    path = root / TEST_PATH
    text = path.read_text(encoding="utf-8")
    marker = "test_release43_missing_dependency_without_redundant_outcome_evidence_is_repairable"
    if marker in text:
        raise SystemExit("Release 43 regression already present")
    addition = r'''


def test_release43_missing_dependency_without_redundant_outcome_evidence_is_repairable() -> None:
    """Release #43: a machine-grounded graph mismatch must reach redeclaration."""
    from agent_core.lifecycle.goal_planning import _alignment_repair_feedback

    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", [])]
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    first = _response({
        "verdict": "incomplete",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_edges": [edge],
        "reason_code": "missing_true_result_dependency",
    })
    blind = _response({
        "verdict": "exact",
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "candidate_blind_true_result_reference",
    })

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.evidence_spans == ()
    assert verdict.missing_spans == ()
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == [edge]
    assert verdict.details["dependency_mismatch_grounding"] == "machine_dependency_proof"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"

    feedback = _alignment_repair_feedback(verdict)["independent_verifier_feedback"]
    assert feedback["required_action"] == "redeclaration_preserving_grounded_dependency_graph"
    assert feedback["dependency_edges"] == [edge]
    assert feedback["candidate_declared_dependency_edges"] == []


def test_release43_machine_dependency_proof_never_certifies_exact_without_outcome_evidence() -> None:
    """The repair changes rejection classification only; exact still needs literal outcome evidence."""
    from agent_core.lifecycle.goal_planning import GoalAlignmentVerdict, _as_alignment_verdict

    text = "查一下键盘订单，再看看它能不能退款"
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    normalized = _as_alignment_verdict(
        GoalAlignmentVerdict(
            "exact",
            (),
            (),
            "all_requested_outcomes_and_dependency_preserved",
            "model",
            True,
            {
                "dependency_authority": "independent_goal_alignment",
                "dependency_proof_complete": True,
                "dependency_graph_match": True,
                "declared_dependency_edges": [{
                    "dependent_goal_id": "g2",
                    "requires_result_of_goal_id": "g1",
                }],
                "dependency_edges": [edge],
            },
        ),
        user_text=text,
        source="model",
        independent=True,
    )
    assert normalized.verdict == "indeterminate"
    assert normalized.reason_code == "goal_alignment_evidence_not_in_current_user_text"
'''
    path.write_text(text + addition, encoding="utf-8")


def patch_release_trigger(root: Path) -> None:
    path = root / TRIGGER_PATH
    path.write_text(
        "release_request: 2026-08-15T17:55:00+08:00\n"
        "provider: deepseek\n"
        "model: deepseek-v4-flash\n"
        "embedding_model: text-embedding-v4\n"
        "embedding_dimension: 1024\n"
        "reason: rerun protected release after machine-grounded dependency-mismatch redeclaration repair\n",
        encoding="utf-8",
    )


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_regressions(root)
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
