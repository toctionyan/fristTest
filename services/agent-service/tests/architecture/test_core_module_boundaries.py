from __future__ import annotations

from agent_core.composition import get_runtime_registry
from agent_core.kernel.capability_registry import CapabilityBinding, CapabilityRegistry
from agent_core.kernel.capability import ToolCapabilityContract
from agent_core.runtime.capability_gate import issue_execution_permit


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


def test_composition_root_constructs_registry_from_explicit_ecommerce_bindings():
    """Module composition replaces the old universal query tool.

    This keeps the original Core--Overlay boundary assertion but verifies the
    formal public surface: an explicit ecommerce capability is registered and
    the retired universal entrypoint is not.
    """
    registry = get_runtime_registry().capabilities
    assert registry.contract_for_tool("list_orders") is not None
    assert registry.function_schema("list_orders") is not None
    assert "list_orders" in registry.tool_names()
    assert "run_contextual_query" not in registry.tool_names()


def test_gate_uses_injected_registry_not_concrete_overlay_import():
    contract = ToolCapabilityContract(
        key="saas_plan_read", tool_name="saas_plan_read", category="query", writes_business_data=False,
        evidence_sources=("saas_service",), planner_rule="read plan", unavailable_response="unavailable",
    )
    registry = CapabilityRegistry([
        CapabilityBinding(domain_id="saas", contract=contract, schema=_schema("saas_plan_read"), dispatcher=lambda *_args, **_kwargs: {"ok": True})
    ])
    decision = issue_execution_permit(
        state={"current_user_input": "查看套餐", "turn_index": 1, "current_thread_id": "t", "current_user_id": "u", "current_tenant_id": "x"},
        tool_name="saas_plan_read", args={}, effect_id="p:1", capability_registry=registry,
    )
    assert decision.permitted is True
    assert decision.execution_permit and decision.execution_permit["tool_name"] == "saas_plan_read"


def test_registry_rejects_schema_contract_drift():
    contract = ToolCapabilityContract(
        key="x", tool_name="correct_name", category="query", writes_business_data=False,
        evidence_sources=(), planner_rule="x", unavailable_response="x",
    )
    try:
        CapabilityRegistry([CapabilityBinding(domain_id="x", contract=contract, schema=_schema("wrong_name"), dispatcher=lambda *_a, **_k: {})])
    except ValueError as exc:
        assert "schema/contract mismatch" in str(exc)
    else:
        raise AssertionError("registry must reject schema/contract drift")
