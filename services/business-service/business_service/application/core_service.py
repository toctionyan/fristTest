"""Cross-domain Business Service application base."""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from ..api_models import (
    AfterSalesCreateRequest, ComplaintCreateRequest, HumanHandoffCreateRequest,
    InvoiceCreateRequest, OperationCommandRequest, RefundCreateRequest,
)
from ..database import BusinessDatabase, as_dict, json_dump, json_load, utcnow
from ..domain import DomainError, tenant_matches
from ..security import Actor, require_any
from .common import (BusinessDomainError, _actor_can_read_any, _row_order)
from .operation_commands import (OperationCommandError, dispatch_operation_command, normalize_operation_command, verify_actor_scope)


class ServiceCore:
    def __init__(self, database: BusinessDatabase):
        self.db = database

    @staticmethod
    def ok(data: Any, **extra: Any) -> dict[str, Any]:
        return {"success": True, "data": data, **extra}

    def _audit(
        self,
        conn,
        actor: Actor,
        action: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        subject_user_id: str | None = None,
        command_name: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        idempotency_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_events(
                tenant_id,actor_user_id,actor_role,subject_user_id,action,command_name,
                resource_type,resource_id,from_status,to_status,details_json,request_id,
                idempotency_key,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                actor.tenant_id,
                actor.user_id,
                actor.role,
                subject_user_id,
                action,
                command_name,
                resource_type,
                resource_id,
                from_status,
                to_status,
                json_dump(details or {}),
                actor.request_id,
                idempotency_key,
                utcnow(),
            ),
        )

    def _idempotent(
        self,
        *,
        actor: Actor,
        command_name: str,
        key: str | None,
        request_body: dict[str, Any],
        operation: Callable[[Any], dict[str, Any]],
    ) -> dict[str, Any]:
        # Tenant and command are part of the storage scope and canonical request
        # hash. This eliminates cross-tenant replay/result leakage.
        normalized = {
            "tenant_id": actor.tenant_id,
            "actor_id": actor.user_id,
            "command": command_name,
            "body": request_body,
        }
        request_hash = hashlib.sha256(json_dump(normalized).encode("utf-8")).hexdigest()
        with self.db.transaction() as conn:
            if key:
                row = conn.execute(
                    """
                    SELECT request_hash,response_json FROM idempotency_records
                    WHERE tenant_id=? AND actor_user_id=? AND command_name=? AND idempotency_key=?
                    """,
                    (actor.tenant_id, actor.user_id, command_name, key),
                ).fetchone()
                if row:
                    if str(row["request_hash"]) != request_hash:
                        raise BusinessDomainError(
                            409,
                            "同一幂等键不能用于不同请求内容。",
                            code="IDEMPOTENCY_KEY_REUSED",
                        )
                    replay = json_load(str(row["response_json"]))
                    replay["idempotent"] = True
                    return replay
            result = operation(conn)
            if key:
                conn.execute(
                    """
                    INSERT INTO idempotency_records(
                        tenant_id,actor_user_id,command_name,idempotency_key,request_hash,response_json,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        actor.tenant_id,
                        actor.user_id,
                        command_name,
                        key,
                        request_hash,
                        json_dump(result),
                        utcnow(),
                    ),
                )
            return result

    def _owned_order(
        self, conn, actor: Actor, order_id: str, *, subject_user_id: str | None = None
    ) -> dict[str, Any]:
        order = _row_order(conn, order_id)
        if not order:
            raise BusinessDomainError(404, "订单不存在。", code="ORDER_NOT_FOUND")
        if not tenant_matches(actor, str(order.get("tenant_id"))):
            raise BusinessDomainError(404, "订单不存在。", code="ORDER_NOT_FOUND")
        if subject_user_id:
            if str(order.get("user_id")) != subject_user_id:
                raise BusinessDomainError(
                    403, "订单不属于当前业务主体。", code="ORDER_SUBJECT_MISMATCH"
                )
        elif (
            not _actor_can_read_any(actor)
            and str(order.get("user_id")) != actor.user_id
        ):
            raise BusinessDomainError(403, "无权访问该订单。", code="ORDER_FORBIDDEN")
        return order

    def _product_for_order(self, conn, order: dict[str, Any]) -> dict[str, Any] | None:
        return as_dict(
            conn.execute(
                "SELECT * FROM products WHERE product_id=?", (order.get("product_id"),)
            ).fetchone()
        )

    def _active_exists(self, conn, table: str, order_id: str) -> bool:
        return (
            conn.execute(
                f"SELECT 1 FROM {table} WHERE order_id=? AND status NOT IN ('已拒绝','已关闭','已完成','已失败') LIMIT 1",
                (order_id,),
            ).fetchone()
            is not None
        )

    def auth_me(self, actor: Actor) -> dict[str, Any]:
        with self.db.read() as conn:
            row = as_dict(
                conn.execute(
                    "SELECT * FROM accounts WHERE user_id=?", (actor.user_id,)
                ).fetchone()
            )
        if not row:
            return self.ok(
                {
                    "user_id": actor.user_id,
                    "tenant_id": actor.tenant_id,
                    "role": actor.role,
                    "permissions": sorted(actor.permissions),
                }
            )
        row["permissions"] = json_load(str(row.pop("permissions_json")))
        return self.ok(row)

    def accounts(self, actor: Actor) -> dict[str, Any]:
        require_any(actor, ["business:read_any"])
        with self.db.read() as conn:
            sql = "SELECT user_id,tenant_id,role,display_name FROM accounts"
            params: list[Any] = []
            if not is_platform_admin(actor):
                sql += " WHERE tenant_id=?"
                params.append(actor.tenant_id)
            return self.ok(
                rows_as_dicts(
                    conn.execute(sql + " ORDER BY tenant_id,user_id", params).fetchall()
                )
            )

    def execute_operation_command(
        self,
        actor: Actor,
        payload: OperationCommandRequest,
        *,
        key: str | None = None,
    ) -> dict[str, Any]:
        """Execute one frozen operation command through the business boundary.

        This is a transport-neutral command port for Agent/UI integrations.
        It does not bypass the existing domain methods: each handler reuses the
        same authorization, version, state-transition and idempotency logic as
        ordinary REST routes.
        """
        try:
            command = normalize_operation_command(payload.model_dump())
            verify_actor_scope(command, user_id=actor.user_id, tenant_id=actor.tenant_id, role=actor.role)
        except OperationCommandError as exc:
            raise BusinessDomainError(400, str(exc), code="INVALID_OPERATION_COMMAND") from exc

        def required_expected_version(values: dict[str, Any]) -> int:
            version = int(values.get("expected_version") or 0)
            if version <= 0:
                raise BusinessDomainError(400, "业务命令缺少有效版本号。", code="EXPECTED_VERSION_REQUIRED")
            return version

        def subject(values: dict[str, Any]) -> str | None:
            return str(values.get("subject_user_id") or "").strip() or None

        handlers = {
            ("order", "APPLY_REFUND"): lambda row: self.create_refund(
                actor,
                RefundCreateRequest(
                    order_id=row.resource_id,
                    expected_version=required_expected_version(row.input_values),
                    reason=str(row.input_values.get("reason") or ""),
                    reason_code=str(row.input_values.get("reason_code") or "") or None,
                    subject_user_id=subject(row.input_values),
                ),
                key=key,
            ),
            ("order", "APPLY_AFTER_SALES"): lambda row: self.create_after_sales(
                actor,
                AfterSalesCreateRequest(
                    order_id=row.resource_id,
                    expected_version=required_expected_version(row.input_values),
                    reason=str(row.input_values.get("reason") or ""),
                    reason_code=str(row.input_values.get("reason_code") or "") or None,
                    subject_user_id=subject(row.input_values),
                ),
                key=key,
            ),
            ("order", "APPLY_INVOICE"): lambda row: self.create_invoice(
                actor,
                InvoiceCreateRequest(
                    order_id=row.resource_id,
                    expected_version=required_expected_version(row.input_values),
                    invoice_title=str(row.input_values.get("invoice_title") or ""),
                    subject_user_id=subject(row.input_values),
                ),
                key=key,
            ),
            ("order", "CANCEL_ORDER"): lambda row: self.cancel_order(
                actor,
                row.resource_id,
                str(row.input_values.get("reason") or "") or None,
                required_expected_version(row.input_values),
                key=key,
            ),
        }
        try:
            return dispatch_operation_command(command, handlers=handlers)
        except OperationCommandError as exc:
            raise BusinessDomainError(400, str(exc), code="UNSUPPORTED_OPERATION_COMMAND") from exc
