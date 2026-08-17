from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {count}")
    write(path, text.replace(old, new))


def replace_block(path: str, start: str, end: str, new_block: str, *, label: str) -> None:
    text = read(path)
    if text.count(start) != 1:
        raise SystemExit(f"{label} start anchor mismatch: {text.count(start)}")
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    write(path, text[:start_index] + new_block + text[end_index:])


# ---------------------------------------------------------------------------
# Shared Attempt/Receipt lifecycle policy.  Repositories remain the authority;
# this module is only their deterministic mutation validator.
# ---------------------------------------------------------------------------
policy_path = ROOT / "services/agent-service/src/agent_core/transaction/persistence_policy.py"
policy_path.write_text('''from __future__ import annotations

from typing import Any


ATTEMPT_STARTED = "STARTED"
ATTEMPT_SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
ATTEMPT_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
ATTEMPT_ACKED = "ACKED"
ATTEMPT_FAILED_RETRYABLE = "FAILED_RETRYABLE"
ATTEMPT_FAILED_FINAL = "FAILED_FINAL"

SEALED_ATTEMPT_STATES = {
    ATTEMPT_ACKED,
    ATTEMPT_FAILED_RETRYABLE,
    ATTEMPT_FAILED_FINAL,
}

_ALLOWED_ATTEMPT_TRANSITIONS: dict[str, set[str]] = {
    ATTEMPT_STARTED: {
        ATTEMPT_STARTED,
        ATTEMPT_SUBMISSION_UNKNOWN,
        ATTEMPT_RECONCILIATION_REQUIRED,
        ATTEMPT_ACKED,
        ATTEMPT_FAILED_RETRYABLE,
        ATTEMPT_FAILED_FINAL,
    },
    ATTEMPT_SUBMISSION_UNKNOWN: {
        ATTEMPT_SUBMISSION_UNKNOWN,
        ATTEMPT_RECONCILIATION_REQUIRED,
        ATTEMPT_ACKED,
        ATTEMPT_FAILED_RETRYABLE,
        ATTEMPT_FAILED_FINAL,
    },
    ATTEMPT_RECONCILIATION_REQUIRED: {
        ATTEMPT_RECONCILIATION_REQUIRED,
        ATTEMPT_ACKED,
        ATTEMPT_FAILED_RETRYABLE,
        ATTEMPT_FAILED_FINAL,
    },
    ATTEMPT_ACKED: {ATTEMPT_ACKED},
    ATTEMPT_FAILED_RETRYABLE: {ATTEMPT_FAILED_RETRYABLE},
    ATTEMPT_FAILED_FINAL: {ATTEMPT_FAILED_FINAL},
}


def validate_receipt_binding(
    *,
    attempt: dict[str, Any] | None,
    grant: dict[str, Any] | None,
    tenant_id: str,
    user_id: str,
    thread_id: str,
    draft_id: str,
    attempt_id: str | None,
    receipt_state: str,
    business_result: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Prove that a Receipt closes one exact persisted Attempt/Grant chain."""
    if not attempt_id:
        return False, "receipt_attempt_id_required"
    if not isinstance(attempt, dict) or not attempt:
        return False, "receipt_attempt_missing"
    if str(attempt.get("attempt_id") or "") != str(attempt_id):
        return False, "receipt_attempt_identity_mismatch"
    for field, expected in (
        ("tenant_id", tenant_id),
        ("user_id", user_id),
        ("thread_id", thread_id),
        ("draft_id", draft_id),
    ):
        if str(attempt.get(field) or "") != str(expected or ""):
            return False, f"receipt_attempt_scope_mismatch:{field}"

    grant_id = str(attempt.get("grant_id") or "")
    if not grant_id or not isinstance(grant, dict) or not grant:
        return False, "receipt_grant_missing"
    if str(grant.get("grant_id") or "") != grant_id:
        return False, "receipt_grant_identity_mismatch"
    for field, expected in (
        ("tenant_id", tenant_id),
        ("user_id", user_id),
        ("thread_id", thread_id),
        ("draft_id", draft_id),
    ):
        if str(grant.get(field) or "") != str(expected or ""):
            return False, f"receipt_grant_scope_mismatch:{field}"
    if int(grant.get("draft_revision") or 0) != int(attempt.get("draft_revision") or 0):
        return False, "receipt_grant_revision_mismatch"
    if str(grant.get("command_digest") or "") != str(attempt.get("command_digest") or ""):
        return False, "receipt_grant_command_mismatch"
    if str(grant.get("state") or "").upper() not in {"RESERVED", "CONSUMED"}:
        return False, "receipt_grant_not_reserved"

    state = str(receipt_state or "").upper()
    if state not in {"SUCCESS", "FAILED"}:
        return False, "receipt_state_invalid"
    result = business_result if isinstance(business_result, dict) else {}
    if state == "SUCCESS" and result.get("success") is not True:
        return False, "success_receipt_requires_success_result"
    if state == "FAILED" and result.get("success") is True:
        return False, "failed_receipt_conflicts_with_success_result"

    attempt_state = str(attempt.get("state") or "").upper()
    if attempt_state == ATTEMPT_ACKED and state != "SUCCESS":
        return False, "acked_attempt_conflicts_with_failed_receipt"
    if attempt_state in {ATTEMPT_FAILED_RETRYABLE, ATTEMPT_FAILED_FINAL} and state != "FAILED":
        return False, "failed_attempt_conflicts_with_success_receipt"
    return True, "receipt_binding_valid"


def attempt_persistence_update_decision(
    current: dict[str, Any] | None,
    *,
    target_state: str,
    business_result: dict[str, Any] | None,
    receipt_handle: str | None,
    receipt: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Validate one mutation of an immutable Attempt identity."""
    if not isinstance(current, dict) or not current:
        return False, "attempt_missing"
    current_state = str(current.get("state") or "").upper()
    incoming_state = str(target_state or "").upper()
    if current_state in SEALED_ATTEMPT_STATES:
        return False, "attempt_sealed"
    allowed = _ALLOWED_ATTEMPT_TRANSITIONS.get(current_state, {current_state})
    if incoming_state not in allowed:
        return False, f"illegal_attempt_transition:{current_state}->{incoming_state}"

    receipt_state = str((receipt or {}).get("receipt_state") or "").upper()
    if incoming_state in SEALED_ATTEMPT_STATES:
        if not receipt_state:
            return False, "terminal_attempt_requires_receipt"
        if receipt_state == "SUCCESS" and incoming_state != ATTEMPT_ACKED:
            return False, "success_receipt_requires_acked_attempt"
        if receipt_state == "FAILED" and incoming_state not in {ATTEMPT_FAILED_RETRYABLE, ATTEMPT_FAILED_FINAL}:
            return False, "failed_receipt_requires_failed_attempt"
        if receipt_state not in {"SUCCESS", "FAILED"}:
            return False, "terminal_attempt_receipt_invalid"
    elif receipt_state:
        # Once a business Receipt is durable, stale workers may not push the
        # Attempt back into an uncertain/nonterminal state.
        return False, "receipt_already_terminal"

    existing_result = current.get("business_result") if isinstance(current.get("business_result"), dict) else None
    if existing_result is not None and business_result is not None and existing_result != business_result:
        return False, "attempt_business_result_conflict"
    existing_receipt = str(current.get("receipt_handle") or "")
    incoming_receipt = str(receipt_handle or "")
    if existing_receipt and incoming_receipt and existing_receipt != incoming_receipt:
        return False, "attempt_receipt_handle_conflict"
    return True, "attempt_transition_valid"
''', encoding="utf-8")


