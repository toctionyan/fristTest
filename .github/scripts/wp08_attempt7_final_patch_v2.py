from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement target, found {count}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


goal_path = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
replace_once(
    goal_path,
    "                        payload=prompt,\n",
    '''                        payload=(
                            {
                                **prompt,
                                # Hide only the candidate dependency graph on the
                                # bounded second audit; Goal IDs/evidence/effects
                                # remain visible so the independent graph is grounded.
                                "DECLARED_GOALS": [
                                    {
                                        key: deepcopy(value)
                                        for key, value in goal.items()
                                        if key != "depends_on"
                                    }
                                    for goal in goals
                                ],
                                "DEPENDENCY_REAUDIT_MODE": "candidate_dependency_graph_hidden",
                            }
                            if verifier_repair_kind == "dependency_independent_reaudit"
                            else prompt
                        ),
''',
)
replace_once(
    goal_path,
    '''                elif raw_verdict == "exact" and dependency_error == "goal_alignment_dependency_graph_mismatch":
                    verdict = GoalAlignmentVerdict(
                        "indeterminate",
                        _literal_spans(user_text, parsed.get("evidence_spans")),
                        (),
                        "goal_alignment_dependency_exact_contradiction",
                        "model",
                        True,
                        dependency_details,
                    )
''',
    '''                elif raw_verdict == "exact" and dependency_error == "goal_alignment_dependency_graph_mismatch":
                    evidence = _literal_spans(user_text, parsed.get("evidence_spans"))
                    if verifier_repair_kind == "dependency_independent_reaudit" and evidence:
                        # The re-auditor intentionally cannot see the candidate
                        # graph.  Runtime owns the deterministic comparison: a
                        # different independently grounded graph means the
                        # declaration dependency relation is incomplete/wrong.
                        verdict = GoalAlignmentVerdict(
                            "incomplete",
                            evidence,
                            (),
                            "goal_alignment_dependency_graph_mismatch",
                            "model",
                            True,
                            dependency_details,
                        )
                    else:
                        verdict = GoalAlignmentVerdict(
                            "indeterminate",
                            evidence,
                            (),
                            (
                                "goal_alignment_dependency_mismatch_without_literal_evidence"
                                if verifier_repair_kind == "dependency_independent_reaudit"
                                else "goal_alignment_dependency_exact_contradiction"
                            ),
                            "model",
                            True,
                            dependency_details,
                        )
''',
)
replace_once(
    goal_path,
    '''            if verdict.verdict in {"exact", "incomplete"}:
                return verdict
''',
    '''            if attempt == 0 and verdict.verdict == "exact" and len(goals) > 1:
                # Dependency authority moved from Goal Granularity into Goal
                # Alignment. Preserve the old candidate-blind self-audit at
                # the new authority boundary so planner + verifier cannot
                # freeze the same execution-order mistake as semantic depends_on.
                verifier_repair_kind = "dependency_independent_reaudit"
                verifier_repair = (
                    "Run a second independent audit of only the semantic result-dependency graph. "
                    "DECLARED_GOALS intentionally omits every candidate depends_on field in this re-audit; "
                    "do not interpret that omission as an empty declared graph and do not reconstruct the candidate graph. "
                    "Infer complete dependency_edges only from USER_TEXT, Goal IDs/evidence/requested effects, and trusted recent public context. "
                    "Keep an edge only when the customer-visible meaning of the dependent outcome itself requires another current-turn Goal result. "
                    "Sentence order, then/然后, shared topic/scope, and lookup needed only to turn an already stated descriptor into an ID/artifact are execution-support dataflow, not semantic dependency. "
                    "Explicit reference to a not-yet-produced current-turn result or an explicit result condition/value input is a true dependency. "
                    "Return the same strict JSON fields with literal evidence_spans and a complete independently judged dependency_edges graph."
                )
                continue
            if verdict.verdict in {"exact", "incomplete"}:
                return verdict
''',
)

