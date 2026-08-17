from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    text = _read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {count}")
    _write(path, text.replace(old, new))


def replace_block(path: str, start: str, end: str, new_block: str, *, label: str) -> None:
    text = _read(path)
    if text.count(start) != 1:
        raise SystemExit(f"{label} start anchor mismatch: {text.count(start)}")
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    _write(path, text[:start_index] + new_block + text[end_index:])


# === SQLite canonical transaction repository =================================
sqlite_path = "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py"
replace_once(
    sqlite_path,
    "from agent_core.persistence.sqlite_base import SQLiteBase\n",
    "from agent_core.persistence.sqlite_base import SQLiteBase\nfrom agent_core.operations.draft import TERMINAL_DRAFT_STATES\n",
    label="sqlite terminal import",
)

replace_block(
    sqlite_path,
    "    def create_draft(self, *, draft_id: str, tenant_id: str, user_id: str, thread_id: str, draft_revision: int, draft_state: str, action_id: str, command_digest: str, command_envelope: dict[str, Any] | None, projection: dict[str, Any] | None, active_grant_id: str | None = None, current_attempt_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:\n",
    "    def get_draft(self, draft_id: str | None) -> dict[str, Any] | None:\n",
    '''    def create_draft(self, *, draft_id: str, tenant_id: str, user_id: str, thread_id: str, draft_revision: int, draft_state: str, action_id: str, command_digest: str, command_envelope: dict[str, Any] | None, projection: dict[str, Any] | None, active_grant_id: str | None = None, current_attempt_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        now = _now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            existing_payload = self._decode_row(dict(existing)) if existing else None
            if existing_payload:
                existing_state = str(existing_payload.get("draft_state") or "").upper()
                existing_revision = int(existing_payload.get("draft_revision") or 0)
                if existing_state in TERMINAL_DRAFT_STATES or existing_revision > int(draft_revision):
                    return existing_payload
            data = (
                tenant_id, user_id, thread_id, int(draft_revision), draft_state, action_id, command_digest,
                _json(command_envelope) if command_envelope is not None else None,
                _json(projection) if projection is not None else None,
                active_grant_id, current_attempt_id, correlation_id, now,
            )
            if existing:
                self.conn.execute(
                    """UPDATE transaction_drafts SET tenant_id=?,user_id=?,thread_id=?,draft_revision=?,draft_state=?,action_id=?,command_digest=?,command_envelope_json=?,projection_json=?,active_grant_id=?,current_attempt_id=?,correlation_id=?,updated_at=? WHERE draft_id=?""",
                    (*data, draft_id),
                )
            else:
                self.conn.execute(
                    """INSERT INTO transaction_drafts(draft_id,tenant_id,user_id,thread_id,draft_revision,draft_state,action_id,command_digest,command_envelope_json,projection_json,active_grant_id,current_attempt_id,correlation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (draft_id, *data, now),
                )
            self.conn.commit()
        return self.get_draft(draft_id) or {}

''',
    label="sqlite create_draft",
)

replace_block(
    sqlite_path,
    "    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, projection: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None) -> None:\n",
    "    def list_drafts_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:\n",
    '''    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, projection: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None) -> None:
        with self.lock:
            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if not existing:
                return
            current = self._decode_row(dict(existing)) or {}
            if str(current.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:
                return
            if draft_revision is not None and int(current.get("draft_revision") or 0) > int(draft_revision):
                return
            cols = ["draft_state=?", "updated_at=?"]
            vals: list[Any] = [draft_state, _now()]
            for name, value in (
                ("draft_revision", draft_revision),
                ("command_digest", command_digest),
                ("command_envelope_json", _json(command_envelope) if command_envelope is not None else None),
                ("projection_json", _json(projection) if projection is not None else None),
                ("active_grant_id", active_grant_id),
                ("current_attempt_id", current_attempt_id),
            ):
                if value is not None:
                    cols.append(f"{name}=?")
                    vals.append(value)
            vals.append(draft_id)
            self.conn.execute(f"UPDATE transaction_drafts SET {', '.join(cols)} WHERE draft_id=?", tuple(vals))
            self.conn.commit()

''',
    label="sqlite advance_draft",
)

