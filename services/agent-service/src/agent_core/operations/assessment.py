from __future__ import annotations

"""Read-only operation assessment contracts.

An assessment is business evidence that may later be promoted into a Draft. It
is not a second transaction lifecycle and cannot authorize a write. This makes
"can I do X?" reusable across refund, cancellation, replacement and future
operations without creating operation-specific side channels.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationAssessmentDefinition:
    assessment_id: str
    label: str
    target_resource_type: str
    promoted_action_id: str
    business_operation: str
    capability_key: str = "operation_assessment"
    supports_promotion: bool = True
