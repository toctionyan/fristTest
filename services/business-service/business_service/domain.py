from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .database import as_dict, utcnow
from .security import Actor


class DomainError(Exception):
    def __init__(self, status_code: int, message: str, *, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


@dataclass(frozen=True)
class ResourceSpec:
    resource_type: str
    table: str
    id_field: str
    subject_field: str = "subject_user_id"
    status_field: str = "status"
    order_field: str | None = None
    review_permission: str | None = None
    transitions: Mapping[str, Mapping[str, str]] | None = None
    owner_commands: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ResourceScope:
    resource_type: str
    resource_id: str
    tenant_id: str
    subject_user_id: str | None
    status: str | None
    version: int
    row: dict[str, Any]


REFUND_TRANSITIONS = {
    "待审核": {"approve": "已通过", "reject": "已拒绝", "cancel": "已关闭"},
    "已通过": {"start_processing": "处理中", "cancel": "已关闭"},
    "处理中": {"complete": "已完成", "fail": "已失败"},
}
AFTER_SALES_TRANSITIONS = {
    "待审核": {"approve": "已通过", "reject": "已拒绝", "cancel": "已关闭"},
    "已通过": {"start_processing": "处理中", "complete": "已完成", "cancel": "已关闭"},
    "处理中": {"complete": "已完成", "fail": "已失败"},
}
INVOICE_TRANSITIONS = {
    "待开票": {"issue": "已开票", "reject": "已拒绝", "cancel": "已关闭"},
}
COMPLAINT_TRANSITIONS = {
    "待处理": {"accept": "已受理", "close": "已关闭"},
    "已受理": {"start_processing": "处理中", "close": "已关闭"},
    "处理中": {"complete": "已完成", "close": "已关闭"},
}
HANDOFF_TRANSITIONS = {
    "排队中": {"accept": "已受理", "close": "已关闭"},
    "已受理": {"start_processing": "处理中", "resolve": "已完成", "close": "已关闭"},
    "处理中": {"resolve": "已完成", "close": "已关闭"},
}

RESOURCE_SPECS: dict[str, ResourceSpec] = {
    "refund": ResourceSpec(
        "refund",
        "refunds",
        "refund_id",
        order_field="order_id",
        review_permission="refund:review",
        transitions=REFUND_TRANSITIONS,
        owner_commands=frozenset({"cancel"}),
    ),
    "after_sales": ResourceSpec(
        "after_sales",
        "after_sales_tickets",
        "ticket_id",
        order_field="order_id",
        review_permission="after_sales:review",
        transitions=AFTER_SALES_TRANSITIONS,
        owner_commands=frozenset({"cancel"}),
    ),
    "invoice": ResourceSpec(
        "invoice",
        "invoices",
        "invoice_id",
        order_field="order_id",
        review_permission="invoice:review",
        transitions=INVOICE_TRANSITIONS,
        owner_commands=frozenset({"cancel"}),
    ),
    "complaint": ResourceSpec(
        "complaint",
        "complaints",
        "complaint_id",
        review_permission="complaint:review",
        transitions=COMPLAINT_TRANSITIONS,
    ),
    "handoff": ResourceSpec(
        "handoff",
        "human_handoffs",
        "handoff_id",
        review_permission="support:handoff:review",
        transitions=HANDOFF_TRANSITIONS,
    ),
}


def is_platform_admin(actor: Actor) -> bool:
    """Only the administrator role can bypass a tenant boundary.

    A developer is deliberately not an operations or platform-administrator
    role. Debug access is granted by the Agent service permission model and
    must never become implicit business write/read-any authority here.
    """
    return actor.role == "admin" and actor.tenant_id in {"*", "platform"}


def is_operator(actor: Actor) -> bool:
    """Return whether this actor may use cross-user operations capabilities."""
    return actor.role in {"operator", "admin"}


def tenant_matches(actor: Actor, tenant_id: str | None) -> bool:
    return is_platform_admin(actor) or str(tenant_id or "default") == actor.tenant_id


def resolve_subject(
    conn, actor: Actor, requested_subject_user_id: str | None, *, permission: str
) -> str:
    subject = str(requested_subject_user_id or actor.user_id).strip() or actor.user_id
    row = as_dict(
        conn.execute(
            "SELECT user_id,tenant_id FROM accounts WHERE user_id=?", (subject,)
        ).fetchone()
    )
    if not row:
        raise DomainError(404, "业务主体用户不存在。", code="SUBJECT_NOT_FOUND")
    if not tenant_matches(actor, str(row.get("tenant_id"))):
        raise DomainError(
            403, "不能为其他租户用户办理业务。", code="CROSS_TENANT_SUBJECT"
        )
    if subject != actor.user_id and not actor.can(permission):
        raise DomainError(
            403, "当前身份没有代办该业务的权限。", code="ON_BEHALF_DENIED"
        )
    return subject


def load_resource_scope(
    conn, actor: Actor, resource_type: str, resource_id: str
) -> tuple[ResourceSpec, ResourceScope]:
    spec = RESOURCE_SPECS.get(resource_type)
    if not spec:
        raise DomainError(
            500, f"未知资源类型：{resource_type}", code="UNKNOWN_RESOURCE"
        )
    row = as_dict(
        conn.execute(
            f"SELECT * FROM {spec.table} WHERE {spec.id_field}=?", (resource_id,)
        ).fetchone()
    )
    if not row:
        raise DomainError(404, "业务记录不存在。", code="RESOURCE_NOT_FOUND")
    if not tenant_matches(actor, str(row.get("tenant_id"))):
        # Do not disclose a cross-tenant record. The event is still audited by
        # the caller at the API/security boundary if desired.
        raise DomainError(404, "业务记录不存在。", code="RESOURCE_NOT_FOUND")
    subject = str(row.get(spec.subject_field) or row.get("user_id") or "") or None
    return spec, ResourceScope(
        resource_type=resource_type,
        resource_id=resource_id,
        tenant_id=str(row.get("tenant_id") or "default"),
        subject_user_id=subject,
        status=str(row.get(spec.status_field) or "") or None,
        version=int(row.get("version") or 1),
        row=row,
    )


def require_command_permission(
    actor: Actor, spec: ResourceSpec, scope: ResourceScope, command: str
) -> None:
    if command in spec.owner_commands and scope.subject_user_id == actor.user_id:
        return
    if spec.review_permission and actor.can(spec.review_permission):
        return
    raise DomainError(403, "当前身份无权执行该业务命令。", code="COMMAND_FORBIDDEN")

def _scope_from_row(spec: ResourceSpec, resource_type: str, row: Mapping[str, Any]) -> ResourceScope:
    """Build a resource scope from an already-visible query row.

    This keeps the command capability projection in the same domain module as
    authorization and transitions. Presentation layers receive capabilities;
    they never infer them from a status label.
    """
    item = dict(row)
    subject = str(item.get(spec.subject_field) or item.get("user_id") or "") or None
    return ResourceScope(
        resource_type=resource_type,
        resource_id=str(item.get(spec.id_field) or ""),
        tenant_id=str(item.get("tenant_id") or "default"),
        subject_user_id=subject,
        status=str(item.get(spec.status_field) or "") or None,
        version=int(item.get("version") or 1),
        row=item,
    )


def available_commands_for_row(
    actor: Actor, resource_type: str, row: Mapping[str, Any]
) -> list[str]:
    """Return the commands this authenticated actor may execute *now*.

    The result is derived from the authoritative transition table and command
    permission check. It is an API capability projection, not a second UI
    state machine.
    """
    spec = RESOURCE_SPECS.get(resource_type)
    if not spec:
        return []
    scope = _scope_from_row(spec, resource_type, row)
    allowed = dict((spec.transitions or {}).get(str(scope.status or ""), {}))
    commands: list[str] = []
    for command in allowed:
        try:
            require_command_permission(actor, spec, scope, command)
        except DomainError:
            continue
        commands.append(command)
    return commands


def resource_status_options(resource_type: str) -> list[str]:
    """Return every lifecycle state for filtering a resource collection."""
    spec = RESOURCE_SPECS.get(resource_type)
    if not spec:
        return []
    ordered: list[str] = []
    for current, transitions in (spec.transitions or {}).items():
        if current not in ordered:
            ordered.append(current)
        for target in transitions.values():
            if target not in ordered:
                ordered.append(target)
    return ordered


def transition_resource(
    conn,
    *,
    actor: Actor,
    resource_type: str,
    resource_id: str,
    command: str,
    expected_version: int,
    note: str | None,
) -> tuple[ResourceScope, dict[str, Any]]:
    spec, scope = load_resource_scope(conn, actor, resource_type, resource_id)
    require_command_permission(actor, spec, scope, command)
    if expected_version != scope.version:
        raise DomainError(
            409, "业务记录已被其他操作更新，请刷新后重试。", code="VERSION_CONFLICT"
        )
    transitions = dict(spec.transitions or {})
    allowed = dict(transitions.get(str(scope.status or ""), {}))
    next_status = allowed.get(command)
    if not next_status:
        raise DomainError(
            409,
            f"当前状态“{scope.status}”不允许执行命令“{command}”。",
            code="INVALID_TRANSITION",
        )
    now = utcnow()
    issued_at = (
        now
        if resource_type == "invoice" and command == "issue"
        else scope.row.get("issued_at")
    )
    cursor = conn.execute(
        f"""
        UPDATE {spec.table}
        SET status=?, operator_note=?, reviewed_by=?, reviewed_by_actor_id=?, reviewed_at=?,
            issued_at=COALESCE(?, issued_at), version=version+1, updated_at=?
        WHERE {spec.id_field}=? AND tenant_id=? AND status=? AND version=?
        """
        if resource_type == "invoice"
        else f"""
        UPDATE {spec.table}
        SET status=?, operator_note=?, reviewed_by=?, reviewed_by_actor_id=?, reviewed_at=?,
            version=version+1, updated_at=?
        WHERE {spec.id_field}=? AND tenant_id=? AND status=? AND version=?
        """,
        (
            (
                next_status,
                note,
                actor.user_id,
                actor.user_id,
                now,
                issued_at,
                now,
                resource_id,
                scope.tenant_id,
                scope.status,
                expected_version,
            )
            if resource_type == "invoice"
            else (
                next_status,
                note,
                actor.user_id,
                actor.user_id,
                now,
                now,
                resource_id,
                scope.tenant_id,
                scope.status,
                expected_version,
            )
        ),
    )
    if cursor.rowcount != 1:
        raise DomainError(
            409, "业务记录状态已变化，请刷新后重试。", code="TRANSITION_CONFLICT"
        )
    row = as_dict(
        conn.execute(
            f"SELECT * FROM {spec.table} WHERE {spec.id_field}=?", (resource_id,)
        ).fetchone()
    )
    assert row is not None
    return scope, row


ISSUE_REASON_OPTIONS = (
    {"value": "QUALITY_ISSUE", "label": "质量问题"},
    {"value": "SPEC_MISMATCH", "label": "规格或描述不符"},
    {"value": "WRONG_ITEM", "label": "发错商品"},
    {"value": "OTHER", "label": "其他"},
)
SPECIAL_PRODUCT_REASON_CODES = frozenset({"QUALITY_ISSUE", "SPEC_MISMATCH", "WRONG_ITEM"})


def _reason_code_required_input() -> list[dict[str, Any]]:
    """Business-owned reason form metadata for preview/UI/Agent alike."""
    return [{
        "name": "reason_code",
        "label": "问题类型",
        "input_kind": "select",
        "allow_custom": True,
        "step": 1,
        "step_title": "选择问题类型",
        "options": [dict(item) for item in ISSUE_REASON_OPTIONS],
    }]


def _quality_or_spec(reason_code: str | None) -> bool:
    """Return whether a user-confirmed business reason qualifies for exceptions.

    This function intentionally does *not* classify free-form language.  The
    raw reason remains stored for audit; the selected reason code is a normal
    business form field confirmed by the user and validated server-side.
    """
    return str(reason_code or "").strip().upper() in SPECIAL_PRODUCT_REASON_CODES


def refund_decision(
    order: Mapping[str, Any],
    product: Mapping[str, Any] | None,
    reason: str,
    active_refund: bool,
    reason_code: str | None = None,
) -> dict[str, Any]:
    """Single server-owned refund policy.

    ``can_submit`` is deliberately separate from automatic completion. A result
    requiring review still permits a normal business application to be created.
    """
    product = product or {}
    if not bool(order.get("paid")):
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "UNPAID",
            "message": "订单尚未付款，不能申请退款。",
        }
    if str(order.get("status")) in {"已取消", "已退款"}:
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "TERMINAL_ORDER",
            "message": "订单当前状态不能申请退款。",
        }
    if active_refund:
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "ACTIVE_REFUND_EXISTS",
            "message": "该订单已有未关闭退款申请。",
        }
    if str(order.get("status")) in {"待发货", "已付款", "已发货", "运输中", "配送中"}:
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "ORDER_NOT_SIGNED",
            "message": "订单尚未签收，请先取消订单或联系商家处理配送。",
        }
    signed_days = int(order.get("signed_days_ago") or 0)
    is_custom = "定制" in str(product.get("category") or "") or "定制" in str(
        order.get("product_name") or ""
    )
    if not bool(product.get("support_after_sales", 1)) and not _quality_or_spec(reason_code):
        if not str(reason_code or "").strip():
            return {
                "decision": "NEEDS_INPUT",
                "can_submit": False,
                "reason_code": "REASON_CODE_REQUIRED",
                "message": "该商品需要确认问题类型后才能判断是否可退款。",
                "required_inputs": _reason_code_required_input(),
            }
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "PRODUCT_NOT_RETURNABLE",
            "message": "该商品不支持普通退货退款。",
        }
    review_codes: list[str] = []
    if signed_days > 15:
        review_codes.append("SIGNED_OVER_15_DAYS")
    if float(order.get("amount") or 0) >= 1000:
        review_codes.append("HIGH_AMOUNT")
    if is_custom:
        if not _quality_or_spec(reason_code):
            if not str(reason_code or "").strip():
                return {
                    "decision": "NEEDS_INPUT",
                    "can_submit": False,
                    "reason_code": "REASON_CODE_REQUIRED",
                    "message": "定制商品需要确认问题类型后才能判断是否可退款。",
                    "required_inputs": _reason_code_required_input(),
                }
            return {
                "decision": "DENY",
                "can_submit": False,
                "reason_code": "CUSTOM_PRODUCT_REASON",
                "message": "定制商品需选择质量问题、规格/描述不符或发错商品等原因后才能申请退款。",
            }
        review_codes.append("CUSTOM_PRODUCT")
    if review_codes:
        return {
            "decision": "REQUIRE_REVIEW",
            "can_submit": True,
            "reason_codes": review_codes,
            "message": "退款申请可以提交，后续需要人工审核。",
        }
    return {
        "decision": "ALLOW_SUBMIT",
        "can_submit": True,
        "reason_codes": [],
        "message": "该订单可以提交退款申请。",
    }


