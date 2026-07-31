from __future__ import annotations

"""Business-side stable operation-command dispatcher.

This module is intentionally independent of FastAPI and the Agent.  It checks
the transport-neutral command envelope and then selects a business-owned
handler.  New Agent operations do not add routing logic to the Agent adapter.
"""

from dataclasses import dataclass
from typing import Any, Callable, Mapping


BUSINESS_COMMAND_CONTRACT = "business.operation.command@1"


class OperationCommandError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedOperationCommand:
    command_id: str | None
    action_id: str
    operation: str
    resource_type: str
    resource_id: str
    input_values: dict[str, Any]
    actor_scope: dict[str, Any]


def normalize_operation_command(payload: Mapping[str, Any]) -> NormalizedOperationCommand:
    row = dict(payload or {})
    if str(row.get("contract") or "") != BUSINESS_COMMAND_CONTRACT:
        raise OperationCommandError("不支持的业务命令合同。")
    command_id = str(row.get("command_id") or "").strip() or None
    action_id = str(row.get("action_id") or "").strip()
    operation = str(row.get("operation") or "").strip().upper()
    target = dict(row.get("target") or {})
    resource_type = str(target.get("resource_type") or "").strip()
    resource_id = str(target.get("resource_id") or "").strip()
    if not action_id or not operation:
        raise OperationCommandError("业务命令缺少动作标识或操作标识。")
    if not resource_type or not resource_id:
        raise OperationCommandError("业务命令缺少明确目标对象。")
    input_values = row.get("input")
    if not isinstance(input_values, dict):
        raise OperationCommandError("业务命令输入必须是对象。")
    actor_scope = row.get("actor_scope")
    if actor_scope is not None and not isinstance(actor_scope, dict):
        raise OperationCommandError("业务命令身份范围无效。")
    return NormalizedOperationCommand(
        command_id=command_id,
        action_id=action_id,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        input_values=dict(input_values),
        actor_scope=dict(actor_scope or {}),
    )


def verify_actor_scope(command: NormalizedOperationCommand, *, user_id: str, tenant_id: str) -> None:
    """Actor scope is an integrity assertion, never an authority override."""
    asserted_user = str(command.actor_scope.get("user_id") or "").strip()
    asserted_tenant = str(command.actor_scope.get("tenant_id") or "").strip()
    if asserted_user and asserted_user != str(user_id):
        raise OperationCommandError("业务命令身份与已认证用户不一致。")
    if asserted_tenant and asserted_tenant != str(tenant_id):
        raise OperationCommandError("业务命令租户与已认证租户不一致。")


def dispatch_operation_command(
    command: NormalizedOperationCommand,
    *,
    handlers: Mapping[tuple[str, str], Callable[[NormalizedOperationCommand], dict[str, Any]]],
) -> dict[str, Any]:
    handler = handlers.get((command.resource_type, command.operation))
    if handler is None:
        raise OperationCommandError("当前业务服务未注册该操作能力。")
    return handler(command)
