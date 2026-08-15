from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replacement_count:{path}:{count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


planning = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
text = planning.read_text(encoding="utf-8")
if "initial_exact_alignment" not in text:
    raise SystemExit("initial_exact_alignment marker missing")
text = text.replace("initial_exact_alignment", "initial_grounded_alignment")
planning.write_text(text, encoding="utf-8")

replace_once(
    str(planning),
    "                        # Outcome grounding was already proven by the first exact\n"
    "                        # call, so preserve that literal evidence while accepting\n",
    "                        # Outcome grounding was already proven by the first candidate-visible\n"
    "                        # verdict that entered this blind audit: either exact, or a grounded\n"
    "                        # dependency-only mismatch. Preserve that literal evidence while accepting\n",
)
replace_once(
    str(planning),
    "                if verdict.exact:\n"
    "                    initial_grounded_alignment = verdict\n"
    "                # Every first-pass exact declaration receives one independent\n"
    "                # semantic-contract re-audit within the existing verifier budget.\n",
    "                # Both entry paths have already passed literal evidence grounding: either the\n"
    "                # first verdict is exact, or its only authoritative disagreement is a\n"
    "                # structurally valid dependency graph that introduces a new edge. Keep that\n"
    "                # grounded outcome evidence available while the blind audit repairs graph format.\n"
    "                initial_grounded_alignment = verdict\n"
    "                # Every first-pass exact declaration, plus a grounded dependency-only\n"
    "                # disagreement that introduces a new edge, receives one independent\n"
    "                # semantic-contract re-audit within the existing verifier budget.\n",
)

test_path = Path("services/agent-service/tests/runtime/test_wp08_attempt5_dependency_authority.py")
marker = "\n\ndef test_unknown_and_self_dependency_edges_fail_closed() -> None:\n"
test_text = test_path.read_text(encoding="utf-8")
if test_text.count(marker) != 1:
    raise SystemExit("test insertion marker mismatch")
addition = r'''


def test_dependency_format_repair_reuses_first_pass_grounded_outcome_evidence() -> None:
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
    malformed_blind = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "鼠标订单",
        }],
        "reason_code": "malformed_blind_basis",
    })
    repaired_blind_without_duplicate_evidence = _response({
        "verdict": "exact",
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "repaired_pairwise_dependency_proof",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[false_positive, malformed_blind, repaired_blind_without_duplicate_evidence],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.evidence_spans == ("查一下鼠标订单", "帮我申请退款")
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_format_repair"
'''
test_path.write_text(test_text.replace(marker, addition + marker, 1), encoding="utf-8")

Path(".github/release-trigger").write_text(
    "release_request: 2026-08-15T14:53:00+08:00\n"
    "provider: deepseek\n"
    "model: deepseek-v4-flash\n"
    "embedding_model: text-embedding-v4\n"
    "embedding_dimension: 1024\n"
    "reason: rerun protected release after candidate-blind verifier repair-evidence composition fix\n",
    encoding="utf-8",
)
