"""Domain-neutral business boundary contracts.

The Agent Core knows only resource-shaped reads, parameterized queries,
non-mutating operation previews and frozen command execution.  It must not
encode order/logistics/refund routes or domain method names here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SubjectContext:
    """Business subject on whose behalf an authenticated Actor operates."""

    user_id: str
    tenant_id: str | None = None


@dataclass(frozen=True)
class ResourceScopeAssertion:
    """Integrity assertion for the exact resource snapshot being mutated."""

    resource_type: str
    resource_id: str
    expected_version: int | None = None
    subject_user_id: str | None = None


@dataclass(frozen=True)
class ActorContext:
    """Authenticated principal propagated to a business-system port.

    ``user_id`` is the Actor. ``subject_user_id`` is independent and never
    changes the Actor identity. ``subject`` remains a read-only migration alias.
    """

    user_id: str
    role: str = "customer"
    tenant_id: str | None = None
    subject_user_id: str | None = None
    subject: str | None = None
    permissions: tuple[str, ...] = field(default_factory=tuple)
    user_token: str | None = None

    @property
    def resolved_subject_user_id(self) -> str:
        return str(self.subject_user_id or self.subject or self.user_id)

    @property
    def subject_context(self) -> SubjectContext:
        return SubjectContext(self.resolved_subject_user_id, self.tenant_id)


class BusinessPort(Protocol):
    """Generic port used by Runtime and operation contracts only."""

    def health(self) -> dict[str, Any]: ...

    def read_resource(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        resource_id: str,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def query_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        query_spec: dict[str, Any],
    ) -> dict[str, Any]: ...

    def query_related_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        relation: dict[str, Any],
        query_spec: dict[str, Any],
    ) -> dict[str, Any]: ...

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
