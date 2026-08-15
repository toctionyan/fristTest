#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
TEST_PATH = "services/agent-service/tests/runtime/test_release48_registered_output_exactness_adjudication.py"
TRIGGER_PATH = ".github/release-trigger"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATH

    helper_anchor = '''\n\n\ndef _declared_scope_constraint_risk(goals: list[dict[str, Any]]) -> dict[str, Any]:\n'''
    helper = '''\n\n\ndef _declared_registered_output_exactness_risk(\n    goals: list[dict[str, Any]],\n) -> dict[str, Any]:\n    \"\"\"Expose only the structural claim that a registered semantic identity is exact.\n\n    A non-``open`` requested output has already passed the declaration schema's\n    registry-membership check before semantic alignment can freeze the plan.  Its\n    presence is therefore a high-impact semantic claim: the Planner asserts that\n    one canonical vocabulary description exactly represents the user's requested\n    information dimension/outcome.  Runtime does not interpret that claim or\n    reject it.  This signal only spends an otherwise-unused bounded verifier slot\n    on adversarial model adjudication; capability availability and tool identity\n    remain absent.\n    \"\"\"\n\n    claims: list[dict[str, str]] = []\n    for goal in goals:\n        goal_id = _clean_text(goal.get("goal_id"), limit=80)\n        effect = goal.get("requested_effect") if isinstance(goal.get("requested_effect"), dict) else {}\n        outputs = effect.get("requested_outputs") if isinstance(effect, dict) else []\n        for raw in list(outputs or []):\n            if not isinstance(raw, dict):\n                continue\n            output_id = _clean_text(raw.get("output_id"), limit=240).casefold()\n            if not output_id or output_id == "open":\n                continue\n            claims.append({\n                "goal_id": goal_id,\n                "output_id": output_id,\n                "evidence_span": _clean_text(raw.get("evidence_span"), limit=240),\n            })\n    return {\n        "risk": bool(claims),\n        "claims": claims,\n        "capability_registry_consulted": False,\n        "language_interpretation_used": False,\n        "runtime_rejection_authority": False,\n    }\n\n\ndef _declared_scope_constraint_risk(goals: list[dict[str, Any]]) -> dict[str, Any]:\n'''
    replace_once(path, helper_anchor, helper, "registered output exactness risk helper")

    old_reaudit_set = '''            semantic_claim_reaudit = verifier_repair_kind in {\n                "candidate_blind_dependency_requested_effect_reaudit",\n                "candidate_blind_dependency_scope_constraint_reaudit",\n                "candidate_blind_dependency_scope_constraint_adjudication",\n            }\n'''
    new_reaudit_set = '''            semantic_claim_reaudit = verifier_repair_kind in {\n                "candidate_blind_dependency_requested_effect_reaudit",\n                "candidate_blind_dependency_requested_output_exactness_adjudication",\n                "candidate_blind_dependency_scope_constraint_reaudit",\n                "candidate_blind_dependency_scope_constraint_adjudication",\n            }\n'''
    replace_once(path, old_reaudit_set, new_reaudit_set, "semantic claim re-audit kinds")

    old_semantic_claim_text = '''                    + "This bounded final call must re-audit only the disputed requested-effect or target-scope semantic claim. "\n'''
    new_semantic_claim_text = '''                    + "This bounded final call must re-audit only the disputed requested-effect/requested-output or target-scope semantic claim. "\n'''
    replace_once(path, old_semantic_claim_text, new_semantic_claim_text, "semantic claim re-audit description")

    old_risk_block = '''                positive_dependency_edges = bool(list(verdict.details.get("dependency_edges") or []))\n                effect_collision_risk = _requested_effect_sibling_collision_risk(goals)\n                scope_constraint_risk = _declared_scope_constraint_risk(goals)\n                if positive_dependency_edges or effect_collision_risk["risk"] or scope_constraint_risk["risk"]:\n'''
    new_risk_block = '''                positive_dependency_edges = bool(list(verdict.details.get("dependency_edges") or []))\n                effect_collision_risk = _requested_effect_sibling_collision_risk(goals)\n                scope_constraint_risk = _declared_scope_constraint_risk(goals)\n                registered_output_exactness_risk = _declared_registered_output_exactness_risk(goals)\n                if (\n                    positive_dependency_edges\n                    or effect_collision_risk["risk"]\n                    or scope_constraint_risk["risk"]\n                    or registered_output_exactness_risk["risk"]\n                ):\n'''
    replace_once(path, old_risk_block, new_risk_block, "third-slot registered output risk")

    old_scope_else = '''                    else:\n                        # A supplied scope constraint is itself a high-impact semantic\n                        # claim. The broad blind audit may miss the inverse-direction\n                        # error (identity/reference/control text mislabeled as scope), so\n                        # spend the otherwise-free third slot on that claim only.\n                        preserved_blind_dependency_details = deepcopy(verdict.details)\n                        verifier_repair_kind = "candidate_blind_dependency_scope_constraint_adjudication"\n                        verifier_repair = (\n                            "Adversarially re-audit every supplied target_candidate.scope_constraints entry from USER_TEXT only. "\n                            "Start each supplied entry from the assumption that it is NOT a population-narrowing predicate. Retain it "\n                            "only when the literal phrase itself is an explicit filter, status predicate, threshold or comparison that "\n                            "changes which members belong in this Goal's target/result population. Object identity, object/member naming, "\n                            "stable identifiers, ordinary target selection, historical/current result references, execution commitments, "\n                            "input/control wording and requested-output wording are not scope constraints even when they help locate one "\n                            "object. If any supplied entry has one of those non-scope roles, return verdict=incomplete and copy that exact "\n                            "smallest supplied literal span into missing_spans with a target-scope-constraint fidelity reason. If every "\n                            "supplied entry is genuine population narrowing and no other mismatch exists, return exact. Do not choose a tool, "\n                            "target, entity, normalized value or capability."\n                        )\n                        prompt = {\n                            "USER_TEXT_UNTRUSTED": user_text,\n                            "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                            "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                            "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                            "CANONICAL_SEMANTIC_OUTPUT_VOCABULARY": semantic_vocabulary,\n                            "DECLARED_SCOPE_CONSTRAINT_RISK": scope_constraint_risk,\n                        }\n                        continue\n                    adjudication_goals = _dependency_adjudication_goal_projection(\n'''
    new_scope_else = '''                    elif scope_constraint_risk["risk"]:\n                        # A supplied scope constraint is itself a high-impact semantic\n                        # claim. The broad blind audit may miss the inverse-direction\n                        # error (identity/reference/control text mislabeled as scope), so\n                        # spend the otherwise-free third slot on that claim only.\n                        preserved_blind_dependency_details = deepcopy(verdict.details)\n                        verifier_repair_kind = "candidate_blind_dependency_scope_constraint_adjudication"\n                        verifier_repair = (\n                            "Adversarially re-audit every supplied target_candidate.scope_constraints entry from USER_TEXT only. "\n                            "Start each supplied entry from the assumption that it is NOT a population-narrowing predicate. Retain it "\n                            "only when the literal phrase itself is an explicit filter, status predicate, threshold or comparison that "\n                            "changes which members belong in this Goal's target/result population. Object identity, object/member naming, "\n                            "stable identifiers, ordinary target selection, historical/current result references, execution commitments, "\n                            "input/control wording and requested-output wording are not scope constraints even when they help locate one "\n                            "object. If any supplied entry has one of those non-scope roles, return verdict=incomplete and copy that exact "\n                            "smallest supplied literal span into missing_spans with a target-scope-constraint fidelity reason. If every "\n                            "supplied entry is genuine population narrowing and no other mismatch exists, return exact. Do not choose a tool, "\n                            "target, entity, normalized value or capability."\n                        )\n                        prompt = {\n                            "USER_TEXT_UNTRUSTED": user_text,\n                            "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                            "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                            "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                            "CANONICAL_SEMANTIC_OUTPUT_VOCABULARY": semantic_vocabulary,\n                            "DECLARED_SCOPE_CONSTRAINT_RISK": scope_constraint_risk,\n                        }\n                        continue\n                    else:\n                        # A registered requested-output identity is itself an exactness\n                        # claim. If no dependency/sibling/scope risk already owns the\n                        # bounded third slot, adversarially challenge that claim without\n                        # revealing an oracle replacement or allowing Runtime to decide\n                        # language meaning. This closes the single-Goal/uncollided blind\n                        # spot where two broad verifier passes can both accept a nearby\n                        # canonical identity.\n                        preserved_blind_dependency_details = deepcopy(verdict.details)\n                        verifier_repair_kind = "candidate_blind_dependency_requested_output_exactness_adjudication"\n                        verifier_repair = (\n                            "Adversarially re-audit only the declared non-open requested_outputs identities from USER_TEXT and "\n                            "CANONICAL_SEMANTIC_OUTPUT_VOCABULARY. REGISTERED_OUTPUT_EXACTNESS_RISK is structural only and does not "\n                            "assert that any identity is wrong. Start each listed identity from the hypothesis of semantic substitution, "\n                            "then retain it only when the canonical vocabulary description exactly covers the literal user's requested "\n                            "information dimension/outcome. Lexical relatedness, shared subject type, a nearby status/eligibility/action "\n                            "meaning, or implementation availability is not enough. If the user's requested user-visible outcome is "\n                            "materially different and no registered description represents it exactly, return verdict=incomplete, use "\n                            "reason_code=semantic_substitution, and copy only the smallest literal USER_TEXT span proving the different "\n                            "requested outcome into missing_spans. Do not propose or expose a replacement output_id; the Semantic Writer "\n                            "must rederive and may use open. If every listed identity is exact, return exact. Do not choose a tool, consult "\n                            "capability availability, normalize to a nearby identity, or re-audit dependency decisions."\n                        )\n                        prompt = {\n                            "USER_TEXT_UNTRUSTED": user_text,\n                            "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                            "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                            "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                            "CANONICAL_SEMANTIC_OUTPUT_VOCABULARY": semantic_vocabulary,\n                            "REGISTERED_OUTPUT_EXACTNESS_RISK": registered_output_exactness_risk,\n                        }\n                        continue\n                    adjudication_goals = _dependency_adjudication_goal_projection(\n'''
    replace_once(path, old_scope_else, new_scope_else, "registered output exactness adjudication branch")


