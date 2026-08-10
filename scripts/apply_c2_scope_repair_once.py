from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = ROOT / "services/agent-service/src/agent_core/runtime/capability_gate.py"
ANSWER = ROOT / "services/agent-service/src/agent_core/runtime/answer_release_alignment.py"
TEST = ROOT / "skill-system/tests/test_c2_strong_context_scope_repair.py"
BASELINE = ROOT / "skill-system/registry/product-source-baseline.json"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


capability = CAPABILITY.read_text(encoding="utf-8")
capability = replace_once(
    capability,
    "from agent_core.kernel.semantic_contract import semantic_goals\nfrom agent_core.kernel.semantic_contract import prove_goal_target_compatibility\n",
    "from agent_core.kernel.semantic_contract import semantic_goals\nfrom agent_core.kernel.semantic_contract import prove_goal_target_compatibility\nfrom agent_core.lifecycle.condition_expression import condition_operands\n",
    label="condition_operands import",
)

helper = '''\n\ndef _formal_goal_condition_coverage_proof(\n    state: dict[str, Any],\n    *,\n    goal_ids: set[str],\n    parameterization: dict[str, Any],\n) -> dict[str, Any]:\n    """Prove that frozen Goal conditions affecting result scope are bound.\n\n    Candidate ``constraint_bindings`` prove only what the model chose to\n    declare.  They cannot prove that a decisive condition from the already\n    frozen semantic contract was not omitted.  This proof therefore projects\n    only ``target_fact`` / ``input`` operands from the bound Goals and requires\n    one unambiguous covered formal-argument leaf for each.  ``goal_output``\n    operands remain workflow dependencies and are intentionally not converted\n    into Tool parameters here.\n    """\n    formal_by_id = {\n        str(goal.get("goal_id") or ""): goal\n        for goal in semantic_goals(state)\n        if str(goal.get("goal_id") or "")\n    }\n    requirements: list[dict[str, Any]] = []\n    seen: set[tuple[str, str, str]] = set()\n    for goal_id in sorted(goal_ids):\n        goal = formal_by_id.get(goal_id) or {}\n        condition = goal.get("condition") if isinstance(goal.get("condition"), dict) else None\n        if condition is None:\n            continue\n        for operand in condition_operands(condition):\n            source = str(operand.get("source") or "")\n            path = str(operand.get("path") or "").strip()\n            if source not in {"target_fact", "input"} or not path:\n                continue\n            key = (goal_id, source, path)\n            if key in seen:\n                continue\n            seen.add(key)\n            requirements.append({\n                "goal_id": goal_id,\n                "operand_source": source,\n                "condition_path": path,\n                "parameter_leaf": path.rsplit(".", 1)[-1],\n            })\n\n    covered_bindings = [\n        dict(row)\n        for row in list(parameterization.get("bindings") or [])\n        if isinstance(row, dict) and str(row.get("status") or "") == "covered"\n    ]\n    checks: list[dict[str, Any]] = []\n    errors: list[str] = []\n    for requirement in requirements:\n        leaf = str(requirement["parameter_leaf"])\n        matches = [\n            row\n            for row in covered_bindings\n            if str(row.get("parameter_path") or "").rsplit(".", 1)[-1] == leaf\n        ]\n        if len(matches) == 1:\n            checks.append({\n                **requirement,\n                "status": "covered",\n                "matched_parameter_path": str(matches[0].get("parameter_path") or ""),\n            })\n            continue\n        code = (\n            f"formal_goal_condition_unbound:{requirement['goal_id']}:{requirement['condition_path']}"\n            if not matches\n            else f"formal_goal_condition_ambiguous:{requirement['goal_id']}:{requirement['condition_path']}"\n        )\n        errors.append(code)\n        checks.append({\n            **requirement,\n            "status": "uncovered",\n            "matched_parameter_paths": [str(row.get("parameter_path") or "") for row in matches],\n        })\n\n    return {\n        "version": "formal-goal-condition-coverage@1",\n        "required": bool(requirements),\n        "goal_ids": sorted(goal_ids),\n        "requirements": requirements,\n        "checks": checks,\n        "complete": not errors,\n        "errors": errors,\n    }\n'''
capability = replace_once(
    capability,
    '''    return {\n        "version": "constraint-coverage-proof@1",\n        "bindings": rows,\n        "parameterization_complete": not errors,\n        "errors": errors,\n    }\n\n\ndef _visible_reference_proof''',
    '''    return {\n        "version": "constraint-coverage-proof@1",\n        "bindings": rows,\n        "parameterization_complete": not errors,\n        "errors": errors,\n    }\n''' + helper + '''\n\ndef _visible_reference_proof''',
    label="formal condition helper insertion",
)
capability = replace_once(
    capability,
    '''    goal_ids = {str(value) for value in list(effect.get("goal_ids") or []) if str(value)}\n    semantic_reference_binding = _semantic_reference_binding_proof(''',
    '''    goal_ids = {str(value) for value in list(effect.get("goal_ids") or []) if str(value)}\n    formal_condition_coverage = _formal_goal_condition_coverage_proof(\n        state, goal_ids=goal_ids, parameterization=parameterization\n    )\n    semantic_reference_binding = _semantic_reference_binding_proof(''',
    label="formal condition proof call",
)
capability = replace_once(
    capability,
    '''        and parameterization.get("parameterization_complete")\n        and visible_reference.get("complete")''',
    '''        and parameterization.get("parameterization_complete")\n        and formal_condition_coverage.get("complete")\n        and visible_reference.get("complete")''',
    label="semantic verifier condition gate",
)
capability = replace_once(
    capability,
    '''        "parameterization_complete": bool(parameterization.get("parameterization_complete")),\n        "visible_result_reference": visible_reference,''',
    '''        "parameterization_complete": bool(parameterization.get("parameterization_complete")),\n        "formal_goal_condition_coverage": formal_condition_coverage,\n        "visible_result_reference": visible_reference,''',
    label="match proof field",
)
capability = replace_once(
    capability,
    '''"constraint_errors": [*list(arg_errors), *list(parameterization.get("errors") or []), *list(visible_reference.get("errors") or [])''',
    '''"constraint_errors": [*list(arg_errors), *list(parameterization.get("errors") or []), *list(formal_condition_coverage.get("errors") or []), *list(visible_reference.get("errors") or [])''',
    label="constraint errors include formal conditions",
)
capability = replace_once(
    capability,
    '''"exact_match": bool(contract is not None and not arg_errors and parameterization.get("parameterization_complete") and visible_reference.get("complete")''',
    '''"exact_match": bool(contract is not None and not arg_errors and parameterization.get("parameterization_complete") and formal_condition_coverage.get("complete") and visible_reference.get("complete")''',
    label="exact match formal condition",
)
capability = replace_once(
    capability,
    '''if contract is None or arg_errors or not parameterization.get("parameterization_complete") or not visible_reference.get("complete")''',
    '''if contract is None or arg_errors or not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete") or not visible_reference.get("complete")''',
    label="permit rejection formal condition",
)
capability = replace_once(
    capability,
    '''else "CAPABILITY_PARAMETERIZATION_INCOMPLETE"\n                    if contract is not None and not arg_errors and not parameterization.get("parameterization_complete")''',
    '''else "CAPABILITY_PARAMETERIZATION_INCOMPLETE"\n                    if contract is not None and not arg_errors and (not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete"))''',
    label="rejection code formal condition",
)
capability = replace_once(
    capability,
    '''else "当前请求中的决定性条件没有被完整绑定到正式参数，系统不会用更宽泛查询代替。"\n                    if contract is not None and not arg_errors and not parameterization.get("parameterization_complete")''',
    '''else "当前请求中的决定性条件没有被完整绑定到正式参数，系统不会用更宽泛查询代替。"\n                    if contract is not None and not arg_errors and (not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete"))''',
    label="rejection message formal condition",
)
CAPABILITY.write_text(capability, encoding="utf-8")

