from __future__ import annotations

import pytest


def test_v2_contract_requires_planning_contract() -> None:
    from agent_core.kernel.capability import ToolCapabilityContract

    with pytest.raises(ValueError, match="planning contract"):
        ToolCapabilityContract(
            key="demo.missing.v2",
            tool_name="demo_missing_v2",
            category="query",
            writes_business_data=False,
            evidence_sources=("demo_service",),
            planner_rule="demo",
            unavailable_response="unavailable",
            completion_effects=("demo.query:item",),
            contract_version="2",
        )


def test_v2_contract_rejects_duplicate_inputs_and_missing_completion_proof() -> None:
    from agent_core.kernel.capability import (
        CapabilityCompletionContract,
        CapabilityInputContract,
        CapabilityOutputContract,
        CapabilityPlanningContract,
        CapabilityPreconditionContract,
        CapabilityTargetContract,
        ToolCapabilityContract,
    )

    planning = CapabilityPlanningContract(
        target=CapabilityTargetContract(resource_types=("item",), cardinality="exactly_one"),
        requires=(
            CapabilityInputContract(name="target", type_name="ResolvedItem", source_types=("target_resolver",)),
            CapabilityInputContract(name="target", type_name="ResolvedItem", source_types=("target_resolver",)),
        ),
        produces=(CapabilityOutputContract(name="snapshot", type_name="ItemSnapshot"),),
        preconditions=(CapabilityPreconditionContract(code="target_verified", description="target verified", verifier_owner="resolver"),),
        completion=CapabilityCompletionContract(mode="tool_output", proof_type="ItemSnapshot", output_name="snapshot"),
    )
    with pytest.raises(ValueError, match="duplicate required input"):
        ToolCapabilityContract(
            key="demo.duplicate.inputs",
            tool_name="demo_duplicate_inputs",
            category="query",
            writes_business_data=False,
            evidence_sources=("demo_service",),
            planner_rule="demo",
            unavailable_response="unavailable",
            completion_effects=("demo.query:item",),
            contract_version="2",
            planning_contract=planning,
        )


def test_v2_contract_rejects_inconsistent_completion_proof_shape() -> None:
    from agent_core.kernel.capability import (
        CapabilityCompletionContract,
        CapabilityInputContract,
        CapabilityOutputContract,
        CapabilityPlanningContract,
        CapabilityPreconditionContract,
        CapabilityTargetContract,
        ToolCapabilityContract,
    )

    planning = CapabilityPlanningContract(
        target=CapabilityTargetContract(resource_types=("item",), cardinality="exactly_one"),
        requires=(CapabilityInputContract(name="target", type_name="ResolvedItem", source_types=("target_resolver",)),),
        produces=(CapabilityOutputContract(name="snapshot", type_name="ItemSnapshot", completion_proof=True),),
        preconditions=(CapabilityPreconditionContract(code="target_verified", description="target verified", verifier_owner="resolver"),),
        completion=CapabilityCompletionContract(mode="tool_output", proof_type="DifferentSnapshot", output_name="snapshot"),
    )
    with pytest.raises(ValueError, match="proof type"):
        ToolCapabilityContract(
            key="demo.invalid.proof-type",
            tool_name="demo_invalid_proof_type",
            category="query",
            writes_business_data=False,
            evidence_sources=("demo_service",),
            planner_rule="demo",
            unavailable_response="unavailable",
            completion_effects=("demo.query:item",),
            contract_version="2",
            planning_contract=planning,
        )

    with pytest.raises(ValueError, match="cannot reference a produced output"):
        CapabilityCompletionContract(
            mode="transaction_receipt",
            proof_type="ItemReceipt",
            proof_source="transaction_authority",
            output_name="draft",
        )


def test_ecommerce_verticals_expose_v2_planning_contracts() -> None:
    from agent_modules.ecommerce.capabilities.registry import BY_TOOL_NAME

    expected = {
        "get_order_logistics",
        "list_invoices",
        "evaluate_refund_eligibility",
        "prepare_refund",
        "prepare_refund_from_eligibility",
        "prepare_invoice",
    }
    for tool_name in expected:
        contract = BY_TOOL_NAME[tool_name].contract
        assert contract.contract_version == "2"
        assert contract.planning_contract is not None
        assert contract.planning_contract.target.resource_types
        assert contract.planning_contract.requires
        assert contract.planning_contract.produces
        assert contract.planning_contract.preconditions
        assert contract.planning_contract.completion.proof_type


def test_query_and_action_completion_proofs_are_distinct() -> None:
    from agent_modules.ecommerce.capabilities.registry import BY_TOOL_NAME

    logistics = BY_TOOL_NAME["get_order_logistics"].contract.planning_contract
    invoice_query = BY_TOOL_NAME["list_invoices"].contract.planning_contract
    refund = BY_TOOL_NAME["prepare_refund"].contract.planning_contract
    invoice = BY_TOOL_NAME["prepare_invoice"].contract.planning_contract
    assert logistics is not None and logistics.completion.mode == "tool_output"
    assert invoice_query is not None and invoice_query.completion.mode == "tool_output"
    assert refund is not None and refund.completion.mode == "transaction_receipt"
    assert invoice is not None and invoice.completion.mode == "transaction_receipt"
    assert refund.completion.proof_source == "transaction_authority"
    assert invoice.completion.proof_source == "transaction_authority"
    assert {output.type_name for output in refund.produces} == {"TransactionDraft"}
    assert {output.type_name for output in invoice.produces} == {"TransactionDraft"}
    assert not any(output.completion_proof for output in refund.produces)
    assert not any(output.completion_proof for output in invoice.produces)


def test_refund_eligibility_output_is_fresh_capability_input() -> None:
    from agent_modules.ecommerce.capabilities.registry import BY_TOOL_NAME

    eligibility = BY_TOOL_NAME["evaluate_refund_eligibility"].contract.planning_contract
    promote = BY_TOOL_NAME["prepare_refund_from_eligibility"].contract.planning_contract
    assert eligibility is not None and promote is not None
    assessment = next(output for output in eligibility.produces if output.name == "eligibility_assessment")
    required = next(value for value in promote.requires if value.name == "eligibility_assessment")
    assert assessment.type_name == required.type_name
    assert required.source_types == ("capability_output",)
    assert required.freshness_seconds == 300


def test_registry_exposes_deterministic_v2_snapshot() -> None:
    from agent_core.kernel.capability_registry import CapabilityRegistry
    from agent_modules.ecommerce.module import EcommerceModule

    registry = CapabilityRegistry(EcommerceModule().contribution().capabilities)
    snapshot = registry.planning_contract_snapshot(["get_order_logistics", "prepare_refund"])
    assert snapshot["version"] == "capability-planning-snapshot@2"
    assert [row["tool_name"] for row in snapshot["capabilities"]] == [
        "get_order_logistics",
        "prepare_refund",
    ]
    assert snapshot["capabilities"][1]["planning_contract"]["completion"]["mode"] == "transaction_receipt"
