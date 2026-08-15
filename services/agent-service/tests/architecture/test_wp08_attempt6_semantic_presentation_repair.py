from __future__ import annotations

import json

import pytest

from agent_core.modules import ModuleRegistry
from agent_core.runtime.outcomes import outcome
from agent_modules.ecommerce import EcommerceModule
from agent_modules.ecommerce.capabilities import CAPABILITIES
from agent_modules.ecommerce.presentation.adapter import EcommerceObservationAdapter
from agent_modules.ecommerce.semantic_vocabulary import SEMANTIC_OUTPUTS


def test_shipment_semantic_vocabulary_distinguishes_status_from_tracking_progress() -> None:
    definitions = {row.output_id: row for row in SEMANTIC_OUTPUTS}
    status = definitions["shipment.current_status"]
    tracking = definitions["shipment.tracking"]

    assert "生命周期" in status.description
    assert "不表示当前位置节点、轨迹或运输进展" in status.description
    assert "当前位置节点" in tracking.description
    assert "不等同于仅有生命周期状态标签" in tracking.description

    # This remains a domain-semantic distinction, not a capability hint. Both
    # meanings keep the same legacy migration alias and the public vocabulary
    # exposes only the capability-independent public semantic fields.
    assert status.legacy_effect_aliases == tracking.legacy_effect_aliases == (
        "order.query_logistics:order",
    )
    snapshot = ModuleRegistry((EcommerceModule(),)).semantic_vocabulary_snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["availability_exposed"] is False
    assert snapshot["tool_names_exposed"] is False
    assert "get_order_logistics" not in serialized
    for row in snapshot["outputs"]:
        assert set(row) == {
            "output_id",
            "subject_type",
            "effect_kinds",
            "description",
            "included_result_meanings",
            "excluded_result_meanings",
        }
        assert isinstance(row["included_result_meanings"], list)
        assert isinstance(row["excluded_result_meanings"], list)
        assert "tool_name" not in row
        assert "available" not in row
        assert "legacy_effect_aliases" not in row


def test_every_ecommerce_structured_read_has_a_real_trace_to_declared_contract_route() -> None:
    routes = dict(EcommerceObservationAdapter.TRACE_PRESENTATION_ROUTES)
    checked: set[str] = set()
    for capability in CAPABILITIES:
        if capability.execution_kind not in {"grounding_read", "knowledge_read"}:
            continue
        assert capability.presentation_contract
        assert routes.get(capability.tool_name) == capability.presentation_contract, capability.tool_name
        checked.add(capability.tool_name)

    assert {
        "query_transaction_lifecycle",
        "list_active_eligibilities",
        "list_active_offers",
    } <= checked


@pytest.mark.parametrize(
    ("tool_name", "outcome_type", "payload"),
    [
        (
            "query_transaction_lifecycle",
            "transaction_status",
            {"transaction_handle": "txn:test", "status": "AWAITING_AUTHORIZATION"},
        ),
        (
            "list_active_eligibilities",
            "query",
            {"eligibilities": []},
        ),
        (
            "list_active_offers",
            "query",
            {"offers": []},
        ),
    ],
)
def test_runtime_structured_reads_project_through_registered_runtime_contract(
    tool_name: str,
    outcome_type: str,
    payload: dict,
) -> None:
    runtime_outcome = outcome(
        outcome_type,
        effects="none",
        safe_to_continue=True,
        correlation_id="corr-wp08-attempt6",
        evidence_handles=[],
        customer_safe_summary="已查询当前办理状态。",
        next_interaction="show_status" if outcome_type == "transaction_status" else "none",
        payload=payload,
    ).as_dict()
    blocks = EcommerceObservationAdapter().blocks_from_trace([
        {
            "name": tool_name,
            "goal_ids": ["g-runtime"],
            "trace_id": f"trace:{tool_name}",
            "result": {
                "ok": True,
                "data": dict(payload),
                "runtime_outcome": runtime_outcome,
            },
        }
    ])

    assert len(blocks) == 1
    block = blocks[0]
    assert block["contract_id"] == "runtime.transaction_status@1"
    assert block["producer"] == "runtime.transaction_status.outcome"
    assert block["role"] == "primary"
    assert block["summary"] == "已查询当前办理状态。"
    assert block["data"] == payload
    assert block["_goal_ids"] == ["g-runtime"]
    assert block.get("type") != "projection_contract_violation"
