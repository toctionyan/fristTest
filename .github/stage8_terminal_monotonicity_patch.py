from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical SQLite transaction repository: a terminal Draft identity is
# immutable.  Stale Workflow/checkpoint projections may never reopen it.
# ---------------------------------------------------------------------------
replace_once(
    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",
    "from agent_core.persistence.sqlite_base import SQLiteBase\n",
    "from agent_core.persistence.sqlite_base import SQLiteBase\nfrom agent_core.operations.draft import TERMINAL_DRAFT_STATES\n",
    label="sqlite terminal-state import",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",
    '''            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (data["draft_id"],)).fetchone()\n            if existing and int(existing["draft_revision"] or 0) > int(data["draft_revision"] or 0):\n                return self._decode_row(dict(existing)) or {}\n            data["command_envelope_json"] = _json(command_envelope) if command_envelope is not None else None\n''',
    '''            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (data["draft_id"],)).fetchone()\n            existing_payload = self._decode_row(dict(existing)) if existing else None\n            if existing_payload:\n                existing_state = str(existing_payload.get("draft_state") or "").upper()\n                existing_revision = int(existing_payload.get("draft_revision") or 0)\n                incoming_revision = int(data.get("draft_revision") or 0)\n                # A terminal Draft is an immutable transaction fact. A stale\n                # checkpoint may still carry the old pending card, but it may\n                # not overwrite the repository after commit/final failure/\n                # revocation/expiry/review closure. A newer operation must use\n                # a new Draft identity rather than resurrect this one.\n                if existing_state in TERMINAL_DRAFT_STATES or existing_revision > incoming_revision:\n                    return existing_payload\n            data["command_envelope_json"] = _json(command_envelope) if command_envelope is not None else None\n''',
    label="sqlite create_draft terminal guard",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",
    '''    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:\n        cols=["draft_state=?","updated_at=?"]; vals:[Any]=[draft_state,_now()]\n        for name,value in (("draft_revision",draft_revision),("command_digest",command_digest),("active_grant_id",active_grant_id),("current_attempt_id",current_attempt_id)):\n            if value is not None: cols.append(f"{name}=?"); vals.append(value)\n        if command_envelope is not None: cols.append("command_envelope_json=?"); vals.append(_json(command_envelope))\n        if projection is not None: cols.append("projection_json=?"); vals.append(_json(projection))\n        vals.append(draft_id); self.execute(f"UPDATE transaction_drafts SET {', '.join(cols)} WHERE draft_id=?",tuple(vals))\n''',
    '''    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:\n        with self.lock:\n            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (draft_id,)).fetchone()\n            if not existing:\n                return\n            current = self._decode_row(dict(existing)) or {}\n            current_state = str(current.get("draft_state") or "").upper()\n            if current_state in TERMINAL_DRAFT_STATES:\n                return\n            if draft_revision is not None and int(current.get("draft_revision") or 0) > int(draft_revision):\n                return\n            cols=["draft_state=?","updated_at=?"]; vals:[Any]=[draft_state,_now()]\n            for name,value in (("draft_revision",draft_revision),("command_digest",command_digest),("active_grant_id",active_grant_id),("current_attempt_id",current_attempt_id)):\n                if value is not None: cols.append(f"{name}=?"); vals.append(value)\n            if command_envelope is not None: cols.append("command_envelope_json=?"); vals.append(_json(command_envelope))\n            if projection is not None: cols.append("projection_json=?"); vals.append(_json(projection))\n            vals.append(draft_id)\n            self.conn.execute(f"UPDATE transaction_drafts SET {', '.join(cols)} WHERE draft_id=?",tuple(vals))\n            self.conn.commit()\n''',
    label="sqlite advance_draft terminal guard",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",
    '''                if existing:\n                    grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                    self.conn.commit()\n                    return {\n                        "reserved": False,\n                        "grant": self._decode_row(dict(grant) if grant else None) or {},\n                        "created": False,\n                        "attempt": self._decode_row(dict(existing)) or {},\n                    }\n\n                self.conn.execute(\n''',
    '''                if existing:\n                    grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                    self.conn.commit()\n                    return {\n                        "reserved": False,\n                        "grant": self._decode_row(dict(grant) if grant else None) or {},\n                        "created": False,\n                        "attempt": self._decode_row(dict(existing)) or {},\n                    }\n\n                canonical = self.conn.execute(\n                    "SELECT * FROM transaction_drafts WHERE draft_id=? AND tenant_id=? AND user_id=? AND thread_id=?",\n                    (draft_id, tenant_id, user_id, thread_id),\n                ).fetchone()\n                canonical_payload = self._decode_row(dict(canonical)) if canonical else None\n                if canonical_payload:\n                    canonical_state = str(canonical_payload.get("draft_state") or "").upper()\n                    snapshot_mismatch = (\n                        int(canonical_payload.get("draft_revision") or 0) != int(draft_revision)\n                        or str(canonical_payload.get("command_digest") or "") != str(command_digest or "")\n                    )\n                    if canonical_state in TERMINAL_DRAFT_STATES or snapshot_mismatch:\n                        reason = "draft_terminal" if canonical_state in TERMINAL_DRAFT_STATES else "draft_snapshot_mismatch"\n                        self.conn.execute(\n                            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state='ISSUED'",\n                            (now, reason, grant_id),\n                        )\n                        grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                        self.conn.commit()\n                        return {\n                            "reserved": False,\n                            "grant": self._decode_row(dict(grant) if grant else None) or {},\n                            "created": False,\n                            "attempt": {},\n                        }\n\n                self.conn.execute(\n''',
    label="sqlite atomic reserve canonical guard",
)

