"""Versioned generic presentation contracts and release gates."""

from .governance import (
    ContractValidationResult,
    PresentationContractRegistry,
    ProjectionContractViolation,
    controlled_violation_block,
    validate_block_against_manifest,
)
from .release_gate import StructuredResultReleaseDecision, StructuredResultReleaseGate
from .renderer_registry import RendererRegistration, RendererRegistry

__all__ = [
    "ContractValidationResult",
    "PresentationContractRegistry",
    "ProjectionContractViolation",
    "RendererRegistration",
    "RendererRegistry",
    "StructuredResultReleaseDecision",
    "StructuredResultReleaseGate",
    "controlled_violation_block",
    "validate_block_against_manifest",
]
