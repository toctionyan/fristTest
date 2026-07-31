"""Build the release manifest from authoritative vertical capability definitions."""
from __future__ import annotations

from agent_modules.ecommerce.capabilities import CAPABILITIES


CAPABILITY_TEST_CONTRACT = {
    "schema": "services/agent-service/tests/architecture/test_module_installation.py::test_every_declared_capability_schema_is_registered",
    "permit": "services/agent-service/tests/architecture/test_module_installation.py::test_every_declared_capability_rejects_dispatch_without_permit",
    "execution": "services/agent-service/tests/architecture/test_module_installation.py::test_every_declared_capability_executes_its_owned_adapter",
    "presentation": "services/agent-service/tests/architecture/test_module_installation.py::test_every_declared_capability_owns_a_registered_presentation_contract",
    "negative_substitution": "services/agent-service/tests/architecture/test_module_installation.py::test_every_disabled_capability_is_rejected_without_substitution",
}


def build_module_manifest() -> dict:
    return {
        "$schema": "agent-module-manifest@1",
        "module_id": "ecommerce",
        "version": "20.6.1",
        "ownership": {
            "resources": ["order", "logistics", "refund", "after_sales", "invoice", "product", "coupon"],
            "operations": ["cancel_order", "create_after_sales_request", "create_refund", "create_invoice"],
            "presentation_contracts": [
                "commerce.order_list@1",
                "commerce.logistics_overview@1",
                "commerce.business_status_list@1",
                "commerce.next_actions@1",
                "commerce.eligibility_decision@1",
                "commerce.advisory@1",
                "runtime.transaction_status@1",
            ],
        },
        "capabilities": [
            {
                "key": item.key,
                "tool_name": item.tool_name,
                "executor": f"capabilities/{item.tool_name}.py",
                "presentation_contract": item.presentation_contract,
                "test_contract": dict(CAPABILITY_TEST_CONTRACT),
            }
            for item in CAPABILITIES
        ],
        "dependencies": ["ecommerce-business-service"],
        "unsupported_behavior": "Return explicit unsupported; never substitute nearest capability.",
        "tests": ["tests/architecture/test_module_installation.py"],
        "retired_path_status": {
            "formal_retired_path_reachable": False,
            "evidence": "current Composition binds only agent_modules.ecommerce.EcommerceModule and derives bindings from capabilities/*.py.",
        },
    }
