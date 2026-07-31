"""Generic resource query and reviewed command application slice."""
from __future__ import annotations

from typing import Any, Literal

from ..api_models import CommandRequest
from ..database import as_dict, json_dump, rows_as_dicts, utcnow
from ..domain import (RESOURCE_SPECS, available_commands_for_row, load_resource_scope, require_command_permission, resource_status_options, resolve_subject, tenant_matches, transition_resource, is_platform_admin)
from ..security import Actor
from .common import BusinessDomainError, _actor_can_read_any, _visible_subject


class ResourceCommandMixin:
    def list_resources(
        self,
        actor: Actor,
        resource_type: Literal[
            "refund", "after_sales", "invoice", "complaint", "handoff"
        ],
        *,
        order_id: str | None = None,
        resource_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        spec = RESOURCE_SPECS[resource_type]
        subject = _visible_subject(actor, user_id)
        with self.db.read() as conn:
            sql = f"SELECT * FROM {spec.table} WHERE 1=1"
            params: list[Any] = []
            if subject:
                sql += " AND subject_user_id=?"
                params.append(subject)
            if not is_platform_admin(actor):
                sql += " AND tenant_id=?"
                params.append(actor.tenant_id)
            if order_id and spec.order_field:
                sql += f" AND {spec.order_field}=?"
                params.append(order_id)
            if resource_id:
                sql += f" AND {spec.id_field}=?"
                params.append(resource_id)
            if status:
                sql += " AND status=?"
                params.append(status)
            rows = rows_as_dicts(
                conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()
            )
            projected: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["version"] = int(item.get("version") or 1)
                item["available_commands"] = available_commands_for_row(
                    actor, resource_type, item
                )
                projected.append(item)
            return self.ok(
                projected,
                meta={"status_options": resource_status_options(resource_type)},
            )

    def command_resource(
        self,
        actor: Actor,
        resource_type: str,
        resource_id: str,
        request: CommandRequest,
        *,
        key: str | None,
    ) -> dict[str, Any]:
        command = request.command.strip().lower()
        if resource_type not in RESOURCE_SPECS:
            raise BusinessDomainError(404, "业务资源类型不存在。")

        def operation(conn):
            before, row = transition_resource(
                conn,
                actor=actor,
                resource_type=resource_type,
                resource_id=resource_id,
                command=command,
                expected_version=request.expected_version,
                note=request.note,
            )
            self._audit(
                conn,
                actor,
                f"{resource_type}.{command}",
                resource_type=resource_type,
                resource_id=resource_id,
                subject_user_id=before.subject_user_id,
                command_name=command,
                from_status=before.status,
                to_status=str(row.get("status")),
                idempotency_key=key,
                details={"note": request.note},
            )
            row["version"] = int(row.get("version") or 1)
            row["available_commands"] = available_commands_for_row(
                actor, resource_type, row
            )
            return self.ok(row)

        return self._idempotent(
            actor=actor,
            command_name=f"{resource_type}.{command}",
            key=key,
            request_body={
                "resource_id": resource_id,
                "command": command,
                "expected_version": request.expected_version,
                "note": request.note,
            },
            operation=operation,
        )

    def legacy_update_rejected(self) -> None:
        raise BusinessDomainError(
            410,
            "通用状态编辑接口已废弃。请改用资源 commands 接口；调用方不能直接写 status、reviewed_by 或 reviewed_at。",
            code="LEGACY_STATUS_PATCH_REMOVED",
        )