# ---------------------------------------------------------------------------
# SQLAlchemy repository must enforce the same authority contract as SQLite.
# ---------------------------------------------------------------------------
replace_once(
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    "from agent_core.storage.repositories.base import TransactionScope, ActiveDraftValidationCode, ActiveDraftValidationResult\n",
    "from agent_core.storage.repositories.base import TransactionScope, ActiveDraftValidationCode, ActiveDraftValidationResult\nfrom agent_core.operations.draft import TERMINAL_DRAFT_STATES\n",
    label="sqlalchemy terminal-state import",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    '''        grant_id = str(kwargs["grant_id"])\n        attempt_id = str(kwargs["attempt_id"])\n        try:\n''',
    '''        grant_id = str(kwargs["grant_id"])\n        attempt_id = str(kwargs["attempt_id"])\n        drafts = self.t["transaction_drafts"]\n        try:\n''',
    label="sqlalchemy reserve drafts table",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    '''                if existing:\n                    grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": self._decode_row(existing) or {}}\n                conn.execute(grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED", grants.c.expires_at.is_not(None), grants.c.expires_at <= now)).values(state="EXPIRED", revoked_at=now, reason="grant_expired"))\n''',
    '''                if existing:\n                    grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": self._decode_row(existing) or {}}\n                canonical = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(\n                    drafts.c.draft_id == kwargs["draft_id"],\n                    drafts.c.tenant_id == kwargs["tenant_id"],\n                    drafts.c.user_id == kwargs["user_id"],\n                    drafts.c.thread_id == kwargs["thread_id"],\n                ))).first())\n                canonical_payload = self._decode_row(canonical) if canonical else None\n                if canonical_payload:\n                    canonical_state = str(canonical_payload.get("draft_state") or "").upper()\n                    snapshot_mismatch = (\n                        int(canonical_payload.get("draft_revision") or 0) != int(kwargs["draft_revision"])\n                        or str(canonical_payload.get("command_digest") or "") != str(kwargs["command_digest"] or "")\n                    )\n                    if canonical_state in TERMINAL_DRAFT_STATES or snapshot_mismatch:\n                        reason = "draft_terminal" if canonical_state in TERMINAL_DRAFT_STATES else "draft_snapshot_mismatch"\n                        conn.execute(grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED")).values(state="REVOKED", revoked_at=now, reason=reason))\n                        grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                        return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}\n                conn.execute(grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED", grants.c.expires_at.is_not(None), grants.c.expires_at <= now)).values(state="EXPIRED", revoked_at=now, reason="grant_expired"))\n''',
    label="sqlalchemy atomic reserve canonical guard",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    '''            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == values["draft_id"])).first())\n            if existing:\n                conn.execute(table.update().where(table.c.draft_id == values["draft_id"]).values(**{k:v for k,v in values.items() if k not in {"draft_id", "created_at"}}))\n            else:\n''',
    '''            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == values["draft_id"])).first())\n            if existing:\n                existing_payload = self._decode_row(existing) or {}\n                existing_state = str(existing_payload.get("draft_state") or "").upper()\n                existing_revision = int(existing_payload.get("draft_revision") or 0)\n                incoming_revision = int(values.get("draft_revision") or 0)\n                if existing_state in TERMINAL_DRAFT_STATES or existing_revision > incoming_revision:\n                    return existing_payload\n                conn.execute(table.update().where(table.c.draft_id == values["draft_id"]).values(**{k:v for k,v in values.items() if k not in {"draft_id", "created_at"}}))\n            else:\n''',
    label="sqlalchemy create_draft terminal guard",
)

