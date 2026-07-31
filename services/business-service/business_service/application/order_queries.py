"""Order, logistics and catalog read application slice."""
from __future__ import annotations

from typing import Any

from ..api_models import LogisticsQueryRequest, OrderQueryRequest
from ..database import as_dict, rows_as_dicts
from ..domain import RESOURCE_SPECS, is_platform_admin, refund_decision, after_sales_decision
from ..security import Actor
from .common import (BusinessDomainError, _actor_can_read_any, _decorate_order, _decorate_orders, _row_order, _visible_subject)


class OrderQueryMixin:
    def list_orders(
        self, actor: Actor, requested_user_id: str | None = None
    ) -> dict[str, Any]:
        subject = _visible_subject(actor, requested_user_id)
        with self.db.read() as conn:
            sql = "SELECT * FROM orders WHERE 1=1"
            params: list[Any] = []
            if subject:
                sql += " AND user_id=?"
                params.append(subject)
            if not is_platform_admin(actor):
                sql += " AND tenant_id=?"
                params.append(actor.tenant_id)
            rows = rows_as_dicts(
                conn.execute(
                    sql + " ORDER BY created_at DESC,order_id DESC", params
                ).fetchall()
            )
            return self.ok(_decorate_orders(rows))

    def _available_actions(
        self, conn, actor: Actor, order: dict[str, Any], reason: str = ""
    ) -> dict[str, Any]:
        product = self._product_for_order(conn, order)
        refund = refund_decision(
            order,
            product,
            reason,
            self._active_exists(conn, "refunds", str(order["order_id"])),
        )
        after = after_sales_decision(
            order,
            product,
            reason,
            self._active_exists(conn, "after_sales_tickets", str(order["order_id"])),
        )
        invoice = {
            "can_submit": bool(order.get("paid"))
            and str(order.get("status")) != "已取消",
            "message": "订单已支付，可以申请发票。",
        }
        actions: list[str] = []
        if refund.get("can_submit"):
            actions.append("APPLY_REFUND")
        if after.get("can_submit"):
            actions.append("APPLY_AFTER_SALES")
        if invoice.get("can_submit"):
            actions.append("APPLY_INVOICE")
        if str(order.get("status")) in {"待发货", "已付款"}:
            actions.extend(["CANCEL_ORDER", "CHANGE_ADDRESS", "URGE_DELIVERY"])
        return {
            "available_actions": actions,
            "refund": refund,
            "after_sales": after,
            "invoice": invoice,
            "policy_version": "business-policy-v6.6",
        }

    def get_order(
        self, actor: Actor, order_id: str, requested_user_id: str | None = None
    ) -> dict[str, Any]:
        subject = _visible_subject(actor, requested_user_id)
        with self.db.read() as conn:
            order = self._owned_order(conn, actor, order_id, subject_user_id=subject)
            result = _decorate_order(order) or {}
            result["operation_availability"] = self._available_actions(
                conn, actor, order
            )
            return self.ok(result)

    def order_available_actions(
        self, actor: Actor, order_id: str, reason: str = ""
    ) -> dict[str, Any]:
        with self.db.read() as conn:
            order = self._owned_order(conn, actor, order_id)
            return self.ok(
                {
                    "order_id": order_id,
                    **self._available_actions(conn, actor, order, reason),
                }
            )

    def query_orders(self, actor: Actor, request: OrderQueryRequest) -> dict[str, Any]:
        subject = _visible_subject(actor, request.user_id)
        filters = dict(request.filters or {})
        scope = dict(request.scope or {})
        sql = "SELECT * FROM orders WHERE 1=1"
        total_sql = "SELECT COUNT(*) AS cnt FROM orders WHERE 1=1"
        params: list[Any] = []
        total_params: list[Any] = []
        if subject:
            sql += " AND user_id=?"
            params.append(subject)
            total_sql += " AND user_id=?"
            total_params.append(subject)
        if not is_platform_admin(actor):
            sql += " AND tenant_id=?"
            params.append(actor.tenant_id)
            total_sql += " AND tenant_id=?"
            total_params.append(actor.tenant_id)
        selected = [str(v) for v in (scope.get("order_ids") or []) if str(v).strip()]
        if scope.get("type") == "selected_order_ids":
            if not selected:
                return self.ok([], matched=[], summary={"scope_total": 0, "matched": 0})
            clause = " AND order_id IN (" + ",".join("?" for _ in selected) + ")"
            sql += clause
            params.extend(selected)
        keyword = filters.get("product_keyword")
        if keyword:
            sql += " AND lower(product_name) LIKE ?"
            params.append(f"%{str(keyword).lower()}%")
        statuses = filters.get("status") or filters.get("order_status")
        if statuses:
            values = statuses if isinstance(statuses, list) else [statuses]
            sql += " AND status IN (" + ",".join("?" for _ in values) + ")"
            params.extend([str(v) for v in values])
        if filters.get("paid") is not None:
            sql += " AND paid=?"
            params.append(_normalize_bool(filters["paid"]))
        if filters.get("amount_min") is not None:
            sql += " AND amount>=?"
            params.append(float(filters["amount_min"]))
        if filters.get("amount_max") is not None:
            sql += " AND amount<=?"
            params.append(float(filters["amount_max"]))
        time_range = filters.get("time_range") or {}
        if isinstance(time_range, dict):
            lower = (
                time_range.get("start")
                or time_range.get("gte")
                or time_range.get("created_at_gte")
            )
            upper = (
                time_range.get("end")
                or time_range.get("lt")
                or time_range.get("created_at_lt")
            )
            if lower:
                sql += " AND created_at>=?"
                params.append(str(lower))
            if upper:
                sql += " AND created_at<?"
                params.append(str(upper))
        if filters.get("created_at_gte"):
            sql += " AND created_at>=?"
            params.append(str(filters["created_at_gte"]))
        if filters.get("created_at_lt"):
            sql += " AND created_at<?"
            params.append(str(filters["created_at_lt"]))
        with self.db.read() as conn:
            rows = _decorate_orders(
                rows_as_dicts(
                    conn.execute(
                        sql + " ORDER BY created_at DESC,order_id DESC", params
                    ).fetchall()
                )
            )
            total = conn.execute(total_sql, total_params).fetchone()
            return self.ok(
                rows,
                matched=rows,
                summary={"scope_total": int(total["cnt"]), "matched": len(rows)},
            )

    def logistics(self, actor: Actor, order_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            self._owned_order(conn, actor, order_id)
            row = as_dict(
                conn.execute(
                    "SELECT order_id,status,latest,eta,updated_at FROM logistics WHERE order_id=?",
                    (order_id,),
                ).fetchone()
            )
            if not row:
                raise BusinessDomainError(404, "物流信息不存在。")
            return self.ok(row)

    def query_logistics(self, actor: Actor, request: LogisticsQueryRequest) -> dict[str, Any]:
        """Run a visibility-scoped logistics query with server-owned filters.

        ``delivery_status`` is intentionally a logistics fact, not an order
        status alias. ``dispatched`` is the server-owned phase predicate for
        whether a shipment has left the merchant (anything except 待发货).
        The response carries both source and matched counts so
        an Agent can prove that it rendered the requested population rather
        than the broader observation scope.
        """
        subject = _visible_subject(actor, request.user_id)
        scope = dict(request.scope or {})
        filters = dict(request.filters or {})
        allowed_filters = {"delivery_status", "dispatched"}
        unknown = sorted(set(filters) - allowed_filters)
        if unknown:
            raise BusinessDomainError(400, f"不支持的物流查询条件：{', '.join(unknown)}", code="UNSUPPORTED_LOGISTICS_FILTER")

        delivery_status = filters.get("delivery_status")
        dispatched = filters.get("dispatched") if "dispatched" in filters else None
        if delivery_status is not None and dispatched is not None:
            raise BusinessDomainError(400, "精确物流状态与是否已发出不能同时筛选。", code="LOGISTICS_FILTER_CONFLICT")
        if delivery_status is not None:
            delivery_status = str(delivery_status).strip()
            allowed_statuses = {"待发货", "运输中", "派送中", "已签收", "已取消"}
            if delivery_status not in allowed_statuses:
                raise BusinessDomainError(400, "不支持的物流状态筛选值。", code="INVALID_LOGISTICS_FILTER_VALUE")
        if dispatched is not None and not isinstance(dispatched, bool):
            raise BusinessDomainError(400, "是否已发出必须是布尔值。", code="INVALID_LOGISTICS_FILTER_VALUE")

        selected = [str(value) for value in (scope.get("order_ids") or []) if str(value).strip()]
        if scope and str(scope.get("type") or "") != "selected_order_ids":
            raise BusinessDomainError(400, "物流查询范围必须是当前用户可见订单集合。", code="INVALID_LOGISTICS_SCOPE")
        if scope.get("type") == "selected_order_ids" and not selected:
            return self.ok([], summary={"source_population_count": 0, "matched_population_count": 0, "applied_filters": {}})

        base_sql = """
            FROM orders AS o
            JOIN logistics AS l ON l.order_id = o.order_id
            WHERE 1=1
        """
        base_params: list[Any] = []
        if subject:
            base_sql += " AND o.user_id=?"
            base_params.append(subject)
        if not is_platform_admin(actor):
            base_sql += " AND o.tenant_id=?"
            base_params.append(actor.tenant_id)
        if selected:
            base_sql += " AND o.order_id IN (" + ",".join("?" for _ in selected) + ")"
            base_params.extend(selected)

        source_sql = "SELECT COUNT(*) AS cnt " + base_sql
        filtered_sql = base_sql
        filtered_params = list(base_params)
        if delivery_status:
            filtered_sql += " AND l.status=?"
            filtered_params.append(delivery_status)
        elif dispatched is True:
            filtered_sql += " AND l.status<>?"
            filtered_params.append("待发货")
        elif dispatched is False:
            filtered_sql += " AND l.status=?"
            filtered_params.append("待发货")

        select_sql = (
            "SELECT o.order_id,o.product_name,o.status AS order_status,o.amount,"
            "l.status AS delivery_status,l.latest,l.eta,l.updated_at "
            + filtered_sql
            + " ORDER BY o.created_at DESC,o.order_id DESC"
        )
        with self.db.read() as conn:
            source_row = conn.execute(source_sql, base_params).fetchone()
            rows = rows_as_dicts(conn.execute(select_sql, filtered_params).fetchall())
        return self.ok(
            rows,
            summary={
                "source_population_count": int(source_row["cnt"] if source_row else 0),
                "matched_population_count": len(rows),
                "applied_filters": (
                    {"delivery_status": delivery_status}
                    if delivery_status
                    else ({"dispatched": dispatched} if dispatched is not None else {})
                ),
            },
        )

    def products(self, actor: Actor, keyword: str | None = None) -> dict[str, Any]:
        with self.db.read() as conn:
            if keyword:
                rows = conn.execute(
                    "SELECT * FROM products WHERE lower(product_name) LIKE ? OR lower(description) LIKE ? ORDER BY product_id",
                    (f"%{keyword.lower()}%", f"%{keyword.lower()}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM products ORDER BY product_id"
                ).fetchall()
            return self.ok(rows_as_dicts(rows))

    def product(self, actor: Actor, product_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = as_dict(
                conn.execute(
                    "SELECT * FROM products WHERE product_id=?", (product_id,)
                ).fetchone()
            )
            if not row:
                raise BusinessDomainError(404, "商品不存在。")
            return self.ok(row)

    def coupons(self, actor: Actor, requested_user_id: str | None) -> dict[str, Any]:
        subject = _visible_subject(actor, requested_user_id)
        with self.db.read() as conn:
            sql = "SELECT * FROM coupons WHERE 1=1"
            params: list[Any] = []
            if subject:
                sql += " AND user_id=?"
                params.append(subject)
            return self.ok(
                rows_as_dicts(
                    conn.execute(sql + " ORDER BY coupon_id", params).fetchall()
                )
            )