# ---------------------------------------------------------------------------
# SQLite: Receipt requires exact Attempt+Grant binding; Attempt transitions are
# monotonic and terminal transitions require the durable Receipt first.
# ---------------------------------------------------------------------------
sqlite_path = "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py"
replace_once(
    sqlite_path,
    "from agent_core.operations.draft import draft_persistence_update_decision\n",
    "from agent_core.operations.draft import draft_persistence_update_decision\nfrom agent_core.transaction.persistence_policy import attempt_persistence_update_decision, validate_receipt_binding\n",
    label="sqlite persistence-policy import",
)
replace_block(
    sqlite_path,
    "    def record_receipt(self, *, receipt_id: str, tenant_id: str, user_id: str, thread_id: str, draft_id: str, attempt_id: str | None, receipt_handle: str | None, receipt_state: str, business_result: dict[str, Any], business_resource_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:\n",
    "    def get_receipt(self, receipt_id: str | None) -> dict[str, Any] | None:\n",
    '''    def record_receipt(self, *, receipt_id: str, tenant_id: str, user_id: str, thread_id: str, draft_id: str, attempt_id: str | None, receipt_handle: str | None, receipt_state: str, business_result: dict[str, Any], business_resource_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        now = _now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            attempt = self.conn.execute("SELECT * FROM transaction_attempts WHERE attempt_id=?", (str(attempt_id or ""),)).fetchone()
            attempt_payload = self._decode_row(dict(attempt)) if attempt else None
            grant_id = str((attempt_payload or {}).get("grant_id") or "")
            grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone() if grant_id else None
            grant_payload = self._decode_row(dict(grant)) if grant else None
            valid, reason = validate_receipt_binding(
                attempt=attempt_payload,
                grant=grant_payload,
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=thread_id,
                draft_id=draft_id,
                attempt_id=attempt_id,
                receipt_state=receipt_state,
                business_result=business_result,
            )
            if not valid:
                raise ValueError(f"transaction receipt attempt binding rejected: {reason}")
            existing = self.conn.execute("SELECT * FROM transaction_receipts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if existing:
                return self._decode_row(dict(existing)) or {}
            receipt_id_conflict = self.conn.execute("SELECT * FROM transaction_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
            if receipt_id_conflict:
                raise ValueError("transaction receipt id already belongs to another attempt")
            self.conn.execute(
                """INSERT INTO transaction_receipts(receipt_id,tenant_id,user_id,thread_id,draft_id,attempt_id,receipt_handle,receipt_state,business_result_json,business_resource_id,created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (receipt_id, tenant_id, user_id, thread_id, draft_id, attempt_id, receipt_handle, str(receipt_state).upper(), _json(business_result), business_resource_id, now, correlation_id),
            )
            self.conn.commit()
        return self.get_receipt(receipt_id) or {}

''',
    label="sqlite record_receipt binding",
)
replace_block(
    sqlite_path,
    "    def transition_attempt(\n",
    "    def list_reconcilable_attempts(self, *, scope: TransactionScope | None = None, tenant_id: str | None = None, user_id: str | None = None, thread_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:\n",
    '''    def transition_attempt(
        self,
        attempt_id: str,
        *,
        state: str,
        business_result: dict[str, Any] | None = None,
        receipt_handle: str | None = None,
        error_code: str | None = None,
        error: str | None = None,
        reconciled: bool = False,
    ) -> None:
        with self.lock:
            existing = self.conn.execute("SELECT * FROM transaction_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not existing:
                raise ValueError("transaction attempt missing")
            current = self._decode_row(dict(existing)) or {}
            receipt = self.conn.execute("SELECT * FROM transaction_receipts WHERE attempt_id=?", (attempt_id,)).fetchone()
            receipt_payload = self._decode_row(dict(receipt)) if receipt else None
            allowed, _reason = attempt_persistence_update_decision(
                current,
                target_state=state,
                business_result=business_result,
                receipt_handle=receipt_handle,
                receipt=receipt_payload,
            )
            if not allowed:
                return
            self.conn.execute(
                """UPDATE transaction_attempts SET state=?, business_result_json=COALESCE(?, business_result_json),
                   receipt_handle=COALESCE(?, receipt_handle), error_code=?, error=?, updated_at=?,
                   reconciled_at=CASE WHEN ? THEN ? ELSE reconciled_at END WHERE attempt_id=?""",
                (
                    state,
                    _json(business_result) if business_result is not None else None,
                    receipt_handle,
                    error_code,
                    error,
                    _now(),
                    1 if reconciled else 0,
                    _now(),
                    attempt_id,
                ),
            )
            self.conn.commit()

''',
    label="sqlite transition_attempt policy",
)


