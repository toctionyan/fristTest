from pathlib import Path
import hashlib
import json
from textwrap import dedent

root = Path('.')
protocol_path = root / 'services/agent-service/src/agent_core/lifecycle/protocol.py'
planning_path = root / 'services/agent-service/src/agent_core/lifecycle/goal_planning.py'
baseline_path = root / 'skill-system/registry/product-source-baseline.json'
test_path = root / 'skill-system/tests/test_v20_18_scope_identity_adjudication.py'
workflow_path = root / '.github/workflows/tmp-v20.18-scope-adjudication-repair.yml'
script_path = root / 'scripts/tmp_apply_v20_18_scope_repair.py'

protocol = protocol_path.read_text(encoding='utf-8')
old = (
    '        "不得在此猜测归一化业务值；普通目标集合筛选也不得为了结构化而伪装成 Goal.condition。历史结果/成员引用"\n'
    '        "必须只进入 reference_expression；不要提交/只查询等执行承诺、输入或控制措辞也不是人口筛选，禁止复制到"\n'
)
new = (
    '        "不得在此猜测归一化业务值；普通目标集合筛选也不得为了结构化而伪装成 Goal.condition。对象名称、成员名称、稳定标识等"\n'
    '        "只用于识别或选择目标的身份文字不是人口筛选，禁止写入 scope_constraints。历史结果/成员引用必须只进入 reference_expression；"\n'
    '        "不要提交/只查询等执行承诺、输入或控制措辞也不是人口筛选，禁止复制到"\n'
)
assert protocol.count(old) == 1, ('protocol', protocol.count(old))
protocol_path.write_text(protocol.replace(old, new), encoding='utf-8')

planning = planning_path.read_text(encoding='utf-8')
marker = '\n\ndef _requested_effect_reaudit_collision_guard(\n'
helper = dedent('''


def _declared_scope_constraint_risk(goals: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose only the structural fact that Planner supplied scope constraints.

    This signal grants no rejection or language authority. It only reserves the
    already-bounded third verifier slot for an adversarial inverse-direction
    semantic audit so an exact broad audit cannot silently bless target identity
    or another non-scope phrase as a population-narrowing predicate.
    """
    rows: list[dict[str, Any]] = []
    for goal in goals:
        goal_id = _clean_text(goal.get("goal_id"), limit=80)
        target = goal.get("target_candidate") if isinstance(goal.get("target_candidate"), dict) else {}
        for index, raw in enumerate(list(target.get("scope_constraints") or [])):
            if not isinstance(raw, dict):
                continue
            span = _clean_text(raw.get("evidence_span"), limit=240)
            if not span:
                continue
            rows.append({"goal_id": goal_id, "scope_index": index, "evidence_span": span})
    return {
        "risk": bool(rows),
        "constraints": rows,
        "language_interpretation_used": False,
        "runtime_rejection_authority": False,
    }
''')
assert planning.count(marker) == 1, ('helper', planning.count(marker))
planning = planning.replace(marker, helper + marker)

old = '''def _dependency_adjudication_goal_projection(
    goals: list[dict[str, Any]],
    *,
    include_requested_effect: bool = False,
) -> list[dict[str, Any]]:'''
new = '''def _dependency_adjudication_goal_projection(
    goals: list[dict[str, Any]],
    *,
    include_requested_effect: bool = False,
    include_target_candidate: bool = False,
) -> list[dict[str, Any]]:'''
assert planning.count(old) == 1, ('adjudication-signature', planning.count(old))
planning = planning.replace(old, new)

old = '''        if include_requested_effect and isinstance(goal.get("requested_effect"), dict):
            row["requested_effect"] = deepcopy(goal.get("requested_effect"))
        rows.append(row)'''
new = '''        if include_requested_effect and isinstance(goal.get("requested_effect"), dict):
            row["requested_effect"] = deepcopy(goal.get("requested_effect"))
        if include_target_candidate and isinstance(goal.get("target_candidate"), dict):
            row["target_candidate"] = deepcopy(goal.get("target_candidate"))
        rows.append(row)'''
assert planning.count(old) == 1, ('adjudication-body', planning.count(old))
planning = planning.replace(old, new)

old = '''            semantic_claim_reaudit = verifier_repair_kind in {
                "candidate_blind_dependency_requested_effect_reaudit",
                "candidate_blind_dependency_scope_constraint_reaudit",
            }'''
new = '''            semantic_claim_reaudit = verifier_repair_kind in {
                "candidate_blind_dependency_requested_effect_reaudit",
                "candidate_blind_dependency_scope_constraint_reaudit",
                "candidate_blind_dependency_scope_constraint_adjudication",
            }'''
assert planning.count(old) == 1, ('semantic-claim-set', planning.count(old))
planning = planning.replace(old, new)

old = '''                positive_dependency_edges = bool(list(verdict.details.get("dependency_edges") or []))
                effect_collision_risk = _requested_effect_sibling_collision_risk(goals)
                if positive_dependency_edges or effect_collision_risk["risk"]:'''
new = '''                positive_dependency_edges = bool(list(verdict.details.get("dependency_edges") or []))
                effect_collision_risk = _requested_effect_sibling_collision_risk(goals)
                scope_constraint_risk = _declared_scope_constraint_risk(goals)
                if positive_dependency_edges or effect_collision_risk["risk"] or scope_constraint_risk["risk"]:'''
assert planning.count(old) == 1, ('third-slot-condition', planning.count(old))
planning = planning.replace(old, new)

old = '''                    else:
                        verifier_repair_kind = "candidate_blind_dependency_effect_collision_adjudication"
                        verifier_repair = (
                            "Adversarially re-audit the structurally signaled sibling requested-effect collision from USER_TEXT only while also "'''
