from __future__ import annotations

"""Operation-level extension contracts.

Operation plugins own operation-specific policy translation: input schema,
preview identity, command construction and result artifact projection.  They do
not own resource resolution, tool dispatch, transaction state or HTTP routing.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent_core.business import ActorContext, BusinessPort
from agent_core.operations.capability import OperationCapability, single_target_operation_capability


InputSchema = list[dict[str, Any]]
PayloadTransformer = Callable[[dict[str, Any]], dict[str, Any]]


class OperationPlugin(Protocol):
    action_id: str
    business_code: str
    business_operation: str
    label: str
    risk_level: str
    target_resource_type: str
    input_schema: InputSchema
    operation_capability: OperationCapability


    def public_metadata(self, *, target: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def preview(self, adapter: BusinessPort, actor: ActorContext, *, target: dict[str, Any], input_values: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def build_commit_payload(self, *, actor: ActorContext, target: dict[str, Any], input_values: dict[str, Any], preview: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def build_business_command_envelope(self, *, actor: ActorContext, target: dict[str, Any], input_values: dict[str, Any], preview: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def commit_envelope(self, adapter: BusinessPort, actor: ActorContext, *, envelope: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
    def commit(self, adapter: BusinessPort, actor: ActorContext, *, target: dict[str, Any], payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
    def project_result_artifacts(self, *, target: dict[str, Any], result: dict[str, Any], existing_target: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DeclarativeOperationPlugin:
    """Generic operation implementation driven by declarative metadata.

    The object deliberately has no order-id, REST-path or action-specific
    branch.  Resource identity is normalized by a ResourcePlugin before this
    class is called; business transport is delegated to BusinessGateway.
    """

    action_id: str
    business_code: str
    business_operation: str
    label: str
    risk_level: str
    input_schema: InputSchema
    target_resource_type: str
    mode: str = "agent_transaction"
    intent_template: str = ""
    capability_key: str = "business_application"
    required_payload_fields: tuple[str, ...] = field(default_factory=tuple)
    result_resource_type: str | None = None
    result_id_field: str | None = None
    refresh_target_on_success: bool = False
    requires_expected_version: bool = True
    payload_transformer: PayloadTransformer | None = None
    operation_capability: OperationCapability | None = None

    def __post_init__(self) -> None:
        capability = self.operation_capability or single_target_operation_capability(
            action_id=self.action_id,
            target_type=self.target_resource_type,
        )
        if self.target_resource_type not in capability.target_resource_types:
            raise ValueError("operation capability target type does not match operation target type")
        object.__setattr__(self, "operation_capability", capability)


    def _resource_id(self, target: dict[str, Any]) -> str:
        resource_id = str((target or {}).get("resource_id") or "").strip()
        if not resource_id:
            raise ValueError("operation target requires resource_id")
        return resource_id

    def public_metadata(self, *, target: dict[str, Any] | None = None) -> dict[str, Any]:
        row = dict(target or {})
        resource_id = str(row.get("resource_id") or "")
        intent = self.intent_template.format(resource_id=resource_id) if self.intent_template else self.label
        return {
            "id": self.action_id,
            "action_id": self.action_id,
            "business_code": self.business_code,
            "business_operation": self.business_operation,
            "label": self.label,
            "risk_level": self.risk_level,
            "target_type": self.target_resource_type,
            "target_resource_type": self.target_resource_type,
            "mode": self.mode,
            "intent": intent,
            "capability_key": self.capability_key,
            "operation_capability": {
                "capability_id": self.operation_capability.capability_id,
                "version": self.operation_capability.version,
                "target_cardinality": self.operation_capability.target_cardinality,
                "max_targets": self.operation_capability.max_targets,
                "execution_mode": self.operation_capability.execution_mode,
                "supports_lifecycle_query": self.operation_capability.supports_lifecycle_query,
            },
            "target": {"resource_type": self.target_resource_type, "resource_id": resource_id} if resource_id else {"resource_type": self.target_resource_type},
            "input_schema": [dict(item) for item in self.input_schema],
            "input_hints": {},
        }

    def preview(self, adapter: BusinessPort, actor: ActorContext, *, target: dict[str, Any], input_values: dict[str, Any] | None = None) -> dict[str, Any]:
        return adapter.preview_operation(
            actor,
            resource_type=self.target_resource_type,
            resource_id=self._resource_id(target),
            operation=self.business_operation,
            input_values=input_values or {},
        )

    def build_commit_payload(self, *, actor: ActorContext, target: dict[str, Any], input_values: dict[str, Any], preview: dict[str, Any] | None = None) -> dict[str, Any]:
        self._resource_id(target)
        snapshot = (preview or {}).get("snapshot") if isinstance((preview or {}).get("snapshot"), dict) else {}
        values = dict(input_values or {})
        if self.requires_expected_version and "expected_version" not in values and snapshot.get("version") is not None:
            values["expected_version"] = int(snapshot.get("version") or 1)
        values.setdefault("subject_user_id", actor.user_id)
        if self.payload_transformer:
            values = self.payload_transformer(values)
        required_by_preview = {
            str(row.get("name") or "")
            for row in ((preview or {}).get("required_inputs") or [])
            if isinstance(row, dict) and bool(row.get("required", True))
        }
        missing = [
            name for name in self.required_payload_fields
            if name in required_by_preview and not str(values.get(name) or "").strip()
        ]
        if missing:
            raise ValueError("missing commit payload fields: " + ", ".join(missing))
        if self.requires_expected_version and int(values.get("expected_version") or 0) <= 0:
            raise ValueError("expected_version is required for versioned transaction commits")
        return values

    def build_business_command_envelope(self, *, actor: ActorContext, target: dict[str, Any], input_values: dict[str, Any], preview: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.build_commit_payload(actor=actor, target=target, input_values=input_values, preview=preview)
        resource_id = self._resource_id(target)
        return {
            "contract": "business.operation.command@1",
            "action_id": self.action_id,
            "operation": self.business_operation,
            "target": {"resource_type": self.target_resource_type, "resource_id": resource_id},
            "input": payload,
            "actor_scope": {"tenant_id": actor.tenant_id, "user_id": actor.user_id, "subject": actor.subject or actor.user_id},
        }

    def commit_envelope(self, adapter: BusinessPort, actor: ActorContext, *, envelope: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if str(envelope.get("contract") or "") != "business.operation.command@1":
            raise ValueError("unsupported business operation command envelope")
        if str(envelope.get("action_id") or "") != self.action_id:
            raise ValueError("business command envelope action mismatch")
        target = dict(envelope.get("target") or {})
        if str(target.get("resource_type") or "") != self.target_resource_type:
            raise ValueError("business command envelope resource type mismatch")
        self._resource_id(target)
        return adapter.execute_command(
            actor,
            command=dict(envelope),
            idempotency_key=idempotency_key,
        )

    def commit(self, adapter: BusinessPort, actor: ActorContext, *, target: dict[str, Any], payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        envelope = {
            "contract": "business.operation.command@1",
            "action_id": self.action_id,
            "operation": self.business_operation,
            "target": {"resource_type": self.target_resource_type, "resource_id": self._resource_id(target)},
            "input": dict(payload or {}),
            "actor_scope": {"tenant_id": actor.tenant_id, "user_id": actor.user_id, "subject": actor.subject or actor.user_id},
        }
        return self.commit_envelope(adapter, actor, envelope=envelope, idempotency_key=idempotency_key)

    def project_result_artifacts(self, *, target: dict[str, Any], result: dict[str, Any], existing_target: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not result.get("success") or not isinstance(result.get("data"), dict):
            return []
        data = dict(result.get("data") or {})
        rows: list[dict[str, Any]] = []
        if self.result_resource_type and self.result_id_field:
            resource_id = str(data.get(self.result_id_field) or "")
            if resource_id:
                rows.append({
                    "resource_type": self.result_resource_type,
                    "resource_id": resource_id,
                    "label": f"{self.result_resource_type}:{resource_id}",
                    "facts": data,
                    "freshness_version": int(data.get("version") or 1),
                })
        if self.refresh_target_on_success:
            resource_id = str(data.get("resource_id") or target.get("resource_id") or (existing_target or {}).get("resource_id") or "")
            if resource_id:
                rows.append({
                    "resource_type": self.target_resource_type,
                    "resource_id": resource_id,
                    "label": str((existing_target or {}).get("label") or f"{self.target_resource_type}:{resource_id}"),
                    "facts": data,
                    "freshness_version": int(data.get("version") or 1),
                    "handle": str((existing_target or {}).get("handle") or "") or None,
                })
        return rows