# ---------------------------------------------------------------------------
# SQLAlchemy backend consumes the exact same binding/transition policy.
# ---------------------------------------------------------------------------
sqla_path = "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py"
replace_once(
    sqla_path,
    "from agent_core.operations.draft import draft_persistence_update_decision\n",
    "from agent_core.operations.draft import draft_persistence_update_decision\nfrom agent_core.transaction.persistence_policy import attempt_persistence_update_decision, validate_receipt_binding\n",
    label="sqlalchemy persistence-policy import",
)
replace_block(
    sqla_path,
    "    def record_receipt(self, **kwargs: Any) -> dict[str, Any]:\n",
    "    def get_receipt_by_attempt(self, attempt_id: str | None) -> dict[str, Any] | None:\n",
    '''    def record_receipt(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_receipts"]
        attempts = self.t["transaction_attempts"]
        grants = self.t["transaction_grants"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        business_result = kwargs.pop("business_result", None)
        values = {
            **kwargs,
            "receipt_state": str(kwargs.get("receipt_state") or "").upper(),
            "business_result_json": _json(business_result),
            "created_at": _now(),
        }
        attempt_id = str(values.get("attempt_id") or "")
        with self.p.conn() as conn:
            attempt = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == attempt_id)).first())
            attempt_payload = self._decode_row(attempt) if attempt else None
            grant_id = str((attempt_payload or {}).get("grant_id") or "")
            grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first()) if grant_id else None
            grant_payload = self._decode_row(grant) if grant else None
            valid, reason = validate_receipt_binding(
                attempt=attempt_payload,
                grant=grant_payload,
                tenant_id=str(values.get("tenant_id") or ""),
                user_id=str(values.get("user_id") or ""),
                thread_id=str(values.get("thread_id") or ""),
                draft_id=str(values.get("draft_id") or ""),
                attempt_id=attempt_id,
                receipt_state=str(values.get("receipt_state") or ""),
                business_result=business_result,
            )
            if not valid:
                raise ValueError(f"transaction receipt attempt binding rejected: {reason}")
            existing = _row(conn.execute(self.sa.select(table).where(table.c.attempt_id == attempt_id)).first())
            if existing:
                return self._decode_row(existing) or {}
            receipt_id_conflict = _row(conn.execute(self.sa.select(table).where(table.c.receipt_id == values["receipt_id"])).first())
            if receipt_id_conflict:
                raise ValueError("transaction receipt id already belongs to another attempt")
            conn.execute(table.insert().values(**values))
        return self.get_receipt_by_attempt(attempt_id) or {}

''',
    label="sqlalchemy record_receipt binding",
)
replace_block(
    sqla_path,
    "    def transition_attempt(self, attempt_id: str, *, state: str, business_result: dict[str, Any] | None = None, receipt_handle: str | None = None, error_code: str | None = None, error: str | None = None, reconciled: bool = False) -> None:\n",
    "    def list_reconcilable_attempts(self, *, scope: TransactionScope | None = None, tenant_id: str | None = None, user_id: str | None = None, thread_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:\n",
    '''    def transition_attempt(self, attempt_id: str, *, state: str, business_result: dict[str, Any] | None = None, receipt_handle: str | None = None, error_code: str | None = None, error: str | None = None, reconciled: bool = False) -> None:
        table = self.t["transaction_attempts"]
        receipts = self.t["transaction_receipts"]
        values: dict[str, Any] = {"state": state, "updated_at": _now(), "error_code": error_code, "error": error}
        if business_result is not None:
            values["business_result_json"] = _json(business_result)
        if receipt_handle is not None:
            values["receipt_handle"] = receipt_handle
        if reconciled:
            values["reconciled_at"] = _now()
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.attempt_id == attempt_id)).first())
            if not existing:
                raise ValueError("transaction attempt missing")
            current = self._decode_row(existing) or {}
            receipt = _row(conn.execute(self.sa.select(receipts).where(receipts.c.attempt_id == attempt_id)).first())
            receipt_payload = self._decode_row(receipt) if receipt else None
            allowed, _reason = attempt_persistence_update_decision(
                current,
                target_state=state,
                business_result=business_result,
                receipt_handle=receipt_handle,
                receipt=receipt_payload,
            )
            if not allowed:
                return
            conn.execute(table.update().where(table.c.attempt_id == attempt_id).values(**values))

''',
    label="sqlalchemy transition_attempt policy",
)


