from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_core.runtime.turn_fencing import (
    AtomicallyFencedPostgresSaver,
    FencedCheckpointer,
    StaleTurnFenceError,
    TurnFence,
    activate_turn_fence,
)


def _checkpoint(checkpoint_id: str) -> dict:
    return {
        "v": 1,
        "ts": datetime.now(UTC).isoformat(),
        "id": checkpoint_id,
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


@pytest.mark.integration
def test_postgres_checkpoint_physically_rejects_replaced_fencing_token() -> None:
    url = os.getenv("AGENT_TEST_POSTGRES_URL")
    if not url:
        pytest.fail("AGENT_TEST_POSTGRES_URL is required")
    psycopg_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    with PostgresSaver.from_conn_string(psycopg_url) as setup:
        setup.setup()
    admin = psycopg.connect(psycopg_url, autocommit=True, row_factory=dict_row)
    admin.execute(
        """CREATE TABLE IF NOT EXISTS agent_action_locks (
            lock_key TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            renewed_at TEXT NOT NULL
        )"""
    )
    suffix = uuid4().hex
    lock_key = f"conversation-turn:integration:{suffix}"
    owner = f"worker-a-{suffix}"
    valid_id = str(uuid4())
    stale_id = str(uuid4())
    now = datetime.now(UTC)
    admin.execute(
        "INSERT INTO agent_action_locks(lock_key,owner,fencing_token,expires_at,created_at,renewed_at) VALUES(%s,%s,%s,%s,%s,%s)",
        (lock_key, owner, 1, (now + timedelta(minutes=5)).isoformat(), now.isoformat(), now.isoformat()),
    )
    connection = psycopg.connect(
        psycopg_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    saver = FencedCheckpointer(AtomicallyFencedPostgresSaver(connection))
    config = {"configurable": {"thread_id": f"thread-{suffix}", "checkpoint_ns": ""}}
    try:
        with activate_turn_fence(TurnFence(lock_key, owner, 1, lambda: None)):
            saver.put(config, _checkpoint(valid_id), {}, {})
        assert admin.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id=%s AND checkpoint_id=%s",
            (f"thread-{suffix}", valid_id),
        ).fetchone()

        admin.execute(
            "UPDATE agent_action_locks SET owner=%s,fencing_token=2 WHERE lock_key=%s",
            (f"worker-b-{suffix}", lock_key),
        )
        with activate_turn_fence(TurnFence(lock_key, owner, 1, lambda: None)):
            with pytest.raises(StaleTurnFenceError):
                saver.put(config, _checkpoint(stale_id), {}, {})
        assert admin.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id=%s AND checkpoint_id=%s",
            (f"thread-{suffix}", stale_id),
        ).fetchone() is None
    finally:
        admin.execute("DELETE FROM agent_action_locks WHERE lock_key=%s", (lock_key,))
        admin.execute("DELETE FROM checkpoint_writes WHERE thread_id=%s", (f"thread-{suffix}",))
        admin.execute("DELETE FROM checkpoints WHERE thread_id=%s", (f"thread-{suffix}",))
        admin.execute("DELETE FROM checkpoint_blobs WHERE thread_id=%s", (f"thread-{suffix}",))
        connection.close()
        admin.close()