answer = ANSWER.read_text(encoding="utf-8")
answer = replace_once(
    answer,
    '''    proofs = _effective_match_proofs(result)\n    for proof in proofs:\n        if proof.get("candidate_tool") and not bool(proof.get("parameterization_complete", True)):\n            return AnswerAlignmentVerdict("reject", "capability_parameterization_incomplete", "deterministic", False, {"match_proof": proof})\n    for evidence in _runtime_evidence(result):''',
    '''    proofs = _effective_match_proofs(result)\n    runtime_evidence = _runtime_evidence(result)\n    for proof in proofs:\n        if proof.get("candidate_tool") and not bool(proof.get("parameterization_complete", True)):\n            return AnswerAlignmentVerdict("reject", "capability_parameterization_incomplete", "deterministic", False, {"match_proof": proof})\n        formal_condition = (\n            proof.get("formal_goal_condition_coverage")\n            if isinstance(proof.get("formal_goal_condition_coverage"), dict)\n            else {}\n        )\n        if proof.get("candidate_tool") and bool(formal_condition.get("required")) and not bool(formal_condition.get("complete")):\n            return AnswerAlignmentVerdict(\n                "reject",\n                "formal_goal_condition_parameterization_incomplete",\n                "deterministic",\n                False,\n                {"match_proof": proof},\n            )\n    condition_required_tools = {\n        str(proof.get("candidate_tool") or "")\n        for proof in proofs\n        if str(proof.get("candidate_tool") or "")\n        and isinstance(proof.get("formal_goal_condition_coverage"), dict)\n        and bool(proof["formal_goal_condition_coverage"].get("required"))\n        and bool(proof["formal_goal_condition_coverage"].get("complete"))\n    }\n    parameterized_tools = {\n        str(evidence.get("tool_name") or "")\n        for evidence in runtime_evidence\n        if str(evidence.get("evidence_kind") or "") == "current_tool_parameterization"\n        and bool(evidence.get("ok"))\n        and isinstance(evidence.get("parameterization"), dict)\n    }\n    missing_condition_execution = sorted(condition_required_tools - parameterized_tools)\n    if missing_condition_execution:\n        return AnswerAlignmentVerdict(\n            "reject",\n            "required_condition_execution_evidence_missing",\n            "deterministic",\n            False,\n            {"tools": missing_condition_execution},\n        )\n    for evidence in runtime_evidence:''',
    label="answer deterministic condition evidence",
)
answer = replace_once(
    answer,
    '''            and bool(proof.get("parameterization_complete", True))\n            and bool((proof.get("visible_result_reference") or {}).get("complete", True))''',
    '''            and bool(proof.get("parameterization_complete", True))\n            and (\n                not bool((proof.get("formal_goal_condition_coverage") or {}).get("required"))\n                or bool((proof.get("formal_goal_condition_coverage") or {}).get("complete"))\n            )\n            and bool((proof.get("visible_result_reference") or {}).get("complete", True))''',
    label="fast pass formal condition guard",
)
ANSWER.write_text(answer, encoding="utf-8")