replace_once(
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    '''    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:\n        table = self.t["transaction_drafts"]\n        values: dict[str, Any] = {"draft_state": draft_state, "updated_at": _now()}\n        if draft_revision is not None: values["draft_revision"] = int(draft_revision)\n        if command_digest is not None: values["command_digest"] = command_digest\n        if command_envelope is not None: values["command_envelope_json"] = _json(command_envelope)\n        if active_grant_id is not None: values["active_grant_id"] = active_grant_id\n        if current_attempt_id is not None: values["current_attempt_id"] = current_attempt_id\n        if projection is not None: values["projection_json"] = _json(projection)\n        with self.p.conn() as conn:\n            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))\n''',
    '''    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:\n        table = self.t["transaction_drafts"]\n        values: dict[str, Any] = {"draft_state": draft_state, "updated_at": _now()}\n        if draft_revision is not None: values["draft_revision"] = int(draft_revision)\n        if command_digest is not None: values["command_digest"] = command_digest\n        if command_envelope is not None: values["command_envelope_json"] = _json(command_envelope)\n        if active_grant_id is not None: values["active_grant_id"] = active_grant_id\n        if current_attempt_id is not None: values["current_attempt_id"] = current_attempt_id\n        if projection is not None: values["projection_json"] = _json(projection)\n        with self.p.conn() as conn:\n            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == draft_id)).first())\n            if not existing:\n                return\n            current = self._decode_row(existing) or {}\n            if str(current.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:\n                return\n            if draft_revision is not None and int(current.get("draft_revision") or 0) > int(draft_revision):\n                return\n            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))\n''',
    label="sqlalchemy advance_draft terminal guard",
)

# ---------------------------------------------------------------------------
# In-memory ledger is a projection only.  It must never outvote a terminal
# Draft already observed for the same immutable Draft handle.
# ---------------------------------------------------------------------------
replace_once(
    "services/agent-service/src/agent_core/ledger/ledger.py",
    "from agent_core.operations.draft import display_projection, ensure_transaction_draft, transition_draft\n",
    "from agent_core.operations.draft import TERMINAL_DRAFT_STATES, display_projection, ensure_transaction_draft, transition_draft\n",
    label="ledger terminal-state import",
)

replace_once(
    "services/agent-service/src/agent_core/ledger/ledger.py",
    '''        item = deepcopy(raw)\n        if str(item.get("kind") or "") == "offer":\n            item = ensure_transaction_draft(item, previous=deduped.get(str(item.get("handle") or "")))\n        if _expired(item, now=current):\n''',
    '''        item = deepcopy(raw)\n        handle = str(item.get("handle") or "")\n        previous = deduped.get(handle)\n        if str(item.get("kind") or "") == "offer":\n            # Once this Draft identity is terminal, later stale Workflow\n            # projections cannot resurrect it.  A subsequent transaction must\n            # use a new Draft handle; Receipt/business artifacts carry later\n            # facts without rewriting the closed Draft.\n            if previous is not None and str(previous.get("kind") or "") == "offer" and str(previous.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:\n                continue\n            item = ensure_transaction_draft(item, previous=previous)\n        if _expired(item, now=current):\n''',
    label="ledger terminal projection guard",
)

