from __future__ import annotations

from pathlib import Path

import pytest

from business_service.database import BusinessDatabase
from business_service.domain import DomainError
from business_service.main import (
    BusinessService,
    ComplaintCreateRequest,
    CommandRequest,
    RefundCreateRequest,
    seed_demo_data,
)
from business_service.security import Actor


def build_service(tmp_path: Path) -> BusinessService:
    db = BusinessDatabase(tmp_path / "business.db")
    db.initialize()
    seed_demo_data(db)
    return BusinessService(db)


def customer(user_id: str = "u001", tenant: str = "default") -> Actor:
    return Actor(user_id, "customer", tenant, user_id, frozenset())




def order_version(service: BusinessService, actor: Actor, order_id: str) -> int:
    return int(service.get_order(actor, order_id)["data"]["version"])

def operator(user_id: str = "operator001", tenant: str = "default") -> Actor:
    return Actor(
        user_id,
        "operator",
        tenant,
        user_id,
        frozenset({
            "business:read_any", "refund:review", "complaint:review",
            "complaint:create_on_behalf",
        }),
    )


def test_refund_command_is_tenant_scoped_versioned_and_server_owned(tmp_path: Path):
    service = build_service(tmp_path)
    created = service.create_refund(
        customer(), RefundCreateRequest(order_id="10002", expected_version=order_version(service, customer(), "10002"), reason="键盘损坏"), key="refund-1"
    )["data"]
    assert created["status"] == "待审核"
    approved = service.command_resource(
        operator(),
        "refund",
        created["refund_id"],
        CommandRequest(command="approve", expected_version=1, note="通过"),
        key="approve-1",
    )["data"]
    assert approved["status"] == "已通过"
    assert approved["reviewed_by"] == "operator001"
    assert approved["reviewed_by_actor_id"] == "operator001"
    with pytest.raises(DomainError) as invalid:
        service.command_resource(
            operator(),
            "refund",
            created["refund_id"],
            CommandRequest(command="reject", expected_version=2, note="倒流"),
            key="reject-1",
        )
    assert invalid.value.status_code == 409


def test_cross_tenant_resource_command_is_hidden(tmp_path: Path):
    service = build_service(tmp_path)
    # Seed data already includes tenant-B customer u003 and order 30001.
    # Make the demo order eligible for a refund application without creating
    # cross-tenant fixture data outside the service's own schema.
    with service.db.transaction() as conn:
        conn.execute(
            "UPDATE orders SET status='已签收', signed_at='2026-06-20T00:00:00+00:00', updated_at='2026-06-20T00:00:00+00:00' WHERE order_id='30001'"
        )
    created = service.create_refund(
        customer("u003", "tenant-B"), RefundCreateRequest(order_id="30001", expected_version=order_version(service, customer("u003", "tenant-B"), "30001"), reason="质量问题"), key="b-refund"
    )["data"]
    with pytest.raises(DomainError) as denied:
        service.command_resource(
            operator(),
            "refund",
            created["refund_id"],
            CommandRequest(command="approve", expected_version=1),
            key="bad-cross-tenant",
        )
    assert denied.value.status_code == 404


def test_actor_subject_separation_and_tenant_scoped_idempotency(tmp_path: Path):
    service = build_service(tmp_path)
    complaint = service.create_complaint(
        operator(),
        ComplaintCreateRequest(subject_user_id="u002", reason="客服代客创建"),
        key="on-behalf",
    )["data"]
    assert complaint["created_by_actor_id"] == "operator001"
    assert complaint["subject_user_id"] == "u002"

    a = Actor("same", "customer", "tenant-A", "same", frozenset())
    b = Actor("same", "customer", "tenant-B", "same", frozenset())
    a_result = service._idempotent(
        actor=a, command_name="test.command", key="same", request_body={"x": 1},
        operation=lambda _conn: {"success": True, "data": {"tenant": "A"}},
    )
    b_result = service._idempotent(
        actor=b, command_name="test.command", key="same", request_body={"x": 1},
        operation=lambda _conn: {"success": True, "data": {"tenant": "B"}},
    )
    assert a_result["data"]["tenant"] == "A"
    assert b_result["data"]["tenant"] == "B"


def test_policy_and_query_filters_are_service_owned(tmp_path: Path):
    service = build_service(tmp_path)
    actor = customer()
    decision = service.refund_eligibility(actor, "10004", reason="不喜欢")["data"]["eligibility"]
    assert decision["can_submit"] is False
    with pytest.raises(DomainError):
        service.create_refund(actor, RefundCreateRequest(order_id="10004", expected_version=order_version(service, actor, "10004"), reason="不喜欢"), key="custom-denied")

    with service.db.transaction() as conn:
        conn.execute("UPDATE orders SET created_at='2025-01-01T00:00:00+00:00' WHERE order_id='10001'")
        conn.execute("UPDATE orders SET created_at='2026-06-01T00:00:00+00:00' WHERE order_id='10002'")
    from business_service.main import OrderQueryRequest
    rows = service.query_orders(actor, OrderQueryRequest(filters={"time_range": {"start": "2026-01-01T00:00:00+00:00", "end": "2027-01-01T00:00:00+00:00"}}))["data"]
    assert "10001" not in {row["order_id"] for row in rows}


