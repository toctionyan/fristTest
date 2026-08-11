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
PROTOCOL_PATH = Path("services/agent-service/src/agent_core/lifecycle/protocol.py")
NEW_TEST = Path("skill-system/tests/test_wp08_attempt3_verifier_stability_repair.py")
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

    old_rule = '''            "reference_expression.expected_cardinality describes the historical referent being pointed at, not the Goal output: use single when the user refers to one prior visible object/member, and collection when the user refers to a prior visible set that will be filtered/sorted/compared; it may therefore differ from expected_result_cardinality for a single-result selection over a collection",\n            "incomplete when distinct outcomes are collapsed into one goal or at least one literal requested outcome is absent",\n'''
    new_rule = '''            "reference_expression.expected_cardinality describes the historical referent being pointed at, not the Goal output: use single when the user refers to one prior visible object/member, and collection when the user refers to a prior visible set that will be filtered/sorted/compared; it may therefore differ from expected_result_cardinality for a single-result selection over a collection",\n            "reference_expression.evidence_span is the smallest literal phrase that performs the historical reference and may be a strict subspan of Goal.evidence_span; surrounding attribute, predicate, comparison or action wording belongs to the Goal effect/scope and must not be required inside the reference span",\n            "incomplete when distinct outcomes are collapsed into one goal or at least one literal requested outcome is absent",\n'''
    text = replace_once(text, old_rule, new_rule, label="reference evidence decision rule")

    old_blind_rule = '''            "target-member selection, historical-result/member reference, execution commitment, input/control wording, unprovided form values and current business facts are not scope constraints; if one is explicitly placed in scope_constraints return incomplete instead of letting Runtime bind it as a filter",\n            "judge semantic result dependency independently from execution-support dataflow",\n'''
    new_blind_rule = '''            "target-member selection, historical-result/member reference, execution commitment, input/control wording, unprovided form values and current business facts are not scope constraints; if one is explicitly placed in scope_constraints return incomplete instead of letting Runtime bind it as a filter",\n            "a historical reference span is the smallest literal referring phrase and may be shorter than the Goal evidence_span; never demand that surrounding status/detail/filter/action wording be copied into reference_expression.evidence_span",\n            "judge semantic result dependency independently from execution-support dataflow",\n'''
    text = replace_once(text, old_blind_rule, new_blind_rule, label="reference evidence blind rule")

    old_state = '''        initial_exact_alignment: GoalAlignmentVerdict | None = None\n        requested_effect_reaudit_guard: dict[str, Any] | None = None\n        for attempt in range(3):\n            blind_dependency_audit = str(verifier_repair_kind or "").startswith("candidate_blind_dependency_")\n            effective_instruction = (\n                blind_dependency_instruction\n                + " Dependency absence must also be explicitly proven. Return dependency_decisions with exactly one row "\n                "for every unordered pair of supplied Goal IDs. Each row has goal_a_id, goal_b_id and "\n                "relation=a_depends_on_b|b_depends_on_a|independent. For a dependency relation also include "\n                "basis_kind=result_reference|result_condition|result_value_input and basis_span copied literally from inside "\n                "the dependent Goal evidence_span. Do not omit independent pairs; dependency_decisions=[] is valid only when "\n                "fewer than two Goals are supplied. For requested_effect or target-scope-constraint mismatch, do not alter the "\n                "dependency decisions: set verdict=incomplete, copy the literal mismatched phrase into missing_spans, and use "\n                "a reason_code that identifies requested-effect fidelity or target-scope-constraint coverage. Return JSON only "\n                "with verdict, evidence_spans, missing_spans, dependency_decisions and reason_code."\n                if blind_dependency_audit else instruction\n            )\n'''
    new_state = '''        initial_exact_alignment: GoalAlignmentVerdict | None = None\n        requested_effect_reaudit_guard: dict[str, Any] | None = None\n        preserved_blind_dependency_details: dict[str, Any] | None = None\n        for attempt in range(3):\n            blind_dependency_audit = str(verifier_repair_kind or "").startswith("candidate_blind_dependency_")\n            semantic_claim_reaudit = verifier_repair_kind in {\n                "candidate_blind_dependency_requested_effect_reaudit",\n                "candidate_blind_dependency_scope_constraint_reaudit",\n            }\n            if semantic_claim_reaudit:\n                effective_instruction = (\n                    blind_dependency_instruction\n                    + " The previous candidate-blind call already produced a complete structurally grounded dependency proof. "\n                    "This bounded final call must re-audit only the disputed requested-effect or target-scope semantic claim. "\n                    "Do not re-judge, replace or return dependency_decisions; dependency authority remains the preserved prior proof. "\n                    "Return JSON only with verdict, evidence_spans, missing_spans and reason_code."\n                )\n            elif blind_dependency_audit:\n                effective_instruction = (\n                    blind_dependency_instruction\n                    + " Dependency absence must also be explicitly proven. Return dependency_decisions with exactly one row "\n                    "for every unordered pair of supplied Goal IDs. Each row has goal_a_id, goal_b_id and "\n                    "relation=a_depends_on_b|b_depends_on_a|independent. For a dependency relation also include "\n                    "basis_kind=result_reference|result_condition|result_value_input and basis_span copied literally from inside "\n                    "the dependent Goal evidence_span. Do not omit independent pairs; dependency_decisions=[] is valid only when "\n                    "fewer than two Goals are supplied. For requested_effect or target-scope-constraint mismatch, do not alter the "\n                    "dependency decisions: set verdict=incomplete, copy the literal mismatched phrase into missing_spans, and use "\n                    "a reason_code that identifies requested-effect fidelity or target-scope-constraint coverage. Return JSON only "\n                    "with verdict, evidence_spans, missing_spans, dependency_decisions and reason_code."\n                )\n            else:\n                effective_instruction = instruction\n'''
    text = replace_once(text, old_state, new_state, label="preserved dependency state and instruction")

    old_parser = '''                if raw_verdict in {"exact", "incomplete"}:\n                    if blind_dependency_audit:\n                        dependency_details, dependency_error = _model_alignment_pairwise_dependency_proof(\n                            user_text=user_text,\n                            goals=goals,\n                            values=parsed.get("dependency_decisions"),\n                        )\n                    else:\n                        dependency_details, dependency_error = _model_alignment_dependency_proof(\n                            user_text=user_text,\n                            goals=goals,\n                            values=parsed.get("dependency_edges"),\n                        )\n'''
    new_parser = '''                if raw_verdict in {"exact", "incomplete"}:\n                    if semantic_claim_reaudit and isinstance(preserved_blind_dependency_details, dict):\n                        # The second candidate-blind call already closed graph authority.\n                        # A third call exists only to arbitrate one semantic-field claim;\n                        # letting it emit a fresh graph reopens a proven dimension and can\n                        # turn harmless semantic arbitration into a spurious dependency\n                        # grounding failure. Preserve, do not weaken, the prior proof.\n                        dependency_details = deepcopy(preserved_blind_dependency_details)\n                    elif blind_dependency_audit:\n                        dependency_details, dependency_error = _model_alignment_pairwise_dependency_proof(\n                            user_text=user_text,\n                            goals=goals,\n                            values=parsed.get("dependency_decisions"),\n                        )\n                    else:\n                        dependency_details, dependency_error = _model_alignment_dependency_proof(\n                            user_text=user_text,\n                            goals=goals,\n                            values=parsed.get("dependency_edges"),\n                        )\n'''
    text = replace_once(text, old_parser, new_parser, label="semantic claim parser preserves dependency proof")

    old_requested_trigger = '''                requested_effect_reaudit_guard = _requested_effect_reaudit_collision_guard(\n                    goals, verdict.missing_spans\n                )\n                verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"\n                verifier_repair = (\n                    "Re-audit only the previous requested-effect fidelity mismatch claim while preserving the complete "\n                    "candidate-blind dependency proof. requested_effect is an open semantic identity of the customer's "\n                    "user-visible business outcome, not a capability-selection result. Judge domain, operation, object_type "\n                    "and raw_description together against the literal Goal evidence_span. An unsupported/unregistered effect "\n                    "or harmless naming granularity is not itself a mismatch, and capability availability must not be used as "\n                    "evidence. Withdraw the mismatch only when the declared effect still denotes the same user-visible outcome. "\n                    "If it substitutes a different lookup, action, object or business effect, remain incomplete and copy only "\n                    "the smallest literal USER_TEXT span proving that substitution into missing_spans. If the disputed Goal uses "\n                    "the exact same structured domain/operation/object_type as a sibling Goal with a distinct independently requested "\n                    "outcome, do not erase the mismatch merely because raw_description is broad enough to sound compatible; that is a "\n                    "high-risk effect-collapse signal and requires a faithful fresh declaration. Do not choose a tool, "\n                    "consult a capability registry, normalize to a nearby registered effect, or rewrite the declaration. Return "\n                    "the full candidate-blind JSON contract, including one dependency_decisions row for every unordered Goal pair."\n                )\n'''
    new_requested_trigger = '''                requested_effect_reaudit_guard = _requested_effect_reaudit_collision_guard(\n                    goals, verdict.missing_spans\n                )\n                preserved_blind_dependency_details = deepcopy(semantic_details)\n                verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"\n                verifier_repair = (\n                    "Re-audit only the previous requested-effect fidelity mismatch claim while preserving the complete "\n                    "candidate-blind dependency proof. requested_effect is an open semantic identity of the customer's "\n                    "user-visible business outcome, not a capability-selection result. Judge domain, operation, object_type "\n                    "and raw_description together against the literal Goal evidence_span. Do not infer a mismatch merely because "\n                    "an operation identifier is lexically broader or narrower than the literal attribute wording; require an actual "\n                    "different user-visible business effect. An unsupported/unregistered effect or harmless naming granularity is not "\n                    "itself a mismatch, and capability availability must not be used as evidence. Withdraw the mismatch only when the "\n                    "declared effect still denotes the same user-visible outcome. If it substitutes a different lookup, action, object "\n                    "or business effect, remain incomplete and copy only the smallest literal USER_TEXT span proving that substitution "\n                    "into missing_spans. If the disputed Goal uses the exact same structured domain/operation/object_type as a sibling "\n                    "Goal with a distinct independently requested outcome, do not erase the mismatch merely because raw_description is "\n                    "broad enough to sound compatible; that is a high-risk effect-collapse signal and requires a faithful fresh "\n                    "declaration. Do not choose a tool, consult a capability registry, normalize to a nearby registered effect, or "\n                    "rewrite the declaration. Do not re-audit or return dependency_decisions; the prior complete dependency proof "\n                    "remains authoritative. Return only verdict, evidence_spans, missing_spans and reason_code."\n                )\n'''
    text = replace_once(text, old_requested_trigger, new_requested_trigger, label="requested effect semantic-only re-audit")

    old_scope_trigger = '''                verifier_repair_kind = "candidate_blind_dependency_scope_constraint_reaudit"\n                verifier_repair = (\n                    "Re-audit only the previous target-scope-constraint mismatch claim while preserving the complete "\n                    "candidate-blind dependency proof. A target_candidate.scope_constraints entry is required only for an "\n                    "explicit filter, status predicate, threshold or comparison that narrows which members belong in this "\n                    "Goal's requested target/result population. Object identity, member naming, ordinary target selection, "\n                    "and an explicit reference to an earlier current-turn Goal result are not scope constraints; a true "\n                    "current-turn result reference belongs only in dependency_decisions. If the prior missing_spans confused "\n                    "one of those target/reference forms with a narrowing predicate, withdraw that scope mismatch and return "\n                    "exact only when no other semantic mismatch remains. If USER_TEXT really contains an omitted narrowing "\n                    "predicate, remain incomplete and copy only its smallest literal span into missing_spans. Do not choose a "\n                    "tool, target, entity, normalized business value, capability or implementation step. Return the full "\n                    "candidate-blind JSON contract, including one dependency_decisions row for every unordered Goal pair."\n                )\n'''
    new_scope_trigger = '''                preserved_blind_dependency_details = deepcopy(scope_details)\n                verifier_repair_kind = "candidate_blind_dependency_scope_constraint_reaudit"\n                verifier_repair = (\n                    "Re-audit only the previous target-scope-constraint mismatch claim while preserving the complete "\n                    "candidate-blind dependency proof. A target_candidate.scope_constraints entry is required only for an "\n                    "explicit filter, status predicate, threshold or comparison that narrows which members belong in this "\n                    "Goal's requested target/result population. Object identity, member naming, ordinary target selection, "\n                    "historical-result/member references, and execution/input/control wording are not scope constraints. A historical "\n                    "reference belongs in reference_expression; a true current-turn result reference belongs only in the already "\n                    "preserved dependency proof. If the prior missing_spans confused one of those target/reference forms with a "\n                    "narrowing predicate, withdraw that scope mismatch and return exact only when no other semantic mismatch remains. "\n                    "If USER_TEXT really contains an omitted narrowing predicate, remain incomplete and copy only its smallest literal "\n                    "span into missing_spans. Do not choose a tool, target, entity, normalized business value, capability or implementation "\n                    "step. Do not re-audit or return dependency_decisions; the prior complete dependency proof remains authoritative. "\n                    "Return only verdict, evidence_spans, missing_spans and reason_code."\n                )\n'''
    text = replace_once(text, old_scope_trigger, new_scope_trigger, label="scope semantic-only re-audit")

    write(path, text)


