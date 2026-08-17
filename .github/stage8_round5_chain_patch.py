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
# Shared chain-integrity policy. The repository is still the only authority;
# these pure validators prevent its individual mutation methods from becoming
# alternate ways to fabricate a partial transaction lifecycle.
# ---------------------------------------------------------------------------
policy_path = "services/agent-service/src/agent_core/transaction/persistence_policy.py"
text = read(policy_path)
if "def grant_issue_decision(" in text:
    raise SystemExit("complete-chain policy already present")
text += '''\n\ndef grant_issue_decision(\n    draft: dict[str, Any] | None,\n    *,\n    tenant_id: str, user_id: str, thread_id: str, draft_id: str,\n    draft_revision: int, command_digest: str, confirmation_id: str,\n) -> tuple[bool, str]:\n    if not isinstance(draft, dict) or not draft:\n        return False, "canonical_Draft_missing"\n    if str(draft.get("draft_id") or "") != str(draft_id):\n        return False, "canonical_Draft_identity_mismatch"\n    for field, expected in (("tenant_id", tenant_id), ("user_id", user_id), ("thread_id", thread_id)):\n        if str(draft.get(field) or "") != str(expected or ""):\n            return False, f"canonical_Draft_scope_mismatch:{field}"\n    if str(draft.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n        return False, "canonical_Draft_not_awaiting_authority"\n    if int(draft.get("draft_revision") or 0) != int(draft_revision):\n        return False, "canonical_Draft_revision_mismatch"\n    if str(draft.get("command_digest") or "") != str(command_digest or ""):\n        return False, "canonical_Draft_command_mismatch"\n    projection = draft.get("projection") if isinstance(draft.get("projection"), dict) else {}\n    durable_confirmation = str(projection.get("confirmation_id") or "")\n    if durable_confirmation and durable_confirmation != str(confirmation_id or ""):\n        return False, "canonical_Draft_confirmation_mismatch"\n    return True, "grant_issue_valid"\n\n\ndef grant_reservation_decision(\n    grant: dict[str, Any] | None, draft: dict[str, Any] | None,\n    *,\n    tenant_id: str, user_id: str, thread_id: str, draft_id: str,\n    draft_revision: int, command_digest: str,\n) -> tuple[bool, str]:\n    if not isinstance(grant, dict) or not grant:\n        return False, "grant_missing"\n    if str(grant.get("state") or "").upper() != "ISSUED":\n        return False, "grant_not_issued"\n    for field, expected in (\n        ("tenant_id", tenant_id), ("user_id", user_id), ("thread_id", thread_id), ("draft_id", draft_id)\n    ):\n        if str(grant.get(field) or "") != str(expected or ""):\n            return False, f"grant_request_mismatch:{field}"\n    if int(grant.get("draft_revision") or 0) != int(draft_revision):\n        return False, "grant_request_revision_mismatch"\n    if str(grant.get("command_digest") or "") != str(command_digest or ""):\n        return False, "grant_request_command_mismatch"\n    issue_ok, reason = grant_issue_decision(\n        draft, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id,\n        draft_id=draft_id, draft_revision=draft_revision, command_digest=command_digest,\n        confirmation_id=str(grant.get("confirmation_id") or ""),\n    )\n    if not issue_ok:\n        return False, "reservation_" + reason\n    return True, "grant_reservation_valid"\n\n\ndef existing_attempt_matches_request(\n    attempt: dict[str, Any] | None,\n    *, grant_id: str, tenant_id: str, user_id: str, thread_id: str,\n    draft_id: str, draft_revision: int, action_id: str, command_digest: str,\n) -> bool:\n    if not isinstance(attempt, dict) or not attempt:\n        return False\n    for field, expected in (\n        ("grant_id", grant_id), ("tenant_id", tenant_id), ("user_id", user_id),\n        ("thread_id", thread_id), ("draft_id", draft_id), ("action_id", action_id),\n    ):\n        if str(attempt.get(field) or "") != str(expected or ""):\n            return False\n    return (\n        int(attempt.get("draft_revision") or 0) == int(draft_revision)\n        and str(attempt.get("command_digest") or "") == str(command_digest or "")\n    )\n\n\ndef draft_terminal_observation_decision(\n    current: dict[str, Any],\n    *, target_state: str, attempt: dict[str, Any] | None, receipt: dict[str, Any] | None,\n) -> tuple[bool, str]:\n    current_state = str(current.get("draft_state") or "").upper()\n    target = str(target_state or "").upper()\n    effect_terminal = target == "COMMITTED" or (\n        current_state in {"COMMITTING", "SUBMISSION_UNKNOWN", "FAILED_RETRYABLE"}\n        and target in {"FAILED_RETRYABLE", "FAILED_FINAL"}\n    )\n    if not effect_terminal:\n        return True, "no_business_receipt_required"\n    attempt_id = str(current.get("current_attempt_id") or "")\n    if not attempt_id:\n        return False, "terminal_Draft_attempt_missing"\n    if not isinstance(attempt, dict) or str(attempt.get("attempt_id") or "") != attempt_id:\n        return False, "terminal_Draft_attempt_not_found"\n    for field in ("tenant_id", "user_id", "thread_id", "draft_id"):\n        if str(attempt.get(field) or "") != str(current.get(field) or ""):\n            return False, f"terminal_Draft_attempt_scope_mismatch:{field}"\n    if int(attempt.get("draft_revision") or 0) != int(current.get("draft_revision") or 0):\n        return False, "terminal_Draft_attempt_revision_mismatch"\n    if str(attempt.get("command_digest") or "") != str(current.get("command_digest") or ""):\n        return False, "terminal_Draft_attempt_command_mismatch"\n    if not isinstance(receipt, dict) or str(receipt.get("attempt_id") or "") != attempt_id:\n        return False, "terminal_Draft_receipt_missing"\n    if str(receipt.get("draft_id") or "") != str(current.get("draft_id") or ""):\n        return False, "terminal_Draft_receipt_draft_mismatch"\n    receipt_state = str(receipt.get("receipt_state") or "").upper()\n    result = receipt.get("business_result") if isinstance(receipt.get("business_result"), dict) else {}\n    if target == "COMMITTED":\n        if receipt_state != "SUCCESS" or result.get("success") is not True:\n            return False, "committed_Draft_requires_success_receipt"\n    else:\n        if receipt_state != "FAILED" or result.get("success") is True:\n            return False, "failed_Draft_requires_failed_receipt"\n    return True, "terminal_Draft_receipt_valid"\n'''
write(policy_path, text)


