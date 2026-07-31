from __future__ import annotations

"""Stable top-level architecture contracts for the clean runtime."""

RUNTIME_ARCHITECTURE_VERSION = "18.0"
BUSINESS_COMMAND_CONTRACT = "business.operation.command@1"

# The rules are intentionally executable/documentable constants.  Tests and
# startup validation use the registries below; this list is the human-facing
# contract for code review.
ARCHITECTURE_INVARIANTS: tuple[str, ...] = (
    "runtime_kernel_must_not_import_domain_resource_plugins",
    "resource_plugins_own_identity_not_operation_rules",
    "operation_plugins_own_preview_command_projection_not_transport",
    "business_gateway_owns_transport_not_action_policy",
    "transaction_runtime_owns_draft_grant_attempt_receipt",
    "business_service_owns_business_facts_and_final_mutation",
    "customer_output_must_be_projected_from_runtime_outcome",
    "single_target_operations_must_not_be_widened_into_batch",
)