# ---------------------------------------------------------------------------
# A durable Receipt must be written before the Attempt/Draft terminal projection
# so a crash can always be recovered by replaying the existing Receipt.
# ---------------------------------------------------------------------------
reconcile_path = "services/agent-service/src/agent_core/transaction/reconciliation.py"
replace_once(
    reconcile_path,
    '''            additions.extend([completed,receipt]); additions.extend(new_resource_artifacts(state, ledger, completed, result))\n            store.transition_attempt(attempt_id,state="ACKED",business_result=result,receipt_handle=str(receipt.get("handle") or ""),reconciled=True)\n            store.advance_draft(draft_id,draft_state="COMMITTED",current_attempt_id=attempt_id)\n            record_transaction_receipt_fn(state=state,offer=completed,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""),receipt_state="SUCCESS",business_result=result)\n''',
    '''            additions.extend([completed,receipt]); additions.extend(new_resource_artifacts(state, ledger, completed, result))\n            record_transaction_receipt_fn(state=state,offer=completed,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""),receipt_state="SUCCESS",business_result=result)\n            store.advance_draft(draft_id,draft_state="COMMITTED",current_attempt_id=attempt_id)\n            store.transition_attempt(attempt_id,state="ACKED",business_result=result,receipt_handle=str(receipt.get("handle") or ""),reconciled=True)\n''',
    label="reconciliation success receipt-first ordering",
)
replace_once(
    reconcile_path,
    '''        additions.extend([failed,receipt]); store.transition_attempt(attempt_id,state=classified,business_result=result,receipt_handle=str(receipt.get("handle") or ""),error_code=str(result.get("code") or ""),error=str(result.get("error") or ""),reconciled=True); store.advance_draft(draft_id,draft_state=classified,current_attempt_id=attempt_id)\n        record_transaction_receipt_fn(state=state,offer=failed,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""),receipt_state="FAILED",business_result=result)\n''',
    '''        additions.extend([failed,receipt]); record_transaction_receipt_fn(state=state,offer=failed,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""),receipt_state="FAILED",business_result=result)\n        store.advance_draft(draft_id,draft_state=classified,current_attempt_id=attempt_id); store.transition_attempt(attempt_id,state=classified,business_result=result,receipt_handle=str(receipt.get("handle") or ""),error_code=str(result.get("code") or ""),error=str(result.get("error") or ""),reconciled=True)\n''',
    label="reconciliation failure receipt-first ordering",
)


