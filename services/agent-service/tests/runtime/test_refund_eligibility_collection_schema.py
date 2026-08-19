from __future__ import annotations

from agent_core.composition import get_runtime_registry
from agent_core.runtime.capability_gate import validate_tool_arguments


def _eligibility_errors(target: dict) -> list[str]:
    return validate_tool_arguments(
        "evaluate_refund_eligibility",
        {
            "target": target,
            "reference_span": "它们",
            "question_span": "它们都能退吗？",
        },
        capability_registry=get_runtime_registry().capabilities,
    )


def test_refund_eligibility_accepts_verified_collection_without_invented_reason() -> None:
    assert _eligibility_errors(
        {"mode": "collection", "left_handle": "h_result:signed-orders"}
    ) == []


def test_refund_eligibility_rejects_unbound_collection() -> None:
    assert _eligibility_errors({"mode": "collection"}) == ["$.target: one_of_mismatch"]