# ---------------------------------------------------------------------------
# SQLite: Grant issuance/reservation require canonical Draft; direct non-atomic
# reserve surface is fail-closed; terminal business Drafts require a Receipt.
# ---------------------------------------------------------------------------
sqlite_path = "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py"
replace_once(
    sqlite_path,
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, grant_consumption_decision, validate_receipt_binding\n",
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, draft_terminal_observation_decision, existing_attempt_matches_request, grant_consumption_decision, grant_issue_decision, grant_reservation_decision, validate_receipt_binding\n",
    label="sqlite complete-chain imports",
)
replace_once(
    sqlite_path,
    '''        with self.lock:\n            existing = self.conn.execute(\n                """SELECT * FROM transaction_grants WHERE tenant_id=? AND user_id=? AND thread_id=?\n                   AND draft_id=? AND draft_revision=? AND command_digest=? AND confirmation_id=?""",\n                (tenant_id, user_id, thread_id, draft_id, int(draft_revision), command_digest, confirmation_id),\n            ).fetchone()\n''',
    '''        with self.lock:\n            draft_row = self.conn.execute(\n                "SELECT * FROM transaction_drafts WHERE draft_id=? AND tenant_id=? AND user_id=? AND thread_id=?",\n                (draft_id, tenant_id, user_id, thread_id),\n            ).fetchone()\n            draft = self._decode_row(dict(draft_row)) if draft_row else None\n            allowed, reason = grant_issue_decision(\n                draft, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id,\n                draft_id=draft_id, draft_revision=draft_revision, command_digest=command_digest, confirmation_id=confirmation_id,\n            )\n            if not allowed:\n                raise ValueError(f"canonical Draft rejected Grant issuance: {reason}")\n            existing = self.conn.execute(\n                """SELECT * FROM transaction_grants WHERE tenant_id=? AND user_id=? AND thread_id=?\n                   AND draft_id=? AND draft_revision=? AND command_digest=? AND confirmation_id=?""",\n                (tenant_id, user_id, thread_id, draft_id, int(draft_revision), command_digest, confirmation_id),\n            ).fetchone()\n''',
    label="sqlite issue_grant canonical Draft",
)
replace_block(
    sqlite_path,
    "    def reserve_grant(self, grant_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:\n",
    "    def reserve_grant_and_start_attempt(\n",
    '''    def reserve_grant(self, grant_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:
        """Compatibility surface: reservation is inseparable from Attempt creation.

        Transaction authority is mutated only by ``reserve_grant_and_start_attempt``.
        """
        return {"reserved": False, "grant": self.get_grant(grant_id) or {}, "reason": "atomic_attempt_required"}

''',
    label="sqlite standalone reserve fail closed",
)
replace_once(
    sqlite_path,
    '''                if existing:\n                    grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                    self.conn.commit()\n                    return {\n                        "reserved": False,\n                        "grant": self._decode_row(dict(grant) if grant else None) or {},\n                        "created": False,\n                        "attempt": self._decode_row(dict(existing)) or {},\n                    }\n\n                canonical = self.conn.execute(\n''',
    '''                if existing:\n                    existing_payload = self._decode_row(dict(existing)) or {}\n                    grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                    if not existing_attempt_matches_request(\n                        existing_payload, grant_id=grant_id, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id,\n                        draft_id=draft_id, draft_revision=draft_revision, action_id=action_id, command_digest=command_digest,\n                    ):\n                        self.conn.commit()\n                        return {"reserved": False, "grant": {}, "created": False, "attempt": {}}\n                    self.conn.commit()\n                    return {\n                        "reserved": False,\n                        "grant": self._decode_row(dict(grant) if grant else None) or {},\n                        "created": False,\n                        "attempt": existing_payload,\n                    }\n\n                grant_row = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                grant_payload = self._decode_row(dict(grant_row)) if grant_row else None\n                canonical = self.conn.execute(\n''',
    label="sqlite reserve existing attempt binding",
)
replace_once(
    sqlite_path,
    '''                canonical_payload = self._decode_row(dict(canonical)) if canonical else None\n                if canonical_payload:\n                    committing_projection = dict(canonical_payload)\n''',
    '''                canonical_payload = self._decode_row(dict(canonical)) if canonical else None\n                reservation_ok, reservation_reason = grant_reservation_decision(\n                    grant_payload, canonical_payload, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id,\n                    draft_id=draft_id, draft_revision=draft_revision, command_digest=command_digest,\n                )\n                if not reservation_ok:\n                    if grant_payload and str(grant_payload.get("state") or "").upper() == "ISSUED" and reservation_reason.startswith("reservation_canonical_Draft_missing"):\n                        self.conn.execute(\n                            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state='ISSUED'",\n                            (now, "draft_missing", grant_id),\n                        )\n                    self.conn.commit()\n                    return {"reserved": False, "grant": self._decode_row(dict(grant_row)) if grant_row else {}, "created": False, "attempt": {}}\n                if canonical_payload:\n                    committing_projection = dict(canonical_payload)\n''',
    label="sqlite reserve exact Grant Draft binding",
)
# The reason string above for a missing Draft is reservation_canonical_Draft_missing
# only after grant_reservation_decision reaches grant_issue_decision. Normalize
# the generic missing condition too so the fail-closed behavior remains stable.
replace_once(
    sqlite_path,
    'reservation_reason.startswith("reservation_canonical_Draft_missing")',
    '("Draft_missing" in reservation_reason)',
    label="sqlite missing Draft reason",
)

