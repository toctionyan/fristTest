"""Deterministic business fixtures for lifecycle conversation regression.

The conversation catalog is intentionally independent from a running Business
Service.  This port is a small in-memory implementation of the public
``BusinessPort`` protocol, not a mock of individual Agent methods: the real
lifecycle graph still performs capability gating, target resolution, previews,
ledger updates and transaction routing against it.

Most importantly, ``execute_command`` raises.  A scripted conversation test
cannot accidentally turn a regression into a hidden business write merely
because a lifecycle path regressed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from agent_core.business.contracts import ActorContext
from agent_core.context.visible_result_refs import mark_visible_result_refs
from agent_core.ledger import artifact_entry, result_entry, view_entry


FIXTURE_ID = "customer_orders_v1"
FIXTURE_EVIDENCE_HANDLE = "result:fixture:orders"


@dataclass
class FixtureBusinessPort:
    """A recordable, read/preview-only BusinessPort for graph regressions."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    orders: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "10001": {
            "order_id": "10001",
            "product_name": "蓝牙耳机",
            "product_id": "p-earphone",
            "status": "已签收",
            "amount": 199.0,
            "version": 7,
            "created_at": "2026-01-02T10:00:00Z",
        },
        "10002": {
            "order_id": "10002",
            "product_name": "机械键盘",
            "product_id": "p-keyboard",
            "status": "待发货",
            "amount": 599.0,
            "version": 5,
            "created_at": "2026-01-03T10:00:00Z",
        },
        "10003": {
            "order_id": "10003",
            "product_name": "无线鼠标",
            "product_id": "p-mouse",
            "status": "运输中",
            "amount": 99.0,
            "version": 3,
            "created_at": "2026-01-04T10:00:00Z",
        },
    })

    def _record(self, kind: str, **data: Any) -> None:
        self.calls.append({"kind": kind, **deepcopy(data)})

    def count(self, kind: str) -> int:
        return sum(1 for call in self.calls if call.get("kind") == kind)

    def health(self) -> dict[str, Any]:
        self._record("health")
        return {"success": True, "status": "ok"}

    def read_resource(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        resource_id: str,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._record("read_resource", resource_type=resource_type, resource_id=resource_id, query=query or {})
        if resource_type == "order":
            row = self.orders.get(str(resource_id))
            return {"success": bool(row), "data": deepcopy(row) if row else None, "error": "not_found" if not row else None}
        if resource_type == "logistics":
            row = self.orders.get(str(resource_id))
            if row is None:
                return {"success": False, "error": "not_found"}
            delivery = {
                "10001": "已签收",
                "10002": "待发货",
                "10003": "运输中",
            }[str(resource_id)]
            return {
                "success": True,
                "data": {
                    "order_id": str(resource_id),
                    "delivery_status": delivery,
                    "latest": f"订单 {resource_id} 的固定物流事件",
                    "eta": None if delivery == "已签收" else "2026-01-10",
                    "updated_at": "2026-01-05T10:00:00Z",
                },
            }
        return {"success": False, "error": f"unsupported_resource:{resource_type}"}

    def query_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        self._record("query_resources", resource_type=resource_type, query_spec=query_spec)
        if resource_type == "order":
            rows = list(self.orders.values())
            status = str(query_spec.get("status") or "")
            if status:
                rows = [row for row in rows if str(row.get("status") or "") == status]
            return {"success": True, "data": deepcopy(rows)}
        if resource_type == "logistics":
            scope = query_spec.get("scope") if isinstance(query_spec.get("scope"), dict) else {}
            order_ids = [str(value) for value in scope.get("order_ids") or []]
            filters = query_spec.get("filters") if isinstance(query_spec.get("filters"), dict) else {}
            expected = str(filters.get("delivery_status") or "")
            rows: list[dict[str, Any]] = []
            for order_id in order_ids:
                payload = self.read_resource(actor, resource_type="logistics", resource_id=order_id)
                item = dict(payload.get("data") or {})
                if item and (not expected or str(item.get("delivery_status") or "") == expected):
                    rows.append(item)
            return {
                "success": True,
                "data": rows,
                "summary": {
                    "source_population_count": len(order_ids),
                    "matched_population_count": len(rows),
                    "applied_filters": deepcopy(filters),
                },
            }
        if resource_type in {"refund", "after_sales", "invoice", "product", "coupon"}:
            return {"success": True, "data": []}
        return {"success": False, "error": f"unsupported_resource:{resource_type}"}

    def query_related_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        relation: dict[str, Any],
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        self._record("query_related_resources", resource_type=resource_type, relation=relation, query_spec=query_spec)
        order_id = str(relation.get("order_id") or "")
        if resource_type == "refund":
            return {"success": True, "data": [{"refund_id": "refund:fixture:10003", "order_id": order_id or "10003", "status": "处理中", "version": 1}]}
        if resource_type == "after_sales":
            return {"success": True, "data": [{"ticket_id": "after-sales:fixture:10001", "order_id": order_id or "10001", "status": "处理中", "version": 1}]}
        if resource_type == "invoice":
            return {"success": True, "data": [{"invoice_id": "invoice:fixture:10002", "order_id": order_id or "10002", "status": "可申请", "version": 1}]}
        return {"success": False, "error": f"unsupported_relation:{resource_type}"}

    def preview_operation(
        self,
        actor: ActorContext,
        *,
        resource_type: str | None,
        resource_id: str | None,
        operation: str,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._record(
            "preview_operation",
            resource_type=resource_type,
            resource_id=resource_id,
            operation=operation,
            input_values=input_values or {},
        )
        row = self.orders.get(str(resource_id or ""))
        if resource_type != "order" or row is None:
            return {"success": False, "error": "target_not_found"}
        return {
            "success": True,
            "data": {
                "decision": "ALLOWED",
                "message": "固定夹具允许预检；仍必须通过结构化授权。",
                "snapshot": {"version": int(row.get("version") or 1)},
                "required_inputs": [],
                "policy_version": "fixture-policy@1",
            },
        }

    def execute_command(
        self,
        actor: ActorContext,
        *,
        command: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._record("execute_command", command=command, idempotency_key=idempotency_key)
        raise AssertionError("conversation regression attempted a business write")


def fixture_ledger(*, tenant_id: str, user_id: str, thread_id: str) -> list[dict[str, Any]]:
    """Create fixed, customer-visible order references for one graph thread."""
    scope = {"tenant_id": tenant_id, "user_id": user_id, "thread_id": thread_id}
    rows = list(FixtureBusinessPort().orders.values())
    artifacts = [
        artifact_entry(
            resource_type="order",
            resource_id=str(row["order_id"]),
            label=f"{row['product_name']}（订单 {row['order_id']}）",
            facts=row,
            scope=scope,
            turn=0,
            source="conversation_regression_fixture",
            freshness_version=int(row["version"]),
            handle=f"artifact:fixture:order:{row['order_id']}",
        )
        for row in rows
    ]
    handles = [str(item["handle"]) for item in artifacts]
    labels = [str(item["label"]) for item in artifacts]
    view = view_entry(
        view_type="orders",
        member_handles=handles,
        labels=labels,
        scope=scope,
        turn=0,
        source="conversation_regression_fixture",
        query={"fixture": FIXTURE_ID},
        handle="view:fixture:orders",
    )
    # Stable visible subsets make set filters, differences and ordinal
    # references executable without inventing a hidden current-target pointer.
    # They are deliberately ordinary ledger views, so the same scope and
    # VisibleResultRef checks apply as they do after a real customer response.
    views = [
        view,
        view_entry(
            view_type="orders",
            member_handles=["artifact:fixture:order:10001"],
            labels=["蓝牙耳机（订单 10001）"],
            scope=scope,
            turn=0,
            source="conversation_regression_fixture",
            query={"fixture": FIXTURE_ID, "subset": "earphone"},
            handle="view:fixture:earphone",
        ),
        view_entry(
            view_type="orders",
            member_handles=["artifact:fixture:order:10001"],
            labels=["蓝牙耳机（订单 10001）"],
            scope=scope,
            turn=0,
            source="conversation_regression_fixture",
            query={"fixture": FIXTURE_ID, "subset": "signed"},
            handle="view:fixture:signed",
        ),
        view_entry(
            view_type="orders",
            member_handles=["artifact:fixture:order:10002"],
            labels=["机械键盘（订单 10002）"],
            scope=scope,
            turn=0,
            source="conversation_regression_fixture",
            query={"fixture": FIXTURE_ID, "subset": "pending"},
            handle="view:fixture:pending",
        ),
    ]
    result = result_entry(
        capability="fixture.orders",
        member_handles=handles,
        labels=labels,
        scope=scope,
        turn=0,
        source_target={"fixture": FIXTURE_ID},
        handle=FIXTURE_EVIDENCE_HANDLE,
    )
    state = {
        "current_tenant_id": tenant_id,
        "current_user_id": user_id,
        "current_thread_id": thread_id,
        "turn_index": 0,
    }
    return mark_visible_result_refs(
        [*artifacts, *views, result],
        state=state,
        evidence_handles=[*handles, *(item["handle"] for item in views), result["handle"]],
    )


__all__ = ["FIXTURE_EVIDENCE_HANDLE", "FIXTURE_ID", "FixtureBusinessPort", "fixture_ledger"]
