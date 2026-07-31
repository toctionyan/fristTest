from __future__ import annotations

"""Ecommerce-owned assessment definitions.

Assessments are capability metadata, not Kernel defaults.  Keeping them next to
this module prevents an empty Kernel from silently retaining refund semantics.
"""

from agent_core.operations.assessment import OperationAssessmentDefinition


def ecommerce_operation_assessments() -> tuple[OperationAssessmentDefinition, ...]:
    return (
        OperationAssessmentDefinition(
            assessment_id="refund_eligibility",
            label="退款资格核验",
            target_resource_type="order",
            promoted_action_id="create_refund",
            business_operation="APPLY_REFUND",
        ),
    )
