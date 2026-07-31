"""Order preview and normal command application slice."""
from __future__ import annotations

from typing import Any

from ..api_models import (
    AddressChangeRequest,
    DeliveryUrgeRequest,
    _cancel_reason_input,
    _issue_description_input,
    _issue_type_input,
)
from ..database import as_dict, utcnow
from ..domain import (
    DomainError, RESOURCE_SPECS, available_commands_for_row, refund_decision,
    after_sales_decision, load_resource_scope, require_command_permission,
)
from ..security import Actor
from .common import (BusinessDomainError, _decorate_order, _row_order)


class OrderOperationMixin:
    @staticmethod
    def _preview_payload(
        *,
        snapshot: dict[str, Any],
        decision: str,
        message: str,
        blockers: list[dict[str, Any]] | None = None,
        required_inputs: list[dict[str, Any]] | None = None,
        alternatives: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "snapshot": snapshot,
            "decision": decision,
            "message": message,
            "blockers": blockers or [],
            "required_inputs": required_inputs or [],
            "alternatives": alternatives or [],
            "fetched_at": utcnow(),
        }

    def _order_preview_snapshot(self, conn, order: dict[str, Any]) -> dict[str, Any]:
        decorated = _decorate_order(order) or {}
        product = self._product_for_order(conn, order) or {}
        logistics = as_dict(
            conn.execute(
                "SELECT status,latest,eta,updated_at FROM logistics WHERE order_id=?",
                (str(order.get("order_id") or ""),),
            ).fetchone()
        ) or {}
        return {
            "resource_type": "order",
            "resource_id": str(order.get("order_id") or ""),
            "order_id": str(order.get("order_id") or ""),
            "product_name": str(order.get("product_name") or product.get("product_name") or ""),
            "status": decorated.get("status"),
            "received": str(order.get("status") or "") == "已签收",
            "version": int(order.get("version") or 1),
            "order": decorated,
            "product": product,
            "logistics": logistics,
        }

    def _blocked_with_alternatives(
        self,
        *,
        snapshot: dict[str, Any],
        code: str,
        message: str,
        alternatives: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._preview_payload(
            snapshot=snapshot,
            decision="BLOCKED",
            message=message,
            blockers=[{"code": code, "message": message}],
            alternatives=alternatives,
        )

    def operation_preview(
        self, actor: Actor, request: OperationPreviewRequest
    ) -> dict[str, Any]:
        """Authoritatively project command availability without mutating data.

        This is intentionally business-side and reusable by web UI, mobile,
        human service tools and Agent.  It centralises state/transition/policy
        contradictions once rather than duplicating them in the Agent.
        """
        operation = str(request.operation or "").strip().upper()
        resource_type = str(request.resource_type or "").strip() or None
        resource_id = str(request.resource_id or "").strip() or None
        values = dict(request.input or {})
        with self.db.read() as conn:
            # Target-free commands still receive a preview so every write path
            # has the same contract before user confirmation.
            if operation in {"CREATE_COMPLAINT", "CREATE_HUMAN_HANDOFF"}:
                reason = str(values.get("reason") or "").strip()
                if not reason:
                    return self.ok(
                        self._preview_payload(
                            snapshot={"resource_type": None, "resource_id": None},
                            decision="NEEDS_INPUT",
                            message="请补充处理原因。",
                            required_inputs=[{"name": "reason", "label": "处理原因"}],
                        )
                    )
                return self.ok(
                    self._preview_payload(
                        snapshot={"resource_type": None, "resource_id": None},
                        decision="ALLOWED",
                        message="可以提交该业务申请。",
                    )
                )

            if not resource_type or not resource_id:
                return self.ok(
                    self._preview_payload(
                        snapshot={"resource_type": resource_type, "resource_id": resource_id},
                        decision="NEEDS_INPUT",
                        message="请先明确要处理的业务对象。",
                        required_inputs=[{"name": "target", "label": "业务对象"}],
                    )
                )

            if resource_type == "order":
                order = self._owned_order(conn, actor, resource_id)
                snapshot = self._order_preview_snapshot(conn, order)
                product = self._product_for_order(conn, order)
                status = str(order.get("status") or "")
                if operation == "APPLY_REFUND":
                    reason = str(values.get("reason") or "").strip()
                    if not reason:
                        return self.ok(
                            self._preview_payload(
                                snapshot=snapshot,
                                decision="NEEDS_INPUT",
                                message="申请退款需要补充退款原因。",
                                required_inputs=[_issue_type_input(), _issue_description_input("退款原因")],
                            )
                        )
                    decision = refund_decision(
                        order, product, reason,
                        self._active_exists(conn, "refunds", resource_id),
                        str(values.get("reason_code") or "") or None,
                    )
                    if str(decision.get("decision") or "") == "NEEDS_INPUT":
                        return self.ok(self._preview_payload(snapshot=snapshot, decision="NEEDS_INPUT", message=str(decision.get("message") or "请补充必要信息。"), required_inputs=[dict(item) for item in decision.get("required_inputs") or [] if isinstance(item, dict)]))
                    if not decision.get("can_submit"):
                        alternatives: list[dict[str, Any]] = []
                        if str(decision.get("reason_code") or "") == "ORDER_NOT_SIGNED":
                            if status in {"待发货", "已付款"}:
                                alternatives.append({"operation": "CANCEL_ORDER", "allowed": True, "message": "订单尚未发货，可以改为取消订单。"})
                            else:
                                alternatives.append({"operation": "CONTACT_SUPPORT", "allowed": True, "message": "订单已发货但尚未签收，可处理配送或状态异常。"})
                        return self.ok(
                            self._blocked_with_alternatives(
                                snapshot=snapshot,
                                code=str(decision.get("reason_code") or "POLICY_DENIED"),
                                message=str(decision.get("message") or "当前不能申请退款。"),
                                alternatives=alternatives,
                            )
                        )
                    preview_decision = "NEEDS_REVIEW" if decision.get("decision") == "REQUIRE_REVIEW" else "ALLOWED"
                    return self.ok(
                        self._preview_payload(
                            snapshot=snapshot,
                            decision=preview_decision,
                            message=str(decision.get("message") or "可以提交退款申请。"),
                        )
                    )

                if operation == "APPLY_AFTER_SALES":
                    reason = str(values.get("reason") or "").strip()
                    if not reason or not str(values.get("reason_code") or "").strip():
                        return self.ok(self._preview_payload(snapshot=snapshot, decision="NEEDS_INPUT", message="请先选择问题类型并描述具体情况。", required_inputs=[_issue_type_input(), _issue_description_input("问题描述")]))
                    decision = after_sales_decision(order, product, reason, self._active_exists(conn, "after_sales_tickets", resource_id), str(values.get("reason_code") or "") or None)
                    if str(decision.get("decision") or "") == "NEEDS_INPUT":
                        return self.ok(self._preview_payload(snapshot=snapshot, decision="NEEDS_INPUT", message=str(decision.get("message") or "请补充必要信息。"), required_inputs=[dict(item) for item in decision.get("required_inputs") or [] if isinstance(item, dict)]))
                    if not decision.get("can_submit"):
                        return self.ok(self._blocked_with_alternatives(snapshot=snapshot, code=str(decision.get("reason_code") or "POLICY_DENIED"), message=str(decision.get("message") or "当前不能申请售后。"), alternatives=[{"operation":"CONTACT_SUPPORT","allowed":True,"message":"可联系人工客服进一步处理。"}]))
                    return self.ok(self._preview_payload(snapshot=snapshot, decision="ALLOWED", message=str(decision.get("message") or "可以提交售后申请。")))

                if operation == "APPLY_INVOICE":
                    title = str(values.get("invoice_title") or "").strip()
                    if not title:
                        return self.ok(self._preview_payload(snapshot=snapshot, decision="NEEDS_INPUT", message="申请发票需要补充发票抬头。", required_inputs=[{"name":"invoice_title","label":"发票抬头","input_kind":"text","step":1,"step_title":"填写开票信息"}]))
                    if not int(order.get("paid") or 0) or status == "已取消":
                        return self.ok(self._blocked_with_alternatives(snapshot=snapshot, code="INVOICE_NOT_ELIGIBLE", message="该订单当前不能申请发票。"))
                    return self.ok(self._preview_payload(snapshot=snapshot, decision="ALLOWED", message="该订单可以申请发票。"))

                if operation == "CANCEL_ORDER":
                    reason = str(values.get("reason") or "").strip()
                    if status not in {"待发货", "已付款"}:
                        return self.ok(self._blocked_with_alternatives(snapshot=snapshot, code="ORDER_CANCEL_NOT_ALLOWED", message=f"订单当前状态为 {status}，不能取消。", alternatives=[{"operation":"CONTACT_SUPPORT","allowed":True,"message":"可联系人工客服处理异常情况。"}]))
                    if not reason:
                        return self.ok(self._preview_payload(snapshot=snapshot, decision="NEEDS_INPUT", message="请选择取消原因。", required_inputs=[_cancel_reason_input()]))
                    return self.ok(self._preview_payload(snapshot=snapshot, decision="ALLOWED", message="订单当前可以取消。"))

                if operation == "CHANGE_ADDRESS":
                    address = str(values.get("address") or "").strip()
                    if not address:
                        return self.ok(self._preview_payload(snapshot=snapshot, decision="NEEDS_INPUT", message="修改地址需要提供新的收货地址。", required_inputs=[{"name":"address","label":"新的收货地址"}]))
                    if status not in {"待发货", "已付款"}:
                        return self.ok(self._blocked_with_alternatives(snapshot=snapshot, code="ADDRESS_CHANGE_NOT_ALLOWED", message="订单已进入发货流程，不能在线修改收货地址。", alternatives=[{"operation":"CONTACT_SUPPORT","allowed":True,"message":"可联系人工客服处理地址异常。"}]))
                    return self.ok(self._preview_payload(snapshot=snapshot, decision="ALLOWED", message="订单当前可以修改收货地址。"))

                if operation == "URGE_DELIVERY":
                    if status not in {"待发货", "已付款"}:
                        return self.ok(self._blocked_with_alternatives(snapshot=snapshot, code="URGE_NOT_ALLOWED", message=f"订单当前状态为 {status}，不需要催发货。", alternatives=[{"operation":"QUERY_LOGISTICS","allowed":True,"message":"可查询当前物流状态。"}]))
                    return self.ok(self._preview_payload(snapshot=snapshot, decision="ALLOWED", message="订单当前可以提交催发货。"))

                return self.ok(self._blocked_with_alternatives(snapshot=snapshot, code="UNKNOWN_OPERATION", message="当前订单不支持该业务操作。"))

            if resource_type == "refund" and operation == "CANCEL_REFUND":
                try:
                    spec, scope = load_resource_scope(conn, actor, "refund", resource_id)
                    require_command_permission(actor, spec, scope, "cancel")
                except DomainError as exc:
                    return self.ok(self._blocked_with_alternatives(snapshot={"resource_type":"refund", "resource_id":resource_id}, code=exc.code or "COMMAND_FORBIDDEN", message=exc.message))
                allowed = dict((spec.transitions or {}).get(str(scope.status or ""), {}))
                if "cancel" not in allowed:
                    return self.ok(self._blocked_with_alternatives(snapshot={"resource_type":"refund", "resource_id":resource_id, "status":scope.status, "version":scope.version}, code="INVALID_TRANSITION", message=f"当前退款状态“{scope.status}”不能取消。"))
                return self.ok(self._preview_payload(snapshot={"resource_type":"refund", "resource_id":resource_id, "status":scope.status, "version":scope.version}, decision="ALLOWED", message="当前退款申请可以取消。"))

            return self.ok(self._blocked_with_alternatives(snapshot={"resource_type":resource_type, "resource_id":resource_id}, code="UNKNOWN_OPERATION", message="当前资源不支持该业务操作。"))

    # ------------------------- ordinary business commands ------------------
    def cancel_order(
        self,
        actor: Actor,
        order_id: str,
        reason: str | None,
        expected_version: int,
        *,
        key: str | None,
    ) -> dict[str, Any]:
        def operation(conn):
            order = self._owned_order(conn, actor, order_id)
            if order["status"] not in {"待发货", "已付款"}:
                raise BusinessDomainError(
                    409,
                    f"订单当前状态为 {order['status']}，不能取消。",
                    code="ORDER_CANCEL_NOT_ALLOWED",
                )
            if int(order.get("version") or 1) != expected_version:
                raise BusinessDomainError(
                    409, "订单已被其他操作更新，请刷新后重试。", code="VERSION_CONFLICT"
                )
            now = utcnow()
            cursor = conn.execute(
                "UPDATE orders SET status='已取消',version=version+1,updated_at=? WHERE order_id=? AND tenant_id=? AND status IN ('待发货','已付款') AND version=?",
                (now, order_id, actor.tenant_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise BusinessDomainError(
                    409, "订单状态已变化，请刷新后重试。", code="ORDER_CONFLICT"
                )
            conn.execute(
                "UPDATE logistics SET status='已取消',latest='用户已取消订单',eta='无需配送',updated_at=? WHERE order_id=?",
                (now, order_id),
            )
            row = _row_order(conn, order_id)
            self._audit(
                conn,
                actor,
                "order.cancel",
                resource_type="order",
                resource_id=order_id,
                subject_user_id=str(order["user_id"]),
                from_status=str(order["status"]),
                to_status="已取消",
                idempotency_key=key,
                details={"reason": reason},
            )
            return self.ok(_decorate_order(row))

        return self._idempotent(
            actor=actor,
            command_name="order.cancel",
            key=key,
            request_body={
                "order_id": order_id,
                "reason": reason,
                "expected_version": expected_version,
            },
            operation=operation,
        )

    def change_address(
        self,
        actor: Actor,
        order_id: str,
        address: str,
        expected_version: int,
        *,
        key: str | None,
    ) -> dict[str, Any]:
        def operation(conn):
            order = self._owned_order(conn, actor, order_id)
            if order["status"] not in {"待发货", "已付款"}:
                raise BusinessDomainError(
                    409,
                    "订单已进入发货流程，不能在线修改收货地址。",
                    code="ADDRESS_CHANGE_NOT_ALLOWED",
                )
            if int(order.get("version") or 1) != expected_version:
                raise BusinessDomainError(
                    409, "订单已被其他操作更新，请刷新后重试。", code="VERSION_CONFLICT"
                )
            now = utcnow()
            cursor = conn.execute(
                "UPDATE orders SET shipping_address=?,version=version+1,updated_at=? WHERE order_id=? AND tenant_id=? AND version=?",
                (
                    address.strip(),
                    now,
                    order_id,
                    actor.tenant_id,
                    int(order.get("version") or 1),
                ),
            )
            if cursor.rowcount != 1:
                raise BusinessDomainError(
                    409, "订单已被其他操作更新，请刷新后重试。", code="VERSION_CONFLICT"
                )
            row = _row_order(conn, order_id)
            self._audit(
                conn,
                actor,
                "order.change_address",
                resource_type="order",
                resource_id=order_id,
                subject_user_id=str(order["user_id"]),
                idempotency_key=key,
                details={"address_changed": True},
            )
            return self.ok(_decorate_order(row))

        return self._idempotent(
            actor=actor,
            command_name="order.change_address",
            key=key,
            request_body={
                "order_id": order_id,
                "address": address,
                "expected_version": expected_version,
            },
            operation=operation,
        )

    def urge_delivery(
        self, actor: Actor, order_id: str, expected_version: int, *, key: str | None
    ) -> dict[str, Any]:
        def operation(conn):
            order = self._owned_order(conn, actor, order_id)
            if order["status"] not in {"待发货", "已付款"}:
                raise BusinessDomainError(
                    409,
                    f"订单当前状态为 {order['status']}，不需要催发货。",
                    code="URGE_NOT_ALLOWED",
                )
            if int(order.get("version") or 1) != expected_version:
                raise BusinessDomainError(
                    409, "订单已被其他操作更新，请刷新后重试。", code="VERSION_CONFLICT"
                )
            active = as_dict(
                conn.execute(
                    "SELECT * FROM delivery_urges WHERE order_id=? AND tenant_id=? AND status='待处理' ORDER BY created_at DESC LIMIT 1",
                    (order_id, actor.tenant_id),
                ).fetchone()
            )
            if active:
                return self.ok(active)
            now = utcnow()
            urge_id = _new_id("URGE")
            conn.execute(
                "INSERT INTO delivery_urges(urge_id,order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    urge_id,
                    order_id,
                    order["user_id"],
                    order["user_id"],
                    actor.user_id,
                    order["tenant_id"],
                    "待处理",
                    1,
                    now,
                    now,
                ),
            )
            row = as_dict(
                conn.execute(
                    "SELECT * FROM delivery_urges WHERE urge_id=?", (urge_id,)
                ).fetchone()
            )
            self._audit(
                conn,
                actor,
                "order.urge_delivery",
                resource_type="delivery_urge",
                resource_id=urge_id,
                subject_user_id=str(order["user_id"]),
                idempotency_key=key,
                details={"order_id": order_id},
            )
            return self.ok(row)

        return self._idempotent(
            actor=actor,
            command_name="order.urge_delivery",
            key=key,
            request_body={"order_id": order_id, "expected_version": expected_version},
            operation=operation,
        )