def patch_protocol(root: Path) -> None:
    path = root / PROTOCOL_PATH
    text = read(path)
    old = '''        "只用于引用已经在更早轮次向客户可见的 ResultRef、历史轮次或其展示成员。reference_expression.expected_cardinality 描述被引用对象本身：指向一个可见对象/成员时用 single，指向将继续筛选/排序/比较的可见集合时用 collection；它不是 Goal 最终输出数量。"\n        "同一当前轮中一个 Goal 依赖另一个尚未执行 Goal 的未来结果时禁止填写 reference_expression；"\n        "这种当前轮先后/结果依赖只能用 depends_on 表达。"\n'''
    new = '''        "只用于引用已经在更早轮次向客户可见的 ResultRef、历史轮次或其展示成员。reference_expression.expected_cardinality 描述被引用对象本身：指向一个可见对象/成员时用 single，指向将继续筛选/排序/比较的可见集合时用 collection；它不是 Goal 最终输出数量。"\n        "reference_expression.evidence_span 必须只复制当前用户原话中真正承担历史指代的最小连续片段；它允许只是 Goal.evidence_span 的严格子串。对象状态、属性、筛选、比较、动作等其余问题文字仍属于 Goal 本身，禁止为了凑满 Goal 证据而扩大 reference_expression.evidence_span。"\n        "同一当前轮中一个 Goal 依赖另一个尚未执行 Goal 的未来结果时禁止填写 reference_expression；"\n        "这种当前轮先后/结果依赖只能用 depends_on 表达。"\n'''
    text = replace_once(text, old, new, label="reference expression minimal span contract")
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


