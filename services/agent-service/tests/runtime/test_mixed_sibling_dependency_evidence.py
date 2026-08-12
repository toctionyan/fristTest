from __future__ import annotations

from agent_core.lifecycle.goal_planning import _model_alignment_pairwise_dependency_proof

def _goal(goal_id: str, *, evidence_span: str, output_id: str, output_span: str, depends_on: list[str] | None = None) -> dict:
    return {"goal_id": goal_id, "description": evidence_span, "evidence_span": evidence_span, "required": True, "depends_on": list(depends_on or []), "requested_effect": {"domain": "example", "operation": "read", "object_type": "example", "requested_outputs": [{"output_id": output_id, "evidence_span": output_span}]}}

def test_dependency_proof_rejects_requested_output_phrase_as_dependency_basis() -> None:
    goals=[_goal("g1",evidence_span="查一下鼠标物流",output_id="shipment.tracking",output_span="鼠标物流"),_goal("g2",evidence_span="快递员手机号",output_id="courier.contact.phone",output_span="快递员手机号",depends_on=["g1"])]
    _,error=_model_alignment_pairwise_dependency_proof(user_text="查一下鼠标物流，再告诉我快递员手机号",goals=goals,values=[{"goal_a_id":"g1","goal_b_id":"g2","relation":"b_depends_on_a","basis_kind":"result_value_input","basis_span":"快递员手机号"}])
    assert error == "goal_alignment_dependency_basis_is_requested_output:0"

def test_dependency_proof_keeps_distinct_literal_result_reference_valid() -> None:
    goals=[_goal("g1",evidence_span="查一下订单",output_id="order.details",output_span="订单"),_goal("g2",evidence_span="查它的物流",output_id="shipment.tracking",output_span="物流",depends_on=["g1"])]
    details,error=_model_alignment_pairwise_dependency_proof(user_text="查一下订单，然后查它的物流",goals=goals,values=[{"goal_a_id":"g1","goal_b_id":"g2","relation":"b_depends_on_a","basis_kind":"result_reference","basis_span":"它"}])
    assert error is None
    assert details["dependency_proof_complete"] is True
    assert details["dependency_graph_match"] is True