def test_resource_query_projects_authoritative_commands_and_lifecycle_statuses(tmp_path: Path):
    service = build_service(tmp_path)
    created = service.create_refund(
        customer(), RefundCreateRequest(order_id="10002", expected_version=order_version(service, customer(), "10002"), reason="键盘损坏"), key="refund-capability"
    )["data"]

    queued = service.list_resources(operator(), "refund")
    record = next(row for row in queued["data"] if row["refund_id"] == created["refund_id"])
    assert record["available_commands"] == ["approve", "reject", "cancel"]
    assert queued["meta"]["status_options"] == ["待审核", "已通过", "已拒绝", "已关闭", "处理中", "已完成", "已失败"]

    approved = service.command_resource(
        operator(),
        "refund",
        created["refund_id"],
        CommandRequest(command="approve", expected_version=1, note="通过"),
        key="refund-capability-approve",
    )["data"]
    assert approved["status"] == "已通过"
    assert approved["available_commands"] == ["start_processing", "cancel"]


def test_developer_is_not_business_operator_or_implicit_admin(tmp_path: Path):
    service = build_service(tmp_path)
    created = service.create_refund(
        customer(), RefundCreateRequest(order_id="10002", expected_version=order_version(service, customer(), "10002"), reason="键盘损坏"), key="refund-developer"
    )["data"]
    developer = Actor("developer001", "developer", "default", "developer001", frozenset({"debug:read", "trace:read"}))

    # It can neither see cross-user operations records nor execute a review command.
    assert service.list_resources(developer, "refund")["data"] == []
    with pytest.raises(DomainError) as denied:
        service.command_resource(
            developer,
            "refund",
            created["refund_id"],
            CommandRequest(command="approve", expected_version=1),
            key="developer-approve",
        )
    assert denied.value.status_code == 403
    assert denied.value.code == "COMMAND_FORBIDDEN"


def test_operation_preview_centralizes_logistics_refund_conflicts_and_reason_choices(tmp_path: Path):
    from business_service.main import OperationPreviewRequest

    service = build_service(tmp_path)
    actor = customer()

    # Order 10003 is not signed. The business service—not the Agent—owns the
    # blocker and returns the legal next action.
    unsigned = service.operation_preview(
        actor,
        OperationPreviewRequest(
            resource_type="order",
            resource_id="10003",
            operation="APPLY_REFUND",
            input={"reason": "不想要了"},
        ),
    )["data"]
    assert unsigned["decision"] == "BLOCKED"
    assert unsigned["blockers"][0]["code"] == "ORDER_NOT_SIGNED"
    assert any(item["operation"] == "CANCEL_ORDER" and item["allowed"] for item in unsigned["alternatives"])

    shipped_cancel = service.operation_preview(
        actor,
        OperationPreviewRequest(
            resource_type="order",
            resource_id="10001",
            operation="CANCEL_ORDER",
            input={},
        ),
    )["data"]
    assert shipped_cancel["decision"] == "BLOCKED"
    assert shipped_cancel["blockers"][0]["code"] == "ORDER_CANCEL_NOT_ALLOWED"

    # A custom cup does not rely on Agent keyword classification. The service
    # asks for a user-confirmed business reason code, then decides review.
    needs_reason_code = service.operation_preview(
        actor,
        OperationPreviewRequest(
            resource_type="order",
            resource_id="10004",
            operation="APPLY_REFUND",
            input={"reason": "装水太少，尺寸不符合预期"},
        ),
    )["data"]
    assert needs_reason_code["decision"] == "NEEDS_INPUT"
    options = needs_reason_code["required_inputs"][0]["options"]
    assert {item["value"] for item in options} >= {"SPEC_MISMATCH", "QUALITY_ISSUE"}

    reviewed = service.operation_preview(
        actor,
        OperationPreviewRequest(
            resource_type="order",
            resource_id="10004",
            operation="APPLY_REFUND",
            input={"reason": "装水太少，尺寸不符合预期", "reason_code": "SPEC_MISMATCH"},
        ),
    )["data"]
    assert reviewed["decision"] == "NEEDS_REVIEW"
    assert reviewed["snapshot"]["product_name"] == "定制马克杯"


