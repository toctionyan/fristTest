"""V15 migration regression tests.

The V14 semantic guard was deliberately removed from the normal read loop.
These tests keep the important safety invariant in its new location: an unknown
capability has no automatic substitute, and transaction policy is explicit.
"""

from tests.support.runtime_support import runtime_deps
from agent_core.transaction.authority import policy_for_action
from agent_core.lifecycle.protocol import classify_tool
from agent_core.lifecycle.graph import build_lifecycle_graph


def test_unknown_tool_is_not_mapped_to_nearest_registered_capability():
    from agent_core.composition import get_runtime_registry

    classification = classify_tool("diagnose_earphone_hardware", get_runtime_registry().capabilities)
    assert classification.category == "unknown"
    assert classification.name == "diagnose_earphone_hardware"
    # No similarity-based fallback lives in the protocol classifier.
    registry = get_runtime_registry().capabilities
    assert classify_tool("prepare_contextual_operation", registry).category == "unknown"
    assert classify_tool("consult_contextual_information", registry).category == "unknown"


def test_transaction_policy_is_explicit_and_not_a_language_router():
    cancel = policy_for_action("cancel_order")
    refund = policy_for_action("create_refund")
    unknown = policy_for_action("change_shipping_address")

    assert cancel.authority_requirement == "ui_action_authority"
    assert refund.authority_requirement == "ui_action_authority"
    assert unknown.authority_requirement == "ui_action_authority"
    assert unknown.risk_level == "high_risk"


def test_guard_is_absent_from_v15_default_graph():
    graph = build_lifecycle_graph(runtime_deps())
    assert "semantic_guard" not in str(graph.get_graph().nodes)
