from __future__ import annotations

"""Business boundary contracts.

The Agent Runtime emits a stable, business-neutral operation command.  HTTP
paths and backend-specific client methods are adapter details and must not leak
into operation plugins or transaction nodes.
"""

from typing import Any, Protocol

from agent_core.business.contracts import ActorContext


class BusinessGateway(Protocol):
    def preview_operation(
        self,
        actor: ActorContext,
        *,
        resource_type: str | None,
        resource_id: str | None,
        operation: str,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def execute_command(
        self,
        actor: ActorContext,
        *,
        command: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...
