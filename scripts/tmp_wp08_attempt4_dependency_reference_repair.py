#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import py_compile
import subprocess

GOAL_PATH = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
OLD_TEST = Path("services/agent-service/tests/runtime/test_wp08_attempt7_final_authority_and_retry.py")
NEW_TEST = Path("skill-system/tests/test_wp08_attempt4_dependency_reference_repair.py")
BASELINE_PATH = Path("skill-system/registry/product-source-baseline.json")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_goal_planning(root: Path) -> None:
    path = root / GOAL_PATH
    text = read(path)

    old_projection = '''            "target_candidate": deepcopy(goal.get("target_candidate"))\n            if isinstance(goal.get("target_candidate"), dict)\n            else None,\n            "condition": deepcopy(goal.get("condition"))\n'''
    new_projection = '''            "target_candidate": deepcopy(goal.get("target_candidate"))\n            if isinstance(goal.get("target_candidate"), dict)\n            else None,\n            "reference_expression": deepcopy(goal.get("reference_expression"))\n            if isinstance(goal.get("reference_expression"), dict)\n            else None,\n            "condition": deepcopy(goal.get("condition"))\n'''
    text = replace_once(text, old_projection, new_projection, label="blind projection historical reference")

    helper_anchor = '''    return rows\n\n\ndef _requested_effect_identity_key(goal: dict[str, Any]) -> tuple[str, str, str]:\n'''
    helper = '''    return rows\n\n\ndef _has_unique_historical_reference(goals: list[dict[str, Any]]) -> bool:\n    """Return whether Runtime already proved at least one historical reference unique.\n\n    This is structural liveness evidence only. It does not interpret the user's\n    language or pick a target; resolution already happened through the\n    historical ResultRef authority before semantic alignment runs.\n    """\n    for goal in goals:\n        reference = goal.get("reference_expression")\n        proof = goal.get("referent_resolution_proof")\n        if not isinstance(reference, dict) or not isinstance(proof, dict):\n            continue\n        if (\n            str(reference.get("evidence_span") or "").strip()\n            and str(proof.get("resolution_status") or "") == "UNIQUE"\n        ):\n            return True\n    return False\n\n\ndef _requested_effect_identity_key(goal: dict[str, Any]) -> tuple[str, str, str]:\n'''
    text = replace_once(text, helper_anchor, helper, label="unique historical reference helper")

    old_blind = '''            "Audit three things from USER_TEXT: (1) the complete current-turn semantic result-dependency graph; "\n            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer's actual business effect instead of "\n            "coercing an unsupported/open effect into a nearby registered effect; and (3) whether every explicit user-stated "\n            "filter, status predicate, threshold or comparison that narrows the Goal target/result population is preserved as "\n            "literal evidence in DECLARED_GOAL.target_candidate.scope_constraints. A scope constraint stores only the smallest "\n'''
    new_blind = '''            "Audit four things from USER_TEXT: (1) the complete current-turn semantic result-dependency graph; "\n            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer's actual business effect instead of "\n            "coercing an unsupported/open effect into a nearby registered effect; (3) whether every explicit user-stated "\n            "filter, status predicate, threshold or comparison that narrows the Goal target/result population is preserved as "\n            "literal evidence in DECLARED_GOAL.target_candidate.scope_constraints; and (4) when reference_expression is supplied, "\n            "whether it preserves the smallest literal historical referring phrase and its stated historical relation/cardinality "\n            "against RECENT_PUBLIC_CONTEXT. Do not require surrounding status/detail/filter/action wording inside the historical "\n            "reference evidence_span. A scope constraint stores only the smallest "\n'''
    text = replace_once(text, old_blind, new_blind, label="blind historical reference audit")

    old_rule = '''            "a historical reference span is the smallest literal referring phrase and may be shorter than the Goal evidence_span; never demand that surrounding status/detail/filter/action wording be copied into reference_expression.evidence_span",\n            "judge semantic result dependency independently from execution-support dataflow",\n'''
    new_rule = '''            "a historical reference span is the smallest literal referring phrase and may be shorter than the Goal evidence_span; never demand that surrounding status/detail/filter/action wording be copied into reference_expression.evidence_span",\n            "when Runtime has already resolved a supplied historical reference uniquely, judge the semantic fidelity of the declared referring phrase against RECENT_PUBLIC_CONTEXT; do not reopen target selection or require non-reference wording inside the reference span",\n            "judge semantic result dependency independently from execution-support dataflow",\n'''
    text = replace_once(text, old_rule, new_rule, label="blind historical reference rule")

    insert_anchor = '''                prompt = {\n                    "USER_TEXT_UNTRUSTED": user_text,\n                    "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                    "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                    "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                }\n                continue\n            normalized_semantic_reason = (\n'''
    insert = '''                prompt = {\n                    "USER_TEXT_UNTRUSTED": user_text,\n                    "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                    "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                    "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                }\n                continue\n            if (\n                blind_dependency_audit\n                and verifier_repair_kind == "candidate_blind_dependency_reaudit"\n                and verdict.exact\n                and isinstance(verdict.details, dict)\n                and verdict.details.get("dependency_proof_complete") is True\n                and verdict.details.get("dependency_graph_match") is True\n                and bool(list(verdict.details.get("dependency_edges") or []))\n                and attempt < 2\n            ):\n                # Positive same-turn result dependencies are high-impact because a\n                # false edge blocks an otherwise independently reportable sibling.\n                # Spend the existing third verifier slot on an adversarial graph-only\n                # confirmation while still hiding Planner depends_on. Runtime does\n                # not infer language or rewrite the graph; disagreement stays\n                # fail-closed and flows through ordinary redeclaration feedback.\n                verifier_repair_kind = "candidate_blind_dependency_positive_edge_adjudication"\n                verifier_repair = (\n                    "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Start every unordered "\n                    "Goal pair from independent and retain a positive edge only when a literal basis_span inside the dependent Goal "\n                    "proves that the user-visible later outcome itself consumes the earlier current-turn Goal result as a result_reference, "\n                    "result_condition or result_value_input. Sequencing, shared topic/scope, repeated business object, and stable-ID/artifact "\n                    "lookup needed only by execution are not result dependencies. Do not see or reconstruct Planner depends_on from tool "\n                    "needs. Return one dependency_decisions row for every unordered Goal pair together with the normal requested-effect and "\n                    "scope audit fields. A true explicit result reference/condition/value dependency must still be retained."\n                )\n                prompt = {\n                    "USER_TEXT_UNTRUSTED": user_text,\n                    "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                    "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                    "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                }\n                continue\n            normalized_semantic_reason = (\n'''
    text = replace_once(text, insert_anchor, insert, label="positive dependency adjudication")

    old_attempt0 = '''            if attempt == 0:\n                original_verdict = str(verdict.details.get("original_verdict") or "")\n                if (\n                    verdict.reason_code == "goal_alignment_missing_span_not_grounded"\n'''
    new_attempt0 = '''            if attempt == 0:\n                original_verdict = str(verdict.details.get("original_verdict") or "")\n                if verdict.verdict == "indeterminate" and _has_unique_historical_reference(goals):\n                    verifier_repair_kind = "candidate_blind_dependency_historical_reference_reaudit"\n                    verifier_repair = (\n                        "Re-audit this structurally valid historical-reference declaration without seeing Planner depends_on. Runtime has "\n                        "already resolved the supplied historical ResultRef/member reference uniquely; do not reopen target selection. "\n                        "Judge whether each requested outcome is preserved and whether reference_expression.evidence_span is the smallest "\n                        "literal phrase in USER_TEXT that performs the historical reference. It may be a strict subspan of the Goal "\n                        "evidence_span; surrounding status/detail/filter/action wording must not be required inside it. Re-audit every "\n                        "unordered current-turn Goal pair independently and return the strict candidate-blind JSON contract. If a real "\n                        "semantic mismatch exists, remain incomplete with a literal missing span; otherwise return exact."\n                    )\n                    prompt = {\n                        "USER_TEXT_UNTRUSTED": user_text,\n                        "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                        "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                        "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                    }\n                elif (\n                    verdict.reason_code == "goal_alignment_missing_span_not_grounded"\n'''
    text = replace_once(text, old_attempt0, new_attempt0, label="historical reference blind fallback")

    write(path, text)


