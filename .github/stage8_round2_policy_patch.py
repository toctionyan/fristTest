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


# ---------------------------------------------------------------------------
# Pure lifecycle policy.  The repository remains the authority: this helper
# only decides whether an attempted mutation is a legal update to the same
# immutable Draft identity/revision.
# ---------------------------------------------------------------------------
draft_path = "services/agent-service/src/agent_core/operations/draft.py"
anchor = '''TERMINAL_DRAFT_STATES = {\n    DRAFT_COMMITTED,\n    DRAFT_FAILED_FINAL,\n    DRAFT_EXPIRED,\n    DRAFT_REVOKED,\n    DRAFT_REQUIRES_REVIEW,\n}\n\n'''
policy = anchor + '''# Persistence sealing is intentionally narrower than presentation/focus\n# terminality. REQUIRES_REVIEW is read-only for execution but remains\n# explicitly cancellable, so the repository must permit REQUIRES_REVIEW ->\n# REVOKED while never permitting a successful/failed/revoked/expired Draft to\n# be resurrected.\nSEALED_DRAFT_STATES = {\n    DRAFT_COMMITTED,\n    DRAFT_FAILED_FINAL,\n    DRAFT_EXPIRED,\n    DRAFT_REVOKED,\n}\n\n_IN_FLIGHT_DRAFT_STATES = {DRAFT_COMMITTING, DRAFT_SUBMISSION_UNKNOWN}\n\n_ALLOWED_SAME_REVISION_TRANSITIONS: dict[str, set[str]] = {\n    DRAFT_NEEDS_INPUT: {\n        DRAFT_NEEDS_INPUT, DRAFT_READY, DRAFT_AWAITING_AUTHORIZATION,\n        DRAFT_FAILED_FINAL, DRAFT_EXPIRED, DRAFT_REVOKED, DRAFT_REQUIRES_REVIEW,\n    },\n    DRAFT_READY: {\n        DRAFT_READY, DRAFT_NEEDS_INPUT, DRAFT_AWAITING_AUTHORIZATION,\n        DRAFT_FAILED_FINAL, DRAFT_EXPIRED, DRAFT_REVOKED, DRAFT_REQUIRES_REVIEW,\n    },\n    DRAFT_AWAITING_AUTHORIZATION: {\n        DRAFT_AWAITING_AUTHORIZATION, DRAFT_AUTHORIZED, DRAFT_COMMITTING,\n        DRAFT_FAILED_FINAL, DRAFT_EXPIRED, DRAFT_REVOKED, DRAFT_REQUIRES_REVIEW,\n    },\n    DRAFT_AUTHORIZED: {\n        DRAFT_AUTHORIZED, DRAFT_AWAITING_AUTHORIZATION, DRAFT_COMMITTING,\n        DRAFT_FAILED_FINAL, DRAFT_EXPIRED, DRAFT_REVOKED, DRAFT_REQUIRES_REVIEW,\n    },\n    DRAFT_COMMITTING: {\n        DRAFT_COMMITTING, DRAFT_SUBMISSION_UNKNOWN, DRAFT_COMMITTED,\n        DRAFT_FAILED_RETRYABLE, DRAFT_FAILED_FINAL,\n    },\n    DRAFT_SUBMISSION_UNKNOWN: {\n        DRAFT_SUBMISSION_UNKNOWN, DRAFT_COMMITTED, DRAFT_FAILED_RETRYABLE, DRAFT_FAILED_FINAL,\n    },\n    DRAFT_FAILED_RETRYABLE: {\n        DRAFT_FAILED_RETRYABLE, DRAFT_SUBMISSION_UNKNOWN, DRAFT_COMMITTED, DRAFT_FAILED_FINAL,\n    },\n    DRAFT_REQUIRES_REVIEW: {DRAFT_REQUIRES_REVIEW, DRAFT_REVOKED, DRAFT_EXPIRED},\n    DRAFT_COMMITTED: {DRAFT_COMMITTED},\n    DRAFT_FAILED_FINAL: {DRAFT_FAILED_FINAL},\n    DRAFT_EXPIRED: {DRAFT_EXPIRED},\n    DRAFT_REVOKED: {DRAFT_REVOKED},\n}\n\n\ndef _int_field(row: dict[str, Any], name: str) -> int:\n    try:\n        return int(row.get(name) or 0)\n    except (TypeError, ValueError):\n        return 0\n\n\ndef _projection(row: dict[str, Any] | None) -> dict[str, Any]:\n    if not isinstance(row, dict):\n        return {}\n    value = row.get("projection")\n    if isinstance(value, dict):\n        return value\n    return row\n\n\ndef _interaction_projection_is_fresh(\n    *, current_state: str, current: dict[str, Any], incoming: dict[str, Any]\n) -> tuple[bool, str]:\n    current_projection = _projection(current)\n    incoming_projection = _projection(incoming)\n    # An update without projection metadata cannot prove that a newer card/form\n    # is being replaced, so state-only repository transitions remain valid.\n    if not incoming_projection:\n        return True, "state_only_update"\n\n    current_turn = _int_field(current_projection, "updated_turn")\n    incoming_turn = _int_field(incoming_projection, "updated_turn")\n    if current_turn and incoming_turn and incoming_turn < current_turn:\n        return False, "projection_turn_regression"\n\n    if current_state == DRAFT_AWAITING_AUTHORIZATION:\n        current_version = _int_field(current_projection, "confirmation_version")\n        incoming_version = _int_field(incoming_projection, "confirmation_version")\n        current_revision = _int_field(current_projection, "authority_revision")\n        incoming_revision = _int_field(incoming_projection, "authority_revision")\n        if current_version and incoming_version < current_version:\n            return False, "confirmation_version_regression"\n        if current_revision and incoming_revision < current_revision:\n            return False, "authority_revision_regression"\n        current_id = str(current_projection.get("confirmation_id") or "")\n        incoming_id = str(incoming_projection.get("confirmation_id") or "")\n        if current_version and incoming_version == current_version and current_id and incoming_id and current_id != incoming_id:\n            return False, "confirmation_identity_conflict"\n\n    if current_state == DRAFT_NEEDS_INPUT:\n        current_version = _int_field(current_projection, "input_form_version")\n        incoming_version = _int_field(incoming_projection, "input_form_version")\n        current_revision = _int_field(current_projection, "interaction_revision")\n        incoming_revision = _int_field(incoming_projection, "interaction_revision")\n        if current_version and incoming_version < current_version:\n            return False, "input_form_version_regression"\n        if current_revision and incoming_revision < current_revision:\n            return False, "interaction_revision_regression"\n        current_id = str(current_projection.get("input_form_id") or "")\n        incoming_id = str(incoming_projection.get("input_form_id") or "")\n        if current_version and incoming_version == current_version and current_id and incoming_id and current_id != incoming_id:\n            return False, "input_form_identity_conflict"\n\n    return True, "projection_fresh"\n\n\ndef draft_persistence_update_decision(\n    current: dict[str, Any] | None, incoming: dict[str, Any]\n) -> tuple[bool, str]:\n    """Validate one proposed mutation of the canonical persisted Draft.\n\n    This function does not own state and performs no persistence. It is a pure\n    transition contract consumed by every repository backend so SQLite and\n    SQLAlchemy cannot develop competing lifecycle semantics.\n    """\n    if not isinstance(current, dict) or not current:\n        return True, "new_draft"\n\n    for field in ("draft_id", "tenant_id", "user_id", "thread_id", "action_id"):\n        old = str(current.get(field) or "")\n        new = str(incoming.get(field) or "")\n        if old and new and old != new:\n            return False, f"identity_mismatch:{field}"\n\n    current_state = draft_state_for_offer(current)\n    incoming_state = draft_state_for_offer(incoming)\n    current_revision = max(1, int(current.get("draft_revision") or 1))\n    incoming_revision = max(1, int(incoming.get("draft_revision") or current_revision))\n\n    if current_state in SEALED_DRAFT_STATES:\n        return False, "sealed_draft"\n    if incoming_revision < current_revision:\n        return False, "draft_revision_regression"\n\n    current_digest = str(current.get("command_digest") or "")\n    incoming_digest = str(incoming.get("command_digest") or current_digest)\n    if incoming_revision == current_revision and current_digest and incoming_digest and incoming_digest != current_digest:\n        return False, "command_digest_changed_without_revision"\n\n    if incoming_revision > current_revision:\n        if current_state in _IN_FLIGHT_DRAFT_STATES:\n            return False, "revision_change_while_in_flight"\n        if current_state == DRAFT_REQUIRES_REVIEW:\n            return False, "review_draft_requires_new_identity"\n        if incoming_state not in {\n            DRAFT_NEEDS_INPUT, DRAFT_READY, DRAFT_AWAITING_AUTHORIZATION,\n            DRAFT_FAILED_FINAL, DRAFT_EXPIRED, DRAFT_REVOKED, DRAFT_REQUIRES_REVIEW,\n        }:\n            return False, "new_revision_must_reenter_pre_execution_boundary"\n        return True, "new_revision"\n\n    allowed = _ALLOWED_SAME_REVISION_TRANSITIONS.get(current_state, {current_state})\n    if incoming_state not in allowed:\n        return False, f"illegal_state_transition:{current_state}->{incoming_state}"\n    if incoming_state == current_state:\n        return _interaction_projection_is_fresh(\n            current_state=current_state, current=current, incoming=incoming\n        )\n    return True, "legal_state_transition"\n\n'''
replace_once(draft_path, anchor, policy, label="draft lifecycle policy")

