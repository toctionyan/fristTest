from __future__ import annotations

"""Explicit dependencies for the lifecycle graph.

The graph must never construct hidden storage dependencies during a model turn.
Application composition owns StoreProvider lookup and passes the concrete
transaction repository and trace sink in once.
"""

from dataclasses import dataclass
from typing import Any, Callable

from agent_core.context import ContextBundleBuilder
from agent_core.storage.repositories.base import TransactionLifecycleRepository
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.business import BusinessPort
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.runtime.outcomes import outcome


@dataclass(frozen=True)
class LifecycleRuntimeDeps:
    context_bundle_builder: ContextBundleBuilder
    transactions: TransactionLifecycleRepository
    capability_registry: CapabilityRegistry
    transaction_execution: TransactionExecutionDeps
    trace_logger: Any | None = None
    # Production passes the sole model gateway. Tests may pass a scripted
    # resolver to exercise the compiled lifecycle graph without network I/O.
    model_resolver: Callable[[], Any] | None = None


def lifecycle_runtime_deps(
    *,
    transactions: TransactionLifecycleRepository,
    capability_registry: CapabilityRegistry,
    business_port: BusinessPort,
    trace_logger: Any | None = None,
    model_resolver: Callable[[], Any] | None = None,
) -> LifecycleRuntimeDeps:
    if model_resolver is None:
        from agent_core.config import get_model

        model_resolver = get_model
    return LifecycleRuntimeDeps(
        context_bundle_builder=ContextBundleBuilder(transactions=transactions, trace_logger=trace_logger),
        transactions=transactions,
        trace_logger=trace_logger,
        capability_registry=capability_registry,
        transaction_execution=TransactionExecutionDeps(
            business_port=business_port,
            outcome_factory=outcome,
        ),
        model_resolver=model_resolver,
    )