def _goal(goal_id: str, span: str, operation: str, *, target_candidate=None, reference_expression=None):
    row = {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "record",
            "operation": operation,
            "object_type": "record",
            "raw_description": span,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }
    if target_candidate is not None:
        row["target_candidate"] = target_candidate
    if reference_expression is not None:
        row["reference_expression"] = reference_expression
    return row


def _independent_pair():
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_scope_claim_reaudit_preserves_prior_dependency_proof_even_if_final_model_drifts() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "List my records, then show their current summary"
    goals = [
        _goal("g1", "List my records", "list"),
        _goal("g2", "show their current summary", "query_summary"),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["List my records", "show their current summary"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind_scope_false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["List my records", "show their current summary"],
        "missing_spans": ["current summary"],
        "dependency_decisions": _independent_pair(),
        "reason_code": "target-scope-constraint coverage",
    })
    # Reproduce Attempt 3's failure mode: the semantic-only arbitration invents
    # a fresh dependency/basis even though the preceding blind proof was complete.
    # The repaired Runtime must ignore this field entirely rather than reopen graph authority.
    semantic_with_graph_drift = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "List my records",
        }],
        "reason_code": "scope_claim_withdrawn",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, blind_scope_false_positive, semantic_with_graph_drift],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["dependency_pair_decisions"] == _independent_pair()
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_reaudit"
    final_messages = invoke.call_args_list[2].kwargs["payload"]
    final_text = "\n".join(str(getattr(message, "content", "") or "") for message in final_messages)
    assert "Do not re-judge, replace or return dependency_decisions" in final_text