# ---------------------------------------------------------------------------
# Authority entry validates the canonical repository before resuming a stale
# Workflow interrupt.  The checkpoint cannot authorize over a closed Draft.
# ---------------------------------------------------------------------------
replace_once(
    "services/agent-service/app/services/agent_service.py",
    '''        if not offer or self._draft_state_for_validation(offer) != "AWAITING_AUTHORIZATION":\n            return "offer_not_awaiting_authority"\n        if str(offer.get("action_id") or "") != request.action_id:\n''',
    '''        if not offer or self._draft_state_for_validation(offer) != "AWAITING_AUTHORIZATION":\n            return "offer_not_awaiting_authority"\n        repository = getattr(self, "transactions", None)\n        get_durable = getattr(repository, "get_draft_for_scope", None)\n        if callable(get_durable):\n            durable = get_durable(\n                scope=TransactionScope(\n                    tenant_id=str(request.tenant_id or "default"),\n                    user_id=str(request.user_id),\n                    thread_id=str(request.thread_id),\n                ),\n                draft_id=expected_handle,\n            )\n            if durable is None:\n                return "durable_draft_missing"\n            if self._draft_state_for_validation(durable) != "AWAITING_AUTHORIZATION":\n                return "durable_draft_not_awaiting_authority"\n            if int(durable.get("draft_revision") or 0) != int(offer.get("draft_revision") or 0):\n                return "durable_draft_revision_mismatch"\n            durable_projection = durable.get("projection") if isinstance(durable.get("projection"), dict) else {}\n            if str(durable_projection.get("confirmation_id") or "") != str(offer.get("confirmation_id") or ""):\n                return "durable_confirmation_id_mismatch"\n            if int(durable_projection.get("confirmation_version") or 0) != int(offer.get("confirmation_version") or 0):\n                return "durable_confirmation_version_mismatch"\n        if str(offer.get("action_id") or "") != request.action_id:\n''',
    label="API durable authority validation",
)

# ---------------------------------------------------------------------------
# Grant minting is downstream of the canonical Draft state.  Even an internal
# caller bypassing HTTP validation must fail closed against a terminal/moved
# Draft instead of persisting the stale card and minting fresh authority.
# ---------------------------------------------------------------------------
replace_once(
    "services/agent-service/src/agent_core/transaction/coordinator.py",
    '''    store = transaction_store(state)\n    persist_draft_from_offer(state=state, offer=offer, draft_state=str(offer.get("draft_state") or "AWAITING_AUTHORIZATION"))\n    record = store.issue_grant(\n''',
    '''    store = transaction_store(state)\n    draft_id = str(offer.get("draft_id") or offer.get("handle") or "")\n    scoped_getter = getattr(store, "get_draft_for_scope", None)\n    if callable(scoped_getter):\n        current = scoped_getter(\n            scope=TransactionScope(\n                tenant_id=scope["tenant_id"],\n                user_id=scope["user_id"],\n                thread_id=scope["thread_id"],\n            ),\n            draft_id=draft_id,\n        )\n        if current is not None:\n            if str(current.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n                raise ValueError("canonical Draft is no longer awaiting authority")\n            if int(current.get("draft_revision") or 0) != int(offer.get("draft_revision") or 1):\n                raise ValueError("canonical Draft revision no longer matches authority card")\n    persisted = persist_draft_from_offer(state=state, offer=offer, draft_state=str(offer.get("draft_state") or "AWAITING_AUTHORIZATION"))\n    if str(persisted.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n        raise ValueError("canonical Draft rejected stale authority projection")\n    record = store.issue_grant(\n''',
    label="coordinator canonical grant guard",
)

# Coordinator now needs the repository scope data class for the exact lookup.
replace_once(
    "services/agent-service/src/agent_core/transaction/coordinator.py",
    "from agent_core.operations.draft import canonical_command_payload, command_digest_for_offer\n",
    "from agent_core.operations.draft import canonical_command_payload, command_digest_for_offer\nfrom agent_core.storage.repositories.base import TransactionScope\n",
    label="coordinator TransactionScope import",
)

