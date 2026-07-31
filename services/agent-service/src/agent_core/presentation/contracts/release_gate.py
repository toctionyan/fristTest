"""Fail-closed formal release gate for structured customer results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .governance import (
    PresentationContractRegistry,
    ProjectionContractViolation,
    controlled_violation_block,
)
from .renderer_registry import RendererRegistry


@dataclass(frozen=True)
class StructuredResultReleaseDecision:
    released: bool
    blocks: tuple[dict[str, Any], ...]
    violation: ProjectionContractViolation | None = None


class StructuredResultReleaseGate:
    """Release only registered, renderable and coverage-complete formal blocks.

    The gate is domain-neutral: manifests define fields and coverage semantics;
    this class enforces that formal structured results cannot bypass a contract,
    a registered renderer, or their declared coverage policy.
    """

    _CONTROLLED_TYPES = {"projection_contract_violation", "notice"}
    _SECONDARY_TEXT_TYPES = {"summary", "text"}

    def __init__(self, contracts: PresentationContractRegistry, renderers: RendererRegistry) -> None:
        self._contracts = contracts
        self._renderers = renderers

    @staticmethod
    def _formal(block: dict[str, Any]) -> bool:
        if str(block.get("type") or "") in StructuredResultReleaseGate._CONTROLLED_TYPES:
            return False
        if str(block.get("type") or "") in StructuredResultReleaseGate._SECONDARY_TEXT_TYPES and str(block.get("role") or "") == "secondary":
            return False
        return bool(block.get("contract_id")) or str(block.get("role") or "") == "primary"

    @staticmethod
    def _violation(
        *,
        block: dict[str, Any],
        consumer: str,
        trace_id: str | None,
        semantics: tuple[str, ...] | list[str],
        contract_id: str | None = None,
        version: int | None = None,
    ) -> ProjectionContractViolation:
        return ProjectionContractViolation(
            contract_id=contract_id or str(block.get("contract_id") or "unknown_contract"),
            version=version,
            producer=str(block.get("producer") or "unknown_producer"),
            consumer=consumer,
            projection_boundary=str(block.get("projection_boundary") or "unknown_boundary"),
            missing_required_semantics=tuple(sorted(set(str(value) for value in semantics if str(value)))),
            degradation_level="controlled_error",
            trace_id=trace_id,
        )

    def _coverage_violation(
        self,
        block: dict[str, Any],
        manifest: dict[str, Any],
        *,
        consumer: str,
        trace_id: str | None,
    ) -> ProjectionContractViolation | None:
        declared = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
        actual = block.get("coverage") if isinstance(block.get("coverage"), dict) else {}
        expected_mode = str(declared.get("mode") or "")
        problems: list[str] = []
        if not expected_mode:
            problems.append("coverage_policy")
        if str(actual.get("mode") or "") != expected_mode:
            problems.append("coverage_mode")
        if str(actual.get("source_population") or "") != str(declared.get("source_population") or ""):
            problems.append("coverage_source_population")
        if expected_mode == "full":
            if str(actual.get("status") or "") != "complete":
                problems.append("coverage_complete")
            resolved = actual.get("resolved_member_count")
            presented = actual.get("presented_member_count")
            if not isinstance(resolved, int) or resolved < 0:
                problems.append("coverage_resolved_member_count")
            if not isinstance(presented, int) or presented < 0:
                problems.append("coverage_presented_member_count")
            if isinstance(resolved, int) and isinstance(presented, int) and resolved != presented:
                problems.append("coverage_population_mismatch")
            items = block.get("items")
            if isinstance(items, list) and isinstance(presented, int) and len(items) != presented:
                problems.append("coverage_rendered_item_count")
        elif expected_mode == "paged":
            if str(actual.get("status") or "") != "partial_visible":
                problems.append("coverage_partial_visible")
            if not isinstance(actual.get("total_count"), int) or not isinstance(actual.get("presented_member_count"), int):
                problems.append("coverage_paged_counts")
            if actual.get("continuation_exposed") is not True:
                problems.append("coverage_continuation")
        elif expected_mode == "summary":
            if str(actual.get("status") or "") != "summary_visible":
                problems.append("coverage_summary_visible")
        elif expected_mode == "not_collection":
            if str(actual.get("status") or "") != "not_applicable":
                problems.append("coverage_not_applicable")
        else:
            problems.append("coverage_mode")
        if not problems:
            return None
        return self._violation(
            block=block,
            consumer=consumer,
            trace_id=trace_id,
            semantics=problems,
            contract_id=str(manifest.get("contract_id") or "unknown_contract"),
            version=_int_or_none(manifest.get("version")),
        )

    def release(
        self,
        blocks: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        channel: str = "web",
        consumer: str = "structured_result_release_gate",
        trace_id: str | None = None,
        require_primary: bool = False,
    ) -> StructuredResultReleaseDecision:
        normalized = [dict(block) for block in blocks if isinstance(block, dict)]
        if require_primary and not normalized:
            violation = self._violation(
                block={},
                consumer=consumer,
                trace_id=trace_id,
                semantics=("registered_primary_presentation",),
            )
            return StructuredResultReleaseDecision(False, (controlled_violation_block(violation),), violation)
        for block in normalized:
            if not self._formal(block):
                continue
            validation = self._contracts.validate(block, consumer=consumer, trace_id=trace_id, require_contract=True)
            if not validation.valid:
                violation = validation.violations[0]
                return StructuredResultReleaseDecision(False, (controlled_violation_block(violation),), violation)
            contract_id = str(block.get("contract_id") or "")
            manifest = self._contracts.manifest(contract_id)
            assert manifest is not None
            release = manifest.get("release") if isinstance(manifest.get("release"), dict) else {}
            expected_renderer = str((manifest.get("renderer") or {}).get(channel) or "")
            registry_key = str(release.get("renderer_registry_key") or "")
            if release.get("formal_response_eligible") is not True:
                violation = self._violation(
                    block=block,
                    consumer=consumer,
                    trace_id=trace_id,
                    semantics=("formal_response_eligibility",),
                    contract_id=contract_id,
                    version=_int_or_none(manifest.get("version")),
                )
                return StructuredResultReleaseDecision(False, (controlled_violation_block(violation),), violation)
            if not expected_renderer or registry_key != contract_id or not self._renderers.is_registered(contract_id, channel, expected_renderer_id=expected_renderer):
                violation = self._violation(
                    block=block,
                    consumer=consumer,
                    trace_id=trace_id,
                    semantics=("registered_channel_renderer",),
                    contract_id=contract_id,
                    version=_int_or_none(manifest.get("version")),
                )
                return StructuredResultReleaseDecision(False, (controlled_violation_block(violation),), violation)
            coverage = self._coverage_violation(block, manifest, consumer=consumer, trace_id=trace_id)
            if coverage is not None:
                return StructuredResultReleaseDecision(False, (controlled_violation_block(coverage),), coverage)
        return StructuredResultReleaseDecision(True, tuple(normalized), None)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