def test_requested_effect_reaudit_preserves_empty_single_goal_dependency_proof() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "What is its current state?"
    goal = _goal(
        "g1",
        text,
        "query_details",
        reference_expression={
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "expected_cardinality": "single",
            "evidence_span": "its",
        },
    )
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    false_effect_claim = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["current state"],
        "dependency_decisions": [],
        "reason_code": "requested-effect fidelity",
    })
    corrected = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "reason_code": "same_user_visible_effect_naming_granularity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, false_effect_claim, corrected],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    final_messages = invoke.call_args_list[2].kwargs["payload"]
    final_text = "\n".join(str(getattr(message, "content", "") or "") for message in final_messages)
    assert "lexically broader or narrower" in final_text
    assert "Do not re-audit or return dependency_decisions" in final_text


def test_semantic_only_scope_reaudit_still_fails_closed_when_omission_is_confirmed() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Show records above the threshold"
    goal = _goal("g1", text, "list")
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["above the threshold"],
        "dependency_decisions": [],
        "reason_code": "target-scope-constraint coverage",
    })
    confirmed = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["above the threshold"],
        "reason_code": "target-scope-constraint coverage",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, mismatch, confirmed],
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("above the threshold",)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True


def test_reference_expression_contract_requires_minimal_referring_subspan() -> None:
    from agent_core.lifecycle.protocol import REFERENCE_EXPRESSION_SCHEMA

    description = str(REFERENCE_EXPRESSION_SCHEMA["description"])
    assert "最小连续片段" in description
    assert "严格子串" in description
    assert "禁止为了凑满 Goal 证据" in description


def test_attempt3_repair_does_not_relax_positive_dependency_basis_grounding() -> None:
    from agent_core.lifecycle.goal_planning import _model_alignment_pairwise_dependency_proof

    text = "Load the records, then use that result"
    goals = [
        _goal("g1", "Load the records", "list"),
        _goal("g2", "use that result", "summarize"),
    ]
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "Load the records",
        }],
    )
    assert error == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert details["dependency_proof_complete"] is False


def test_attempt3_repair_is_domain_neutral() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("preserved_blind_dependency_details")
    end = source.index("class CandidateOnlyGoalAlignmentVerifier", start)
    repair = source[start:end]
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "蓝牙耳机", "退款"):
        assert forbidden not in repair
    assert "CapabilityRegistry" not in repair
    assert "dependency_proof_complete" in repair
    assert "dependency_graph_match" in repair
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
    patch_protocol(root)
    add_tests(root)
    for relative in (GOAL_PATH, PROTOCOL_PATH, NEW_TEST):
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