replace_block(
    sqlite_path,
    "    def reserve_grant_and_start_attempt(\n",
    "    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:\n",
    '''    def reserve_grant_and_start_attempt(
        self,
        *,
        grant_id: str,
        attempt_id: str,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        draft_id: str,
        draft_revision: int,
        action_id: str,
        command_digest: str,
        idempotency_key: str,
        canonical_payload: dict[str, Any],
        business_command_envelope: dict[str, Any] | None = None,
        draft_projection: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Reserve one Grant and its first Attempt in the same transaction.

        If a canonical Draft exists, its terminal state/revision/digest outranks
        every stale Workflow copy. A compatibility-only store probe without a
        Draft is still supported, but production authority flow always persists
        the Draft before Grant issuance.
        """
        now = _now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                existing = self.conn.execute(
                    "SELECT * FROM transaction_attempts WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
                    self.conn.commit()
                    return {
                        "reserved": False,
                        "grant": self._decode_row(dict(grant) if grant else None) or {},
                        "created": False,
                        "attempt": self._decode_row(dict(existing)) or {},
                    }

                canonical = self.conn.execute(
                    "SELECT * FROM transaction_drafts WHERE draft_id=? AND tenant_id=? AND user_id=? AND thread_id=?",
                    (draft_id, tenant_id, user_id, thread_id),
                ).fetchone()
                canonical_payload = self._decode_row(dict(canonical)) if canonical else None
                if canonical_payload:
                    canonical_state = str(canonical_payload.get("draft_state") or "").upper()
                    snapshot_mismatch = (
                        int(canonical_payload.get("draft_revision") or 0) != int(draft_revision)
                        or str(canonical_payload.get("command_digest") or "") != str(command_digest or "")
                    )
                    if canonical_state in TERMINAL_DRAFT_STATES or snapshot_mismatch:
                        reason = "draft_terminal" if canonical_state in TERMINAL_DRAFT_STATES else "draft_snapshot_mismatch"
                        self.conn.execute(
                            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state='ISSUED'",
                            (now, reason, grant_id),
                        )
                        grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
                        self.conn.commit()
                        return {
                            "reserved": False,
                            "grant": self._decode_row(dict(grant) if grant else None) or {},
                            "created": False,
                            "attempt": {},
                        }

                self.conn.execute(
                    "UPDATE transaction_grants SET state='EXPIRED', revoked_at=?, reason='grant_expired' "
                    "WHERE grant_id=? AND state='ISSUED' AND expires_at IS NOT NULL AND expires_at<=?",
                    (now, grant_id, now),
                )
                reserved = self.conn.execute(
                    "UPDATE transaction_grants SET state='RESERVED', reserved_at=?, attempt_id=? "
                    "WHERE grant_id=? AND state='ISSUED' AND (expires_at IS NULL OR expires_at>?)",
                    (now, attempt_id, grant_id, now),
                )
                grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
                if int(reserved.rowcount or 0) != 1:
                    self.conn.commit()
                    return {
                        "reserved": False,
                        "grant": self._decode_row(dict(grant) if grant else None) or {},
                        "created": False,
                        "attempt": {},
                    }
                self.conn.execute(
                    """INSERT INTO transaction_attempts(
                        attempt_id, tenant_id, user_id, thread_id, draft_id, draft_revision, grant_id, action_id,
                        command_digest, idempotency_key, canonical_payload_json, business_command_envelope_json, state, created_at, updated_at, correlation_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        attempt_id, tenant_id, user_id, thread_id, draft_id, int(draft_revision), grant_id, action_id,
                        command_digest, idempotency_key, _json(canonical_payload),
                        _json(business_command_envelope) if business_command_envelope is not None else None,
                        "STARTED", now, now, correlation_id,
                    ),
                )
                self.conn.execute(
                    """INSERT INTO transaction_drafts(draft_id,tenant_id,user_id,thread_id,draft_revision,draft_state,action_id,command_digest,command_envelope_json,projection_json,active_grant_id,current_attempt_id,created_at,updated_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET draft_revision=excluded.draft_revision,draft_state=excluded.draft_state,action_id=excluded.action_id,command_digest=excluded.command_digest,command_envelope_json=excluded.command_envelope_json,projection_json=excluded.projection_json,active_grant_id=excluded.active_grant_id,current_attempt_id=excluded.current_attempt_id,updated_at=excluded.updated_at,correlation_id=excluded.correlation_id""",
                    (
                        draft_id, tenant_id, user_id, thread_id, int(draft_revision), "COMMITTING", action_id, command_digest,
                        _json(business_command_envelope) if business_command_envelope is not None else None,
                        _json(draft_projection) if draft_projection is not None else None,
                        grant_id, attempt_id, now, now, correlation_id,
                    ),
                )
                attempt = self.conn.execute("SELECT * FROM transaction_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                self.conn.commit()
                return {
                    "reserved": True,
                    "grant": self._decode_row(dict(grant) if grant else None) or {},
                    "created": True,
                    "attempt": self._decode_row(dict(attempt) if attempt else None) or {},
                }
            except Exception:
                self.conn.rollback()
                raise

''',
    label="sqlite atomic reserve",
)