def write_regression(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"regression already exists: {TEST_PATH}")
    path.write_text(r'''from __future__ import annotations

import json
from types import SimpleNamespace

import agent_core.config as config_module
import agent_core.model_calls as model_calls_module
from agent_core.lifecycle import goal_planning
from agent_core.lifecycle.goal_planning import (
    ModelGoalAlignmentVerifier,
    _declared_registered_output_exactness_risk,
)


def _vocabulary() -> dict:
    return {
        "version": "semantic-output-vocabulary@1",
        "authority": "domain_semantics_only_capability_independent",
        "availability_exposed": False,
        "tool_names_exposed": False,
        "outputs": [
            {
                "output_id": "refund.status",
                "subject_type": "refund",
                "effect_kinds": ["read"],
                "description": "读取已经存在的退款申请记录以及当前处理状态。",
            }
        ],
    }


def _goal(*, output_id: str, evidence_span: str, open_description: str = "") -> dict:
    output = {"output_id": output_id, "evidence_span": evidence_span}
    if open_description:
        output["open_description"] = open_description
    return {
        "goal_id": "g1",
        "description": evidence_span,
        "evidence_span": evidence_span,
        "goal_type": "query",
        "required": True,
        "depends_on": [],
        "requested_effect": {
            "domain": "refund",
            "operation": "query",
            "object_type": "refund",
            "raw_description": evidence_span,
            "requested_outputs": [output],
        },
        "expected_result_cardinality": "single",
    }


def _run_verifier(monkeypatch, *, user_text: str, goals: list[dict], responses: list[dict]):
    calls: list[dict] = []

    def fake_structured_verifier_messages(*, role, instruction, decision_rules, payload, format_repair=None):
        row = {
            "role": role,
            "instruction": instruction,
            "decision_rules": list(decision_rules),
            "payload": payload,
            "format_repair": format_repair,
        }
        calls.append(row)
        return row

    def fake_invoke_model(*, purpose, model, payload):
        index = len(calls) - 1
        return SimpleNamespace(content=json.dumps(responses[index], ensure_ascii=False)), {"purpose": purpose}

    monkeypatch.setattr(config_module, "get_model", lambda: object())
    monkeypatch.setattr(model_calls_module, "structured_verifier_messages", fake_structured_verifier_messages)
    monkeypatch.setattr(model_calls_module, "invoke_model", fake_invoke_model)
    monkeypatch.setattr(goal_planning, "_semantic_vocabulary_for_alignment", _vocabulary)

    verdict = ModelGoalAlignmentVerifier().verify(
        user_text=user_text,
        goals=goals,
        known_tools=set(),
    )
    return verdict, calls


def test_release48_structural_risk_marks_non_open_identity_but_not_open():
    registered = _declared_registered_output_exactness_risk([
        _goal(output_id="refund.status", evidence_span="退款什么时候到账")
    ])
    assert registered == {
        "risk": True,
        "claims": [{
            "goal_id": "g1",
            "output_id": "refund.status",
            "evidence_span": "退款什么时候到账",
        }],
        "capability_registry_consulted": False,
        "language_interpretation_used": False,
        "runtime_rejection_authority": False,
    }

    open_risk = _declared_registered_output_exactness_risk([
        _goal(
            output_id="open",
            evidence_span="退款什么时候到账",
            open_description="退款到账时间",
        )
    ])
    assert open_risk["risk"] is False
    assert open_risk["claims"] == []


def test_release48_third_slot_adversarially_rejects_nearby_registered_output(monkeypatch):
    user_text = "鼠标订单的退款什么时候到账"
    evidence = "退款什么时候到账"
    goals = [_goal(output_id="refund.status", evidence_span=evidence)]
    responses = [
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_decisions": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "incomplete",
            "evidence_spans": [evidence],
            "missing_spans": [evidence],
            "reason_code": "semantic_substitution",
        },
    ]

    verdict, calls = _run_verifier(
        monkeypatch,
        user_text=user_text,
        goals=goals,
        responses=responses,
    )

    assert len(calls) == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "semantic_substitution"
    assert verdict.missing_spans == (evidence,)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_output_exactness_adjudication"

    third = calls[2]
    assert "Start each listed identity from the hypothesis of semantic substitution" in third["format_repair"]
    assert third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"]["claims"] == [{
        "goal_id": "g1",
        "output_id": "refund.status",
        "evidence_span": evidence,
    }]
    assert third["payload"]["CANONICAL_SEMANTIC_OUTPUT_VOCABULARY"] == _vocabulary()
    assert "capability" not in json.dumps(third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"], ensure_ascii=False).casefold()


def test_release48_exact_registered_output_survives_adversarial_third_slot(monkeypatch):
    user_text = "鼠标订单退款状态怎么样"
    evidence = "退款状态怎么样"
    goals = [_goal(output_id="refund.status", evidence_span=evidence)]
    responses = [
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_decisions": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "reason_code": "requested_output_exactness_confirmed",
        },
    ]

    verdict, calls = _run_verifier(
        monkeypatch,
        user_text=user_text,
        goals=goals,
        responses=responses,
    )

    assert len(calls) == 3
    assert verdict.exact is True
    assert verdict.reason_code == "requested_output_exactness_confirmed"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True


def test_release48_open_identity_does_not_spend_registered_output_third_slot(monkeypatch):
    user_text = "鼠标订单的退款什么时候到账"
    evidence = "退款什么时候到账"
    goals = [_goal(output_id="open", evidence_span=evidence, open_description="退款到账时间")]
    responses = [
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_decisions": [],
            "reason_code": "goal_alignment_exact",
        },
    ]

    verdict, calls = _run_verifier(
        monkeypatch,
        user_text=user_text,
        goals=goals,
        responses=responses,
    )

    assert len(calls) == 2
    assert verdict.exact is True
''', encoding="utf-8")


def patch_release_trigger(root: Path) -> None:
    (root / TRIGGER_PATH).write_text(
        "release_request: 2026-08-15T20:35:00+08:00\n"
        "provider: deepseek\n"
        "model: deepseek-v4-flash\n"
        "embedding_model: text-embedding-v4\n"
        "embedding_dimension: 1024\n"
        "reason: rerun protected release after registered requested-output exactness adjudication\n",
        encoding="utf-8",
    )


def patch(root: Path) -> None:
    patch_goal_planning(root)
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