# ---------------------------------------------------------------------------
# Pre-attempt validation failures are not Business Receipts.  For terminal
# business failures, let _transaction_commit_update persist the Receipt before
# sealing the Attempt; only SUBMISSION_UNKNOWN is persisted early.
# ---------------------------------------------------------------------------
commit_path = "services/agent-service/src/agent_core/transaction/commit_runtime.py"
replace_once(
    commit_path,
    'status="ActionCommitPreflightRejected", write_receipt=True, deps=deps)',
    'status="ActionCommitPreflightRejected", write_receipt=False, deps=deps)',
    label="preflight rejection no receipt",
)
replace_once(
    commit_path,
    'status="ActionCommitEnvelopeInvalid", write_receipt=True, deps=deps)',
    'status="ActionCommitEnvelopeInvalid", write_receipt=False, deps=deps)',
    label="envelope validation no receipt",
)
replace_once(
    commit_path,
    '''    transaction_store(state).transition_attempt(\n        str(attempt_id or ""),\n        state=failed_state,\n        business_result=result if failed_state != "SUBMISSION_UNKNOWN" else None,\n        error_code=str(result.get("code") or ""),\n        error=str(result.get("error") or ""),\n    )\n''',
    '''    if failed_state == "SUBMISSION_UNKNOWN":\n        transaction_store(state).transition_attempt(\n            str(attempt_id or ""),\n            state=failed_state,\n            business_result=None,\n            error_code=str(result.get("code") or ""),\n            error=str(result.get("error") or ""),\n        )\n''',
    label="terminal attempt sealed only after receipt",
)
replace_once(
    commit_path,
    '''        status=status,\n        write_receipt=True,\n        deps=deps,\n    )\n''',
    '''        status=status,\n        write_receipt=False,\n        deps=deps,\n    )\n''',
    label="compat observation is not transaction receipt",
)