# === SQLAlchemy canonical repository =========================================
sqla_path = "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py"
replace_once(
    sqla_path,
    "from agent_core.storage.repositories.base import TransactionScope, ActiveDraftValidationCode, ActiveDraftValidationResult\n",
    "from agent_core.storage.repositories.base import TransactionScope, ActiveDraftValidationCode, ActiveDraftValidationResult\nfrom agent_core.operations.draft import TERMINAL_DRAFT_STATES\n",
    label="sqlalchemy terminal import",
)

replace_block(
    sqla_path,
    "    def reserve_grant_and_start_attempt(self, **kwargs: Any) -> dict[str, Any]:\n",
    "    def create_draft(self, **kwargs: Any) -> dict[str, Any]:\n",
    '''    def reserve_grant_and_start_attempt(self, **kwargs: Any) -> dict[str, Any]:
        grants = self.t["transaction_grants"]
        attempts = self.t["transaction_attempts"]
        drafts = self.t["transaction_drafts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        now = _now()
        key = str(kwargs["idempotency_key"])
        grant_id = str(kwargs["grant_id"])
        attempt_id = str(kwargs["attempt_id"])
        try:
            with self.p.conn() as conn:
                existing = _row(conn.execute(self.sa.select(attempts).where(attempts.c.idempotency_key == key)).first())
                if existing:
                    grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": self._decode_row(existing) or {}}

                canonical = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(
                    drafts.c.draft_id == kwargs["draft_id"],
                    drafts.c.tenant_id == kwargs["tenant_id"],
                    drafts.c.user_id == kwargs["user_id"],
                    drafts.c.thread_id == kwargs["thread_id"],
                ))).first())
                canonical_payload = self._decode_row(canonical) if canonical else None
                if canonical_payload:
                    canonical_state = str(canonical_payload.get("draft_state") or "").upper()
                    snapshot_mismatch = (
                        int(canonical_payload.get("draft_revision") or 0) != int(kwargs["draft_revision"])
                        or str(canonical_payload.get("command_digest") or "") != str(kwargs["command_digest"] or "")
                    )
                    if canonical_state in TERMINAL_DRAFT_STATES or snapshot_mismatch:
                        reason = "draft_terminal" if canonical_state in TERMINAL_DRAFT_STATES else "draft_snapshot_mismatch"
                        conn.execute(
                            grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED"))
                            .values(state="REVOKED", revoked_at=now, reason=reason)
                        )
                        grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                        return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}

                conn.execute(
                    grants.update().where(self.sa.and_(
                        grants.c.grant_id == grant_id, grants.c.state == "ISSUED",
                        grants.c.expires_at.is_not(None), grants.c.expires_at <= now,
                    )).values(state="EXPIRED", revoked_at=now, reason="grant_expired")
                )
                reserved = conn.execute(
                    grants.update().where(self.sa.and_(
                        grants.c.grant_id == grant_id, grants.c.state == "ISSUED",
                        self.sa.or_(grants.c.expires_at.is_(None), grants.c.expires_at > now),
                    )).values(state="RESERVED", reserved_at=now, attempt_id=attempt_id)
                )
                grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                if int(reserved.rowcount or 0) != 1:
                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}
                payload = kwargs["canonical_payload"]
                values = {
                    "attempt_id": attempt_id,
                    "tenant_id": kwargs["tenant_id"], "user_id": kwargs["user_id"], "thread_id": kwargs["thread_id"],
                    "draft_id": kwargs["draft_id"], "draft_revision": int(kwargs["draft_revision"]),
                    "grant_id": grant_id, "action_id": kwargs["action_id"], "command_digest": kwargs["command_digest"],
                    "idempotency_key": key, "canonical_payload_json": _json(payload),
                    "business_command_envelope_json": _json(kwargs.get("business_command_envelope")) if kwargs.get("business_command_envelope") is not None else None,
                    "state": "STARTED", "business_result_json": None, "receipt_handle": None,
                    "error_code": None, "error": None, "created_at": now, "updated_at": now,
                    "reconciled_at": None, "correlation_id": kwargs.get("correlation_id"),
                }
                conn.execute(attempts.insert().values(**values))
                projection = kwargs.get("draft_projection") or {}
                draft_values = {
                    "draft_id": kwargs["draft_id"], "tenant_id": kwargs["tenant_id"], "user_id": kwargs["user_id"],
                    "thread_id": kwargs["thread_id"], "draft_revision": int(kwargs["draft_revision"]), "draft_state": "COMMITTING",
                    "action_id": kwargs["action_id"], "command_digest": kwargs["command_digest"],
                    "command_envelope_json": _json(kwargs.get("business_command_envelope")) if kwargs.get("business_command_envelope") is not None else None,
                    "projection_json": _json(projection), "active_grant_id": grant_id, "current_attempt_id": attempt_id,
                    "created_at": now, "updated_at": now, "correlation_id": kwargs.get("correlation_id"),
                }
                existing_draft = _row(conn.execute(self.sa.select(drafts).where(drafts.c.draft_id == kwargs["draft_id"])).first())
                if existing_draft:
                    conn.execute(drafts.update().where(drafts.c.draft_id == kwargs["draft_id"]).values(**{k:v for k,v in draft_values.items() if k not in {"draft_id", "created_at"}}))
                else:
                    conn.execute(drafts.insert().values(**draft_values))
                attempt = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == attempt_id)).first())
                return {"reserved": True, "grant": self._decode_row(grant) or {}, "created": True, "attempt": self._decode_row(attempt) or {}}
        except self.sa.exc.IntegrityError:
            existing = self.get_attempt_by_idempotency_key(key) or {}
            return {"reserved": False, "grant": self.get_grant(grant_id) or {}, "created": False, "attempt": existing}

''',
    label="sqlalchemy atomic reserve",
)

