from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from agent_core.storage.repositories.base import (
    ActiveDraftValidationCode,
    TransactionLifecycleRepository,
    TransactionScope,
)


_TERMINAL = {"COMMITTED", "FAILED_FINAL", "EXPIRED", "REVOKED"}


def _public_draft(row: dict[str, Any]) -> dict[str, Any]:
    projection = row.get("projection") if isinstance(row.get("projection"), dict) else {}
    return {
        "draft_id": row.get("draft_id"),
        "thread_id": row.get("thread_id"),
        "action_id": row.get("action_id"),
        "draft_state": row.get("draft_state"),
        "revision": row.get("draft_revision"),
        "target_summary": projection.get("label") or projection.get("target_label") or projection.get("target_handle"),
        "required_inputs": projection.get("required_inputs") or [],
        "updated_at": row.get("updated_at"),
        "created_at": row.get("created_at"),
        "terminal": str(row.get("draft_state") or "").upper() in _TERMINAL,
    }


def _public_attempt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": row.get("attempt_id"),
        "draft_id": row.get("draft_id"),
        "state": row.get("state"),
        "error_code": row.get("error_code"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "reconciled_at": row.get("reconciled_at"),
    }


def _public_receipt(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = row.get("business_result") if isinstance(row.get("business_result"), dict) else {}
    return {
        "receipt_id": row.get("receipt_id"),
        "draft_id": row.get("draft_id"),
        "attempt_id": row.get("attempt_id"),
        "receipt_state": row.get("receipt_state"),
        "business_resource_id": row.get("business_resource_id"),
        "created_at": row.get("created_at"),
        # The customer receives the business outcome projection, never internal
        # command payload/digest/grant values.
        "business_result": result,
    }


@dataclass
class TransactionQueryService:
    repository: TransactionLifecycleRepository

    @staticmethod
    def _scope(*, tenant_id: str | None, user_id: str, thread_id: str | None = None) -> TransactionScope:
        return TransactionScope(tenant_id=str(tenant_id or "default"), user_id=str(user_id), thread_id=thread_id or None)

    def list_for_customer(
        self,
        *,
        tenant_id: str | None,
        user_id: str,
        thread_id: str | None = None,
        limit: int = 50,
        states: set[str] | None = None,
    ) -> dict[str, Any]:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
        rows = self.repository.list_drafts_for_scope(scope=scope, states=states, limit=limit)
        items: list[dict[str, Any]] = []
        for draft in rows:
            receipt = self.repository.get_latest_receipt_for_draft(scope=scope, draft_id=str(draft.get("draft_id") or ""))
            items.append({**_public_draft(draft), "latest_receipt": _public_receipt(receipt)})
        return {"items": items, "count": len(items), "thread_id": thread_id}

    def get_for_customer(self, *, tenant_id: str | None, user_id: str, draft_id: str) -> dict[str, Any]:
        # No thread filter: users may retrieve a transaction created in another
        # conversation/device. Scope remains tenant+user.
        scope = self._scope(tenant_id=tenant_id, user_id=user_id)
        draft = self.repository.get_draft_for_scope(scope=scope, draft_id=draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        attempts = self.repository.list_attempts_for_draft(scope=scope, draft_id=draft_id)
        receipt = self.repository.get_latest_receipt_for_draft(scope=scope, draft_id=draft_id)
        return {
            "transaction": _public_draft(draft),
            "attempts": [_public_attempt(row) for row in attempts],
            "latest_receipt": _public_receipt(receipt),
        }

    def receipt_for_customer(self, *, tenant_id: str | None, user_id: str, draft_id: str) -> dict[str, Any]:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id)
        draft = self.repository.get_draft_for_scope(scope=scope, draft_id=draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        receipt = self.repository.get_latest_receipt_for_draft(scope=scope, draft_id=draft_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="transaction receipt not found")
        return {"receipt": _public_receipt(receipt)}

    def validate_active(
        self,
        *,
        tenant_id: str | None,
        user_id: str,
        thread_id: str,
        draft_id: str,
        expected_revision: int | None = None,
        allowed_states: set[str] | None = None,
    ) -> dict[str, Any] | None:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
        result = self.repository.validate_active_draft(
            scope=scope,
            draft_id=draft_id,
            expected_revision=expected_revision,
            allowed_states=allowed_states,
        )
        if result.code is ActiveDraftValidationCode.OK:
            return result.draft
        # ContextBundle treats a stale pointer as absent; public mutation routes
        # map codes to explicit HTTP responses at their boundary.
        return None
