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
# Extend the shared deterministic persistence policy with Grant consumption.
# Transaction Repository remains the sole lifecycle authority; this helper only
# validates whether the canonical Grant may make RESERVED -> CONSUMED.
# ---------------------------------------------------------------------------
policy_path = "services/agent-service/src/agent_core/transaction/persistence_policy.py"
text = read(policy_path)
if "def grant_consumption_decision(" in text:
    raise SystemExit("grant consumption policy already present")
text += '''\n\ndef grant_consumption_decision(\n    grant: dict[str, Any] | None,\n    *,\n    attempt: dict[str, Any] | None,\n    receipt: dict[str, Any] | None,\n    attempt_id: str | None,\n    receipt_handle: str | None,\n) -> tuple[bool, str]:\n    """Validate the single legal Grant consumption boundary.\n\n    Consumption means a successful business effect is durably known.  The\n    exact Grant must therefore already own the exact ACKED Attempt and the\n    exact SUCCESS Receipt.  No caller may use ``consume_grant`` as a generic\n    state setter.\n    """\n    if not isinstance(grant, dict) or not grant:\n        return False, "grant_missing"\n    grant_state = str(grant.get("state") or "").upper()\n    requested_attempt = str(attempt_id or "")\n    requested_receipt = str(receipt_handle or "")\n\n    if grant_state == "CONSUMED":\n        if (\n            requested_attempt\n            and requested_receipt\n            and str(grant.get("attempt_id") or "") == requested_attempt\n            and str(grant.get("receipt_handle") or "") == requested_receipt\n        ):\n            return True, "already_consumed_same_binding"\n        return False, "consumed_grant_binding_immutable"\n\n    if grant_state != "RESERVED":\n        return False, f"grant_not_reserved:{grant_state or 'UNKNOWN'}"\n    if not requested_attempt:\n        return False, "consume_attempt_id_required"\n    if not requested_receipt:\n        return False, "consume_receipt_handle_required"\n    if str(grant.get("attempt_id") or "") != requested_attempt:\n        return False, "grant_attempt_binding_mismatch"\n\n    if not isinstance(attempt, dict) or not attempt:\n        return False, "consume_attempt_missing"\n    if str(attempt.get("attempt_id") or "") != requested_attempt:\n        return False, "consume_attempt_identity_mismatch"\n    if str(attempt.get("grant_id") or "") != str(grant.get("grant_id") or ""):\n        return False, "consume_attempt_grant_mismatch"\n    for field in ("tenant_id", "user_id", "thread_id", "draft_id"):\n        if str(attempt.get(field) or "") != str(grant.get(field) or ""):\n            return False, f"consume_attempt_scope_mismatch:{field}"\n    if int(attempt.get("draft_revision") or 0) != int(grant.get("draft_revision") or 0):\n        return False, "consume_attempt_revision_mismatch"\n    if str(attempt.get("command_digest") or "") != str(grant.get("command_digest") or ""):\n        return False, "consume_attempt_command_mismatch"\n    if str(attempt.get("state") or "").upper() != ATTEMPT_ACKED:\n        return False, "consume_attempt_not_acked"\n    if str(attempt.get("receipt_handle") or "") != requested_receipt:\n        return False, "consume_attempt_receipt_mismatch"\n\n    if not isinstance(receipt, dict) or not receipt:\n        return False, "consume_receipt_missing"\n    if str(receipt.get("attempt_id") or "") != requested_attempt:\n        return False, "consume_receipt_attempt_mismatch"\n    if str(receipt.get("receipt_handle") or "") != requested_receipt:\n        return False, "consume_receipt_handle_mismatch"\n    if str(receipt.get("receipt_state") or "").upper() != "SUCCESS":\n        return False, "consume_receipt_not_success"\n    for field in ("tenant_id", "user_id", "thread_id", "draft_id"):\n        if str(receipt.get(field) or "") != str(grant.get(field) or ""):\n            return False, f"consume_receipt_scope_mismatch:{field}"\n    result = receipt.get("business_result") if isinstance(receipt.get("business_result"), dict) else {}\n    if result.get("success") is not True:\n        return False, "consume_receipt_result_not_success"\n    return True, "grant_consumption_valid"\n'''
write(policy_path, text)


