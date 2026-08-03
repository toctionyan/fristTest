from __future__ import annotations

import pytest

from business_service.application.operation_commands import (
    OperationCommandError,
    normalize_operation_command,
    verify_actor_scope,
)
from business_service.security import Actor


def _command(*, actor="operator-1", tenant="tenant-a", subject="customer-1", resource="10002", version=3):
    return normalize_operation_command({
        "contract": "business.operation.command@1",
        "action_id": "create_refund",
        "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": resource},
        "input": {"reason": "broken", "subject_user_id": subject, "expected_version": version},
        "actor_scope": {"actor_user_id": actor, "tenant_id": tenant},
        "subject_scope": {"subject_user_id": subject, "tenant_id": tenant},
        "resource_scope": {"resource_type": "order", "resource_id": resource, "expected_version": version},
    })


def test_actor_subject_and_resource_scopes_are_independent_and_consistent():
    command = _command()
    verify_actor_scope(command, user_id="operator-1", tenant_id="tenant-a")
    assert command.actor_scope["actor_user_id"] == "operator-1"
    assert command.subject_scope["subject_user_id"] == "customer-1"
    assert command.resource_scope["resource_id"] == "10002"


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda row: row["actor_scope"].update(actor_user_id="attacker"), "已认证用户"),
        (lambda row: row["actor_scope"].update(tenant_id="tenant-b"), "已认证租户"),
        (lambda row: row["subject_scope"].update(subject_user_id="other"), "业务主体"),
        (lambda row: row["resource_scope"].update(resource_id="99999"), "目标资源"),
        (lambda row: row["resource_scope"].update(expected_version=99), "资源版本"),
    ],
)
def test_scope_tampering_is_rejected(mutator, message):
    raw = {
        "contract": "business.operation.command@1",
        "action_id": "create_refund",
        "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "broken", "subject_user_id": "customer-1", "expected_version": 3},
        "actor_scope": {"actor_user_id": "operator-1", "tenant_id": "tenant-a"},
        "subject_scope": {"subject_user_id": "customer-1", "tenant_id": "tenant-a"},
        "resource_scope": {"resource_type": "order", "resource_id": "10002", "expected_version": 3},
    }
    mutator(raw)
    command = normalize_operation_command(raw)
    with pytest.raises(OperationCommandError, match=message):
        verify_actor_scope(command, user_id="operator-1", tenant_id="tenant-a")


def test_legacy_user_id_assertion_is_accepted_but_cannot_override_actor():
    command = normalize_operation_command({
        "contract": "business.operation.command@1",
        "action_id": "create_refund", "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "broken", "subject_user_id": "customer-1", "expected_version": 1},
        "actor_scope": {"user_id": "operator-1", "tenant_id": "tenant-a", "subject": "customer-1"},
    })
    verify_actor_scope(command, user_id="operator-1", tenant_id="tenant-a")
    bad = normalize_operation_command({
        "contract": "business.operation.command@1",
        "action_id": "create_refund", "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "broken", "subject_user_id": "customer-1", "expected_version": 1},
        "actor_scope": {"user_id": "customer-1", "tenant_id": "tenant-a", "subject": "customer-1"},
    })
    with pytest.raises(OperationCommandError):
        verify_actor_scope(bad, user_id="operator-1", tenant_id="tenant-a")


def test_api_model_preserves_subject_and_resource_scopes_before_verification():
    from business_service.api_models import OperationCommandRequest

    raw = {
        "contract": "business.operation.command@1",
        "action_id": "create_refund",
        "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "broken", "subject_user_id": "u001", "expected_version": 3},
        "actor_scope": {"actor_user_id": "u001", "actor_role": "customer", "tenant_id": "default"},
        "subject_scope": {"subject_user_id": "u001", "tenant_id": "default"},
        "resource_scope": {"resource_type": "order", "resource_id": "10002", "expected_version": 3, "subject_user_id": "u001"},
    }
    dumped = OperationCommandRequest(**raw).model_dump()
    assert dumped["subject_scope"] == raw["subject_scope"]
    assert dumped["resource_scope"] == raw["resource_scope"]


def test_actor_role_and_resource_subject_tampering_are_rejected():
    raw = {
        "contract": "business.operation.command@1",
        "action_id": "create_refund", "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "broken", "subject_user_id": "customer-1", "expected_version": 3},
        "actor_scope": {"actor_user_id": "operator-1", "actor_role": "admin", "tenant_id": "tenant-a"},
        "subject_scope": {"subject_user_id": "customer-1", "tenant_id": "tenant-a"},
        "resource_scope": {"resource_type": "order", "resource_id": "10002", "expected_version": 3, "subject_user_id": "other"},
    }
    with pytest.raises(OperationCommandError, match="角色"):
        verify_actor_scope(normalize_operation_command(raw), user_id="operator-1", tenant_id="tenant-a", role="operator")
    raw["actor_scope"]["actor_role"] = "operator"
    with pytest.raises(OperationCommandError, match="资源主体"):
        verify_actor_scope(normalize_operation_command(raw), user_id="operator-1", tenant_id="tenant-a", role="operator")


def test_real_command_port_accepts_owned_subject_and_rejects_consistent_but_unauthorized_subject(tmp_path):
    from business_service.api_models import OperationCommandRequest
    from business_service.database import BusinessDatabase
    from business_service.domain import DomainError
    from business_service.main import BusinessService, seed_demo_data

    db = BusinessDatabase(tmp_path / "stage4-business.db")
    db.initialize()
    seed_demo_data(db)
    service = BusinessService(db)
    actor = Actor("u001", "customer", "default", "u001", frozenset())
    version = int(service.get_order(actor, "10002")["data"]["version"])

    def request_for(subject: str) -> OperationCommandRequest:
        return OperationCommandRequest(**{
            "contract": "business.operation.command@1",
            "action_id": "create_refund",
            "operation": "APPLY_REFUND",
            "target": {"resource_type": "order", "resource_id": "10002"},
            "input": {"reason": "质量问题", "subject_user_id": subject, "expected_version": version},
            "actor_scope": {"actor_user_id": "u001", "actor_role": "customer", "tenant_id": "default"},
            "subject_scope": {"subject_user_id": subject, "tenant_id": "default"},
            "resource_scope": {
                "resource_type": "order", "resource_id": "10002",
                "expected_version": version, "subject_user_id": subject,
            },
        })

    created = service.execute_operation_command(actor, request_for("u001"), key="stage4-owned")["data"]
    assert created["subject_user_id"] == "u001"
    assert created["created_by_actor_id"] == "u001"

    # A coherent envelope is still only an assertion. The domain authority
    # rejects a customer attempting to act on another subject's behalf.
    with pytest.raises(DomainError) as denied:
        service.execute_operation_command(actor, request_for("u002"), key="stage4-on-behalf-denied")
    assert denied.value.status_code == 403
    assert denied.value.code == "ON_BEHALF_DENIED"
