"""Refund, after-sales, invoice and handoff application slice."""
from __future__ import annotations

from typing import Any

from ..api_models import (AfterSalesCreateRequest, ComplaintCreateRequest, HumanHandoffCreateRequest, InvoiceCreateRequest, RefundCreateRequest)
from ..database import as_dict, utcnow
from ..domain import ISSUE_REASON_OPTIONS, RESOURCE_SPECS, after_sales_decision, refund_decision, resolve_subject
from ..security import Actor
from .common import (BusinessDomainError, _decorate_order, _new_id, _visible_subject)


class ApplicationMixin:
    def _create_order_application(
        self,
        *,
        actor: Actor,
        request: ApplicationBase,
        table: str,
        id_field: str,
        prefix: str,
        command_name: str,
        policy: Callable[
            [dict[str, Any], dict[str, Any] | None, str, bool], dict[str, Any]
        ],
        key: str | None,
    ) -> dict[str, Any]:
        order_id = str(getattr(request, "order_id"))
        reason = str(getattr(request, "reason"))
        reason_code = str(getattr(request, "reason_code", "") or "").strip() or None

        def operation(conn):
            subject = resolve_subject(
                conn,
                actor,
                request.requested_subject,
                permission=f"{command_name.split('.')[0]}:create_on_behalf",
            )
            order = self._owned_order(conn, actor, order_id, subject_user_id=subject)
            if int(order.get("version") or 1) != int(getattr(request, "expected_version")):
                raise BusinessDomainError(
                    409,
                    "订单已被其他操作更新，请刷新后重新确认。",
                    code="VERSION_CONFLICT",
                )
            product = self._product_for_order(conn, order)
            decision = policy(
                order, product, reason, self._active_exists(conn, table, order_id), reason_code
            )
            if not decision.get("can_submit"):
                raise BusinessDomainError(
                    409,
                    str(decision.get("message") or "当前不能提交业务申请。"),
                    code=str(decision.get("reason_code") or "POLICY_DENIED"),
                )
            now = utcnow()
            record_id = _new_id(prefix)
            conn.execute(
                f"INSERT INTO {table}({id_field},order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,reason,reason_code,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    order_id,
                    subject,
                    subject,
                    actor.user_id,
                    order["tenant_id"],
                    reason.strip(),
                    reason_code,
                    "待审核",
                    1,
                    now,
                    now,
                ),
            )
            row = as_dict(
                conn.execute(
                    f"SELECT * FROM {table} WHERE {id_field}=?", (record_id,)
                ).fetchone()
            )
            self._audit(
                conn,
                actor,
                command_name,
                resource_type="refund" if table == "refunds" else "after_sales",
                resource_id=record_id,
                subject_user_id=subject,
                to_status="待审核",
                idempotency_key=key,
                details={
                    "order_id": order_id,
                    "policy_decision": decision.get("decision"),
                    "reason_codes": decision.get("reason_codes") or [],
                },
            )
            return self.ok(row, policy_decision=decision)

        return self._idempotent(
            actor=actor,
            command_name=command_name,
            key=key,
            request_body={
                "order_id": order_id,
                "expected_version": int(getattr(request, "expected_version")),
                "subject_user_id": request.requested_subject,
                "reason": reason,
                "reason_code": reason_code,
            },
            operation=operation,
        )

    def create_refund(
        self, actor: Actor, request: RefundCreateRequest, *, key: str | None
    ) -> dict[str, Any]:
        return self._create_order_application(
            actor=actor,
            request=request,
            table="refunds",
            id_field="refund_id",
            prefix="RF",
            command_name="refund.apply",
            policy=refund_decision,
            key=key,
        )

    def create_after_sales(
        self, actor: Actor, request: AfterSalesCreateRequest, *, key: str | None
    ) -> dict[str, Any]:
        return self._create_order_application(
            actor=actor,
            request=request,
            table="after_sales_tickets",
            id_field="ticket_id",
            prefix="AS",
            command_name="after_sales.apply",
            policy=after_sales_decision,
            key=key,
        )

    def refund_eligibility(
        self, actor: Actor, order_id: str, reason: str = "", reason_code: str | None = None
    ) -> dict[str, Any]:
        with self.db.read() as conn:
            order = self._owned_order(conn, actor, order_id)
            decision = refund_decision(
                order,
                self._product_for_order(conn, order),
                reason,
                self._active_exists(conn, "refunds", order_id),
                reason_code,
            )
            return self.ok(
                {
                    "order": _decorate_order(order),
                    "eligibility": {
                        "allowed": bool(decision.get("can_submit")),
                        "reason": decision.get("message"),
                        **decision,
                    },
                }
            )

    def create_invoice(
        self, actor: Actor, request: InvoiceCreateRequest, *, key: str | None
    ) -> dict[str, Any]:
        def operation(conn):
            subject = resolve_subject(
                conn,
                actor,
                request.requested_subject,
                permission="invoice:create_on_behalf",
            )
            order = self._owned_order(
                conn, actor, request.order_id, subject_user_id=subject
            )
            if int(order.get("version") or 1) != int(request.expected_version):
                raise BusinessDomainError(
                    409,
                    "订单已被其他操作更新，请刷新后重新确认。",
                    code="VERSION_CONFLICT",
                )
            if not int(order["paid"]) or order["status"] == "已取消":
                raise BusinessDomainError(
                    409, "该订单当前不能申请发票。", code="INVOICE_NOT_ELIGIBLE"
                )
            title = request.invoice_title.strip()
            existing = as_dict(
                conn.execute(
                    "SELECT * FROM invoices WHERE order_id=? AND tenant_id=? AND invoice_title=?",
                    (request.order_id, actor.tenant_id, title),
                ).fetchone()
            )
            if existing:
                return self.ok(existing)
            now = utcnow()
            invoice_id = _new_id("INV")
            conn.execute(
                "INSERT INTO invoices(invoice_id,order_id,user_id,subject_user_id,created_by_actor_id,tenant_id,invoice_title,amount,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    invoice_id,
                    request.order_id,
                    subject,
                    subject,
                    actor.user_id,
                    order["tenant_id"],
                    title,
                    order["amount"],
                    "待开票",
                    1,
                    now,
                    now,
                ),
            )
            row = as_dict(
                conn.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                ).fetchone()
            )
            self._audit(
                conn,
                actor,
                "invoice.apply",
                resource_type="invoice",
                resource_id=invoice_id,
                subject_user_id=subject,
                to_status="待开票",
                idempotency_key=key,
                details={"order_id": request.order_id},
            )
            return self.ok(row)

        return self._idempotent(
            actor=actor,
            command_name="invoice.apply",
            key=key,
            request_body={
                "order_id": request.order_id,
                "expected_version": request.expected_version,
                "subject_user_id": request.requested_subject,
                "invoice_title": request.invoice_title,
            },
            operation=operation,
        )

    def _create_subject_record(
        self,
        *,
        actor: Actor,
        request: ApplicationBase,
        table: str,
        id_field: str,
        prefix: str,
        command_name: str,
        initial_status: str,
        on_behalf_permission: str,
        key: str | None,
    ) -> dict[str, Any]:
        reason = str(getattr(request, "reason"))
        reason_code = str(getattr(request, "reason_code", "") or "").strip() or None

        def operation(conn):
            subject = resolve_subject(
                conn, actor, request.requested_subject, permission=on_behalf_permission
            )
            now = utcnow()
            record_id = _new_id(prefix)
            conn.execute(
                f"INSERT INTO {table}({id_field},user_id,subject_user_id,created_by_actor_id,tenant_id,reason,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    subject,
                    subject,
                    actor.user_id,
                    actor.tenant_id,
                    reason.strip(),
                    initial_status,
                    1,
                    now,
                    now,
                ),
            )
            row = as_dict(
                conn.execute(
                    f"SELECT * FROM {table} WHERE {id_field}=?", (record_id,)
                ).fetchone()
            )
            self._audit(
                conn,
                actor,
                command_name,
                resource_type="complaint" if table == "complaints" else "handoff",
                resource_id=record_id,
                subject_user_id=subject,
                to_status=initial_status,
                idempotency_key=key,
            )
            return self.ok(row)

        return self._idempotent(
            actor=actor,
            command_name=command_name,
            key=key,
            request_body={
                "subject_user_id": request.requested_subject,
                "reason": reason,
                "reason_code": reason_code,
            },
            operation=operation,
        )

    def create_complaint(
        self, actor: Actor, request: ComplaintCreateRequest, *, key: str | None
    ) -> dict[str, Any]:
        return self._create_subject_record(
            actor=actor,
            request=request,
            table="complaints",
            id_field="complaint_id",
            prefix="CP",
            command_name="complaint.create",
            initial_status="待处理",
            on_behalf_permission="complaint:create_on_behalf",
            key=key,
        )

    def create_handoff(
        self, actor: Actor, request: HumanHandoffCreateRequest, *, key: str | None
    ) -> dict[str, Any]:
        # Normal user self-service avoids duplicate live handoffs; on-behalf
        # creation is explicit and preserves actor/subject separately.
        def operation(conn):
            subject = resolve_subject(
                conn,
                actor,
                request.requested_subject,
                permission="support:handoff:create_on_behalf",
            )
            active = as_dict(
                conn.execute(
                    "SELECT * FROM human_handoffs WHERE subject_user_id=? AND tenant_id=? AND status IN ('排队中','已受理','处理中') ORDER BY created_at DESC LIMIT 1",
                    (subject, actor.tenant_id),
                ).fetchone()
            )
            if active:
                return self.ok(active)
            now = utcnow()
            handoff_id = _new_id("HUM")
            conn.execute(
                "INSERT INTO human_handoffs(handoff_id,user_id,subject_user_id,created_by_actor_id,tenant_id,reason,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    handoff_id,
                    subject,
                    subject,
                    actor.user_id,
                    actor.tenant_id,
                    request.reason.strip(),
                    "排队中",
                    1,
                    now,
                    now,
                ),
            )
            row = as_dict(
                conn.execute(
                    "SELECT * FROM human_handoffs WHERE handoff_id=?", (handoff_id,)
                ).fetchone()
            )
            self._audit(
                conn,
                actor,
                "handoff.create",
                resource_type="handoff",
                resource_id=handoff_id,
                subject_user_id=subject,
                to_status="排队中",
                idempotency_key=key,
            )
            return self.ok(row)

        return self._idempotent(
            actor=actor,
            command_name="handoff.create",
            key=key,
            request_body={
                "subject_user_id": request.requested_subject,
                "reason": request.reason,
            },
            operation=operation,
        )