def after_sales_decision(
    order: Mapping[str, Any],
    product: Mapping[str, Any] | None,
    reason: str,
    active_ticket: bool,
    reason_code: str | None = None,
) -> dict[str, Any]:
    product = product or {}
    if not bool(order.get("paid")) or str(order.get("status")) in {"已取消", "已退款"}:
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "ORDER_NOT_ELIGIBLE",
            "message": "该订单当前不能申请售后。",
        }
    if str(order.get("status")) != "已签收":
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "ORDER_NOT_SIGNED",
            "message": "订单尚未签收，暂不能申请售后。",
        }
    if active_ticket:
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "ACTIVE_AFTER_SALES_EXISTS",
            "message": "该订单已有未关闭售后申请。",
        }
    if not bool(product.get("support_after_sales", 1)) and not _quality_or_spec(reason_code):
        if not str(reason_code or "").strip():
            return {
                "decision": "NEEDS_INPUT",
                "can_submit": False,
                "reason_code": "REASON_CODE_REQUIRED",
                "message": "该商品需要确认问题类型后才能判断是否可申请售后。",
                "required_inputs": _reason_code_required_input(),
            }
        return {
            "decision": "DENY",
            "can_submit": False,
            "reason_code": "PRODUCT_NOT_SUPPORTED",
            "message": "该商品不支持普通售后。",
        }
    return {
        "decision": "ALLOW_SUBMIT",
        "can_submit": True,
        "reason_codes": [],
        "message": "该订单可以提交售后申请。",
    }