def patch_old_test(root: Path) -> None:
    path = root / OLD_TEST
    text = read(path)
    old = '''    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind]\n    ) as invoke:\n        verdict = ModelGoalAlignmentVerifier().verify(\n            user_text=text,\n            goals=goals,\n            known_tools=set(),\n        )\n\n    assert invoke.call_count == 2\n    assert verdict.exact\n'''
    new = '''    adversarial_confirmation = _response({\n        "verdict": "exact",\n        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],\n        "missing_spans": [],\n        "dependency_decisions": [{\n            "goal_a_id": "g1",\n            "goal_b_id": "g2",\n            "relation": "b_depends_on_a",\n            "basis_kind": "result_reference",\n            "basis_span": "它",\n        }],\n        "reason_code": "adversarial_true_result_reference",\n    })\n\n    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind, adversarial_confirmation]\n    ) as invoke:\n        verdict = ModelGoalAlignmentVerifier().verify(\n            user_text=text,\n            goals=goals,\n            known_tools=set(),\n        )\n\n    assert invoke.call_count == 3\n    assert verdict.exact\n'''
    text = replace_once(text, old, new, label="preserved true dependency regression")
    write(path, text)


def add_tests(root: Path) -> None:
    path = root / NEW_TEST
    if path.exists():
        raise SystemExit(f"test path already exists: {NEW_TEST}")
    path.write_text(r'''from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services" / "agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for value in (AGENT_ROOT, AGENT_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, *, depends_on=None, reference=None, proof=None):
    row = {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "record",
            "operation": "query",
            "object_type": "record",
            "raw_description": span,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }
    if reference is not None:
        row["reference_expression"] = reference
    if proof is not None:
        row["referent_resolution_proof"] = proof
    return row


def _positive_decision(*, basis_span: str):
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": basis_span,
    }]


def test_matching_positive_dependency_requires_adversarial_third_audit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Load the records, then show the summary"
    goals = [
        _goal("g1", "Load the records"),
        _goal("g2", "show the summary", depends_on=["g1"]),
    ]
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "show the summary"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "summary",
        }],
        "reason_code": "candidate_positive",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "show the summary"],
        "missing_spans": [],
        "dependency_decisions": _positive_decision(basis_span="summary"),
        "reason_code": "blind_positive",
    })
    adversarial = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "show the summary"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "support_flow_not_result_dependency",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"
    third_payload = repr(invoke.call_args_list[2].kwargs.get("payload"))
    assert "Start every unordered Goal pair from independent" in third_payload
    assert "'depends_on'" not in third_payload


def test_true_positive_dependency_survives_adversarial_third_audit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Load the records, then use that result"
    goals = [
        _goal("g1", "Load the records"),
        _goal("g2", "use that result", depends_on=["g1"]),
    ]
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "use that result"],
        "missing_spans": [],
        "dependency_edges": [edge],
        "reason_code": "true_reference",
    })
    decision = [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "use that result"],
        "missing_spans": [],
        "dependency_decisions": decision,
        "reason_code": "blind_true_reference",
    })
    adversarial = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "use that result"],
        "missing_spans": [],
        "dependency_decisions": decision,
        "reason_code": "adversarial_true_reference",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"][0]["basis_span"] == "that result"


def test_unique_historical_reference_indeterminate_gets_candidate_blind_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "What is its current state?"
    reference = {
        "reference_type": "temporal_visible_result",
        "temporal_relation": "latest",
        "expected_cardinality": "single",
        "evidence_span": "its",
    }
    goal = _goal(
        "g1",
        text,
        reference=reference,
        proof={"resolution_status": "UNIQUE"},
    )
    ungrounded = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_reference_exact_but_ungrounded",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "historical_reference_faithful",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[ungrounded, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
            recent_public_context=[{"answer_summary": "One record was shown", "historical_only": True}],
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_historical_reference_reaudit"
    second_payload = repr(invoke.call_args_list[1].kwargs.get("payload"))
    assert "reference_expression" in second_payload
    assert "strict subspan" in second_payload
    assert "'depends_on'" not in second_payload


def test_historical_reference_reaudit_remains_fail_closed_on_real_mismatch() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "What is its current state?"
    goal = _goal(
        "g1",
        text,
        reference={
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "expected_cardinality": "single",
            "evidence_span": "its",
        },
        proof={"resolution_status": "UNIQUE"},
    )
    ungrounded = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_reference_exact_but_ungrounded",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["current state"],
        "dependency_decisions": [],
        "reason_code": "requested_effect_fidelity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[ungrounded, mismatch]
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("current state",)


def test_attempt4_repair_is_domain_neutral_and_does_not_rewrite_dependencies() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("candidate_blind_dependency_positive_edge_adjudication")
    end = source.index("normalized_semantic_reason", start)
    repair = source[start:end]
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "蓝牙耳机", "退款"):
        assert forbidden not in repair
    assert "capability_registry" not in repair
    assert "Runtime does" in repair
    assert "rewrite the graph" in repair
''', encoding="utf-8")