# ---------------------------------------------------------------------------
# Duplicate UI/network submissions after a known Receipt must project the
# already-known terminal result, not downgrade the stale Workflow copy to
# SUBMISSION_UNKNOWN.  No second Business Service write is performed.
# ---------------------------------------------------------------------------
replace_once(
    "services/agent-service/src/agent_core/transaction/commit_runtime.py",
    '''    if not reservation.get("reserved"):\n        # A duplicate click, a second tab, or a previous process may already own\n        # this grant.  Fail closed and show a read-only reconciliation state.\n        unknown = transition_draft(refreshed, "SUBMISSION_UNKNOWN", reason="grant_already_reserved_or_consumed")\n        unknown["commit_attempt_id"] = attempt_id\n        ledger = append_entries(ledger, [unknown])\n        return _transaction_commit_update(\n            state, ledger, unknown,\n            result={"success": False, "error": "提交结果正在确认中，请勿重复操作。", "code": "SUBMISSION_UNKNOWN"},\n            draft_state="SUBMISSION_UNKNOWN", attempt_id=attempt_id, idempotency_key=idempotency_key,\n            status="ActionCommitAlreadyInProgress", write_receipt=False, deps=deps,\n        )\n''',
    '''    if not reservation.get("reserved"):\n        # A duplicate click, a second tab, or a previous process may already\n        # own this exact idempotent attempt.  The durable Receipt outranks the\n        # stale Workflow projection: if the result is already known, project\n        # that terminal fact and never downgrade it to SUBMISSION_UNKNOWN.\n        repository = transaction_store(state)\n        existing_receipt = repository.get_receipt_by_attempt(attempt_id) if attempt_id else None\n        if isinstance(existing_receipt, dict):\n            known_result = existing_receipt.get("business_result") if isinstance(existing_receipt.get("business_result"), dict) else {}\n            receipt_state = str(existing_receipt.get("receipt_state") or "").upper()\n            if receipt_state == "SUCCESS" and bool(known_result.get("success")):\n                return _transaction_commit_update(\n                    state, ledger, refreshed,\n                    result=dict(known_result), draft_state="COMMITTED",\n                    attempt_id=attempt_id, idempotency_key=idempotency_key,\n                    status="ActionAlreadyCommitted", write_receipt=False, deps=deps,\n                )\n            if receipt_state == "FAILED":\n                known_attempt_state = str(attempt.get("state") or "").upper()\n                known_failure_state = known_attempt_state if known_attempt_state in {"FAILED_RETRYABLE", "FAILED_FINAL"} else "FAILED_FINAL"\n                return _transaction_commit_update(\n                    state, ledger, refreshed,\n                    result=dict(known_result or {"success": False, "error": "业务提交已失败。"}),\n                    draft_state=known_failure_state, attempt_id=attempt_id,\n                    idempotency_key=idempotency_key, status="ActionAlreadyFailed",\n                    write_receipt=False, deps=deps,\n                )\n        # Without a Receipt the outcome is genuinely uncertain. Keep the exact\n        # existing Attempt read-only and require reconciliation; do not execute\n        # a second business command.\n        unknown = transition_draft(refreshed, "SUBMISSION_UNKNOWN", reason="grant_already_reserved_or_consumed")\n        unknown["commit_attempt_id"] = attempt_id\n        ledger = append_entries(ledger, [unknown])\n        return _transaction_commit_update(\n            state, ledger, unknown,\n            result={"success": False, "error": "提交结果正在确认中，请勿重复操作。", "code": "SUBMISSION_UNKNOWN"},\n            draft_state="SUBMISSION_UNKNOWN", attempt_id=attempt_id, idempotency_key=idempotency_key,\n            status="ActionCommitAlreadyInProgress", write_receipt=False, deps=deps,\n        )\n''',
    label="duplicate commit durable receipt projection",
)