dialogue_path = Path("services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py")
replace_once(
    dialogue_path,
    '''def _workflow_repair_tools(
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    surface: dict[str, Any],
) -> tuple[set[str], set[str], set[str]]:
    """Return pending Goal ids plus legal completion/absence reporters.

    Newly frozen turns use exact business-effect identities. Historical
    checkpoints without requested_effect retain their old goal-type completion
    filter as a compatibility-only path.
    """
''',
    '''def _workflow_repair_tools(
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    surface: dict[str, Any],
    *,
    pretool_execution_policy: dict[str, Any] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Return pending Goal ids plus the legal repair business frontier.

    The exact completion surface remains useful for legacy/migration paths, but
    a WorkflowIncompleteRetry must not discard a support/continuation Tool that
    the current Pre-tool Execution Policy has already proven to be the legal
    frontier for a pending Goal. This function can only narrow the schemas
    already compiled from that policy; it never creates a permit or widens the
    provider surface beyond the current policy.
    """
''',
)
replace_once(
    dialogue_path,
    '''    completion_tools = {
        str(name)
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") in pending_goal_ids
        for name in list(row.get("completion_tools") or [])
        if str(name)
    }
''',
    '''    completion_tools = {
        str(name)
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") in pending_goal_ids
        for name in list(row.get("completion_tools") or [])
        if str(name)
    }
    policy = pretool_execution_policy if isinstance(pretool_execution_policy, dict) else {}
    policy_frontier_tools = {
        str(name)
        for row in list(policy.get("goal_policies") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") in pending_goal_ids
        for name in list(row.get("allowed_tools") or [])
        if str(name)
    }
    # agent_loop_schemas was already compiled from the same policy's global
    # allowed_capability_tools. This union only prevents the later repair
    # filter from deleting a still-legal support/continuation step; it cannot
    # widen the provider surface or create an ExecutionPermit.
    completion_tools.update(policy_frontier_tools)
''',
)
replace_once(
    dialogue_path,
    '''            _pending_goal_ids, completion_tools, unsupported_tools = _workflow_repair_tools(
                state, capability_registry, capability_surface or {}
            )
''',
    '''            _pending_goal_ids, completion_tools, unsupported_tools = _workflow_repair_tools(
                state,
                capability_registry,
                capability_surface or {},
                pretool_execution_policy=pretool_execution_policy,
            )
''',
)

test_path = Path("services/agent-service/tests/runtime/test_wp08_attempt7_final_repair.py")
if test_path.exists():
    raise SystemExit(f"{test_path}: must not already exist")
test_path.write_text(r'''from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.dialogue_runtime import _workflow_repair_tools
from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "depends_on": depends_on,
        "requested_effect": {
            "domain": "order" if goal_id == "g1" else "refund",
            "operation": "list" if goal_id == "g1" else "create",
            "object_type": "order",
        },
    }


def test_attempt7_dependency_reaudit_rejects_shared_scope_false_edge() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", ["g1"])]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_value_input",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "exact",
    })
    second = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "independent_dependency_audit_exact",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["verifier_repair_kind"] == "dependency_independent_reaudit"
    rendered = "\n".join(
        str(getattr(message, "content", message))
        for message in invoke.call_args_list[1].kwargs["payload"]
    )
    assert '"depends_on": ["g1"]' not in rendered
    assert "candidate_dependency_graph_hidden" in rendered


def test_attempt7_dependency_reaudit_preserves_true_result_reference_edge() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [
        _goal("g1", "查一下键盘订单", []),
        {
            **_goal("g2", "它能不能退款", ["g1"]),
            "requested_effect": {"domain": "refund", "operation": "assess_eligibility", "object_type": "order"},
        },
    ]
    payload = {
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "exact",
    }
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[_response(payload), _response(payload)]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "exact"
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "dependency_independent_reaudit"


class _Registry:
    def contract_for_tool(self, _name: str):
        return None


def test_attempt7_workflow_repair_keeps_current_policy_support_frontier() -> None:
    surface = {"goals": [{
        "goal_id": "g1",
        "completion_tools": [],
        "candidate_tools": ["get_order_details"],
        "status": "matched",
    }]}
    policy = {"goal_policies": [
        {"goal_id": "g1", "allowed_tools": ["get_order_details"]},
        {"goal_id": "other", "allowed_tools": ["list_orders"]},
    ]}
    with patch(
        "agent_core.lifecycle.dialogue_runtime.read_plan_projection",
        return_value={"goals": [
            {"goal_id": "g1", "required": True, "coverage_status": "PENDING"},
            {"goal_id": "done", "required": True, "coverage_status": "COMPLETE"},
        ]},
    ):
        pending, repair_tools, unsupported = _workflow_repair_tools(
            {}, _Registry(), surface, pretool_execution_policy=policy
        )
    assert pending == {"g1"}
    assert repair_tools == {"get_order_details"}
    assert unsupported == set()
    assert "list_orders" not in repair_tools


def test_workflow_repair_preserves_exact_completion_tools_without_policy() -> None:
    surface = {"goals": [{
        "goal_id": "g1",
        "completion_tools": ["get_order_details"],
        "candidate_tools": ["get_order_details"],
        "status": "matched",
    }]}
    with patch(
        "agent_core.lifecycle.dialogue_runtime.read_plan_projection",
        return_value={"goals": [{"goal_id": "g1", "required": True, "coverage_status": "PENDING"}]},
    ):
        pending, repair_tools, unsupported = _workflow_repair_tools({}, _Registry(), surface)
    assert pending == {"g1"}
    assert repair_tools == {"get_order_details"}
    assert unsupported == set()
''', encoding="utf-8")