def regenerate_baseline(root: Path, product_sha: str) -> None:
    path = root / BASELINE_PATH
    payload = json.loads(read(path))
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit("invalid protected source baseline")
    tracked = subprocess.check_output(
        ["git", "ls-files", "services", "web", "contracts"],
        cwd=root,
        text=True,
    ).splitlines()
    tracked_set = {row.strip() for row in tracked if row.strip()}
    baseline_set = set(files)
    if tracked_set != baseline_set:
        missing = sorted(tracked_set - baseline_set)
        stale = sorted(baseline_set - tracked_set)
        raise SystemExit(
            "protected source set drift: "
            f"missing_from_baseline={missing[:20]} stale_in_baseline={stale[:20]}"
        )
    refreshed: dict[str, str] = {}
    for relative in sorted(baseline_set):
        file_path = root / relative
        if not file_path.is_file():
            raise SystemExit(f"protected source missing: {relative}")
        refreshed[relative] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    payload["files"] = refreshed
    payload["file_count"] = len(refreshed)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = f"git:{product_sha}"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_old_test(root)
    add_tests(root)
    for relative in (GOAL_PATH, OLD_TEST, NEW_TEST):
        py_compile.compile(str(root / relative), doraise=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("patch")
    p.add_argument("--workspace", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--workspace", required=True)
    b.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        regenerate_baseline(root, str(args.product_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