# ---------------------------------------------------------------------------
# SQLite repository consumes the one policy for create/update/reservation.
# ---------------------------------------------------------------------------
sqlite_path = "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py"
replace_once(
    sqlite_path,
    "from agent_core.operations.draft import TERMINAL_DRAFT_STATES\n",
    "from agent_core.operations.draft import draft_persistence_update_decision\n",
    label="sqlite policy import",
)
replace_once(
    sqlite_path,
    '''            existing_payload = self._decode_row(dict(existing)) if existing else None\n            if existing_payload:\n                existing_state = str(existing_payload.get("draft_state") or "").upper()\n                existing_revision = int(existing_payload.get("draft_revision") or 0)\n                if existing_state in TERMINAL_DRAFT_STATES or existing_revision > int(draft_revision):\n                    return existing_payload\n            data = (\n''',
    '''            existing_payload = self._decode_row(dict(existing)) if existing else None\n            incoming_payload = {\n                "draft_id": draft_id, "tenant_id": tenant_id, "user_id": user_id, "thread_id": thread_id,\n                "draft_revision": int(draft_revision), "draft_state": draft_state, "action_id": action_id,\n                "command_digest": command_digest, "command_envelope": command_envelope, "projection": projection,\n            }\n            allowed, _reason = draft_persistence_update_decision(existing_payload, incoming_payload)\n            if existing_payload and not allowed:\n                return existing_payload\n            data = (\n''',
    label="sqlite create policy",
)
replace_once(
    sqlite_path,
    '''            current = self._decode_row(dict(existing)) or {}\n            if str(current.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:\n                return\n            if draft_revision is not None and int(current.get("draft_revision") or 0) > int(draft_revision):\n                return\n            cols = ["draft_state=?", "updated_at=?"]\n''',
    '''            current = self._decode_row(dict(existing)) or {}\n            incoming = dict(current)\n            incoming["draft_state"] = draft_state\n            if draft_revision is not None: incoming["draft_revision"] = int(draft_revision)\n            if command_digest is not None: incoming["command_digest"] = command_digest\n            if command_envelope is not None: incoming["command_envelope"] = command_envelope\n            if projection is not None: incoming["projection"] = projection\n            allowed, _reason = draft_persistence_update_decision(current, incoming)\n            if not allowed:\n                return\n            cols = ["draft_state=?", "updated_at=?"]\n''',
    label="sqlite advance policy",
)
replace_once(
    sqlite_path,
    '''                canonical_payload = self._decode_row(dict(canonical)) if canonical else None\n                if canonical_payload:\n                    canonical_state = str(canonical_payload.get("draft_state") or "").upper()\n                    snapshot_mismatch = (\n                        int(canonical_payload.get("draft_revision") or 0) != int(draft_revision)\n                        or str(canonical_payload.get("command_digest") or "") != str(command_digest or "")\n                    )\n                    if canonical_state in TERMINAL_DRAFT_STATES or snapshot_mismatch:\n                        reason = "draft_terminal" if canonical_state in TERMINAL_DRAFT_STATES else "draft_snapshot_mismatch"\n                        self.conn.execute(\n                            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state='ISSUED'",\n                            (now, reason, grant_id),\n                        )\n                        grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                        self.conn.commit()\n                        return {\n                            "reserved": False,\n                            "grant": self._decode_row(dict(grant) if grant else None) or {},\n                            "created": False,\n                            "attempt": {},\n                        }\n\n''',
    '''                canonical_payload = self._decode_row(dict(canonical)) if canonical else None\n                if canonical_payload:\n                    committing_projection = dict(canonical_payload)\n                    committing_projection.update({\n                        "draft_state": "COMMITTING",\n                        "draft_revision": int(draft_revision),\n                        "command_digest": command_digest,\n                    })\n                    if draft_projection is not None:\n                        committing_projection["projection"] = draft_projection\n                    allowed, reason = draft_persistence_update_decision(canonical_payload, committing_projection)\n                    if not allowed:\n                        self.conn.execute(\n                            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state='ISSUED'",\n                            (now, "draft_update_rejected:" + reason, grant_id),\n                        )\n                        grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()\n                        self.conn.commit()\n                        return {\n                            "reserved": False,\n                            "grant": self._decode_row(dict(grant) if grant else None) or {},\n                            "created": False,\n                            "attempt": {},\n                        }\n\n''',
    label="sqlite reserve policy",
)

