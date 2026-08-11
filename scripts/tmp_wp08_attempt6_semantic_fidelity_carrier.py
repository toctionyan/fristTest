#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return text.replace(old, new, 1)


def patch_goal_planning(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    text = read(path)

    old_projection = '''            "requested_effect": deepcopy(goal.get("requested_effect"))
            if isinstance(goal.get("requested_effect"), dict)
            else None,
            "expected_result_cardinality": _clean_text(
'''
    new_projection = '''            "requested_effect": deepcopy(goal.get("requested_effect"))
            if isinstance(goal.get("requested_effect"), dict)
            else None,
            "condition": deepcopy(goal.get("condition"))
            if isinstance(goal.get("condition"), dict)
            else None,
            "expected_result_cardinality": _clean_text(
'''
    text = replace_once(text, old_projection, new_projection, "blind projection condition")

    old_blind_instruction = '''        blind_dependency_instruction = (
            "Independently audit only the current-turn semantic result-dependency graph among the supplied Goal IDs. "
            "The Planner's candidate dependency graph has been intentionally withheld, so do not reconstruct one from "
            "tool order, implementation prerequisites, stable-ID lookup needs, capability availability, or sentence order. "
            "A dependency exists only when the later user-visible outcome itself must consume an earlier current-turn "
            "Goal result as its target, value input, or condition. Shared same-turn object/scope ellipsis is not a result "
            "dependency. An explicit reference in the later literal span to the not-yet-produced earlier result is a "
            "dependency. Return JSON only with verdict, evidence_spans, missing_spans, dependency_edges and reason_code. "
            "Use verdict=exact when the supplied outcome inventory is represented; this audit must not invent omitted "
            "outcomes. dependency_edges must be your complete independent graph. Every edge must contain dependent_goal_id, "
            "requires_result_of_goal_id, basis_kind and a literal basis_span inside the dependent Goal evidence_span."
        )
        blind_dependency_rules = [
            "judge semantic result dependency independently from execution-support dataflow",
            "shared object/topic/scope and sequencing words alone never create a result dependency",
            "a stable identifier or artifact lookup needed only by execution is support, not a user-visible result dependency",
            "an explicit later reference to an earlier current-turn result, or a condition/value that genuinely consumes that result, does create a dependency",
            "use only literal USER_TEXT evidence and the supplied Goal IDs; do not use capability, tool, oracle or business-state knowledge",
            "return the complete graph, including an empty list when the outcomes are independently acceptable",
        ]
'''
    new_blind_instruction = '''        blind_dependency_instruction = (
            "Independently re-audit the frozen semantic fields of the supplied Goal IDs without seeing Planner depends_on. "
            "Audit three things from USER_TEXT: (1) the complete current-turn semantic result-dependency graph; "
            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer's actual business effect instead of "
            "coercing an unsupported/open effect into a nearby registered effect; and (3) whether every explicit user-stated "
            "filter, status predicate, threshold, condition or other result-scope constraint inside a Goal evidence_span is "
            "actually represented in that Goal's structured condition rather than existing only in description/evidence prose. "
            "Do not invent a missing target member, slot/form value, current business fact or execution-time cardinality as a "
            "semantic condition; those remain downstream Runtime concerns. If requested_effect is semantically substituted, or "
            "an explicit predicate is missing from structured condition, verdict must be incomplete and missing_spans must copy "
            "the smallest literal USER_TEXT span that proves the mismatch. Do not propose a replacement identity, normalized "
            "predicate value, tool or capability. A result dependency exists only when the later user-visible outcome itself "
            "must consume an earlier current-turn Goal result as target, value input or condition; shared topic/scope, sentence "
            "order, stable-ID lookup and implementation prerequisites are not dependencies."
        )
        blind_dependency_rules = [
            "requested_effect fidelity is judged against the literal business effect in each Goal evidence_span; nearby registered capability identity is never acceptable merely because it exists",
            "an explicit user-stated result filter/status/predicate must be structurally preserved in Goal.condition; repeating the words only in description, raw_description or evidence_span is not enough",
            "target-member selection, unprovided form values and current business facts are downstream Runtime concerns and are not missing semantic conditions",
            "judge semantic result dependency independently from execution-support dataflow",
            "shared object/topic/scope and sequencing words alone never create a result dependency",
            "a stable identifier or artifact lookup needed only by execution is support, not a user-visible result dependency",
            "an explicit later reference to an earlier current-turn result, or a condition/value that genuinely consumes that result, does create a dependency",
            "use only literal USER_TEXT evidence and supplied Goal fields; do not use tool, oracle or business-state knowledge",
            "when any semantic-field mismatch exists return incomplete with literal missing_spans; otherwise return exact",
        ]
'''
    text = replace_once(text, old_blind_instruction, new_blind_instruction, "blind semantic fidelity instruction")

    old_effective = '''            effective_instruction = (
                blind_dependency_instruction
                + " For this candidate-blind audit, dependency absence must also be proven. "
                "Return dependency_decisions with exactly one row for every unordered pair of supplied Goal IDs. "
                "Each row has goal_a_id, goal_b_id and relation=a_depends_on_b|b_depends_on_a|independent. "
                "For a dependency relation also include basis_kind=result_reference|result_condition|result_value_input "
                "and basis_span copied literally from inside the dependent Goal evidence_span. "
                "Do not omit a pair merely because you believe it is independent; an empty dependency_decisions list "
                "is valid only when fewer than two Goals are supplied. Return JSON only with verdict, evidence_spans, "
                "missing_spans, dependency_decisions and reason_code."
                if blind_dependency_audit else instruction
            )
'''
    new_effective = '''            effective_instruction = (
                blind_dependency_instruction
                + " Dependency absence must also be explicitly proven. Return dependency_decisions with exactly one row "
                "for every unordered pair of supplied Goal IDs. Each row has goal_a_id, goal_b_id and "
                "relation=a_depends_on_b|b_depends_on_a|independent. For a dependency relation also include "
                "basis_kind=result_reference|result_condition|result_value_input and basis_span copied literally from inside "
                "the dependent Goal evidence_span. Do not omit independent pairs; dependency_decisions=[] is valid only when "
                "fewer than two Goals are supplied. For requested_effect or structured-condition mismatch, do not alter the "
                "dependency decisions: set verdict=incomplete, copy the literal mismatched phrase into missing_spans, and use "
                "a reason_code that identifies requested-effect fidelity or structured-condition coverage. Return JSON only "
                "with verdict, evidence_spans, missing_spans, dependency_decisions and reason_code."
                if blind_dependency_audit else instruction
            )
'''
    text = replace_once(text, old_effective, new_effective, "blind effective contract")

    old_exact_comment = '''                        # This second verifier call is dependency-only authority.
                        # Outcome grounding was already proven by the first exact
                        # call, so preserve that literal evidence while accepting
                        # only the independently validated pairwise dependency proof.
'''
    new_exact_comment = '''                        # The second verifier is an independent semantic-contract audit:
                        # dependency graph plus requested-effect/condition fidelity.
                        # Outcome grounding was already proven by the first exact
                        # call, so preserve that literal evidence while accepting
                        # only a structurally valid candidate-blind audit result.
'''
    text = replace_once(text, old_exact_comment, new_exact_comment, "blind exact comment")

    old_trigger = '''            if (
                attempt == 0
                and len(goals) > 1
                and (verdict.exact or dependency_mismatch_introduces_new_edge)
            ):
                if verdict.exact:
                    initial_exact_alignment = verdict
                # The first verifier saw Planner's candidate graph and may have
                # anchored on the same execution-dataflow mistake. Spend the
                # existing second-call budget on an independent dependency audit
                # whose Goal projection deliberately omits that graph. Runtime
                # still performs only structural comparison; it never infers an
                # edge itself.
'''
    new_trigger = '''            if (
                attempt == 0
                and (
                    verdict.exact
                    or (len(goals) > 1 and dependency_mismatch_introduces_new_edge)
                )
            ):
                if verdict.exact:
                    initial_exact_alignment = verdict
                # Every first-pass exact declaration receives one independent
                # semantic-contract re-audit within the existing verifier budget.
                # The projection hides Planner depends_on but retains the declared
                # requested_effect and condition so the verifier can detect semantic
                # substitution or a predicate that exists only in prose. Runtime
                # still never interprets language or rewrites a field itself.
'''
    text = replace_once(text, old_trigger, new_trigger, "blind audit trigger")

    old_format_repair = '''                verifier_repair = (
                    "The previous candidate-blind pairwise dependency proof was rejected by the structural grounding contract: "
                    f"{verdict.reason_code}. Re-audit every unordered Goal pair from USER_TEXT only. Assert a dependency only "
                    "when you can copy one literal basis_span from inside the dependent Goal evidence_span and classify it as "
                    "result_reference, result_condition or result_value_input. Shared scope, sentence order, lookup needs and "
                    "business execution prerequisites are not result dependencies. If no grounded positive dependency exists "
                    "for a pair, return relation=independent. Do not fabricate a basis. Return the complete dependency_decisions "
                    "array and the strict JSON fields only."
                )
'''
    new_format_repair = '''                verifier_repair = (
                    "The previous candidate-blind semantic-contract proof was rejected by the structural grounding contract: "
                    f"{verdict.reason_code}. Re-audit requested_effect fidelity, structured condition coverage, and every unordered "
                    "Goal pair from USER_TEXT only. A nearby registered effect is not a faithful replacement for an unsupported/open "
                    "business effect. An explicit filter/status/predicate must be present in Goal.condition, not merely repeated in "
                    "description/evidence prose. Do not treat target-member selection, missing form values or current business facts as "
                    "semantic conditions. If a semantic-field mismatch exists, return verdict=incomplete and copy its smallest literal "
                    "USER_TEXT span into missing_spans without proposing a replacement field/value. For dependencies, assert one only "
                    "when a literal basis_span inside the dependent Goal proves result_reference, result_condition or result_value_input; "
                    "otherwise return relation=independent. Return the complete dependency_decisions array and the strict JSON fields only."
                )
'''
    text = replace_once(text, old_format_repair, new_format_repair, "blind format repair")

    write(path, text)


def add_tests(root: Path) -> None:
    path = root / "skill-system/tests/test_wp08_attempt6_semantic_fidelity_repair.py"
    if path.exists():
        raise SystemExit("Attempt 6 semantic fidelity test file already exists")
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


def _goal(goal_id: str, span: str, effect: dict, *, condition=None) -> dict:
    row = {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": effect,
        "expected_result_cardinality": "collection",
        "required": True,
        "depends_on": [],
    }
    if condition is not None:
        row["condition"] = condition
    return row


def test_single_goal_exact_is_reaudited_for_omitted_structured_filter() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "哪些还在路上？"
    goal = _goal(
        "g1",
        text,
        {"domain": "order", "operation": "query_logistics", "object_type": "order"},
    )
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "first_pass_exact",
    })
    blind = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["在路上"],
        "dependency_decisions": [],
        "reason_code": "explicit_condition_not_structured",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("在路上",)
    assert verdict.reason_code == "explicit_condition_not_structured"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"
    audit_prompt = invoke.call_args_list[1].kwargs["payload"][-1].content
    assert "requested_effect" in audit_prompt
    assert "structured condition" in audit_prompt
    assert "target-member selection" in audit_prompt
    assert "dependency_decisions" in audit_prompt