new = '''                    elif effect_collision_risk["risk"]:
                        verifier_repair_kind = "candidate_blind_dependency_effect_collision_adjudication"
                        verifier_repair = (
                            "Adversarially re-audit the structurally signaled sibling requested-effect collision from USER_TEXT only while also "'''
assert planning.count(old) == 1, ('effect-branch', planning.count(old))
planning = planning.replace(old, new)

old = '''                            "dependency_decisions row using only literal result-reference/result-condition/result-value evidence; otherwise mark it "
                            "independent."
                        )
                    adjudication_goals = _dependency_adjudication_goal_projection('''
new = '''                            "dependency_decisions row using only literal result-reference/result-condition/result-value evidence; otherwise mark it "
                            "independent."
                        )
                    else:
                        # A supplied scope constraint is itself a high-impact semantic
                        # claim. The broad blind audit may miss the inverse-direction
                        # error (identity/reference/control text mislabeled as scope), so
                        # spend the otherwise-free third slot on that claim only.
                        preserved_blind_dependency_details = deepcopy(verdict.details)
                        verifier_repair_kind = "candidate_blind_dependency_scope_constraint_adjudication"
                        verifier_repair = (
                            "Adversarially re-audit every supplied target_candidate.scope_constraints entry from USER_TEXT only. "
                            "Start each supplied entry from the assumption that it is NOT a population-narrowing predicate. Retain it "
                            "only when the literal phrase itself is an explicit filter, status predicate, threshold or comparison that "
                            "changes which members belong in this Goal's target/result population. Object identity, object/member naming, "
                            "stable identifiers, ordinary target selection, historical/current result references, execution commitments, "
                            "input/control wording and requested-output wording are not scope constraints even when they help locate one "
                            "object. If any supplied entry has one of those non-scope roles, return verdict=incomplete and copy that exact "
                            "smallest supplied literal span into missing_spans with a target-scope-constraint fidelity reason. If every "
                            "supplied entry is genuine population narrowing and no other mismatch exists, return exact. Do not choose a tool, "
                            "target, entity, normalized value or capability."
                        )
                        prompt = {
                            "USER_TEXT_UNTRUSTED": user_text,
                            "DECLARED_GOALS": _dependency_blind_goal_projection(goals),
                            "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),
                            "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
                            "DECLARED_SCOPE_CONSTRAINT_RISK": scope_constraint_risk,
                        }
                        continue
                    adjudication_goals = _dependency_adjudication_goal_projection('''
assert planning.count(old) == 1, ('scope-branch-anchor', planning.count(old))
planning = planning.replace(old, new)

old = '''                        goals,
                        include_requested_effect=bool(effect_collision_risk["risk"]),
                    )'''
new = '''                        goals,
                        include_requested_effect=bool(effect_collision_risk["risk"]),
                        include_target_candidate=bool(scope_constraint_risk["risk"]),
                    )'''
assert planning.count(old) == 1, ('adjudication-call', planning.count(old))
planning = planning.replace(old, new)

old = '''                    if effect_collision_risk["risk"]:
                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk
                    continue'''
new = '''                    if effect_collision_risk["risk"]:
                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk
                    if scope_constraint_risk["risk"]:
                        prompt["DECLARED_SCOPE_CONSTRAINT_RISK"] = scope_constraint_risk
                    continue'''
assert planning.count(old) == 1, ('adjudication-prompt', planning.count(old))
planning = planning.replace(old, new)
planning_path.write_text(planning, encoding='utf-8')

test_source = dedent('''
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(text: str, *, scope: str | None) -> dict:
    goal = {
        "goal_id": "g1",
        "description": text,
        "evidence_span": text,
        "requested_effect": {
            "domain": "order",
            "operation": "query",
            "object_type": "order",
            "requested_outputs": [{"output_id": "order.collection", "evidence_span": text}],
            "raw_description": text,
        },
        "expected_result_cardinality": "collection",
        "required": True,
        "depends_on": [],
    }
    if scope is not None:
        goal["target_candidate"] = {"scope_constraints": [{"evidence_span": scope}]}
    return goal


def _first_exact(text: str):
    return _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "outcome_preserved",
    })


def _blind_exact(text: str):
    return _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "blind_audit_exact",
    })


def test_exact_blind_audit_cannot_silently_bless_target_identity_as_scope() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下某型号订单"
    scope = "某型号"
    third = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": [scope],
        "reason_code": "target_scope_constraint_fidelity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[_first_exact(text), _blind_exact(text), third],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text, scope=scope)],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == (scope,)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_adjudication"
    payload = invoke.call_args_list[2].kwargs["payload"][-1].content
    assert "Start each supplied entry from the assumption that it is NOT" in payload
    assert "Object identity, object/member naming" in payload


def test_real_population_filter_survives_same_scope_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下满足条件的订单"
    scope = "满足条件"
    third = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "reason_code": "scope_constraint_is_population_narrowing",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[_first_exact(text), _blind_exact(text), third],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text, scope=scope)],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_adjudication"


def test_scope_free_goal_keeps_two_call_fast_path() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下订单"
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[_first_exact(text), _blind_exact(text)],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text, scope=None)],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact


def test_declaration_protocol_excludes_identity_from_scope_contract() -> None:
    from agent_core.lifecycle.protocol import TARGET_CANDIDATE_SCHEMA

    description = str(TARGET_CANDIDATE_SCHEMA["description"])
    assert "只用于识别或选择目标的身份文字不是人口筛选" in description
    assert "禁止写入 scope_constraints" in description
''').lstrip()
test_path.write_text(test_source, encoding='utf-8')

baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
files = baseline.get('files')
assert isinstance(files, dict)
for path in (protocol_path, planning_path):
    rel = path.as_posix()
    assert rel in files, rel
    files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

workflow_path.unlink()
script_path.unlink()