def test_preview_version_is_rechecked_when_refund_command_executes(tmp_path: Path):
    """A preview is advisory; the write must still reject a stale snapshot."""
    from business_service.main import OperationPreviewRequest

    service = build_service(tmp_path)
    actor = customer()
    preview = service.operation_preview(
        actor,
        OperationPreviewRequest(
            resource_type="order",
            resource_id="10002",
            operation="APPLY_REFUND",
            input={"reason": "键盘损坏", "reason_code": "QUALITY_ISSUE"},
        ),
    )["data"]
    assert preview["decision"] == "ALLOWED"
    preview_version = int(preview["snapshot"]["version"])

    # Simulate a legitimate competing update between preview/confirmation and
    # command submission.  The final command must not silently use the stale
    # preview, even though the order remains otherwise refund-eligible.
    with service.db.transaction() as conn:
        conn.execute(
            "UPDATE orders SET version=version+1 WHERE order_id='10002' AND tenant_id='default'"
        )

    with pytest.raises(DomainError) as stale:
        service.create_refund(
            actor,
            RefundCreateRequest(
                order_id="10002",
                expected_version=preview_version,
                reason="键盘损坏",
                reason_code="QUALITY_ISSUE",
            ),
            key="refund-stale-preview",
        )
    assert stale.value.status_code == 409
    assert stale.value.code == "VERSION_CONFLICT"


def test_every_declared_write_preview_operation_is_implemented_by_business_service(tmp_path: Path):
    """Cross-service contract: no write Action may point at an unknown preview."""
    from business_service.main import OperationPreviewRequest

    service = build_service(tmp_path)
    actor = customer()

    # Create one real refund record only for testing its CANCEL_REFUND preview.
    refund = service.create_refund(
        actor,
        RefundCreateRequest(
            order_id="10002",
            expected_version=order_version(service, actor, "10002"),
            reason="键盘损坏",
            reason_code="QUALITY_ISSUE",
        ),
        key="preview-contract-refund",
    )["data"]

    cases = [
        ("order", "10003", "CANCEL_ORDER", {"reason": "不想要了"}),
        ("order", "10003", "CHANGE_ADDRESS", {"address": "Phoenix 新地址 100 号"}),
        ("order", "10003", "URGE_DELIVERY", {}),
        ("order", "10002", "APPLY_REFUND", {"reason": "键盘损坏", "reason_code": "QUALITY_ISSUE"}),
        ("order", "10002", "APPLY_AFTER_SALES", {"reason": "键盘损坏", "reason_code": "QUALITY_ISSUE"}),
        ("order", "10002", "APPLY_INVOICE", {"invoice_title": "张三"}),
        ("refund", refund["refund_id"], "CANCEL_REFUND", {}),
        (None, None, "CREATE_COMPLAINT", {"reason": "需要客服协助处理。"}),
        (None, None, "CREATE_HUMAN_HANDOFF", {"reason": "需要人工继续处理。"}),
    ]
    for resource_type, resource_id, operation, values in cases:
        result = service.operation_preview(
            actor,
            OperationPreviewRequest(
                resource_type=resource_type,
                resource_id=resource_id,
                operation=operation,
                input=values,
            ),
        )["data"]
        assert result["decision"] in {"ALLOWED", "NEEDS_INPUT", "NEEDS_REVIEW", "BLOCKED"}
        assert all(item.get("code") != "UNKNOWN_OPERATION" for item in result.get("blockers") or [])


def test_agent_draft_previews_accept_empty_form_input_for_every_structured_order_action(tmp_path: Path):
    """The Agent creates the safe Draft before the web form supplies fields.

    A happy-path preview with already populated values cannot protect this
    integration boundary: the first real Agent call intentionally sends an
    empty input object and expects NEEDS_INPUT metadata for the UI.
    """
    from business_service.main import OperationPreviewRequest

    service = build_service(tmp_path)
    actor = customer()
    cases = [
        ("10002", "APPLY_REFUND", {"reason_code", "reason"}),
        ("10002", "APPLY_AFTER_SALES", {"reason_code", "reason"}),
        ("10002", "APPLY_INVOICE", {"invoice_title"}),
        ("10003", "CANCEL_ORDER", {"reason"}),
    ]

    for order_id, operation, expected_fields in cases:
        result = service.operation_preview(
            actor,
            OperationPreviewRequest(
                resource_type="order",
                resource_id=order_id,
                operation=operation,
                input={},
            ),
        )["data"]

        assert result["decision"] == "NEEDS_INPUT"
        assert {str(item.get("name") or "") for item in result["required_inputs"]} == expected_fields