def test_near_capability_effect_coercion_is_rejected_without_runtime_keyword_rules() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标物流，再告诉我快递员手机号"
    goals = [
        _goal(
            "g1",
            "查一下鼠标物流",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
        _goal(
            "g2",
            "再告诉我快递员手机号",
            # Reproduce Attempt 6's wrong nearby registered effect.
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": [
            {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}
        ],
        "reason_code": "requested_effect_not_faithful_to_business_effect",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("快递员手机号",)
    assert verdict.reason_code == "requested_effect_not_faithful_to_business_effect"
    assert verdict.details["dependency_pair_decisions"] == [
        {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}
    ]


def test_single_goal_with_structured_condition_can_pass_same_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "哪些还在路上？"
    condition = {
        "op": "eq",
        "left": {"source": "input", "path": "delivery_status"},
        "right": {"source": "literal", "value": "运输中"},
    }
    goal = _goal(
        "g1",
        text,
        {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        condition=condition,
    )
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "first_pass_exact",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "semantic_fields_and_dependency_exact",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.evidence_spans == (text,)
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"


def test_blind_projection_hides_candidate_dependency_but_keeps_semantic_fields() -> None:
    from agent_core.lifecycle.goal_planning import _dependency_blind_goal_projection

    condition = {
        "op": "eq",
        "left": {"source": "input", "path": "delivery_status"},
        "right": {"source": "literal", "value": "运输中"},
    }
    projected = _dependency_blind_goal_projection([
        {
            "goal_id": "g1",
            "evidence_span": "在路上",
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
            "condition": condition,
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": ["g0"],
        }
    ])[0]
    assert "depends_on" not in projected
    assert projected["condition"] == condition
    assert projected["requested_effect"]["operation"] == "query_logistics"


def test_reaudit_policy_is_domain_neutral_and_does_not_hardcode_attempt6_phrases() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("blind_dependency_instruction =")
    end = source.index("prompt = {", start)
    policy = source[start:end]
    assert "requested_effect" in policy
    assert "structured condition" in policy
    assert "unsupported/open" in policy
    assert "快递员" not in policy
    assert "在路上" not in policy
    assert "运输中" not in policy


def test_existing_capability_gate_still_requires_frozen_condition_binding() -> None:
    source = (AGENT_SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")
    assert "def _formal_goal_condition_coverage_proof" in source
    assert "formal_goal_condition_unbound" in source
    assert "parameterized_query_missing_constraint_binding" in source

''', encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
    add_tests(root)


def baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    data = json.loads(read(path))
    rel = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    target = root / rel
    data["files"][rel] = hashlib.sha256(target.read_bytes()).hexdigest()
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["generated_from"] = f"git:{product_sha}"
    write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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
        baseline(root, args.product_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