# ---------------------------------------------------------------------------
# SQLite consumes only a validated RESERVED Grant. Already-CONSUMED with the
# same binding is idempotent; conflicting late callers cannot rewrite binding.
# ---------------------------------------------------------------------------
sqlite_path = "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py"
replace_once(
    sqlite_path,
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, validate_receipt_binding\n",
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, grant_consumption_decision, validate_receipt_binding\n",
    label="sqlite grant policy import",
)
replace_block(
    sqlite_path,
    "    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:\n",
    "    def revoke_grant(self, grant_id: str, *, reason: str) -> None:\n",
    '''    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:
        with self.lock:
            grant_row = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
            grant = self._decode_row(dict(grant_row)) if grant_row else None
            attempt_row = self.conn.execute("SELECT * FROM transaction_attempts WHERE attempt_id=?", (str(attempt_id or ""),)).fetchone()
            attempt = self._decode_row(dict(attempt_row)) if attempt_row else None
            receipt_row = self.conn.execute("SELECT * FROM transaction_receipts WHERE attempt_id=?", (str(attempt_id or ""),)).fetchone()
            receipt = self._decode_row(dict(receipt_row)) if receipt_row else None
            allowed, reason = grant_consumption_decision(
                grant,
                attempt=attempt,
                receipt=receipt,
                attempt_id=attempt_id,
                receipt_handle=receipt_handle,
            )
            if not allowed:
                return
            if str((grant or {}).get("state") or "").upper() == "CONSUMED":
                return
            updated = self.conn.execute(
                """UPDATE transaction_grants SET state='CONSUMED', consumed_at=?, attempt_id=?, receipt_handle=?
                   WHERE grant_id=? AND state='RESERVED' AND attempt_id=?""",
                (_now(), str(attempt_id or ""), str(receipt_handle or ""), grant_id, str(attempt_id or "")),
            )
            if int(updated.rowcount or 0) != 1:
                self.conn.rollback()
                return
            self.conn.commit()

''',
    label="sqlite consume_grant policy",
)


# ---------------------------------------------------------------------------
# SQLAlchemy backend uses exactly the same validator and conditional mutation.
# ---------------------------------------------------------------------------
sqla_path = "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py"
replace_once(
    sqla_path,
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, validate_receipt_binding\n",
    "from agent_core.transaction.persistence_policy import attempt_persistence_update_decision, grant_consumption_decision, validate_receipt_binding\n",
    label="sqlalchemy grant policy import",
)
replace_block(
    sqla_path,
    "    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:\n",
    "    def revoke_grant(self, grant_id: str, *, reason: str) -> None:\n",
    '''    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:
        grants = self.t["transaction_grants"]
        attempts = self.t["transaction_attempts"]
        receipts = self.t["transaction_receipts"]
        requested_attempt = str(attempt_id or "")
        with self.p.conn() as conn:
            grant_row = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
            grant = self._decode_row(grant_row) if grant_row else None
            attempt_row = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == requested_attempt)).first()) if requested_attempt else None
            attempt = self._decode_row(attempt_row) if attempt_row else None
            receipt_row = _row(conn.execute(self.sa.select(receipts).where(receipts.c.attempt_id == requested_attempt)).first()) if requested_attempt else None
            receipt = self._decode_row(receipt_row) if receipt_row else None
            allowed, _reason = grant_consumption_decision(
                grant,
                attempt=attempt,
                receipt=receipt,
                attempt_id=attempt_id,
                receipt_handle=receipt_handle,
            )
            if not allowed:
                return
            if str((grant or {}).get("state") or "").upper() == "CONSUMED":
                return
            conn.execute(
                grants.update().where(self.sa.and_(
                    grants.c.grant_id == grant_id,
                    grants.c.state == "RESERVED",
                    grants.c.attempt_id == requested_attempt,
                )).values(
                    state="CONSUMED",
                    consumed_at=_now(),
                    attempt_id=requested_attempt,
                    receipt_handle=str(receipt_handle or ""),
                )
            )

''',
    label="sqlalchemy consume_grant policy",
)


# ---------------------------------------------------------------------------
# Permanent Grant adversarial regression coverage, including backend parity.
# ---------------------------------------------------------------------------
test_path = "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
text = read(test_path)
if "test_revoked_grant_cannot_be_consumed" in text:
    raise SystemExit("Round 4 permanent tests already present")
