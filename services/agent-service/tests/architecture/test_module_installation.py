from __future__ import annotations

from tests.support.paths import agent_root

import importlib
import json
import os
from pathlib import Path

import pytest

from agent_core.composition.registry import get_module_registry, get_runtime_registry, reset_runtime_registry_cache
from agent_core.business import reset_business_port_cache
from agent_core.modules import ModuleRegistry
from agent_core.presentation.registry import build_response_blocks
from agent_core.runtime.capability_gate import issue_execution_permit
from agent_modules.ecommerce import EcommerceModule
from agent_modules.support_ticket_demo import SupportTicketDemoModule


MODULE_TYPES = (EcommerceModule, SupportTicketDemoModule)


def _declared_capabilities() -> list[tuple[str, dict, object]]:
    rows: list[tuple[str, dict, object]] = []
    root = agent_root(__file__)
    for module_type in MODULE_TYPES:
        module = module_type()
        manifest = json.loads(
            (root / f"src/agent_modules/{module.module_id}/module_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        bindings = {
            binding.contract.key: binding for binding in module.contribution().capabilities
        }
        for capability in manifest["capabilities"]:
            rows.append((module.module_id, capability, bindings[capability["key"]]))
    return rows


def _set_modules(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("AGENT_ENABLED_MODULES", value)
    reset_runtime_registry_cache()
    reset_business_port_cache()


def test_empty_kernel_can_be_composed_without_domain_module() -> None:
    runtime = ModuleRegistry(()).build_runtime_registry()
    assert runtime.capabilities.tool_names() == set()
    assert runtime.resource_types() == frozenset()
    assert runtime.preparable_action_ids() == frozenset()
    assert runtime.assessments.ids() == set()


def test_empty_kernel_can_be_selected_through_composition_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_modules(monkeypatch, "")
    registry = get_module_registry()
    runtime = get_runtime_registry()
    assert registry.module_ids() == frozenset()
    assert runtime.capabilities.tool_names() == set()
    assert runtime.capabilities.dispatch_permitted(
        {"turn_index": 1, "current_user_id": "u001", "current_tenant_id": "default", "current_thread_id": "t-empty"},
        "list_orders",
        {},
        execution_permit=None,
        effect_id="effect:empty",
    )["code"] == "UNKNOWN_OR_UNSUPPORTED_TOOL"


def test_two_modules_compose_without_tool_or_resource_collision() -> None:
    registry = ModuleRegistry((EcommerceModule(), SupportTicketDemoModule()))
    runtime = registry.build_runtime_registry()
    assert {"list_orders", "list_support_tickets"} <= runtime.capabilities.tool_names()
    assert {"order", "support_ticket"} <= runtime.resource_types()
    assert "run_contextual_query" not in runtime.capabilities.tool_names()
    assert "prepare_contextual_operation" not in runtime.capabilities.tool_names()


def test_disabled_module_does_not_expose_tool_and_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_modules(monkeypatch, "support_ticket_demo")
    runtime = get_runtime_registry()
    assert runtime.capabilities.tool_names() == {"list_support_tickets"}
    missing = runtime.capabilities.dispatch_permitted(
        {"turn_index": 1, "current_user_id": "u001", "current_tenant_id": "default", "current_thread_id": "t1"},
        "list_orders",
        {},
        execution_permit=None,
        effect_id="effect:missing",
    )
    assert missing["code"] == "UNKNOWN_OR_UNSUPPORTED_TOOL"
    assert "相近工具" in missing["message"]


def test_module_query_requires_permit_and_executes_its_own_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_modules(monkeypatch, "support_ticket_demo")
    monkeypatch.setenv("CAPABILITY_SEMANTIC_VERIFIER_MODE", "candidate")
    state = {
        "turn_index": 1,
        "current_user_id": "u001",
        "current_tenant_id": "default",
        "current_thread_id": "t-support",
        "current_user_input": "查询我的支持工单",
    }
    runtime = get_runtime_registry()
    denied = runtime.capabilities.dispatch_permitted(
        state, "list_support_tickets", {}, execution_permit=None, effect_id="effect:1"
    )
    assert denied["code"] == "EXECUTION_PERMIT_INVALID"

    decision = issue_execution_permit(
        state=state,
        tool_name="list_support_tickets",
        args={},
        effect_id="effect:1",
        capability_registry=runtime.capabilities,
    )
    assert decision.permitted is True
    result = runtime.capabilities.dispatch_permitted(
        state,
        "list_support_tickets",
        {},
        execution_permit=decision.execution_permit,
        effect_id="effect:1",
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 2
    assert all(ticket["ticket_id"].startswith("T-") for ticket in result["data"]["tickets"])

    blocks = build_response_blocks({"tool_trace": [{"name": "list_support_tickets", "result": result, "trace_id": "trace-support"}]})
    assert len(blocks) == 1
    assert blocks[0]["contract_id"] == "runtime.resource_list@1"
    assert blocks[0]["coverage"]["status"] == "complete"


def test_kernel_operation_capability_requires_module_target_type() -> None:
    from agent_core.operations.capability import single_target_operation_capability

    with pytest.raises(TypeError):
        single_target_operation_capability(action_id="missing-target")


def test_builtin_knowledge_is_contributed_by_enabled_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.rag.seed_catalog import builtin_knowledge_documents

    _set_modules(monkeypatch, "support_ticket_demo")
    get_module_registry()
    assert builtin_knowledge_documents() == ()

    _set_modules(monkeypatch, "ecommerce")
    get_module_registry()
    documents = builtin_knowledge_documents()
    assert {item["doc_id"] for item in documents} == {
        "policy_after_sales_001", "policy_refund_001", "policy_warranty_001", "policy_logistics_001",
        "policy_invoice_001",
    }
    assert {
        item["doc_id"]: item["metadata"]["policy_domain"]
        for item in documents
    } == {
        "policy_after_sales_001": "after_sales",
        "policy_refund_001": "refund",
        "policy_warranty_001": "warranty",
        "policy_logistics_001": "logistics",
        "policy_invoice_001": "invoice",
    }


def test_ecommerce_and_demo_can_be_enabled_together(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_modules(monkeypatch, "ecommerce,support_ticket_demo")
    registry = get_module_registry()
    runtime = get_runtime_registry()
    assert registry.module_ids() == frozenset({"ecommerce", "support_ticket_demo"})
    assert {"list_orders", "list_support_tickets"} <= runtime.capabilities.tool_names()
    adapter_ids = {adapter.adapter_id for adapter in registry.presentation_adapters()}
    assert {"ecommerce.observations.v4", "support_ticket_demo.observations.v1"} <= adapter_ids


def test_every_declared_capability_schema_is_registered() -> None:
    for _module_id, capability, binding in _declared_capabilities():
        function = binding.schema.get("function")
        assert binding.contract.key == capability["key"]
        assert isinstance(function, dict)
        assert function["name"] == capability["tool_name"]
        assert function["parameters"]["type"] == "object"


def test_every_declared_capability_rejects_dispatch_without_permit() -> None:
    runtime = ModuleRegistry(tuple(module_type() for module_type in MODULE_TYPES)).build_runtime_registry()
    state = {
        "turn_index": 1,
        "current_user_id": "u-test",
        "current_tenant_id": "default",
        "current_thread_id": "t-capability-contract",
    }
    for _module_id, capability, _binding in _declared_capabilities():
        result = runtime.capabilities.dispatch_permitted(
            state,
            capability["tool_name"],
            {},
            execution_permit=None,
            effect_id=f"effect:{capability['key']}",
        )
        assert result["code"] == "EXECUTION_PERMIT_INVALID", capability["key"]


def test_every_declared_capability_binds_its_owned_execution_adapter() -> None:
    for module_id, capability, binding in _declared_capabilities():
        executor_module = importlib.import_module(
            f"agent_modules.{module_id}."
            + str(Path(capability["executor"]).with_suffix("")).replace("/", ".")
        )
        assert callable(executor_module.execute)
        assert callable(binding.dispatcher)
        assert binding.domain_id == module_id
        definition = getattr(executor_module, "DEFINITION", None)
        if definition is not None:
            assert definition.executor is executor_module.execute
            assert definition.key == capability["key"]


def test_every_declared_capability_executes_its_owned_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise each declared adapter and prove its exact owned route.

    This intentionally goes beyond import/callable metadata. Ecommerce wrappers
    must call the engine method owned by the declared tool, while the demo
    module must call its own BusinessPort resource rather than a substitute.
    """
    private_engine_tools = {
        "ask_context_clarification",
        "dismiss_eligibility",
        "dismiss_offer",
        "list_active_eligibilities",
        "list_active_offers",
        "prepare_refund_from_eligibility",
        "query_transaction_lifecycle",
        "report_unsupported_request",
    }
    for module_id, capability, _binding in _declared_capabilities():
        executor_module = importlib.import_module(
            f"agent_modules.{module_id}."
            + str(Path(capability["executor"]).with_suffix("")).replace("/", ".")
        )
        tool_name = str(capability["tool_name"])
        state = {
            "current_user_id": "u-contract",
            "current_tenant_id": "tenant-contract",
            "current_thread_id": "thread-contract",
            "correlation_id": "corr-contract",
        }
        if module_id == "ecommerce":
            calls: list[tuple[str, tuple, dict]] = []

            class Engine:
                def __getattr__(self, name: str):
                    def invoke(*args, **kwargs):
                        calls.append((name, args, kwargs))
                        return {"ok": True, "data": {"route": name}}

                    return invoke

            def execute_one(actual_state, actual_tool, runner):
                assert actual_state is state
                assert actual_tool == tool_name
                return runner(Engine())

            monkeypatch.setattr(executor_module, "execute_one", execute_one)
            result = executor_module.execute(
                state,
                {"contract_probe": tool_name},
                transactions=object(),
            )
            expected_method = (
                f"_{tool_name}" if tool_name in private_engine_tools else f"execute_{tool_name}"
            )
            assert calls and calls[0][0] == expected_method, capability["key"]
            assert result["data"]["route"] == expected_method
            continue

        class DemoPort:
            def query_resources(self, actor, *, resource_type, query_spec):
                assert actor.user_id == "u-contract"
                assert resource_type == "support_ticket"
                assert query_spec["scope"] == "current_user"
                return {"success": True, "data": [{"ticket_id": "T-CONTRACT"}]}

        monkeypatch.setattr(executor_module, "get_business_port", lambda: DemoPort())
        result = executor_module.execute(state, {})
        assert result["ok"] is True
        assert result["data"]["tickets"] == [{"ticket_id": "T-CONTRACT"}]


def test_every_declared_capability_owns_a_registered_presentation_contract() -> None:
    from agent_core.presentation.contracts.runtime import (
        runtime_presentation_contract_manifests,
    )

    registered: set[str] = {
        str(item["contract_id"])
        for item in runtime_presentation_contract_manifests()
        if item.get("contract_id")
    }
    for module_type in MODULE_TYPES:
        for adapter in module_type().contribution().presentation_adapters:
            manifests = getattr(adapter, "presentation_contracts", None)
            if callable(manifests):
                registered.update(
                    str(item["contract_id"])
                    for item in manifests()
                    if isinstance(item, dict) and item.get("contract_id")
                )
    for _module_id, capability, _binding in _declared_capabilities():
        assert capability["presentation_contract"] in registered


def test_every_registered_capability_declares_which_goal_types_it_can_complete() -> None:
    allowed = {"query", "consult", "action", "clarification", "unsupported", "narrative"}
    for _module_id, _capability, binding in _declared_capabilities():
        completion_types = tuple(binding.contract.goal_completion_types)
        assert completion_types, binding.contract.tool_name
        assert set(completion_types) <= allowed, binding.contract.tool_name


def test_every_ecommerce_structured_read_has_a_real_trace_to_contract_route() -> None:
    """Naming a registered contract is insufficient if no adapter can emit it."""
    from agent_modules.ecommerce.capabilities import CAPABILITIES
    from agent_modules.ecommerce.presentation.adapter import EcommerceObservationAdapter

    routes = dict(EcommerceObservationAdapter.TRACE_PRESENTATION_ROUTES)
    for capability in CAPABILITIES:
        if capability.execution_kind not in {"grounding_read", "knowledge_read"}:
            continue
        if not capability.presentation_contract.startswith("commerce."):
            continue
        assert routes.get(capability.tool_name) == capability.presentation_contract, capability.tool_name


def test_every_disabled_capability_is_rejected_without_substitution() -> None:
    empty = ModuleRegistry(()).build_runtime_registry()
    for _module_id, capability, _binding in _declared_capabilities():
        result = empty.capabilities.dispatch_permitted(
            {},
            capability["tool_name"],
            {},
            execution_permit=None,
            effect_id=f"disabled:{capability['key']}",
        )
        assert result["code"] == "UNKNOWN_OR_UNSUPPORTED_TOOL"
        assert capability["tool_name"] in result["message"]


@pytest.fixture(autouse=True)
def _restore_default_modules(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.setenv("AGENT_ENABLED_MODULES", "ecommerce")
    reset_runtime_registry_cache()
    reset_business_port_cache()


def test_ecommerce_release_manifest_is_derived_from_vertical_capability_definitions() -> None:
    import json
    from pathlib import Path
    from agent_modules.ecommerce.manifest import build_module_manifest

    manifest_path = agent_root(__file__) / "src/agent_modules/ecommerce/module_manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == build_module_manifest()


def test_support_ticket_release_manifest_is_derived_from_its_capability_definition() -> None:
    import json
    from pathlib import Path
    from agent_modules.support_ticket_demo.manifest import build_module_manifest

    manifest_path = agent_root(__file__) / "src/agent_modules/support_ticket_demo/module_manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == build_module_manifest()
