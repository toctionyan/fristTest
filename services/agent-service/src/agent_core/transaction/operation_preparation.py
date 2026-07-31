from __future__ import annotations

"""Single capability gate before business preview and Draft creation."""

from dataclasses import dataclass
from typing import Any

from agent_core.operations.registry import OperationPluginRegistry
from agent_core.modules import current_runtime_registry
from agent_core.kernel import RuntimeRegistry
from agent_core.operations.capability import OperationCapability
from agent_core.kernel.outcome_contract import OutcomeFactory, OutcomeReadModel
from agent_core.resources.targets import ResolvedTargetSet
from agent_core.transaction.capability_snapshot import snapshot_for_action


@dataclass(frozen=True)
class PreparedOperation:
    plugin: Any
    capability: OperationCapability
    target_set: ResolvedTargetSet
    capability_snapshot: dict[str, Any]


class OperationPreparationRuntime:
    """Validates action and target shape before *any* preview side effect."""

    def __init__(
        self,
        outcome_factory: OutcomeFactory,
        registry: OperationPluginRegistry | None = None,
        runtime_registry: RuntimeRegistry | None = None,
    ) -> None:
        # The composition root is the permanent dependency.  ``registry`` is
        # retained only for focused tests/custom extensions.
        self._runtime_registry = runtime_registry or current_runtime_registry()
        self._registry = registry or self._runtime_registry.operations
        self._outcome = outcome_factory

    def prepare(
        self,
        *,
        action_id: str,
        target_set: ResolvedTargetSet,
        correlation_id: str | None = None,
    ) -> tuple[PreparedOperation | None, OutcomeReadModel | None]:
        if not target_set.scope_verified or not target_set.handles:
            return None, self._outcome(
                "failure",
                correlation_id=correlation_id,
                evidence_handles=list(target_set.evidence_handles),
                customer_safe_summary="当前对象引用尚未完成可信校验，系统未创建或提交任何业务申请。",
                payload={"action_id": action_id, "target_set": target_set.as_dict()},
            )
        plugin = self._registry.get(str(action_id or ""))
        if plugin is None:
            return None, self._outcome(
                "unsupported_capability",
                correlation_id=correlation_id,
                customer_safe_summary="当前系统未提供该业务申请能力，未创建或提交任何业务操作。",
            )
        if self._runtime_registry.resources.get(target_set.resource_type) is None:
            return None, self._outcome(
                "unsupported_capability",
                correlation_id=correlation_id,
                evidence_handles=list(target_set.evidence_handles),
                customer_safe_summary="当前系统未注册该业务对象类型，未创建或提交任何业务操作。",
                payload={"resource_type": target_set.resource_type},
            )
        capability = getattr(plugin, "operation_capability", None)
        if capability is None:
            return None, self._outcome(
                "failure",
                correlation_id=correlation_id,
                customer_safe_summary="当前业务能力合同不完整，系统未创建或提交任何业务申请。",
            )
        if target_set.resource_type not in set(capability.target_resource_types):
            return None, self._outcome(
                "unsupported_capability",
                correlation_id=correlation_id,
                evidence_handles=list(target_set.evidence_handles),
                customer_safe_summary="当前业务申请不支持该对象类型，未创建或提交任何业务操作。",
                payload={"action_id": action_id, "resource_type": target_set.resource_type},
            )
        count = target_set.count
        max_targets = capability.max_targets
        if count < capability.min_targets or (max_targets is not None and count > max_targets):
            return None, self._outcome(
                "unsupported_cardinality",
                correlation_id=correlation_id,
                evidence_handles=list(target_set.evidence_handles),
                customer_safe_summary=(
                    f"当前{getattr(plugin, 'label', '业务申请')}一次只能处理一个业务对象，不能把多个对象合并为一次办理。"
                    "系统未创建草稿，也未提交任何业务申请。请选择其中一个对象后再继续。"
                ),
                next_interaction="need_selection",
                payload={
                    "action_id": action_id,
                    "target_count": count,
                    "min_targets": capability.min_targets,
                    "max_targets": max_targets,
                    "target_set": target_set.as_dict(),
                },
            )
        snapshot = snapshot_for_action(action_id)
        if snapshot is None:
            return None, self._outcome(
                "failure",
                correlation_id=correlation_id,
                customer_safe_summary="当前业务能力合同无法冻结，系统未创建或提交任何业务申请。",
            )
        return PreparedOperation(plugin=plugin, capability=capability, target_set=target_set, capability_snapshot=snapshot), None