TEST.write_text(r'''from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def _condition_goal() -> dict:
    return {
        "goal_id": "g1",
        "condition": {
            "version": "condition-expression@1",
            "op": "eq",
            "left": {"source": "target_fact", "path": "delivery_status"},
            "right": {"source": "literal", "value": "运输中"},
        },
        "requested_effect": {
            "domain": "logistics",
            "operation": "query",
            "object_type": "order",
        },
    }


def test_formal_goal_condition_rejects_empty_candidate_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    monkeypatch.setattr(capability_gate, "semantic_goals", lambda _state: [_condition_goal()])
    proof = capability_gate._formal_goal_condition_coverage_proof(
        {},
        goal_ids={"g1"},
        parameterization={"bindings": [], "parameterization_complete": True, "errors": []},
    )

    assert proof["required"] is True
    assert proof["complete"] is False
    assert proof["requirements"] == [{
        "goal_id": "g1",
        "operand_source": "target_fact",
        "condition_path": "delivery_status",
        "parameter_leaf": "delivery_status",
    }]
    assert proof["errors"] == ["formal_goal_condition_unbound:g1:delivery_status"]


def test_formal_goal_condition_accepts_exact_formal_argument_leaf(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    monkeypatch.setattr(capability_gate, "semantic_goals", lambda _state: [_condition_goal()])
    proof = capability_gate._formal_goal_condition_coverage_proof(
        {},
        goal_ids={"g1"},
        parameterization={
            "bindings": [{
                "kind": "condition",
                "source_span": "哪些还在路上",
                "parameter_path": "query.delivery_status",
                "normalized_value": "运输中",
                "actual_value": "运输中",
                "status": "covered",
            }],
            "parameterization_complete": True,
            "errors": [],
        },
    )

    assert proof["complete"] is True
    assert proof["checks"][0]["matched_parameter_path"] == "query.delivery_status"


def test_goal_output_condition_is_workflow_dependency_not_tool_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    goal = {
        "goal_id": "g2",
        "condition": {
            "version": "condition-expression@1",
            "op": "eq",
            "left": {"source": "goal_output", "goal_id": "g1", "path": "eligible"},
            "right": {"source": "literal", "value": True},
        },
    }
    monkeypatch.setattr(capability_gate, "semantic_goals", lambda _state: [goal])
    proof = capability_gate._formal_goal_condition_coverage_proof(
        {}, goal_ids={"g2"}, parameterization={"bindings": []}
    )
    assert proof["required"] is False
    assert proof["complete"] is True


def test_answer_release_rejects_conditioned_result_without_backend_execution_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import answer_release_alignment as alignment

    proof = {
        "candidate_tool": "get_order_logistics",
        "parameterization_complete": True,
        "formal_goal_condition_coverage": {
            "required": True,
            "complete": True,
            "requirements": [{"condition_path": "delivery_status"}],
        },
    }
    monkeypatch.setattr(alignment, "_formal_goals", lambda _result: [])
    monkeypatch.setattr(alignment, "_effective_match_proofs", lambda _result: [proof])
    monkeypatch.setattr(alignment, "_runtime_evidence", lambda _result: [])

    verdict = alignment._deterministic_verdict(result={}, blocks=[])
    assert verdict.decision == "reject"
    assert verdict.reason_code == "required_condition_execution_evidence_missing"
    assert verdict.details["tools"] == ["get_order_logistics"]


def test_answer_release_accepts_condition_only_after_backend_proves_same_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import answer_release_alignment as alignment

    proof = {
        "candidate_tool": "get_order_logistics",
        "parameterization_complete": True,
        "formal_goal_condition_coverage": {"required": True, "complete": True},
    }
    evidence = [{
        "evidence_kind": "current_tool_parameterization",
        "tool_name": "get_order_logistics",
        "ok": True,
        "parameterization": {
            "required_backend_conditions": {"delivery_status": "运输中"},
            "backend_applied_conditions": {"delivery_status": "运输中"},
            "source_population_count": 4,
            "matched_population_count": 1,
            "presentation_population": "matched_members",
        },
    }]
    monkeypatch.setattr(alignment, "_formal_goals", lambda _result: [])
    monkeypatch.setattr(alignment, "_effective_match_proofs", lambda _result: [proof])
    monkeypatch.setattr(alignment, "_runtime_evidence", lambda _result: evidence)

    verdict = alignment._deterministic_verdict(
        result={},
        blocks=[{"contract_id": "commerce.logistics_overview@1", "items": [{}]}],
    )
    assert verdict.decision == "pass"
    assert verdict.reason_code == "deterministic_evidence_complete"


def test_exact_scope_fast_path_cannot_bypass_incomplete_formal_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import answer_release_alignment as alignment

    proof = {
        "candidate_tool": "get_order_logistics",
        "exact_match": True,
        "parameterization_complete": True,
        "formal_goal_condition_coverage": {"required": True, "complete": False},
        "visible_result_reference": {"complete": True},
        "explicit_member_scope": {"complete": True},
        "derived_collection_scope": {"complete": True},
        "semantic_verdict": {"verdict": "exact"},
        "constraint_errors": [],
    }
    result = {
        "runtime_outcome": {"outcome_type": "query", "evidence_handles": ["h_result:broad"]},
        "tool_trace": [{"classification": "observation", "result": {"ok": True}}],
    }
    monkeypatch.setattr(alignment, "_effective_match_proofs", lambda _result: [proof])
    monkeypatch.setattr(alignment, "validate_visible_result_ref", lambda **_kwargs: ({"result_ref": "h_result:broad"}, None))
    monkeypatch.setattr(alignment, "_formal_goals", lambda _result: [])

    assert alignment._deterministic_release_authority(result) is None


def test_attempt8_regression_contract_is_structural_not_phrase_heuristic() -> None:
    capability_source = (AGENT_SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")
    answer_source = (AGENT_SRC / "agent_core/runtime/answer_release_alignment.py").read_text(encoding="utf-8")

    assert "formal_goal_condition_coverage" in capability_source
    assert "condition_operands" in capability_source
    assert "required_condition_execution_evidence_missing" in answer_source
    assert "哪些还在路上" not in capability_source
    assert "在路上" not in answer_source
''', encoding="utf-8")

# Refresh the strict protected-source baseline after the two production-source edits.
payload = json.loads(BASELINE.read_text(encoding="utf-8"))
roots = [str(value) for value in payload.get("protected_roots") or ()]
raw = subprocess.check_output(["git", "ls-files", "-z", "--", *roots], cwd=ROOT)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
payload["generated_from"] = "git:" + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
payload["file_count"] = len(tracked)
payload["files"] = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in tracked
}
BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps({
    "status": "PATCHED",
    "files": [str(CAPABILITY.relative_to(ROOT)), str(ANSWER.relative_to(ROOT)), str(TEST.relative_to(ROOT)), str(BASELINE.relative_to(ROOT))],
}, ensure_ascii=False))
