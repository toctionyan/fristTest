#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


TEST_PATH = "skill-system/tests/test_wp08_attempt5_effect_collision_and_control_reaudit.py"
SOURCE_PATHS = (
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATHS[0]

    helper_anchor = '''def _requested_effect_reaudit_collision_guard(
    goals: list[dict[str, Any]],
    missing_spans: tuple[str, ...],
) -> dict[str, Any]:
'''
    helper = '''def _requested_effect_sibling_collision_risk(
    goals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project structural sibling-effect collisions for model adjudication.

    Sharing one structured requested-effect identity is not itself a semantic
    error: two siblings may legitimately request the same effect on different
    targets. Runtime therefore never rejects from this signal. It only spends
    the already-bounded third verifier slot when distinct sibling evidence spans
    reuse an identical structured effect identity, so the independent model can
    adversarially check whether one user-visible effect was collapsed into its
    neighbor. No capability registry or business vocabulary is consulted.
    """
    by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for goal in goals:
        identity = _requested_effect_identity_key(goal)
        if all(identity):
            by_identity.setdefault(identity, []).append(goal)
    collisions: list[dict[str, Any]] = []
    for identity, rows in by_identity.items():
        goal_ids = sorted({
            _clean_text(row.get("goal_id"), limit=80)
            for row in rows
            if _clean_text(row.get("goal_id"), limit=80)
        })
        evidence_spans = sorted({
            _clean_text(row.get("evidence_span"), limit=240)
            for row in rows
            if _clean_text(row.get("evidence_span"), limit=240)
        })
        if len(goal_ids) < 2 or len(evidence_spans) < 2:
            continue
        collisions.append({
            "effect_identity": {
                "domain": identity[0],
                "operation": identity[1],
                "object_type": identity[2],
            },
            "goal_ids": goal_ids,
            "evidence_spans": evidence_spans,
        })
    return {
        "risk": bool(collisions),
        "collisions": collisions,
        "capability_registry_consulted": False,
        "language_interpretation_used": False,
        "runtime_rejection_authority": False,
    }


def _requested_effect_reaudit_collision_guard(
    goals: list[dict[str, Any]],
    missing_spans: tuple[str, ...],
) -> dict[str, Any]:
'''
    replace_once(path, helper_anchor, helper, "goal-planning sibling collision helper")

    old_block = '''            if (
                blind_dependency_audit
                and verifier_repair_kind == "candidate_blind_dependency_reaudit"
                and verdict.exact
                and isinstance(verdict.details, dict)
                and verdict.details.get("dependency_proof_complete") is True
                and verdict.details.get("dependency_graph_match") is True
                and bool(list(verdict.details.get("dependency_edges") or []))
                and attempt < 2
            ):
                # Positive same-turn result dependencies are high-impact because a
                # false edge blocks an otherwise independently reportable sibling.
                # Spend the existing third verifier slot on an adversarial graph-only
                # confirmation while still hiding Planner depends_on. Runtime does
                # not infer language or rewrite the graph; disagreement stays
                # fail-closed and flows through ordinary redeclaration feedback.
                verifier_repair_kind = "candidate_blind_dependency_positive_edge_adjudication"
                verifier_repair = (
                    "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Start every unordered "
                    "Goal pair from independent and retain a positive edge only when a literal basis_span inside the dependent Goal "
                    "proves that the user-visible later outcome itself consumes the earlier current-turn Goal result as a result_reference, "
                    "result_condition or result_value_input. Sequencing, shared topic/scope, repeated business object, and stable-ID/artifact "
                    "lookup needed only by execution are not result dependencies. Do not see or reconstruct Planner depends_on from tool "
                    "needs. Return one dependency_decisions row for every unordered Goal pair together with the normal requested-effect and "
                    "scope audit fields. A true explicit result reference/condition/value dependency must still be retained."
                )
                prompt = {
                    "USER_TEXT_UNTRUSTED": user_text,
                    "DECLARED_GOALS": _dependency_blind_goal_projection(goals),
                    "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),
                    "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
                }
                continue
'''
    new_block = '''            if (
                blind_dependency_audit
                and verifier_repair_kind == "candidate_blind_dependency_reaudit"
                and verdict.exact
                and isinstance(verdict.details, dict)
                and verdict.details.get("dependency_proof_complete") is True
                and verdict.details.get("dependency_graph_match") is True
                and attempt < 2
            ):
                positive_dependency_edges = bool(list(verdict.details.get("dependency_edges") or []))
                effect_collision_risk = _requested_effect_sibling_collision_risk(goals)
                if positive_dependency_edges or effect_collision_risk["risk"]:
                    # The third verifier slot is already the bounded adversarial
                    # adjudicator for high-impact semantic claims. Keep one slot:
                    # confirm positive dependency edges and, when structurally
                    # signaled, independently challenge sibling effect-identity
                    # reuse. Runtime never decides language meaning or rewrites a
                    # requested effect from this structural signal.
                    if positive_dependency_edges:
                        verifier_repair_kind = "candidate_blind_dependency_positive_edge_adjudication"
                        verifier_repair = (
                            "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Start every unordered "
                            "Goal pair from independent and retain a positive edge only when a literal basis_span inside the dependent Goal "
                            "proves that the user-visible later outcome itself consumes the earlier current-turn Goal result as a result_reference, "
                            "result_condition or result_value_input. Sequencing, shared topic/scope, repeated business object, and stable-ID/artifact "
                            "lookup needed only by execution are not result dependencies. Do not see or reconstruct Planner depends_on from tool "
                            "needs. Return one dependency_decisions row for every unordered Goal pair together with the normal requested-effect and "
                            "scope audit fields. A true explicit result reference/condition/value dependency must still be retained. When "
                            "REQUESTED_EFFECT_COLLISION_RISK is supplied, also adversarially verify that each sibling's identical structured "
                            "requested_effect still denotes that sibling's own literal user-visible business effect; if one sibling has been "
                            "collapsed into a different lookup/action/object/effect, return incomplete with the smallest literal mismatch span."
                        )
                    else:
                        verifier_repair_kind = "candidate_blind_dependency_effect_collision_adjudication"
                        verifier_repair = (
                            "Adversarially re-audit the structurally signaled sibling requested-effect collision from USER_TEXT only while also "
                            "returning the complete candidate-blind dependency_decisions proof. REQUESTED_EFFECT_COLLISION_RISK is only a structural "
                            "risk signal: identical structured effects may be legitimate for two different targets, so do not reject merely because "
                            "the identities match. Start by assuming the shared identity is unsafe, then retain it only if domain, operation, "
                            "object_type and raw_description still denote each sibling's own literal user-visible business effect. If a sibling asks "
                            "for a materially different lookup, action, object or business effect, return verdict=incomplete and copy only the "
                            "smallest literal USER_TEXT span proving the substitution into missing_spans. Do not choose a tool, inspect capability "
                            "availability, normalize to a registered effect, or rewrite the declaration. For every unordered Goal pair, return one "
                            "dependency_decisions row using only literal result-reference/result-condition/result-value evidence; otherwise mark it "
                            "independent."
                        )
                    prompt = {
                        "USER_TEXT_UNTRUSTED": user_text,
                        "DECLARED_GOALS": _dependency_blind_goal_projection(goals),
                        "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),
                        "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
                    }
                    if effect_collision_risk["risk"]:
                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk
                    continue
'''
    replace_once(path, old_block, new_block, "goal-planning adversarial collision adjudication")


def patch_goal_granularity(root: Path) -> None:
    path = root / SOURCE_PATHS[1]

    replace_once(
        path,
        '''        verifier_repair: str | None = None
        last_indeterminate = GoalGranularityVerdict(
''',
        '''        verifier_repair: str | None = None
        first_blind_outcome_spans: tuple[str, ...] = ()
        last_indeterminate = GoalGranularityVerdict(
''',
        "granularity first-blind memory",
    )

    replace_once(
        path,
        '''        for attempt in range(2):
            try:
                response, _trace = invoke_model(
''',
        '''        for attempt in range(2):
            verifier_payload: dict[str, Any] = {
                "USER_TEXT_UNTRUSTED": user_text,
                "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
            }
            if attempt > 0 and first_blind_outcome_spans:
                # The second call remains candidate-blind. It sees only its own
                # first-pass hypotheses so it can challenge control/meta spans
                # instead of anchoring on the candidate Goal inventory.
                verifier_payload["FIRST_BLIND_OUTCOME_SPANS"] = list(first_blind_outcome_spans)
            try:
                response, _trace = invoke_model(
''',
        "granularity adversarial payload",
    )

    replace_once(
        path,
        '''                        payload={
                            "USER_TEXT_UNTRUSTED": user_text,
                            "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
                        },
''',
        '''                        payload=verifier_payload,
''',
        "granularity verifier payload wiring",
    )

    replace_once(
        path,
        '''            verifier_repair = (
                "Run one candidate-blind self-audit of USER_TEXT only. Return each independently acceptable business result exactly once. "
                "Do not duplicate a target phrase and its enclosing business action. Filters, target selectors and form values stay inside the outcome. "
                "Do not inspect candidate Goals and do not judge dependency edges. Return only verdict, outcome_spans and reason_code."
            )
''',
        '''            first_blind_outcome_spans = outcome_spans
            verifier_repair = (
                "Adversarially re-audit the first candidate-blind outcome inventory from USER_TEXT only. FIRST_BLIND_OUTCOME_SPANS contains "
                "only your own first-pass literal hypotheses; it is not authority and contains no candidate Goal plan. Start each hypothesis "
                "as NOT an independently judgeable business outcome, then retain it only if the customer independently requests business "
                "information/result/change that can be judged complete or incomplete. A conversational refusal, deferral or suppression of "
                "execution (for example not proceeding/submitting/handling something for now) is interaction control rather than a second "
                "business outcome when ACTIVE_STRUCTURED_INTERACTION does not identify the pending interaction and no identified existing "
                "business object is itself being changed. In contrast, a direct cancel/delete/stop request on an identified existing business "
                "object remains an outcome, and an explicit cancel/stop of the supplied ACTIVE_STRUCTURED_INTERACTION remains a control outcome. "
                "Never prune a separately requested unsupported/open business effect merely because it is unusual or unavailable. Preserve a "
                "separate read-only query. Do not inspect candidate Goals, candidate count, tools, capabilities, oracle data or dependency edges. "
                "Return only verdict, outcome_spans and reason_code with each retained result exactly once."
            )
''',
        "granularity adversarial second blind audit",
    )


def write_tests(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"test path already exists: {TEST_PATH}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('''from __future__ import annotations

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


def _goal(goal_id: str, span: str, effect: dict, *, depends_on: list[str] | None = None) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {**effect, "raw_description": span},
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }


def _independent_pair():
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_exact_blind_audit_with_sibling_effect_collision_gets_third_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect the record status, then provide the private handler contact"
    shared = {"domain": "record", "operation": "query_status", "object_type": "record"}
    goals = [
        _goal("g1", "Inspect the record status", shared),
        _goal("g2", "provide the private handler contact", shared),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect the record status", "provide the private handler contact"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind_exact = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect the record status", "provide the private handler contact"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "blind_exact_but_effects_not_challenged",
    })
    adversarial = _response({
        "verdict": "incomplete",
        "evidence_spans": ["Inspect the record status", "provide the private handler contact"],
        "missing_spans": ["private handler contact"],
        "dependency_decisions": _independent_pair(),
        "reason_code": "requested_effect_fidelity_collision",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind_exact, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("private handler contact",)
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_effect_collision_adjudication"
    third_payload = str(invoke.call_args_list[2].kwargs["payload"])
    assert "REQUESTED_EFFECT_COLLISION_RISK" in third_payload
    assert "capability_registry_consulted" in third_payload
    assert '"depends_on"' not in third_payload


def test_legitimate_same_effect_siblings_survive_collision_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect record A status and inspect record B status"
    shared = {"domain": "record", "operation": "query_status", "object_type": "record"}
    goals = [
        _goal("g1", "Inspect record A status", shared),
        _goal("g2", "inspect record B status", shared),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A status", "inspect record B status"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A status", "inspect record B status"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "blind_exact",
    })
    confirmed = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A status", "inspect record B status"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "same_effect_is_faithful_for_both_targets",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind, confirmed]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_effect_collision_adjudication"


def test_second_blind_inventory_adversarially_prunes_meta_deferral() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "先不办理。那无线鼠标什么时候发货？"
    goals = [{"goal_id": "g1", "evidence_span": "无线鼠标什么时候发货", "depends_on": []}]
    first = _response({
        "verdict": "exact",
        "outcome_spans": ["先不办理", "无线鼠标什么时候发货"],
        "reason_code": "first_pass_treated_deferral_as_outcome",
    })
    second = _response({
        "verdict": "exact",
        "outcome_spans": ["无线鼠标什么时候发货"],
        "reason_code": "adversarial_control_reaudit_query_only",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=None,
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["blind_self_audit_attempted"] is True
    second_payload = str(invoke.call_args_list[1].kwargs["payload"])
    assert "FIRST_BLIND_OUTCOME_SPANS" in second_payload
    assert "not authority" in second_payload
    assert "unsupported/open business effect" in second_payload


def test_true_unsupported_sibling_is_not_pruned_by_second_blind_audit() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "Inspect the record status, then provide the private handler contact"
    goals = [{"goal_id": "g1", "evidence_span": "Inspect the record status", "depends_on": []}]
    first = _response({
        "verdict": "exact",
        "outcome_spans": ["Inspect the record status", "provide the private handler contact"],
        "reason_code": "two_business_outcomes",
    })
    second = _response({
        "verdict": "exact",
        "outcome_spans": ["Inspect the record status", "provide the private handler contact"],
        "reason_code": "unsupported_sibling_remains_outcome",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ):
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=None,
        )

    assert verdict.verdict == "under_split"
    assert verdict.details["inventory_outcome_count"] == 2
    assert verdict.details["matched_outcome_count"] == 1


def test_pending_interaction_cancellation_plus_query_remains_two_outcomes() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "Stop this pending request, then inspect record B status"
    goals = [
        {"goal_id": "g1", "evidence_span": "Stop this pending request", "depends_on": []},
        {"goal_id": "g2", "evidence_span": "inspect record B status", "depends_on": []},
    ]
    active = {
        "interaction_id": "interaction:request:1",
        "lifecycle": "pending",
        "title": "Pending request",
        "target": "record A",
        "required_fields": [],
        "chat_write_authorized": False,
        "runtime_redirect_required": True,
    }
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["Stop this pending request", "inspect record B status"],
            "reason_code": "pending_control_and_read_query",
        }),
    ):
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=active,
        )

    assert verdict.exact
    assert verdict.details["inventory_outcome_count"] == 2