replace_block(
    sqla_path,
    "    def create_draft(self, **kwargs: Any) -> dict[str, Any]:\n",
    "    def get_draft_for_scope(self, *, scope: TransactionScope, draft_id: str) -> dict[str, Any] | None:\n",
    '''    def create_draft(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_drafts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        now = _now()
        command_envelope = kwargs.pop("command_envelope", None)
        projection = kwargs.pop("projection", None)
        values = {
            **kwargs,
            "command_envelope_json": _json(command_envelope),
            "projection_json": _json(projection),
            "created_at": now,
            "updated_at": now,
        }
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == values["draft_id"])).first())
            if existing:
                existing_payload = self._decode_row(existing) or {}
                existing_state = str(existing_payload.get("draft_state") or "").upper()
                existing_revision = int(existing_payload.get("draft_revision") or 0)
                incoming_revision = int(values.get("draft_revision") or 0)
                if existing_state in TERMINAL_DRAFT_STATES or existing_revision > incoming_revision:
                    return existing_payload
                conn.execute(table.update().where(table.c.draft_id == values["draft_id"]).values(**{k:v for k,v in values.items() if k not in {"draft_id", "created_at"}}))
            else:
                conn.execute(table.insert().values(**values))
        return self.get_draft(values["draft_id"]) or {}

''',
    label="sqlalchemy create_draft",
)