# terminal observation check is inserted after the pure Draft transition check
# and before the UPDATE is constructed.
replace_once(
    sqlite_path,
    '''            allowed, _reason = draft_persistence_update_decision(current, incoming)\n            if not allowed:\n                return\n            cols = ["draft_state=?", "updated_at=?"]\n''',
    '''            allowed, _reason = draft_persistence_update_decision(current, incoming)\n            if not allowed:\n                return\n            attempt_id_for_terminal = str(incoming.get("current_attempt_id") or current.get("current_attempt_id") or "")\n            attempt_row = self.conn.execute("SELECT * FROM transaction_attempts WHERE attempt_id=?", (attempt_id_for_terminal,)).fetchone() if attempt_id_for_terminal else None\n            attempt_payload = self._decode_row(dict(attempt_row)) if attempt_row else None\n            receipt_row = self.conn.execute("SELECT * FROM transaction_receipts WHERE attempt_id=?", (attempt_id_for_terminal,)).fetchone() if attempt_id_for_terminal else None\n            receipt_payload = self._decode_row(dict(receipt_row)) if receipt_row else None\n            terminal_ok, _terminal_reason = draft_terminal_observation_decision(\n                {**current, "current_attempt_id": attempt_id_for_terminal},\n                target_state=draft_state, attempt=attempt_payload, receipt=receipt_payload,\n            )\n            if not terminal_ok:\n                return\n            cols = ["draft_state=?", "updated_at=?"]\n''',
    label="sqlite Draft terminal Receipt guard",
)