def test_attempt5_production_repairs_remain_domain_neutral() -> None:
    planning = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    granularity = (AGENT_SRC / "agent_core/lifecycle/goal_granularity.py").read_text(encoding="utf-8")
    start = planning.index("def _requested_effect_sibling_collision_risk")
    end = planning.index("def _literal_role_overlap", start)
    policy = planning[start:end]
    assert "capability_registry_consulted" in policy
    assert "runtime_rejection_authority" in policy
    assert "REQUESTED_EFFECT_COLLISION_RISK" in planning
    assert "FIRST_BLIND_OUTCOME_SPANS" in granularity
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "退款"):
        assert forbidden not in policy
''', encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_goal_granularity(root)
    write_tests(root)


def baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict):
        raise SystemExit("protected baseline files map is missing")
    updated: list[str] = []
    for rel in SOURCE_PATHS:
        if rel not in files:
            raise SystemExit(f"protected baseline does not own {rel}")
        files[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        updated.append(rel)
    payload["file_count"] = len(files)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = "git:" + product_sha
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"updated": updated, "file_count": len(files)}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    patch_parser = sub.add_parser("patch")
    patch_parser.add_argument("--workspace", required=True)
    baseline_parser = sub.add_parser("baseline")
    baseline_parser.add_argument("--workspace", required=True)
    baseline_parser.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        baseline(root, str(args.product_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