replace_block(
    sqla_path,
    "    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:\n",
    "    def list_drafts_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:\n",
    '''    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:
        table = self.t["transaction_drafts"]
        values: dict[str, Any] = {"draft_state": draft_state, "updated_at": _now()}
        if draft_revision is not None: values["draft_revision"] = int(draft_revision)
        if command_digest is not None: values["command_digest"] = command_digest
        if command_envelope is not None: values["command_envelope_json"] = _json(command_envelope)
        if active_grant_id is not None: values["active_grant_id"] = active_grant_id
        if current_attempt_id is not None: values["current_attempt_id"] = current_attempt_id
        if projection is not None: values["projection_json"] = _json(projection)
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == draft_id)).first())
            if not existing:
                return
            current = self._decode_row(existing) or {}
            if str(current.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:
                return
            if draft_revision is not None and int(current.get("draft_revision") or 0) > int(draft_revision):
                return
            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))

''',
    label="sqlalchemy advance_draft",
)

# === Ledger terminal monotonicity ============================================
ledger_path = "services/agent-service/src/agent_core/ledger/ledger.py"
replace_once(
    ledger_path,
    "from agent_core.operations.draft import display_projection, ensure_transaction_draft, transition_draft\n",
    "from agent_core.operations.draft import TERMINAL_DRAFT_STATES, display_projection, ensure_transaction_draft, transition_draft\n",
    label="ledger terminal import",
)
replace_once(
    ledger_path,
    '''        item = deepcopy(raw)\n        if str(item.get("kind") or "") == "offer":\n            item = ensure_transaction_draft(item, previous=deduped.get(str(item.get("handle") or "")))\n        if _expired(item, now=current):\n''',
    '''        item = deepcopy(raw)\n        handle = str(item.get("handle") or "")\n        previous = deduped.get(handle)\n        if str(item.get("kind") or "") == "offer":\n            if previous is not None and str(previous.get("kind") or "") == "offer" and str(previous.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES:\n                continue\n            item = ensure_transaction_draft(item, previous=previous)\n        if _expired(item, now=current):\n''',
    label="ledger terminal guard",
)

# === Structured UI validation reads canonical transaction state =============
service_path = "services/agent-service/app/services/agent_service.py"
replace_once(
    service_path,
    '''        if not offer or self._draft_state_for_validation(offer) != "AWAITING_AUTHORIZATION":\n            return "offer_not_awaiting_authority"\n        if str(offer.get("action_id") or "") != request.action_id:\n''',
    '''        if not offer or self._draft_state_for_validation(offer) != "AWAITING_AUTHORIZATION":\n            return "offer_not_awaiting_authority"\n        repository = getattr(self, "transactions", None)\n        get_durable = getattr(repository, "get_draft_for_scope", None)\n        if callable(get_durable):\n            durable = get_durable(\n                scope=TransactionScope(\n                    tenant_id=str(request.tenant_id or "default"),\n                    user_id=str(request.user_id),\n                    thread_id=str(request.thread_id),\n                ),\n                draft_id=expected_handle,\n            )\n            if durable is None:\n                return "durable_draft_missing"\n            if self._draft_state_for_validation(durable) != "AWAITING_AUTHORIZATION":\n                return "durable_draft_not_awaiting_authority"\n            if int(durable.get("draft_revision") or 0) != int(offer.get("draft_revision") or 0):\n                return "durable_draft_revision_mismatch"\n            durable_projection = durable.get("projection") if isinstance(durable.get("projection"), dict) else {}\n            if str(durable_projection.get("confirmation_id") or "") != str(offer.get("confirmation_id") or ""):\n                return "durable_confirmation_id_mismatch"\n            if int(durable_projection.get("confirmation_version") or 0) != int(offer.get("confirmation_version") or 0):\n                return "durable_confirmation_version_mismatch"\n        if str(offer.get("action_id") or "") != request.action_id:\n''',
    label="durable UI authority validation",
)