round4_tests = r'''


def _start_stage8_grant(store, *, suffix: str):
    offer = _offer()
    _create(store, offer, state="AWAITING_AUTHORIZATION")
    state, authority, attempt = _start_transaction_attempt(store, offer, client_request_id=f"grant-{suffix}")
    return offer, state, authority, attempt


def _complete_stage8_success(store, offer: dict, attempt: dict, *, suffix: str) -> dict:
    attempt_id = str(attempt["attempt_id"])
    result = {"success": True, "data": {"refund_id": f"R-{suffix}"}}
    handle = f"h_receipt:{suffix}"
    receipt = store.record_receipt(
        receipt_id=f"receipt-{suffix}",
        tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle=handle,
        receipt_state="SUCCESS", business_result=result, business_resource_id=f"R-{suffix}",
    )
    store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle=handle)
    return receipt


def test_revoked_grant_cannot_be_consumed(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-revoked.db")
    offer = _offer()
    state = {"current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"], "current_thread_id": SCOPE["thread_id"], "_transaction_repository": store}
    authority = issue_grant_for_authority(
        state=state, offer=offer,
        authority={"actor_id": SCOPE["user_id"], "actor_role": "customer", "client_request_id": "grant-revoked", "authority_type": "ui_confirmed"},
    )
    grant_id = str(authority["grant_id"])
    store.revoke_grant(grant_id, reason="cancelled")
    store.consume_grant(grant_id, attempt_id="attempt-late", receipt_handle="h_receipt:late")
    grant = store.get_grant(grant_id)
    assert grant is not None
    assert grant["state"] == "REVOKED"
    assert not grant.get("consumed_at")


def test_issued_grant_cannot_skip_attempt_and_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-issued.db")
    offer = _offer()
    state = {"current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"], "current_thread_id": SCOPE["thread_id"], "_transaction_repository": store}
    authority = issue_grant_for_authority(
        state=state, offer=offer,
        authority={"actor_id": SCOPE["user_id"], "actor_role": "customer", "client_request_id": "grant-issued", "authority_type": "ui_confirmed"},
    )
    grant_id = str(authority["grant_id"])
    store.consume_grant(grant_id, attempt_id="attempt-never-created", receipt_handle="h_receipt:none")
    grant = store.get_grant(grant_id)
    assert grant is not None
    assert grant["state"] == "ISSUED"
    assert not grant.get("consumed_at")


def test_reserved_grant_requires_exact_acked_attempt_and_success_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-binding.db")
    offer, _state, authority, attempt = _start_stage8_grant(store, suffix="binding")
    grant_id = str(authority["grant_id"])
    attempt_id = str(attempt["attempt_id"])
    assert store.get_grant(grant_id)["state"] == "RESERVED"

    store.consume_grant(grant_id, attempt_id="attempt-wrong", receipt_handle="h_receipt:wrong")
    assert store.get_grant(grant_id)["state"] == "RESERVED"

    receipt = _complete_stage8_success(store, offer, attempt, suffix="binding")
    store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle="h_receipt:wrong")
    assert store.get_grant(grant_id)["state"] == "RESERVED"

    store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle=str(receipt["receipt_handle"]))
    grant = store.get_grant(grant_id)
    assert grant is not None
    assert grant["state"] == "CONSUMED"
    assert grant["attempt_id"] == attempt_id
    assert grant["receipt_handle"] == receipt["receipt_handle"]


def test_consumed_grant_is_idempotent_and_binding_immutable(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-consumed.db")
    offer, _state, authority, attempt = _start_stage8_grant(store, suffix="consumed")
    grant_id = str(authority["grant_id"])
    attempt_id = str(attempt["attempt_id"])
    receipt = _complete_stage8_success(store, offer, attempt, suffix="consumed")
    handle = str(receipt["receipt_handle"])
    store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle=handle)
    first = dict(store.get_grant(grant_id) or {})
    store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle=handle)
    second = dict(store.get_grant(grant_id) or {})
    assert second["state"] == "CONSUMED"
    assert second["attempt_id"] == first["attempt_id"] == attempt_id
    assert second["receipt_handle"] == first["receipt_handle"] == handle

    store.consume_grant(grant_id, attempt_id="attempt-other", receipt_handle="h_receipt:other")
    final = store.get_grant(grant_id)
    assert final is not None
    assert final["state"] == "CONSUMED"
    assert final["attempt_id"] == attempt_id
    assert final["receipt_handle"] == handle


def test_reserved_grant_cannot_consume_before_receipt_and_ack(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "grant-ordering.db")
    offer, _state, authority, attempt = _start_stage8_grant(store, suffix="ordering")
    grant_id = str(authority["grant_id"])
    attempt_id = str(attempt["attempt_id"])
    store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle="h_receipt:not-yet")
    assert store.get_grant(grant_id)["state"] == "RESERVED"
    receipt = _complete_stage8_success(store, offer, attempt, suffix="ordering")
    store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle=str(receipt["receipt_handle"]))
    assert store.get_grant(grant_id)["state"] == "CONSUMED"


def test_sqlalchemy_grant_consumption_enforces_exact_binding(tmp_path: Path) -> None:
    db_file = tmp_path / "grant-binding-sqla.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        offer, _state, authority, attempt = _start_stage8_grant(store, suffix="sqla")
        grant_id = str(authority["grant_id"])
        attempt_id = str(attempt["attempt_id"])
        store.consume_grant(grant_id, attempt_id="attempt-wrong", receipt_handle="h_receipt:wrong")
        assert store.get_grant(grant_id)["state"] == "RESERVED"
        receipt = _complete_stage8_success(store, offer, attempt, suffix="sqla")
        store.consume_grant(grant_id, attempt_id=attempt_id, receipt_handle=str(receipt["receipt_handle"]))
        consumed = store.get_grant(grant_id)
        assert consumed is not None
        assert consumed["state"] == "CONSUMED"
        assert consumed["attempt_id"] == attempt_id
        store.consume_grant(grant_id, attempt_id="attempt-other", receipt_handle="h_receipt:other")
        final = store.get_grant(grant_id)
        assert final is not None
        assert final["attempt_id"] == attempt_id
        assert final["receipt_handle"] == receipt["receipt_handle"]
    finally:
        provider.close()
'''
write(test_path, text + round4_tests)