# ---------------------------------------------------------------------------
# Upgrade existing Stage 8 fixture to a legal Grant -> Attempt -> Receipt chain
# and add permanent Receipt/Attempt adversarial tests for both backends.
# ---------------------------------------------------------------------------
stage8_test = "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
replace_once(
    stage8_test,
    "from agent_core.transaction.coordinator import issue_grant_for_authority\n",
    "from agent_core.transaction.coordinator import issue_grant_for_authority, reserve_grant_and_start_attempt\n",
    label="stage8 test reserve import",
)
replace_once(
    stage8_test,
    '''def _create(store, offer: dict, *, state: str | None = None) -> dict:\n    return store.create_draft(\n        draft_id=offer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],\n        draft_revision=offer["draft_revision"], draft_state=state or offer["draft_state"], action_id=offer["action_id"],\n        command_digest=offer["command_digest"], command_envelope=offer.get("business_command_envelope"), projection=offer,\n    )\n\n\n''',
    '''def _create(store, offer: dict, *, state: str | None = None) -> dict:\n    return store.create_draft(\n        draft_id=offer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],\n        draft_revision=offer["draft_revision"], draft_state=state or offer["draft_state"], action_id=offer["action_id"],\n        command_digest=offer["command_digest"], command_envelope=offer.get("business_command_envelope"), projection=offer,\n    )\n\n\ndef _start_transaction_attempt(store, offer: dict, *, client_request_id: str):\n    state = {\n        "current_tenant_id": SCOPE["tenant_id"],\n        "current_user_id": SCOPE["user_id"],\n        "current_thread_id": SCOPE["thread_id"],\n        "_transaction_repository": store,\n    }\n    authority = issue_grant_for_authority(\n        state=state,\n        offer=offer,\n        authority={\n            "actor_id": SCOPE["user_id"],\n            "actor_role": "customer",\n            "client_request_id": client_request_id,\n            "authority_type": "ui_confirmed",\n        },\n    )\n    reservation, started = reserve_grant_and_start_attempt(state=state, offer=offer, authority=authority)\n    assert reservation["reserved"] is True\n    assert started["created"] is True\n    return state, authority, started["attempt"]\n\n\n''',
    label="stage8 legal attempt helper",
)
replace_once(
    stage8_test,
    '''    _create(store, offer)\n    store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])\n    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])\n    store.record_receipt(\n        receipt_id="receipt:sqlite-stage8", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],\n        draft_id=offer["draft_id"], attempt_id="attempt:sqlite-stage8", receipt_handle="h_receipt:sqlite-stage8", receipt_state="SUCCESS",\n        business_result={"success": True, "data": {"refund_id": "R-stage8"}}, business_resource_id="R-stage8",\n    )\n''',
    '''    _create(store, offer)\n    _state, authority, attempt = _start_transaction_attempt(store, offer, client_request_id="terminal-sqlite")\n    attempt_id = str(attempt["attempt_id"])\n    result = {"success": True, "data": {"refund_id": "R-stage8"}}\n    store.record_receipt(\n        receipt_id="receipt:sqlite-stage8", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],\n        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:sqlite-stage8", receipt_state="SUCCESS",\n        business_result=result, business_resource_id="R-stage8",\n    )\n    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)\n    store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle="h_receipt:sqlite-stage8")\n    store.consume_grant(str(authority["grant_id"]), attempt_id=attempt_id, receipt_handle="h_receipt:sqlite-stage8")\n''',
    label="stage8 terminal sqlite legal chain",
)