def test_cancel_order_preview_and_command_share_the_declared_reason_contract(tmp_path: Path):
    """Protect the structured form path used by the HTTP integration smoke."""
    from business_service.main import OperationCommandRequest, OperationPreviewRequest

    service = build_service(tmp_path)
    actor = customer()
    needs_input = service.operation_preview(
        actor,
        OperationPreviewRequest(resource_type="order", resource_id="10003", operation="CANCEL_ORDER", input={}),
    )["data"]
    assert needs_input["decision"] == "NEEDS_INPUT"
    assert needs_input["required_inputs"][0]["name"] == "reason"

    version = int(needs_input["snapshot"]["version"])
    command = OperationCommandRequest(
        contract="business.operation.command@1",
        action_id="cancel_order",
        operation="CANCEL_ORDER",
        target={"resource_type": "order", "resource_id": "10003"},
        input={"expected_version": version, "reason": "not_needed", "subject_user_id": "u001"},
        actor_scope={"tenant_id": "default", "user_id": "u001", "subject": "u001"},
    )
    result = service.execute_operation_command(actor, command, key="cancel-order-structured")
    assert result["data"]["status"] == "已取消"


def test_stable_operation_command_port_reuses_authoritative_domain_paths(tmp_path: Path):
    from business_service.main import OperationCommandRequest

    service = build_service(tmp_path)
    actor = customer()
    version = order_version(service, actor, "10002")
    request = OperationCommandRequest(
        contract="business.operation.command@1",
        action_id="create_refund",
        operation="APPLY_REFUND",
        target={"resource_type": "order", "resource_id": "10002"},
        input={
            "expected_version": version,
            "reason": "键盘损坏",
            "reason_code": "QUALITY_ISSUE",
            "subject_user_id": "u001",
        },
        actor_scope={"tenant_id": "default", "user_id": "u001", "subject": "u001"},
    )
    created = service.execute_operation_command(actor, request, key="operation-port-1")["data"]
    assert created["status"] == "待审核"

    # The same frozen envelope uses the business service's existing idempotency
    # implementation; it must replay rather than duplicate a refund.
    replay = service.execute_operation_command(actor, request, key="operation-port-1")
    assert replay["idempotent"] is True
    assert replay["data"]["refund_id"] == created["refund_id"]

    bad_scope = request.model_copy(update={"actor_scope": {"tenant_id": "default", "user_id": "u002"}})
    with pytest.raises(DomainError) as rejected:
        service.execute_operation_command(actor, bad_scope, key="operation-port-bad-scope")
    assert rejected.value.code == "INVALID_OPERATION_COMMAND"


def test_parameterized_logistics_query_is_server_side_and_scope_bound(tmp_path: Path):
    from business_service.main import LogisticsQueryRequest

    service = build_service(tmp_path)
    actor = customer()
    result = service.query_logistics(
        actor,
        LogisticsQueryRequest(
            scope={"type": "selected_order_ids", "order_ids": ["10001", "10003"]},
            filters={"delivery_status": "运输中"},
        ),
    )
    assert [row["order_id"] for row in result["data"]] == ["10001"]
    assert result["summary"] == {
        "source_population_count": 2,
        "matched_population_count": 1,
        "applied_filters": {"delivery_status": "运输中"},
    }
    # This proves the condition is evaluated by the business service's join,
    # not by an Agent-side all-order response.
    assert result["data"][0]["delivery_status"] == "运输中"


def test_logistics_dispatched_phase_filter_is_server_side_and_scope_bound(tmp_path: Path):
    from business_service.main import LogisticsQueryRequest

    service = build_service(tmp_path)
    result = service.query_logistics(
        customer(),
        LogisticsQueryRequest(
            scope={"type": "selected_order_ids", "order_ids": ["10001", "10003"]},
            filters={"dispatched": True},
        ),
    )

    assert [row["order_id"] for row in result["data"]] == ["10001"]
    assert result["summary"] == {
        "source_population_count": 2,
        "matched_population_count": 1,
        "applied_filters": {"dispatched": True},
    }


def test_resource_order_filter_uses_declared_domain_field_not_sqlite_schema_introspection() -> None:
    from contextlib import contextmanager

    from business_service.application.service import BusinessService
    from business_service.security import Actor

    class Result:
        def fetchall(self):
            return []

    class Connection:
        statements: list[tuple[str, tuple]] = []

        def execute(self, sql, params=()):
            normalized = str(sql).lower()
            assert "pragma" not in normalized
            self.statements.append((str(sql), tuple(params)))
            return Result()

    class Database:
        connection = Connection()

        @contextmanager
        def read(self):
            yield self.connection

    actor = Actor(
        user_id="u001",
        role="customer",
        tenant_id="tenant-a",
        account_id="u001",
        permissions=frozenset(),
    )
    database = Database()
    result = BusinessService(database).list_resources(
        actor,
        "refund",
        order_id="order-1",
    )
    assert result["success"] is True
    sql, params = database.connection.statements[-1]
    assert "AND order_id=?" in sql
    assert params == ("u001", "tenant-a", "order-1")
