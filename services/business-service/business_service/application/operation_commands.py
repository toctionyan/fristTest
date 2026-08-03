from __future__ import annotations

"""Business-side stable operation-command dispatcher.

Actor, subject and resource are separate security dimensions.  The command
contains integrity assertions for all three, while the authenticated Actor and
business database remain authoritative.  Assertions can narrow a request; they
can never grant authority.
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
    subject_scope: dict[str, Any]
    resource_scope: dict[str, Any]


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
    subject_scope = row.get("subject_scope")
    resource_scope = row.get("resource_scope")
    for name, value in (
        ("actor_scope", actor_scope),
        ("subject_scope", subject_scope),
        ("resource_scope", resource_scope),
    ):
        if value is not None and not isinstance(value, dict):
            raise OperationCommandError(f"业务命令 {name} 无效。")

    # Additive compatibility: older envelopes carried subject inside
    # actor_scope and omitted an explicit resource assertion.  Normalize them
    # into the new three-boundary shape before verification.
    normalized_actor = dict(actor_scope or {})
    normalized_subject = dict(subject_scope or {})
    normalized_resource = dict(resource_scope or {})
    legacy_subject = str(normalized_actor.get("subject") or "").strip()
    payload_subject = str(input_values.get("subject_user_id") or "").strip()
    if not normalized_subject and (legacy_subject or payload_subject):
        normalized_subject = {
            "subject_user_id": legacy_subject or payload_subject,
            "tenant_id": normalized_actor.get("tenant_id"),
        }
    if not normalized_resource:
        normalized_resource = {
            "resource_type": resource_type,
            "resource_id": resource_id,
        }
        if input_values.get("expected_version") is not None:
            normalized_resource["expected_version"] = input_values.get("expected_version")

    return NormalizedOperationCommand(
        command_id=command_id,
        action_id=action_id,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        input_values=dict(input_values),
        actor_scope=normalized_actor,
        subject_scope=normalized_subject,
        resource_scope=normalized_resource,
    )


def verify_actor_scope(command: NormalizedOperationCommand, *, user_id: str, tenant_id: str, role: str | None = None) -> None:
    """Verify Actor/Subject/Resource integrity without granting permission.

    Domain handlers still perform tenant, on-behalf, ownership, state and
    version authorization against business-authoritative records.
    """

    asserted_user = str(
        command.actor_scope.get("actor_user_id")
        or command.actor_scope.get("user_id")
        or ""
    ).strip()
    asserted_tenant = str(command.actor_scope.get("tenant_id") or "").strip()
    asserted_role = str(command.actor_scope.get("actor_role") or command.actor_scope.get("role") or "").strip()
    if asserted_user and asserted_user != str(user_id):
        raise OperationCommandError("业务命令 Actor 与已认证用户不一致。")
    if asserted_tenant and asserted_tenant != str(tenant_id):
        raise OperationCommandError("业务命令 Actor 与已认证租户不一致。")
    if asserted_role and role is not None and asserted_role != str(role):
        raise OperationCommandError("业务命令 Actor 角色与已认证角色不一致。")

    asserted_subject = str(command.subject_scope.get("subject_user_id") or "").strip()
    payload_subject = str(command.input_values.get("subject_user_id") or "").strip()
    subject_tenant = str(command.subject_scope.get("tenant_id") or "").strip()
    if asserted_subject and payload_subject and asserted_subject != payload_subject:
        raise OperationCommandError("业务命令业务主体与提交载荷不一致。")
    if subject_tenant and subject_tenant != str(tenant_id):
        raise OperationCommandError("业务命令业务主体租户与已认证租户不一致。")

    asserted_resource_type = str(command.resource_scope.get("resource_type") or "").strip()
    asserted_resource_id = str(command.resource_scope.get("resource_id") or "").strip()
    if asserted_resource_type and asserted_resource_type != command.resource_type:
        raise OperationCommandError("业务命令目标资源类型不一致。")
    if asserted_resource_id and asserted_resource_id != command.resource_id:
        raise OperationCommandError("业务命令目标资源标识不一致。")
    asserted_resource_subject = str(command.resource_scope.get("subject_user_id") or "").strip()
    effective_subject = asserted_subject or payload_subject
    if asserted_resource_subject and effective_subject and asserted_resource_subject != effective_subject:
        raise OperationCommandError("业务命令目标资源主体与业务主体不一致。")

    asserted_version = command.resource_scope.get("expected_version")
    payload_version = command.input_values.get("expected_version")
    if asserted_version is not None and payload_version is not None:
        try:
            if int(asserted_version) != int(payload_version):
                raise OperationCommandError("业务命令资源版本与提交载荷不一致。")
        except (TypeError, ValueError) as exc:
            raise OperationCommandError("业务命令资源版本无效。") from exc


def dispatch_operation_command(
    command: NormalizedOperationCommand,
    *,
    handlers: Mapping[tuple[str, str], Callable[[NormalizedOperationCommand], dict[str, Any]]],
) -> dict[str, Any]:
    handler = handlers.get((command.resource_type, command.operation))
    if handler is None:
        raise OperationCommandError("当前业务服务未注册该操作能力。")
    return handler(command)