# ---------------------------------------------------------------------------
# SQLAlchemy parity for issue/reserve/terminal observation.
# ---------------------------------------------------------------------------
sqla_path = "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py"
replace_once(
    sqla_path,
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, grant_consumption_decision, validate_receipt_binding\n",
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, draft_terminal_observation_decision, existing_attempt_matches_request, grant_consumption_decision, grant_issue_decision, grant_reservation_decision, validate_receipt_binding\n",
    label="sqlalchemy complete-chain imports",
)
replace_block(
    sqla_path,
    "    def issue_grant(self, **kwargs: Any) -> dict[str, Any]:\n",
    "    def get_grant(self, grant_id: str | None) -> dict[str, Any] | None:\n",
    '''    def issue_grant(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_grants"]
        drafts = self.t["transaction_drafts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        condition = self.sa.and_(
            table.c.tenant_id == kwargs["tenant_id"], table.c.user_id == kwargs["user_id"],
            table.c.thread_id == kwargs["thread_id"], table.c.draft_id == kwargs["draft_id"],
            table.c.draft_revision == int(kwargs["draft_revision"]), table.c.command_digest == kwargs["command_digest"],
            table.c.confirmation_id == kwargs["confirmation_id"],
        )
        with self.p.conn() as conn:
            draft_row = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(
                drafts.c.draft_id == kwargs["draft_id"], drafts.c.tenant_id == kwargs["tenant_id"],
                drafts.c.user_id == kwargs["user_id"], drafts.c.thread_id == kwargs["thread_id"],
            ))).first())
            draft = self._decode_row(draft_row) if draft_row else None
            allowed, reason = grant_issue_decision(
                draft, tenant_id=kwargs["tenant_id"], user_id=kwargs["user_id"], thread_id=kwargs["thread_id"],
                draft_id=kwargs["draft_id"], draft_revision=int(kwargs["draft_revision"]),
                command_digest=kwargs["command_digest"], confirmation_id=kwargs["confirmation_id"],
            )
            if not allowed:
                raise ValueError(f"canonical Draft rejected Grant issuance: {reason}")
            row = _row(conn.execute(self.sa.select(table).where(condition)).first())
            if row:
                return self._decode_row(row) or {}
            values = {**kwargs, "state": "ISSUED", "issued_at": _now(), "reserved_at": None, "consumed_at": None, "revoked_at": None, "attempt_id": None, "receipt_handle": None, "reason": None}
            conn.execute(table.insert().values(**values))
        return self.get_grant(kwargs["grant_id"]) or {}

''',
    label="sqlalchemy issue_grant canonical Draft",
)
replace_block(
    sqla_path,
    "    def reserve_grant(self, grant_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:\n",
    "    def reserve_grant_and_start_attempt(self, **kwargs: Any) -> dict[str, Any]:\n",
    '''    def reserve_grant(self, grant_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:
        """Compatibility surface; authority reservation is atomic with Attempt creation."""
        return {"reserved": False, "grant": self.get_grant(grant_id) or {}, "reason": "atomic_attempt_required"}

''',
    label="sqlalchemy standalone reserve fail closed",
)
replace_once(
    sqla_path,
    '''                if existing:\n                    grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": self._decode_row(existing) or {}}\n\n                canonical = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(\n''',
    '''                if existing:\n                    existing_payload = self._decode_row(existing) or {}\n                    grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                    if not existing_attempt_matches_request(\n                        existing_payload, grant_id=grant_id, tenant_id=kwargs["tenant_id"], user_id=kwargs["user_id"],\n                        thread_id=kwargs["thread_id"], draft_id=kwargs["draft_id"], draft_revision=int(kwargs["draft_revision"]),\n                        action_id=kwargs["action_id"], command_digest=kwargs["command_digest"],\n                    ):\n                        return {"reserved": False, "grant": {}, "created": False, "attempt": {}}\n                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": existing_payload}\n\n                grant_row = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                grant_payload = self._decode_row(grant_row) if grant_row else None\n                canonical = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(\n''',
    label="sqlalchemy reserve existing binding",
)
replace_once(
    sqla_path,
    '''                canonical_payload = self._decode_row(canonical) if canonical else None\n                if canonical_payload:\n                    committing_projection = dict(canonical_payload)\n''',
    '''                canonical_payload = self._decode_row(canonical) if canonical else None\n                reservation_ok, reservation_reason = grant_reservation_decision(\n                    grant_payload, canonical_payload, tenant_id=kwargs["tenant_id"], user_id=kwargs["user_id"],\n                    thread_id=kwargs["thread_id"], draft_id=kwargs["draft_id"], draft_revision=int(kwargs["draft_revision"]),\n                    command_digest=kwargs["command_digest"],\n                )\n                if not reservation_ok:\n                    if grant_payload and str(grant_payload.get("state") or "").upper() == "ISSUED" and "Draft_missing" in reservation_reason:\n                        conn.execute(\n                            grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED"))\n                            .values(state="REVOKED", revoked_at=now, reason="draft_missing")\n                        )\n                    return {"reserved": False, "grant": grant_payload or {}, "created": False, "attempt": {}}\n                if canonical_payload:\n                    committing_projection = dict(canonical_payload)\n''',
    label="sqlalchemy reserve Grant Draft binding",
)
replace_once(
    sqla_path,
    '''            allowed, _reason = draft_persistence_update_decision(current, incoming)\n            if not allowed:\n                return\n            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))\n''',
    '''            allowed, _reason = draft_persistence_update_decision(current, incoming)\n            if not allowed:\n                return\n            attempt_id_for_terminal = str(incoming.get("current_attempt_id") or current.get("current_attempt_id") or "")\n            attempts = self.t["transaction_attempts"]\n            receipts = self.t["transaction_receipts"]\n            attempt_row = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == attempt_id_for_terminal)).first()) if attempt_id_for_terminal else None\n            attempt_payload = self._decode_row(attempt_row) if attempt_row else None\n            receipt_row = _row(conn.execute(self.sa.select(receipts).where(receipts.c.attempt_id == attempt_id_for_terminal)).first()) if attempt_id_for_terminal else None\n            receipt_payload = self._decode_row(receipt_row) if receipt_row else None\n            terminal_ok, _terminal_reason = draft_terminal_observation_decision(\n                {**current, "current_attempt_id": attempt_id_for_terminal},\n                target_state=draft_state, attempt=attempt_payload, receipt=receipt_payload,\n            )\n            if not terminal_ok:\n                return\n            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))\n''',
    label="sqlalchemy Draft terminal Receipt guard",
)