# === Internal Grant issuance also fails closed ===============================
coord_path = "services/agent-service/src/agent_core/transaction/coordinator.py"
replace_once(
    coord_path,
    "from agent_core.operations.draft import canonical_command_payload, command_digest_for_offer\n",
    "from agent_core.operations.draft import canonical_command_payload, command_digest_for_offer\nfrom agent_core.storage.repositories.base import TransactionScope\n",
    label="coordinator scope import",
)
replace_once(
    coord_path,
    '''    store = transaction_store(state)\n    persist_draft_from_offer(state=state, offer=offer, draft_state=str(offer.get("draft_state") or "AWAITING_AUTHORIZATION"))\n    record = store.issue_grant(\n''',
    '''    store = transaction_store(state)\n    draft_id = str(offer.get("draft_id") or offer.get("handle") or "")\n    scoped_getter = getattr(store, "get_draft_for_scope", None)\n    if callable(scoped_getter):\n        current = scoped_getter(\n            scope=TransactionScope(tenant_id=scope["tenant_id"], user_id=scope["user_id"], thread_id=scope["thread_id"]),\n            draft_id=draft_id,\n        )\n        if current is not None:\n            if str(current.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n                raise ValueError("canonical Draft is no longer awaiting authority")\n            if int(current.get("draft_revision") or 0) != int(offer.get("draft_revision") or 1):\n                raise ValueError("canonical Draft revision no longer matches authority card")\n    persisted = persist_draft_from_offer(state=state, offer=offer, draft_state=str(offer.get("draft_state") or "AWAITING_AUTHORIZATION"))\n    if str(persisted.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n        raise ValueError("canonical Draft rejected stale authority projection")\n    record = store.issue_grant(\n''',
    label="coordinator grant guard",
)

# === Duplicate after known Receipt projects terminal fact ====================
commit_path = "services/agent-service/src/agent_core/transaction/commit_runtime.py"
replace_once(
    commit_path,
    '''    if not reservation.get("reserved"):\n        # A duplicate click, a second tab, or a previous process may already own\n        # this grant.  Fail closed and show a read-only reconciliation state.\n        unknown = transition_draft(refreshed, "SUBMISSION_UNKNOWN", reason="grant_already_reserved_or_consumed")\n        unknown["commit_attempt_id"] = attempt_id\n        ledger = append_entries(ledger, [unknown])\n        return _transaction_commit_update(\n            state, ledger, unknown,\n            result={"success": False, "error": "提交结果正在确认中，请勿重复操作。", "code": "SUBMISSION_UNKNOWN"},\n            draft_state="SUBMISSION_UNKNOWN", attempt_id=attempt_id, idempotency_key=idempotency_key,\n            status="ActionCommitAlreadyInProgress", write_receipt=False, deps=deps,\n        )\n''',
    '''    if not reservation.get("reserved"):\n        repository = transaction_store(state)\n        existing_receipt = repository.get_receipt_by_attempt(attempt_id) if attempt_id else None\n        if isinstance(existing_receipt, dict):\n            known_result = existing_receipt.get("business_result") if isinstance(existing_receipt.get("business_result"), dict) else {}\n            receipt_state = str(existing_receipt.get("receipt_state") or "").upper()\n            if receipt_state == "SUCCESS" and bool(known_result.get("success")):\n                return _transaction_commit_update(\n                    state, ledger, refreshed, result=dict(known_result), draft_state="COMMITTED",\n                    attempt_id=attempt_id, idempotency_key=idempotency_key,\n                    status="ActionAlreadyCommitted", write_receipt=False, deps=deps,\n                )\n            if receipt_state == "FAILED":\n                known_attempt_state = str(attempt.get("state") or "").upper()\n                known_failure_state = known_attempt_state if known_attempt_state in {"FAILED_RETRYABLE", "FAILED_FINAL"} else "FAILED_FINAL"\n                return _transaction_commit_update(\n                    state, ledger, refreshed,\n                    result=dict(known_result or {"success": False, "error": "业务提交已失败。"}),\n                    draft_state=known_failure_state, attempt_id=attempt_id, idempotency_key=idempotency_key,\n                    status="ActionAlreadyFailed", write_receipt=False, deps=deps,\n                )\n        # No Receipt means the exact existing Attempt is genuinely uncertain.\n        # Never execute a second business command; reconciliation owns recovery.\n        unknown = transition_draft(refreshed, "SUBMISSION_UNKNOWN", reason="grant_already_reserved_or_consumed")\n        unknown["commit_attempt_id"] = attempt_id\n        ledger = append_entries(ledger, [unknown])\n        return _transaction_commit_update(\n            state, ledger, unknown,\n            result={"success": False, "error": "提交结果正在确认中，请勿重复操作。", "code": "SUBMISSION_UNKNOWN"},\n            draft_state="SUBMISSION_UNKNOWN", attempt_id=attempt_id, idempotency_key=idempotency_key,\n            status="ActionCommitAlreadyInProgress", write_receipt=False, deps=deps,\n        )\n''',
    label="duplicate receipt projection",
)

