# Agent database migrations

This project keeps SQLite/local defaults runnable without optional database
packages.  For production, set:

```env
AGENT_DB_BACKEND=postgres
AGENT_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/agent_db
```

Recommended production migration flow:

1. Install optional database packages: `sqlalchemy`, `alembic`, and the target
   driver (`psycopg[binary]` or `pymysql`).
2. Create Alembic environment in this directory.
3. Import table metadata from `agent_core.persistence.sqlalchemy_provider` or mirror
   the table contract listed in `agent_core.storage.models.AGENT_TABLES`.
4. Generate and review migration scripts.
5. Set `AGENT_DB_CREATE_SCHEMA=false` in production and let migrations manage
   schema changes.

Local/dev can keep `AGENT_DB_BACKEND=sqlite`.
