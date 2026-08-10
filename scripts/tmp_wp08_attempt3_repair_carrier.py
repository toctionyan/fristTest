#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def apply_patch(root: Path) -> None:
    goal_path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    text = _read(goal_path)

    exact_anchor = (
        '            if attempt == 0 and verdict.exact and len(goals) > 1:\n'
        '                initial_exact_alignment = verdict\n'
    )
    if text.count(exact_anchor) != 1:
        raise SystemExit("goal_planning exact blind-reaudit anchor mismatch")
    exact_replacement = ''.join([
        '            dependency_mismatch_introduces_new_edge = False\n',
        '            if (\n',
        '                attempt == 0\n',
        '                and verdict.verdict == "incomplete"\n',
        '                and verdict.reason_code == "goal_alignment_dependency_graph_mismatch"\n',
        '            ):\n',
        '                details = verdict.details if isinstance(verdict.details, dict) else {}\n',
        '                declared_pairs = {\n',
        '                    (str(row.get("dependent_goal_id") or ""), str(row.get("requires_result_of_goal_id") or ""))\n',
        '                    for row in list(details.get("declared_dependency_edges") or [])\n',
        '                    if isinstance(row, dict)\n',
        '                }\n',
        '                verified_pairs = {\n',
        '                    (str(row.get("dependent_goal_id") or ""), str(row.get("requires_result_of_goal_id") or ""))\n',
        '                    for row in list(details.get("dependency_edges") or [])\n',
        '                    if isinstance(row, dict)\n',
        '                }\n',
        '                dependency_mismatch_introduces_new_edge = bool(verified_pairs - declared_pairs)\n',
        '            if (\n',
        '                attempt == 0\n',
        '                and len(goals) > 1\n',
        '                and (verdict.exact or dependency_mismatch_introduces_new_edge)\n',
        '            ):\n',
        '                if verdict.exact:\n',
        '                    initial_exact_alignment = verdict\n',
    ])
    text = text.replace(exact_anchor, exact_replacement, 1)

    dependency_start = (
        '                elif verdict.reason_code.startswith("goal_alignment_dependency_"):\n'
        '                    verifier_repair_kind = "dependency_proof_reaudit"\n'
    )
    dependency_end = (
        '                else:\n'
        '                    verifier_repair_kind = "machine_format_repair"\n'
    )
    start = text.find(dependency_start)
    end = text.find(dependency_end, start + 1)
    if start < 0 or end < 0:
        raise SystemExit("goal_planning dependency repair branch boundaries missing")
    replacement = ''.join([
        '                elif verdict.reason_code.startswith("goal_alignment_dependency_"):\n',
        '                    # A malformed or contradictory candidate-visible dependency proof\n',
        '                    # cannot be repaired by showing the same candidate graph again.\n',
        '                    # Spend the bounded second call on the graph-blind pairwise audit.\n',
        '                    verifier_repair_kind = "candidate_blind_dependency_reaudit"\n',
        '                    verifier_repair = None\n',
        '                    prompt = {\n',
        '                        "USER_TEXT_UNTRUSTED": user_text,\n',
        '                        "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n',
        '                        "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n',
        '                        "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n',
        '                    }\n',
    ])
    text = text[:start] + replacement + text[end:]
    _write(goal_path, text)

    test_path = root / "services/agent-service/tests/runtime/test_wp08_attempt5_dependency_authority.py"
    tests = _read(test_path)
    first_name = "def test_exact_contradictory_graph_self_reaudits_before_touching_candidate() -> None:\n"
    second_name = "def test_malformed_alignment_basis_fails_closed_after_bounded_reaudit() -> None:\n"
    third_name = "def test_unknown_and_self_dependency_edges_fail_closed() -> None:\n"
    start = tests.find(first_name)
    mid = tests.find(second_name, start + 1)
    end = tests.find(third_name, mid + 1)
    if start < 0 or mid < 0 or end < 0:
        raise SystemExit("dependency authority regression function boundaries missing")
    replacement_tests = '''def test_exact_contradictory_graph_self_reaudits_candidate_blind() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "contradictory_exact",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_decisions": [{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "它",
            }],
            "reason_code": "blind_dependency_reaudit_exact",
        }),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"


def test_malformed_alignment_basis_fails_closed_after_blind_reaudit() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    bad = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "键盘订单",
        }],
        "reason_code": "bad_basis",
    })
    blind_bad = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "键盘订单",
        }],
        "reason_code": "bad_blind_basis",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[bad, blind_bad]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"


def test_unproposed_refund_dependency_requires_candidate_blind_confirmation() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", [])]
    false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "refund_needs_query_result",
    })
    blind_independent = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "same_turn_scope_is_not_result_dependency",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[false_positive, blind_independent]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"
    second_payload = str(invoke.call_args_list[1].kwargs["payload"])
    assert '"depends_on"' not in second_payload


'''
    tests = tests[:start] + replacement_tests + tests[end:]
    _write(test_path, tests)

    workflow_path = root / ".github/workflows/wp08-certification.yml"
    workflow = _read(workflow_path)
    if "WP08_HEARTBEAT_SECONDS:" in workflow:
        raise SystemExit("WP-08 heartbeat override already exists")
    anchor = "          WP08_RESUME_REQUESTED: ${{ env.WP08_RESUME_RUN_ID_RESOLVED != '' && '1' || '0' }}\n"
    if workflow.count(anchor) != 1:
        raise SystemExit("WP-08 heartbeat insertion anchor mismatch")
    workflow = workflow.replace(anchor, anchor + "          WP08_HEARTBEAT_SECONDS: '60'\n", 1)
    _write(workflow_path, workflow)


def regenerate_baseline(root: Path, product_sha: str) -> None:
    baseline_path = root / "skill-system/registry/product-source-baseline.json"
    payload = json.loads(_read(baseline_path))
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit("invalid protected source baseline")
    missing: list[str] = []
    for relative in sorted(files):
        path = root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if missing:
        raise SystemExit("baseline path(s) missing: " + ", ".join(missing[:10]))
    payload["file_count"] = len(files)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = "git:" + product_sha
    _write(baseline_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("patch", "baseline"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--product-sha", default="")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.mode == "patch":
        apply_patch(root)
    else:
        if not args.product_sha:
            raise SystemExit("--product-sha is required for baseline mode")
        regenerate_baseline(root, args.product_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