# === Permanent Stage 8 adversarial tests =====================================
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
from agent_core.transaction import transition_draft
from agent_core.transaction.coordinator import issue_grant_for_authority
from agent_core.transaction.deps import TransactionExecutionDeps

SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "stage8-terminal"}


def _offer(*, state: str = "AWAITING_AUTHORIZATION") -> dict:
    row = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle="artifact:order:10002",
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}}, scope=SCOPE,
        turn=2, label="退款申请", handle="draft:refund:stage8-terminal",
    )
    row["confirmation_id"] = "confirm-stage8"
    row["confirmation_version"] = 1
    row["authority_revision"] = 2
    return transition_draft(row, state)


def _create(store, offer: dict, *, state: str | None = None) -> dict:
    return store.create_draft(
        draft_id=offer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_revision=offer["draft_revision"], draft_state=state or offer["draft_state"], action_id=offer["action_id"],
        command_digest=offer["command_digest"], command_envelope=offer.get("business_command_envelope"), projection=offer,
    )


def test_sqlite_terminal_draft_rejects_stale_create_and_advance(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
    store.record_receipt(
        receipt_id="receipt:sqlite-stage8", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id="attempt:sqlite-stage8", receipt_handle="h_receipt:sqlite-stage8", receipt_state="SUCCESS",
        business_result={"success": True, "data": {"refund_id": "R-stage8"}}, business_resource_id="R-stage8",
    )
    _create(store, offer, state="AWAITING_AUTHORIZATION")
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    store.advance_draft(offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"])
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"


def test_sqlalchemy_terminal_draft_rejects_stale_create_and_advance(tmp_path: Path) -> None:
    db_file = tmp_path / "agent-sqlalchemy.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
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
    assert row is not None and row["draft_state"] == "COMMITTED"


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
        grant_id="grant-stage8", attempt_id="attempt-stage8-late", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"],
        thread_id=SCOPE["thread_id"], draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id=offer["action_id"],
        command_digest=offer["command_digest"], idempotency_key="idem-stage8-late", canonical_payload={"action_id": offer["action_id"]},
        business_command_envelope=None, draft_projection=offer,
    )
    assert result["reserved"] is False and result["created"] is False and result["attempt"] == {}
    assert store.get_attempt("attempt-stage8-late") is None
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    assert store.get_grant("grant-stage8")["state"] == "REVOKED"


def test_internal_grant_minting_rejects_terminal_canonical_draft(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="REVOKED", draft_revision=offer["draft_revision"])
    state = {"current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"], "current_thread_id": SCOPE["thread_id"], "_transaction_repository": store}
    authority = {"actor_id": SCOPE["user_id"], "actor_role": "customer", "client_request_id": "late-authority-stage8", "authority_type": "ui_confirmed"}
    with pytest.raises(ValueError, match="no longer awaiting authority"):
        issue_grant_for_authority(state=state, offer=offer, authority=authority)
    assert store.list_grants_by_thread(**SCOPE) == []
    assert store.get_draft(offer["draft_id"])["draft_state"] == "REVOKED"


def test_stale_browser_authority_is_rejected_by_durable_terminal_state(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
    stale_values = {"turn_index": 2, "focused_draft_id": offer["draft_id"], "artifact_ledger": [offer]}

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
        facts={"order_id": "10002", "status": "已签收", "version": 1}, scope=SCOPE,
        turn=2, source="test", freshness_version=1, handle="artifact:order:10002",
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
    _create(store, offer, state="COMMITTED")
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
        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"], "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer", "turn_index": 2, "artifact_ledger": [target, offer], "focused_draft_id": offer["draft_id"],
        "commit_authority": {"grant_id": "grant-known", "command_digest": offer["command_digest"]},
        "action_queue": [], "tool_trace": [], "_transaction_repository": store,
    }
    patch = runtime.commit_action_node(state, deps=TransactionExecutionDeps(business_port=SimpleNamespace(), outcome_factory=outcome))
    assert patch["status"] == "ActionAlreadyCommitted"
    row = find_handle(patch["artifact_ledger"], offer["handle"], scope=SCOPE, allowed_kinds={"offer"}, active_only=False)
    assert row is not None and row["draft_state"] == "COMMITTED"
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
''', encoding="utf-8")