# The older duplicate test used an orphan synthetic Receipt.  Replace only its
# setup with a legal persisted Attempt; the assertion still proves no duplicate
# Business Service execution and terminal projection reuse.
replace_once(
    stage8_test,
    '''    offer = _offer(state="AUTHORIZED")\n    offer["business_command_envelope"] = envelope\n    offer = transition_draft(offer, "AUTHORIZED")\n    offer["active_grant_id"] = "grant-known"\n    _create(store, offer, state="COMMITTED")\n    store.record_receipt(\n        receipt_id="receipt-known", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],\n        draft_id=offer["draft_id"], attempt_id="attempt-known", receipt_handle="h_receipt:known", receipt_state="SUCCESS",\n        business_result={"success": True, "data": {"refund_id": "R-known", "version": 1}}, business_resource_id="R-known",\n    )\n''',
    '''    pending = _offer(state="AWAITING_AUTHORIZATION")\n    pending["business_command_envelope"] = envelope\n    _create(store, pending, state="AWAITING_AUTHORIZATION")\n    _state, authority, attempt = _start_transaction_attempt(store, pending, client_request_id="duplicate-known")\n    attempt_id = str(attempt["attempt_id"])\n    known_result = {"success": True, "data": {"refund_id": "R-known", "version": 1}}\n    store.record_receipt(\n        receipt_id="receipt-known", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],\n        draft_id=pending["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:known", receipt_state="SUCCESS",\n        business_result=known_result, business_resource_id="R-known",\n    )\n    store.advance_draft(pending["draft_id"], draft_state="COMMITTED", draft_revision=pending["draft_revision"], current_attempt_id=attempt_id)\n    store.transition_attempt(attempt_id, state="ACKED", business_result=known_result, receipt_handle="h_receipt:known")\n    store.consume_grant(str(authority["grant_id"]), attempt_id=attempt_id, receipt_handle="h_receipt:known")\n    offer = transition_draft(pending, "AUTHORIZED")\n    offer["active_grant_id"] = authority["grant_id"]\n''',
    label="stage8 duplicate legal receipt setup",
)
replace_once(
    stage8_test,
    '''        {"reserved": False, "grant": {"state": "CONSUMED"}},\n        {"created": False, "attempt": {"attempt_id": "attempt-known", "state": "ACKED", "idempotency_key": "idem-known"}},\n''',
    '''        {"reserved": False, "grant": {"state": "CONSUMED"}},\n        {"created": False, "attempt": {"attempt_id": attempt_id, "state": "ACKED", "idempotency_key": str(attempt.get("idempotency_key") or "")}},\n''',
    label="stage8 duplicate actual attempt id",
)
replace_once(
    stage8_test,
    '''        "commit_authority": {"grant_id": "grant-known", "command_digest": offer["command_digest"]},\n''',
    '''        "commit_authority": {"grant_id": authority["grant_id"], "command_digest": offer["command_digest"]},\n''',
    label="stage8 duplicate actual grant id",
)

