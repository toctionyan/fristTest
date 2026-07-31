"""Database model contract for the Agent service.

The production path uses SQLAlchemy tables defined in
`agent_core.persistence.sqlalchemy_provider._define_tables`.  This lightweight file
keeps the model contract discoverable without forcing SQLAlchemy to be installed
for local SQLite-only tests.

Managed tables:
- agent_threads
- agent_messages
- agent_trace_logs
- agent_action_audit_logs
- agent_idempotency_records
- agent_action_locks
- agent_outbox_events
- agent_action_runs

Use Alembic or your platform migration tool to materialize these tables in
PostgreSQL/MySQL production databases.
"""

AGENT_TABLES = [
    "agent_threads",
    "agent_messages",
    "agent_trace_logs",
    "agent_action_audit_logs",
    "agent_idempotency_records",
    "agent_action_locks",
    "agent_outbox_events",
    "agent_action_runs",
]