# ---------------------------------------------------------------------------
# SQLAlchemy repository consumes the exact same pure policy.
# ---------------------------------------------------------------------------
sqla_path = "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py"
replace_once(
    sqla_path,
    "from agent_core.operations.draft import TERMINAL_DRAFT_STATES\n",
    "from agent_core.operations.draft import draft_persistence_update_decision\n",
    label="sqlalchemy policy import",
)
replace_once(
    sqla_path,
    '''                canonical_payload = self._decode_row(canonical) if canonical else None\n                if canonical_payload:\n                    canonical_state = str(canonical_payload.get("draft_state") or "").upper()\n                    snapshot_mismatch = (\n                        int(canonical_payload.get("draft_revision") or 0) != int(kwargs["draft_revision"])\n                        or str(canonical_payload.get("command_digest") or "") != str(kwargs["command_digest"] or "")\n                    )\n                    if canonical_state in TERMINAL_DRAFT_STATES or snapshot_mismatch:\n                        reason = "draft_terminal" if canonical_state in TERMINAL_DRAFT_STATES else "draft_snapshot_mismatch"\n                        conn.execute(\n                            grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED"))\n                            .values(state="REVOKED", revoked_at=now, reason=reason)\n                        )\n                        grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                        return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}\n\n''',
    '''                canonical_payload = self._decode_row(canonical) if canonical else None\n                if canonical_payload:\n                    committing_projection = dict(canonical_payload)\n                    committing_projection.update({\n                        "draft_state": "COMMITTING",\n                        "draft_revision": int(kwargs["draft_revision"]),\n                        "command_digest": kwargs["command_digest"],\n                    })\n                    if kwargs.get("draft_projection") is not None:\n                        committing_projection["projection"] = kwargs.get("draft_projection")\n                    allowed, reason = draft_persistence_update_decision(canonical_payload, committing_projection)\n                    if not allowed:\n                        conn.execute(\n                            grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED"))\n                            .values(state="REVOKED", revoked_at=now, reason="draft_update_rejected:" + reason)\n                        )\n                        grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())\n                        return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}\n\n''',
    label="sqlalchemy reserve policy",
)
replace_once(
    sqla_path,
    '''            if existing:\n                existing_payload = self._decode_row(existing) or {}\n                existing_state = str(existing_payload.get("draft_state") or "").upper()\n                existing_revision = int(existing_payload.get("draft_revision") or 0)\n                incoming_revision = int(values.get("draft_revision") or 0)\n                if existing_state in TERMINAL_DRAFT_STATES or existing_revision > incoming_revision:\n                    return existing_payload\n                conn.execute(table.update().where(table.c.draft_id == values["draft_id"]).values(**{k:v for k,v in values.items() if k not in {"draft_id", "created_at"}}))\n''',
    '''            if existing:\n                existing_payload = self._decode_row(existing) or {}\n                incoming_payload = {\n                    **kwargs,\n                    "command_envelope": command_envelope,\n                    "projection": projection,\n                }\n                allowed, _reason = draft_persistence_update_decision(existing_payload, incoming_payload)\n                if not allowed:\n                    return existing_payload\n                conn.execute(table.update().where(table.c.draft_id == values["draft_id"]).values(**{k:v for k,v in values.items() if k not in {"draft_id", "created_at"}}))\n''',
    label="sqlalchemy create policy",
)
replace_once(
    sqla_path,
    '''            current = self._decode_row(existing) or {}\n            if str(current.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:\n                return\n            if draft_revision is not None and int(current.get("draft_revision") or 0) > int(draft_revision):\n                return\n            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))\n''',
    '''            current = self._decode_row(existing) or {}\n            incoming = dict(current)\n            incoming["draft_state"] = draft_state\n            if draft_revision is not None: incoming["draft_revision"] = int(draft_revision)\n            if command_digest is not None: incoming["command_digest"] = command_digest\n            if command_envelope is not None: incoming["command_envelope"] = command_envelope\n            if projection is not None: incoming["projection"] = projection\n            allowed, _reason = draft_persistence_update_decision(current, incoming)\n            if not allowed:\n                return\n            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))\n''',
    label="sqlalchemy advance policy",
)