# ---------------------------------------------------------------------------
# Storage-provider contract now exercises the complete Draft -> Grant -> Attempt
# path instead of relying on an orphan Grant compatibility shortcut.
# ---------------------------------------------------------------------------
storage_test = "services/agent-service/tests/transactions/test_transaction_storage.py"
replace_once(
    storage_test,
    '''        grant = provider.transactions.issue_grant(\n''',
    '''        provider.transactions.create_draft(\n            draft_id=draft_id,\n            tenant_id="tenant-a",\n            user_id="u001",\n            thread_id=thread_id,\n            draft_revision=1,\n            draft_state="AWAITING_AUTHORIZATION",\n            action_id="create_invoice",\n            command_digest="digest",\n            command_envelope=None,\n            projection={\n                "kind": "offer", "handle": draft_id, "draft_id": draft_id,\n                "draft_revision": 1, "draft_state": "AWAITING_AUTHORIZATION",\n                "action_id": "create_invoice", "command_digest": "digest",\n                "confirmation_id": confirmation_id, "confirmation_version": 1,\n            },\n        )\n        grant = provider.transactions.issue_grant(\n''',
    label="storage contract canonical Draft before Grant",
)


# ---------------------------------------------------------------------------
# Permanent complete-chain adversarial regressions. Round-5 probe behavior is
# preserved, plus SQLAlchemy parity and the fail-closed standalone reserve.
# ---------------------------------------------------------------------------
stage8_test = "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
text = read(stage8_test)
if "test_committed_draft_requires_durable_success_receipt" in text:
    raise SystemExit("Round 5 permanent tests already present")