round3_tests = r'''


def test_receipt_requires_exact_persisted_attempt_and_grant(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "receipt-binding.db")
    offer = _offer()
    _create(store, offer)
    with pytest.raises(ValueError, match="attempt"):
        store.record_receipt(
            receipt_id="receipt-orphan", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
            draft_id=offer["draft_id"], attempt_id="attempt-missing", receipt_handle="h_receipt:orphan", receipt_state="SUCCESS",
            business_result={"success": True, "data": {"refund_id": "R-orphan"}},
        )
    assert store.get_receipt("receipt-orphan") is None

    _state, _authority, attempt = _start_transaction_attempt(store, offer, client_request_id="receipt-binding")
    attempt_id = str(attempt["attempt_id"])
    with pytest.raises(ValueError, match="attempt"):
        store.record_receipt(
            receipt_id="receipt-wrong-scope", tenant_id="tenant-b", user_id="u999", thread_id="other-thread",
            draft_id="draft:other", attempt_id=attempt_id, receipt_handle="h_receipt:wrong", receipt_state="SUCCESS",
            business_result={"success": True, "data": {"refund_id": "R-wrong"}},
        )
    assert store.get_receipt_by_attempt(attempt_id) is None


def test_acked_attempt_cannot_regress_after_success_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "attempt-monotonic.db")
    offer = _offer()
    _create(store, offer)
    _state, _authority, attempt = _start_transaction_attempt(store, offer, client_request_id="attempt-monotonic")
    attempt_id = str(attempt["attempt_id"])
    result = {"success": True, "data": {"refund_id": "R-acked"}}
    store.record_receipt(
        receipt_id="receipt-acked", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:acked", receipt_state="SUCCESS", business_result=result,
    )
    store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle="h_receipt:acked")
    store.transition_attempt(attempt_id, state="STARTED", error="late stale worker")
    durable = store.get_attempt(attempt_id)
    assert durable is not None
    assert durable["state"] == "ACKED"
    assert durable["business_result"] == result
    assert durable["receipt_handle"] == "h_receipt:acked"


def test_success_receipt_crash_window_blocks_new_grant_and_attempt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "receipt-crash-window.db")
    offer = _offer()
    _create(store, offer)
    state, authority, attempt = _start_transaction_attempt(store, offer, client_request_id="receipt-crash-window")
    attempt_id = str(attempt["attempt_id"])
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTING"
    store.record_receipt(
        receipt_id="receipt-crash-window", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:crash-window", receipt_state="SUCCESS",
        business_result={"success": True, "data": {"refund_id": "R-crash-window"}},
    )
    late = dict(authority)
    late["client_request_id"] = "late-replay"
    with pytest.raises(ValueError, match="no longer awaiting authority"):
        issue_grant_for_authority(state=state, offer=offer, authority=late)
    assert len(store.list_grants_by_thread(**SCOPE)) == 1
    assert len(store.list_attempts_for_draft(scope=TransactionScope(**SCOPE), draft_id=offer["draft_id"])) == 1


def test_sqlalchemy_receipt_attempt_binding_and_monotonicity(tmp_path: Path) -> None:
    db_file = tmp_path / "receipt-binding-sqla.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        offer = _offer()
        _create(store, offer)
        with pytest.raises(ValueError, match="attempt"):
            store.record_receipt(
                receipt_id="receipt-orphan-sqla", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
                draft_id=offer["draft_id"], attempt_id="attempt-missing", receipt_handle="h_receipt:orphan", receipt_state="SUCCESS",
                business_result={"success": True, "data": {"refund_id": "R-orphan"}},
            )
        _state, _authority, attempt = _start_transaction_attempt(store, offer, client_request_id="sqla-receipt-binding")
        attempt_id = str(attempt["attempt_id"])
        result = {"success": True, "data": {"refund_id": "R-sqla"}}
        store.record_receipt(
            receipt_id="receipt-sqla", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
            draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:sqla", receipt_state="SUCCESS", business_result=result,
        )
        store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle="h_receipt:sqla")
        store.transition_attempt(attempt_id, state="STARTED", error="late stale worker")
        durable = store.get_attempt(attempt_id)
        assert durable is not None and durable["state"] == "ACKED"
    finally:
        provider.close()
'''
text = read(stage8_test)
if "test_receipt_requires_exact_persisted_attempt_and_grant" in text:
    raise SystemExit("round3 permanent tests already present")
write(stage8_test, text + round3_tests)