# ---------------------------------------------------------------------------
# Permanent adversarial regression suite.  These tests attack the authority
# boundary instead of proving only the happy path.
# ---------------------------------------------------------------------------
test_path = ROOT / "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
test_path.write_text(r'''from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.chat_schema import ActionAuthorityRequest
from app.services.agent_service import AgentService
from agent_core.ledger import append_entries, artifact_entry, find_handle, offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.persistence.database_settings import DatabaseSettings
from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider
from agent_core.runtime.outcomes import outcome
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction import transition_draft
from agent_core.transaction.coordinator import issue_grant_for_authority
from agent_core.transaction.deps import TransactionExecutionDeps


SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "stage8-terminal"}


def _offer(*, state: str = "AWAITING_AUTHORIZATION") -> dict:
    row = offer_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle="artifact:order:10002",
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
        scope=SCOPE,
        turn=2,
        label="退款申请",
        handle="draft:refund:stage8-terminal",
    )
    row["confirmation_id"] = "confirm-stage8"
    row["confirmation_version"] = 1
    row["authority_revision"] = 2
    return transition_draft(row, state)


def _create(store, offer: dict, *, state: str | None = None) -> dict:
    return store.create_draft(
        draft_id=offer["draft_id"],
        tenant_id=SCOPE["tenant_id"],
        user_id=SCOPE["user_id"],
        thread_id=SCOPE["thread_id"],
        draft_revision=offer["draft_revision"],
        draft_state=state or offer["draft_state"],
        action_id=offer["action_id"],
        command_digest=offer["command_digest"],
        command_envelope=offer.get("business_command_envelope"),
        projection=offer,
    )


def test_sqlite_terminal_draft_rejects_stale_create_and_advance(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
    store.record_receipt(
        receipt_id="receipt:sqlite-stage8",
        tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id="attempt:sqlite-stage8",
        receipt_handle="h_receipt:sqlite-stage8", receipt_state="SUCCESS",
        business_result={"success": True, "data": {"refund_id": "R-stage8"}},
        business_resource_id="R-stage8",
    )

    _create(store, offer, state="AWAITING_AUTHORIZATION")
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    store.advance_draft(
        offer["draft_id"], draft_state="SUBMISSION_UNKNOWN",
        draft_revision=offer["draft_revision"], current_attempt_id="attempt:sqlite-stage8",
    )
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"


def test_sqlalchemy_terminal_draft_rejects_stale_create_and_advance(tmp_path: Path) -> None:
    db_file = tmp_path / "agent-sqlalchemy.db"
    provider = build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="sqlite",
            database_url=f"sqlite:///{db_file}",
            sqlite_path=db_file,
            create_schema=True,
        )
    )
    try:
        store = provider.transactions
        offer = _offer()
        _create(store, offer)
        store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
        _create(store, offer, state="AWAITING_AUTHORIZATION")
        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
        store.advance_draft(offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"])
        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    finally:
        provider.close()


def test_ledger_terminal_offer_cannot_be_reopened_by_stale_projection() -> None:
    committed = _offer(state="COMMITTED")
    stale = _offer(state="AWAITING_AUTHORIZATION")
    stale["updated_turn"] = int(committed.get("updated_turn") or 0) + 1
    ledger = append_entries([committed], [stale])
    row = find_handle(ledger, committed["handle"], scope=SCOPE, allowed_kinds={"offer"}, active_only=False)
    assert row is not None
    assert row["draft_state"] == "COMMITTED"


def test_atomic_reserve_cannot_create_attempt_against_terminal_draft(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.issue_grant(
        grant_id="grant-stage8", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
        confirmation_id=offer["confirmation_id"], client_request_id="client-stage8", actor_id=SCOPE["user_id"], actor_role="customer",
    )
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])

    result = store.reserve_grant_and_start_attempt(
        grant_id="grant-stage8", attempt_id="attempt-stage8-late",
        tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id=offer["action_id"],
        command_digest=offer["command_digest"], idempotency_key="idem-stage8-late",
        canonical_payload=offer["command_payload"], business_command_envelope=None, draft_projection=offer,
    )
    assert result["reserved"] is False
    assert result["created"] is False
    assert result["attempt"] == {}
    assert store.get_attempt("attempt-stage8-late") is None
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    assert store.get_grant("grant-stage8")["state"] == "REVOKED"


def test_internal_grant_minting_rejects_terminal_canonical_draft(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="REVOKED", draft_revision=offer["draft_revision"])
    state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "_transaction_repository": store,
    }
    authority = {
        "actor_id": SCOPE["user_id"], "actor_role": "customer",
        "client_request_id": "late-authority-stage8", "authority_type": "ui_confirmed",
    }
    with pytest.raises(ValueError, match="no longer awaiting authority"):
        issue_grant_for_authority(state=state, offer=offer, authority=authority)
    assert store.list_grants_by_thread(**SCOPE) == []
    assert store.get_draft(offer["draft_id"])["draft_state"] == "REVOKED"


def test_stale_browser_authority_is_rejected_by_durable_terminal_state(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
    stale_values = {
        "turn_index": 2,
        "focused_draft_id": offer["draft_id"],
        "artifact_ledger": [offer],
    }

    class _Hydrator:
        def values(self, _graph, **_kwargs):
            return dict(stale_values)

    service = AgentService.__new__(AgentService)
    service.transactions = store
    service.checkpoint_hydrator = _Hydrator()
    service._config_for_request = lambda *_args, **_kwargs: {"configurable": {"thread_id": "ignored"}}
    request = ActionAuthorityRequest(
        thread_id=SCOPE["thread_id"], user_id=SCOPE["user_id"], role="customer", tenant_id=SCOPE["tenant_id"],
        decision="approved", authority_type="ui_confirmed", offer_handle=offer["draft_id"], action_id=offer["action_id"],
        target_handle=offer["target_handle"], confirmation_id=offer["confirmation_id"], confirmation_version=1,
        conversation_revision=2, client_request_id="late-browser-stage8",
    )
    assert service._validate_action_authority(object(), request) == "durable_draft_not_awaiting_authority"


def test_duplicate_after_success_receipt_projects_committed_without_business_write(tmp_path: Path, monkeypatch) -> None:
    import agent_core.transaction.commit_runtime as runtime

    store = TransactionLifecycleStore(tmp_path / "agent.db")
    target = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002", "status": "已签收", "version": 1},
        scope=SCOPE, turn=2, source="test", freshness_version=1, handle="artifact:order:10002",
    )
    envelope = {
        "contract": "business_adapter.commit@1", "method": "POST", "path": "/refunds",
        "action_id": "create_refund", "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "质量问题", "expected_version": 1},
        "actor_scope": {"tenant_id": SCOPE["tenant_id"], "user_id": SCOPE["user_id"]},
    }
    offer = _offer(state="AUTHORIZED")
    offer["business_command_envelope"] = envelope
    offer = transition_draft(offer, "AUTHORIZED")
    offer["active_grant_id"] = "grant-known"
    store.create_draft(
        draft_id=offer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_revision=offer["draft_revision"], draft_state="COMMITTED", action_id=offer["action_id"],
        command_digest=offer["command_digest"], command_envelope=envelope, projection=offer,
    )
    store.record_receipt(
        receipt_id="receipt-known", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id="attempt-known", receipt_handle="h_receipt:known", receipt_state="SUCCESS",
        business_result={"success": True, "data": {"refund_id": "R-known", "version": 1}}, business_resource_id="R-known",
    )

    monkeypatch.setattr(runtime, "snapshot_matches_registry", lambda _offer: True)
    monkeypatch.setattr(runtime, "validate_ui_authority", lambda **_kwargs: (True, "ok"))
    monkeypatch.setattr(runtime, "_refresh_offer_preflight", lambda *_args, **_kwargs: ({"success": True}, {"decision": "ALLOWED", "snapshot": {"version": 1}}, []))
    monkeypatch.setattr(runtime, "_build_business_command_envelope", lambda *_args, **_kwargs: dict(envelope))
    monkeypatch.setattr(runtime, "reserve_grant_and_start_attempt", lambda **_kwargs: (
        {"reserved": False, "grant": {"state": "CONSUMED"}},
        {"created": False, "attempt": {"attempt_id": "attempt-known", "state": "ACKED", "idempotency_key": "idem-known"}},
    ))
    monkeypatch.setattr(runtime, "_new_resource_artifacts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runtime, "_execute_business_command_envelope", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate must not call Business Service")))

    state = {
        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"], "current_role": "customer", "turn_index": 2,
        "artifact_ledger": [target, offer], "focused_draft_id": offer["draft_id"],
        "commit_authority": {"grant_id": "grant-known", "command_digest": offer["command_digest"]},
        "action_queue": [], "tool_trace": [], "_transaction_repository": store,
    }
    patch = runtime.commit_action_node(
        state,
        deps=TransactionExecutionDeps(business_port=SimpleNamespace(), outcome_factory=outcome),
    )
    assert patch["status"] == "ActionAlreadyCommitted"
    row = find_handle(patch["artifact_ledger"], offer["handle"], scope=SCOPE, allowed_kinds={"offer"}, active_only=False)
    assert row is not None and row["draft_state"] == "COMMITTED"
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
''', encoding="utf-8")
