"""Client-neutral presentation composition and formal structured release gate."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from agent_core.presentation.adapters import PresentationAdapter
from agent_core.presentation.contracts import (
    PresentationContractRegistry,
    RendererRegistry,
    StructuredResultReleaseGate,
)
from agent_core.presentation.contracts.runtime import (
    runtime_presentation_contract_manifests,
    runtime_presentation_renderer_registrations,
)

logger = logging.getLogger(__name__)


class PresentationRegistry:
    """Compose one primary customer view and release it only when renderable.

    Adapters own domain projection.  This registry does not map business fields;
    it only checks that selected formal blocks have an active contract,
    registered channel renderer, and complete declared coverage.
    """

    def __init__(self, adapters: Iterable[PresentationAdapter] | None = None) -> None:
        self._adapters: list[PresentationAdapter] = []
        self._contracts = PresentationContractRegistry(runtime_presentation_contract_manifests())
        self._renderers = RendererRegistry(runtime_presentation_renderer_registrations())
        for adapter in adapters or ():
            self.register(adapter)
        self._release_gate = StructuredResultReleaseGate(self._contracts, self._renderers)

    def register(self, adapter: PresentationAdapter) -> None:
        adapter_id = str(getattr(adapter, "adapter_id", "") or "")
        if not adapter_id:
            raise ValueError("presentation adapter must declare adapter_id")
        self._adapters = [row for row in self._adapters if str(getattr(row, "adapter_id", "")) != adapter_id]
        manifests = getattr(adapter, "presentation_contracts", None)
        if callable(manifests):
            for manifest in manifests() or ():
                if isinstance(manifest, dict):
                    self._contracts.register(manifest)
        renderer_rows = getattr(adapter, "presentation_renderers", None)
        if callable(renderer_rows):
            for registration in renderer_rows() or ():
                self._renderers.register(registration)
        self._adapters.append(adapter)

    def adapters(self) -> tuple[PresentationAdapter, ...]:
        return tuple(self._adapters)

    def release_blocks(
        self,
        blocks: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        trace_id: str | None = None,
        require_primary: bool = False,
    ) -> list[dict[str, Any]]:
        decision = self._release_gate.release(
            blocks,
            channel="web",
            consumer="presentation_registry",
            trace_id=trace_id,
            require_primary=require_primary,
        )
        if not decision.released and decision.violation is not None:
            logger.error("projection_contract_violation", extra={"violation": decision.violation.as_dict()})
        return [dict(block) for block in decision.blocks]

    def compose(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Release one canonical primary block per independent Goal scope.

        Adapters may attach private ``_goal_ids`` provenance.  Candidates in
        the same scope compete by priority, preserving prerequisite
        supersession for a single Goal.  Different Goal scopes are all
        released, preventing a multi-task turn from silently losing otherwise
        verified results.  Legacy blocks without Goal provenance share the
        turn scope and retain the former single-primary behaviour.
        """
        candidates: list[tuple[int, int, int, tuple[str, ...], dict[str, Any]]] = []
        for adapter_index, adapter in enumerate(self._adapters):
            adapter_priority = int(getattr(adapter, "priority", 0) or 0)
            try:
                for candidate_index, row in enumerate(adapter.blocks_from_trace(trace) or ()):
                    if not isinstance(row, dict) or not row.get("type"):
                        continue
                    block = dict(row)
                    block_priority = int(block.get("priority", adapter_priority) or adapter_priority)
                    goal_ids = tuple(sorted({
                        str(value)
                        for value in list(block.get("_goal_ids") or [])
                        if str(value)
                    }))
                    scope = goal_ids or ("__turn__",)
                    order = int(block.get("_presentation_order", candidate_index) or candidate_index)
                    candidates.append((block_priority, -adapter_index, order, scope, block))
            except Exception as exc:
                logger.exception(
                    "presentation_adapter_failed",
                    extra={
                        "adapter_id": str(getattr(adapter, "adapter_id", adapter.__class__.__name__)),
                        "trace_size": len(trace),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue

        primary_candidates = [candidate for candidate in candidates if str(candidate[4].get("role") or "") == "primary"]
        pool = primary_candidates or candidates
        if not pool:
            return []

        selected_by_scope: dict[tuple[str, ...], tuple[int, int, int, tuple[str, ...], dict[str, Any]]] = {}
        for candidate in pool:
            scope = candidate[3]
            current = selected_by_scope.get(scope)
            if current is None or (candidate[0], candidate[1], -candidate[2]) > (current[0], current[1], -current[2]):
                selected_by_scope[scope] = candidate

        selected = sorted(selected_by_scope.values(), key=lambda item: item[2])
        public_blocks: list[dict[str, Any]] = []
        for candidate in selected:
            block = {
                key: value
                for key, value in candidate[4].items()
                if not str(key).startswith("_")
            }
            public_blocks.append(block)
        return self.release_blocks(public_blocks, trace_id=_trace_id(trace), require_primary=True)


def _trace_id(trace: list[dict[str, Any]]) -> str | None:
    for row in reversed(trace):
        if isinstance(row, dict):
            value = str(row.get("trace_id") or row.get("call_id") or "").strip()
            if value:
                return value
    return None


_default_registry_factory: Callable[[], PresentationRegistry] = PresentationRegistry
_default_registry: PresentationRegistry | None = None

def configure_default_presentation_registry(factory: Callable[[], PresentationRegistry]) -> None:
    global _default_registry_factory, _default_registry
    _default_registry_factory = factory
    _default_registry = None

def default_presentation_registry() -> PresentationRegistry:
    global _default_registry
    if _default_registry is None:
        # Response projection must not depend on whether another code path
        # happened to resolve the Runtime Registry first.  Resolve the already
        # configured module provider lazily; this keeps Core domain-neutral
        # while ensuring every entrypoint receives installed adapters.
        if _default_registry_factory is PresentationRegistry:
            try:
                from agent_core.modules import current_module_registry

                _default_registry = PresentationRegistry(current_module_registry().presentation_adapters())
            except RuntimeError:
                _default_registry = _default_registry_factory()
        else:
            _default_registry = _default_registry_factory()
    return _default_registry


def _record_contract_violations(result: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
    violations = [
        dict(block.get("contract_violation") or {})
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "projection_contract_violation"
    ]
    if violations:
        existing = result.get("presentation_contract_violations")
        result["presentation_contract_violations"] = [*(existing if isinstance(existing, list) else []), *violations]


def build_response_blocks(
    result: dict[str, Any],
    *,
    answer: str | None = None,
    registry: PresentationRegistry | None = None,
) -> list[dict[str, Any]]:
    """Build a formal structured view or a plain narrative text block.

    Query traces only leave as structured results through ``compose``.  A text
    block remains available solely for narrative callers with no tool trace;
    it is not a fallback for an unregistered structured result.
    """
    active_registry = registry or default_presentation_registry()
    trace = list(result.get("tool_trace") or [])
    blocks = active_registry.compose(trace)
    if blocks:
        _record_contract_violations(result, blocks)
        return blocks
    if trace:
        violation = active_registry.release_blocks([], trace_id=_trace_id(trace), require_primary=True)
        _record_contract_violations(result, violation)
        return violation
    content = str(answer or "").strip()
    return [{"type": "text", "content": content}] if content else []