round5_tests = r'''


def test_committed_draft_requires_durable_success_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "draft-success-receipt.db")
    offer, _state, _authority, attempt = _start_stage8_grant(store, suffix="draft-success")
    attempt_id = str(attempt["attempt_id"])
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTING"
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTING"
    result = {"success": True, "data": {"refund_id": "R-draft-success"}}
    store.record_receipt(
        receipt_id="receipt-draft-success", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:draft-success", receipt_state="SUCCESS", business_result=result,
    )
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"


def test_failed_inflight_draft_requires_durable_failed_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "draft-failed-receipt.db")
    offer, _state, _authority, attempt = _start_stage8_grant(store, suffix="draft-failed")
    attempt_id = str(attempt["attempt_id"])
    store.advance_draft(offer["draft_id"], draft_state="FAILED_FINAL", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTING"
    failure = {"success": False, "error": "business rejected", "code": 409}
    store.record_receipt(
        receipt_id="receipt-draft-failed", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:draft-failed", receipt_state="FAILED", business_result=failure,
    )
    store.advance_draft(offer["draft_id"], draft_state="FAILED_FINAL", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
    assert store.get_draft(offer["draft_id"])["draft_state"] == "FAILED_FINAL"


def test_pre_execution_failure_can_close_without_business_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "draft-preexecution.db")
    offer = _offer()
    _create(store, offer, state="AWAITING_AUTHORIZATION")
    store.advance_draft(offer["draft_id"], draft_state="FAILED_FINAL", draft_revision=offer["draft_revision"])
    assert store.get_draft(offer["draft_id"])["draft_state"] == "FAILED_FINAL"


def test_repository_grant_issue_requires_canonical_awaiting_draft(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-orphan.db")
    offer = _offer()
    with pytest.raises(ValueError, match="Draft"):
        store.issue_grant(
            grant_id="grant-orphan", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
            draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
            confirmation_id=offer["confirmation_id"], client_request_id="orphan", actor_id=SCOPE["user_id"], actor_role="customer",
        )
    assert store.get_grant("grant-orphan") is None


def test_standalone_reserve_grant_cannot_mutate_authority(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "standalone-reserve.db")
    offer = _offer()
    _create(store, offer, state="AWAITING_AUTHORIZATION")
    grant = store.issue_grant(
        grant_id="grant-standalone", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
        confirmation_id=offer["confirmation_id"], client_request_id="standalone", actor_id=SCOPE["user_id"], actor_role="customer",
    )
    assert grant["state"] == "ISSUED"
    result = store.reserve_grant("grant-standalone", attempt_id="attempt-not-atomic")
    assert result["reserved"] is False
    assert store.get_grant("grant-standalone")["state"] == "ISSUED"
    assert store.get_attempt("attempt-not-atomic") is None


def test_atomic_reservation_rejects_cross_bound_grant_request(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-cross-binding.db")
    offer = _offer()
    _create(store, offer, state="AWAITING_AUTHORIZATION")
    grant = store.issue_grant(
        grant_id="grant-cross", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
        confirmation_id=offer["confirmation_id"], client_request_id="cross", actor_id=SCOPE["user_id"], actor_role="customer",
    )
    result = store.reserve_grant_and_start_attempt(
        grant_id=grant["grant_id"], attempt_id="attempt-cross", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"],
        thread_id=SCOPE["thread_id"], draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id=offer["action_id"],
        command_digest="different-command", idempotency_key="idem-cross", canonical_payload={"action_id": offer["action_id"]},
    )
    assert result["reserved"] is False and result["created"] is False
    assert store.get_attempt("attempt-cross") is None
    assert store.get_grant(grant["grant_id"])["state"] == "ISSUED"


def test_sqlalchemy_complete_chain_guards_match_sqlite(tmp_path: Path) -> None:
    db_file = tmp_path / "complete-chain-sqla.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        offer = _offer()
        with pytest.raises(ValueError, match="Draft"):
            store.issue_grant(
                grant_id="grant-sqla-orphan", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
                draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
                confirmation_id=offer["confirmation_id"], client_request_id="sqla-orphan", actor_id=SCOPE["user_id"], actor_role="customer",
            )
        offer, _state, _authority, attempt = _start_stage8_grant(store, suffix="chain-sqla")
        attempt_id = str(attempt["attempt_id"])
        store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTING"
        result = {"success": True, "data": {"refund_id": "R-chain-sqla"}}
        store.record_receipt(
            receipt_id="receipt-chain-sqla", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
            draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:chain-sqla", receipt_state="SUCCESS", business_result=result,
        )
        store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    finally:
        provider.close()
'''
write(stage8_test, text + round5_tests)
