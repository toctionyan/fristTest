from __future__ import annotations

from agent_modules.support_ticket_demo.capabilities import CONTRACT, PRESENTATION_CONTRACT


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
        "module_id": "support_ticket_demo",
        "version": "1.0.0",
        "ownership": {"resources": ["support_ticket"], "operations": [], "presentation_contracts": [PRESENTATION_CONTRACT]},
        "capabilities": [{
            "key": CONTRACT.key,
            "tool_name": CONTRACT.tool_name,
            "executor": "capabilities/list_tickets.py",
            "presentation_contract": PRESENTATION_CONTRACT,
            "test_contract": dict(CAPABILITY_TEST_CONTRACT),
        }],
        "dependencies": [],
        "unsupported_behavior": "Return explicit unavailable; never substitute another module.",
        "tests": ["tests/architecture/test_module_installation.py"],
        "retired_path_status": {"formal_retired_path_reachable": False, "evidence": "Demo is only installed when configured and owns its own BusinessPort and generic presentation contribution."},
    }
