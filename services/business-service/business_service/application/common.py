"""Shared domain-neutral helpers for Business Service application slices."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ..database import as_dict
from ..domain import DomainError, is_operator
from ..security import Actor


class BusinessDomainError(DomainError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _normalize_bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _decorate_order(order: dict[str, Any] | None) -> dict[str, Any] | None:
    if order is None:
        return None
    row = dict(order)
    signed_at = row.get("signed_at")
    if signed_at:
        try:
            parsed = datetime.fromisoformat(str(signed_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            row["signed_days_ago"] = max(
                0, (datetime.now(UTC).date() - parsed.astimezone(UTC).date()).days
            )
        except Exception:
            row["signed_days_ago"] = None
    else:
        row["signed_days_ago"] = None
    row["paid"] = bool(row.get("paid"))
    row["version"] = int(row.get("version") or 1)
    return row


def _decorate_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for row in rows if (item := _decorate_order(row)) is not None]


def _row_order(conn, order_id: str) -> dict[str, Any] | None:
    return as_dict(
        conn.execute(
            "SELECT * FROM orders WHERE order_id=?", (str(order_id),)
        ).fetchone()
    )


def _actor_can_read_any(actor: Actor) -> bool:
    return is_operator(actor) and actor.can("business:read_any")


def _visible_subject(actor: Actor, requested_user_id: str | None) -> str | None:
    if (
        requested_user_id
        and requested_user_id != actor.user_id
        and not _actor_can_read_any(actor)
    ):
        raise BusinessDomainError(
            403, "普通用户只能访问自己的业务记录。", code="SUBJECT_READ_DENIED"
        )
    return requested_user_id or (None if _actor_can_read_any(actor) else actor.user_id)