# ---------------------------------------------------------------------------
# Extend the permanent Stage 8 adversarial suite with the RED round-2 cases.
# ---------------------------------------------------------------------------
test_path = "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
text = read(test_path)
append = r'''


def _challenge_offer(*, confirmation_id: str, confirmation_version: int, authority_revision: int) -> dict:
    row = offer_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle="artifact:order:10002",
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
        scope=SCOPE,
        turn=authority_revision,
        label="退款申请",
        handle="draft:refund:stage8-challenge",
    )
    row = transition_draft(row, "AWAITING_AUTHORIZATION")
    row["authority_protocol"] = "ui-authority@1"
    row["authority_requirement"] = "ui_action_authority"
    row["authority_revision"] = authority_revision
    row["confirmation_id"] = confirmation_id
    row["confirmation_version"] = confirmation_version
    row["updated_turn"] = authority_revision
    return row


def test_same_revision_old_authority_challenge_cannot_replace_newer_one(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    newest = _challenge_offer(confirmation_id="confirm-new", confirmation_version=2, authority_revision=10)
    stale = _challenge_offer(confirmation_id="confirm-old", confirmation_version=1, authority_revision=9)
    _create(store, newest)
    _create(store, stale)
    durable = store.get_draft(newest["draft_id"])
    assert durable is not None
    assert durable["projection"]["confirmation_id"] == "confirm-new"
    assert durable["projection"]["confirmation_version"] == 2
    assert durable["projection"]["authority_revision"] == 10


def test_same_revision_committing_cannot_regress_to_awaiting_or_ready(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    _create(store, card)
    store.advance_draft(card["draft_id"], draft_state="COMMITTING", draft_revision=card["draft_revision"], current_attempt_id="attempt-round2")
    assert store.get_draft(card["draft_id"])["draft_state"] == "COMMITTING"
    _create(store, card, state="AWAITING_AUTHORIZATION")
    store.advance_draft(card["draft_id"], draft_state="READY", draft_revision=card["draft_revision"])
    durable = store.get_draft(card["draft_id"])
    assert durable is not None
    assert durable["draft_state"] == "COMMITTING"
    assert durable["current_attempt_id"] == "attempt-round2"


def test_same_revision_effect_digest_change_is_rejected(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    _create(store, card)
    store.advance_draft(card["draft_id"], draft_state="READY", draft_revision=card["draft_revision"], command_digest="tampered-digest")
    durable = store.get_draft(card["draft_id"])
    assert durable is not None
    assert durable["command_digest"] == card["command_digest"]
    assert durable["draft_state"] == "AWAITING_AUTHORIZATION"


def test_new_revision_cannot_replace_inflight_attempt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    _create(store, card)
    store.advance_draft(card["draft_id"], draft_state="COMMITTING", draft_revision=card["draft_revision"], current_attempt_id="attempt-round2")
    newer = dict(card)
    newer["draft_revision"] = int(card["draft_revision"]) + 1
    newer["command_digest"] = "new-effect-digest"
    newer["draft_state"] = "AWAITING_AUTHORIZATION"
    store.create_draft(
        draft_id=newer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_revision=newer["draft_revision"], draft_state=newer["draft_state"], action_id=newer["action_id"],
        command_digest=newer["command_digest"], command_envelope=None, projection=newer,
    )
    durable = store.get_draft(card["draft_id"])
    assert durable is not None
    assert durable["draft_revision"] == card["draft_revision"]
    assert durable["draft_state"] == "COMMITTING"
    assert durable["current_attempt_id"] == "attempt-round2"


def test_newer_needs_input_form_cannot_be_replaced_by_stale_form(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    base = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle="artifact:order:10002",
        input_values={}, preview={"decision": "NEEDS_INPUT"}, scope=SCOPE, turn=9,
        label="退款申请", handle="draft:refund:stage8-form",
    )
    newer = transition_draft(base, "NEEDS_INPUT")
    newer.update({"input_form_id": "form-new", "input_form_version": 3, "interaction_revision": 9, "updated_turn": 9})
    stale = dict(newer)
    stale.update({"input_form_id": "form-old", "input_form_version": 2, "interaction_revision": 8, "updated_turn": 8})
    _create(store, newer)
    _create(store, stale)
    durable = store.get_draft(newer["draft_id"])
    assert durable is not None
    assert durable["projection"]["input_form_id"] == "form-new"
    assert durable["projection"]["input_form_version"] == 3


def test_requires_review_can_be_explicitly_revoked(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    card = transition_draft(card, "REQUIRES_REVIEW")
    _create(store, card, state="REQUIRES_REVIEW")
    store.advance_draft(card["draft_id"], draft_state="REVOKED", draft_revision=card["draft_revision"])
    assert store.get_draft(card["draft_id"])["draft_state"] == "REVOKED"


def test_sqlalchemy_same_revision_nonterminal_regressions_are_rejected(tmp_path: Path) -> None:
    db_file = tmp_path / "stage8-round2-sqla.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        newest = _challenge_offer(confirmation_id="confirm-new", confirmation_version=2, authority_revision=10)
        stale = _challenge_offer(confirmation_id="confirm-old", confirmation_version=1, authority_revision=9)
        _create(store, newest)
        _create(store, stale)
        assert store.get_draft(newest["draft_id"])["projection"]["confirmation_id"] == "confirm-new"
        store.advance_draft(newest["draft_id"], draft_state="COMMITTING", draft_revision=newest["draft_revision"], current_attempt_id="attempt-sqla")
        _create(store, stale, state="AWAITING_AUTHORIZATION")
        store.advance_draft(newest["draft_id"], draft_state="READY", draft_revision=newest["draft_revision"])
        durable = store.get_draft(newest["draft_id"])
        assert durable["draft_state"] == "COMMITTING"
        assert durable["current_attempt_id"] == "attempt-sqla"
    finally:
        provider.close()
'''
if "test_same_revision_old_authority_challenge_cannot_replace_newer_one" in text:
    raise SystemExit("Stage 8 round-2 tests already present")
write(test_path, text + append)
