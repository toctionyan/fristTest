from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


config = ROOT / "services/agent-service/src/agent_core/config.py"
text = config.read_text(encoding="utf-8")
text = replace_once(
    text,
    """        if not database_url:
            raise RuntimeError("CHECKPOINT_BACKEND=postgres requires CHECKPOINT_DATABASE_URL or AGENT_DATABASE_URL")
        try:
""",
    """        if not database_url:
            raise RuntimeError("CHECKPOINT_BACKEND=postgres requires CHECKPOINT_DATABASE_URL or AGENT_DATABASE_URL")
        # Agent/Business repositories use SQLAlchemy URLs, while psycopg and
        # LangGraph's PostgresSaver require a native PostgreSQL connection URI.
        # Normalize before both setup and the long-lived connection; otherwise
        # a managed ``postgresql+psycopg://`` authority makes graph compilation
        # fail even though the database itself is healthy.
        psycopg_url = database_url
        for sqlalchemy_scheme in ("postgresql+psycopg://", "postgresql+psycopg2://"):
            if psycopg_url.lower().startswith(sqlalchemy_scheme):
                psycopg_url = "postgresql://" + psycopg_url[len(sqlalchemy_scheme):]
                break
        try:
""",
    label="insert checkpoint URL normalization",
)
text = replace_once(
    text,
    "with PostgresSaver.from_conn_string(database_url) as setup_saver:",
    "with PostgresSaver.from_conn_string(psycopg_url) as setup_saver:",
    label="bind PostgresSaver to normalized URL",
)
text = replace_once(
    text,
    """        psycopg_url = database_url.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
""",
    "",
    label="remove late one-scheme normalization",
)
config.write_text(text, encoding="utf-8")

canary = ROOT / "scripts/verify_full_lifecycle_canary.py"
text = canary.read_text(encoding="utf-8")
text = replace_once(
    text,
    """            else:
                env.update({
                    "BUSINESS_DB_BACKEND": "sqlite",
                    "BUSINESS_DB_PATH": str(self.runtime_dir / "business.db"),
                    "AGENT_DB_BACKEND": "sqlite",
                    "CHECKPOINT_BACKEND": "sqlite",
                    "SQLITE_DB_PATH": str(self.runtime_dir / "agent.db"),
                    "CHECKPOINT_DB_PATH": str(self.runtime_dir / "checkpoints.db"),
                })
""",
    """            else:
                # A nested canary may be launched by a controller that owns a
                # PostgreSQL runtime.  Local SQLite mode must not inherit those
                # URLs: backend=sqlite plus a PostgreSQL URL is an invalid mixed
                # authority and prevents the Agent process from starting.
                for inherited_database_setting in (
                    "AGENT_DATABASE_URL",
                    "DATABASE_URL",
                    "CHECKPOINT_DATABASE_URL",
                    "BUSINESS_DATABASE_URL",
                    "RAG_DATABASE_URL",
                    "DOCUMENT_JOB_DATABASE_URL",
                ):
                    env.pop(inherited_database_setting, None)
                env.update({
                    "BUSINESS_DB_BACKEND": "sqlite",
                    "BUSINESS_DB_PATH": str(self.runtime_dir / "business.db"),
                    "AGENT_DB_BACKEND": "sqlite",
                    "DATABASE_BACKEND": "sqlite",
                    "AGENT_DB_CREATE_SCHEMA": "true",
                    "CHECKPOINT_BACKEND": "sqlite",
                    "CHECKPOINT_SETUP": "true",
                    "STRICT_PERSISTENCE": "false",
                    "SQLITE_DB_PATH": str(self.runtime_dir / "agent.db"),
                    "CHECKPOINT_DB_PATH": str(self.runtime_dir / "checkpoints.db"),
                })
""",
    label="isolate nested SQLite canary authority",
)
canary.write_text(text, encoding="utf-8")

test_path = ROOT / "services/agent-service/tests/runtime/test_release_runtime_database_authority.py"
test_path.write_bytes(base64.b64decode("ZnJvbSBfX2Z1dHVyZV9fIGltcG9ydCBhbm5vdGF0aW9ucwoKaW1wb3J0IGltcG9ydGxpYi51dGlsCmltcG9ydCBzeXMKaW1wb3J0IHR5cGVzCmZyb20gcGF0aGxpYiBpbXBvcnQgUGF0aAoKaW1wb3J0IHB5dGVzdAoKUk9PVCA9IFBhdGgoX19maWxlX18pLnJlc29sdmUoKS5wYXJlbnRzWzRdCgoKZGVmIF9sb2FkKG5hbWU6IHN0ciwgcGF0aDogUGF0aCk6CiAgICBzcGVjID0gaW1wb3J0bGliLnV0aWwuc3BlY19mcm9tX2ZpbGVfbG9jYXRpb24obmFtZSwgcGF0aCkKICAgIGFzc2VydCBzcGVjIGlzIG5vdCBOb25lIGFuZCBzcGVjLmxvYWRlciBpcyBub3QgTm9uZQogICAgbW9kdWxlID0gaW1wb3J0bGliLnV0aWwubW9kdWxlX2Zyb21fc3BlYyhzcGVjKQogICAgc3lzLm1vZHVsZXNbc3BlYy5uYW1lXSA9IG1vZHVsZQogICAgc3BlYy5sb2FkZXIuZXhlY19tb2R1bGUobW9kdWxlKQogICAgcmV0dXJuIG1vZHVsZQoKCmRlZiB0ZXN0X2xvY2FsX3NxbGl0ZV9oYXJuZXNzX2Rpc2NhcmRzX2luaGVyaXRlZF9wb3N0Z3Jlc19hdXRob3JpdGllcygKICAgIG1vbmtleXBhdGNoOiBweXRlc3QuTW9ua2V5UGF0Y2gsCikgLT4gTm9uZToKICAgIGZvciBuYW1lIGluICgKICAgICAgICAiQUdFTlRfREFUQUJBU0VfVVJMIiwKICAgICAgICAiREFUQUJBU0VfVVJMIiwKICAgICAgICAiQ0hFQ0tQT0lOVF9EQVRBQkFTRV9VUkwiLAogICAgICAgICJCVVNJTkVTU19EQVRBQkFTRV9VUkwiLAogICAgICAgICJSQUdfREFUQUJBU0VfVVJMIiwKICAgICAgICAiRE9DVU1FTlRfSk9CX0RBVEFCQVNFX1VSTCIsCiAgICApOgogICAgICAgIG1vbmtleXBhdGNoLnNldGVudigKICAgICAgICAgICAgbmFtZSwKICAgICAgICAgICAgInBvc3RncmVzcWwrcHN5Y29wZzovL3F1YWxpdHk6c2VjcmV0QDEyNy4wLjAuMTo1NTQzMi9xdWFsaXR5IiwKICAgICAgICApCiAgICBtb25rZXlwYXRjaC5zZXRlbnYoIlNUUklDVF9QRVJTSVNURU5DRSIsICJ0cnVlIikKCiAgICBtb2R1bGUgPSBfbG9hZCgKICAgICAgICAicmVsZWFzZV9ydW50aW1lX2RhdGFiYXNlX2hhcm5lc3MiLAogICAgICAgIFJPT1QgLyAic2NyaXB0cyIgLyAidmVyaWZ5X2Z1bGxfbGlmZWN5Y2xlX2NhbmFyeS5weSIsCiAgICApCiAgICBoYXJuZXNzID0gbW9kdWxlLlByb2R1Y3RSdW50aW1lSGFybmVzcygpCiAgICB0cnk6CiAgICAgICAgYXNzZXJ0IGhhcm5lc3MuZW52WyJBR0VOVF9EQl9CQUNLRU5EIl0gPT0gInNxbGl0ZSIKICAgICAgICBhc3NlcnQgaGFybmVzcy5lbnZbIkRBVEFCQVNFX0JBQ0tFTkQiXSA9PSAic3FsaXRlIgogICAgICAgIGFzc2VydCBoYXJuZXNzLmVudlsiQ0hFQ0tQT0lOVF9CQUNLRU5EIl0gPT0gInNxbGl0ZSIKICAgICAgICBhc3NlcnQgaGFybmVzcy5lbnZbIlNUUklDVF9QRVJTSVNURU5DRSJdID09ICJmYWxzZSIKICAgICAgICBmb3IgbmFtZSBpbiAoCiAgICAgICAgICAgICJBR0VOVF9EQVRBQkFTRV9VUkwiLAogICAgICAgICAgICAiREFUQUJBU0VfVVJMIiwKICAgICAgICAgICAgIkNIRUNLUE9JTlRfREFUQUJBU0VfVVJMIiwKICAgICAgICAgICAgIkJVU0lORVNTX0RBVEFCQVNFX1VSTCIsCiAgICAgICAgICAgICJSQUdfREFUQUJBU0VfVVJMIiwKICAgICAgICAgICAgIkRPQ1VNRU5UX0pPQl9EQVRBQkFTRV9VUkwiLAogICAgICAgICk6CiAgICAgICAgICAgIGFzc2VydCBuYW1lIG5vdCBpbiBoYXJuZXNzLmVudgogICAgZmluYWxseToKICAgICAgICBoYXJuZXNzLnN0b3AoKQoKCmRlZiB0ZXN0X3Bvc3RncmVzX2NoZWNrcG9pbnRlcl9ub3JtYWxpemVzX3NxbGFsY2hlbXlfdXJsX2JlZm9yZV9zZXR1cF9hbmRfY29ubmVjdCgKICAgIG1vbmtleXBhdGNoOiBweXRlc3QuTW9ua2V5UGF0Y2gsCikgLT4gTm9uZToKICAgIGFnZW50X3NyYyA9IFJPT1QgLyAic2VydmljZXMiIC8gImFnZW50LXNlcnZpY2UiIC8gInNyYyIKICAgIHN5cy5wYXRoLmluc2VydCgwLCBzdHIoYWdlbnRfc3JjKSkKICAgIGZlbmNpbmdfbW9kdWxlID0gdHlwZXMuTW9kdWxlVHlwZSgiYWdlbnRfY29yZS5ydW50aW1lLnR1cm5fZmVuY2luZyIpCiAgICBmZW5jaW5nX21vZHVsZS5BdG9taWNhbGx5RmVuY2VkUG9zdGdyZXNTYXZlciA9IGxhbWJkYSBjb25uOiBjb25uCiAgICBmZW5jaW5nX21vZHVsZS5GZW5jZWRDaGVja3BvaW50ZXIgPSBsYW1iZGEgc2F2ZXI6IHNhdmVyCiAgICBtb25rZXlwYXRjaC5zZXRpdGVtKAogICAgICAgIHN5cy5tb2R1bGVzLAogICAgICAgICJhZ2VudF9jb3JlLnJ1bnRpbWUudHVybl9mZW5jaW5nIiwKICAgICAgICBmZW5jaW5nX21vZHVsZSwKICAgICkKICAgIHRyeToKICAgICAgICBjb25maWcgPSBfbG9hZCgKICAgICAgICAgICAgImFnZW50X2NvcmUuY29uZmlnX3JlbGVhc2VfcnVudGltZV9kYXRhYmFzZSIsCiAgICAgICAgICAgIGFnZW50X3NyYyAvICJhZ2VudF9jb3JlIiAvICJjb25maWcucHkiLAogICAgICAgICkKICAgIGZpbmFsbHk6CiAgICAgICAgc3lzLnBhdGgucmVtb3ZlKHN0cihhZ2VudF9zcmMpKQoKICAgIHNlZW46IGRpY3Rbc3RyLCBvYmplY3RdID0ge30KCiAgICBjbGFzcyBTZXR1cENvbnRleHQ6CiAgICAgICAgZGVmIF9fZW50ZXJfXyhzZWxmKToKICAgICAgICAgICAgcmV0dXJuIHNlbGYKCiAgICAgICAgZGVmIF9fZXhpdF9fKHNlbGYsICpfYXJncyk6CiAgICAgICAgICAgIHJldHVybiBOb25lCgogICAgICAgIGRlZiBzZXR1cChzZWxmKSAtPiBOb25lOgogICAgICAgICAgICBzZWVuWyJzZXR1cF9jYWxsZWQiXSA9IFRydWUKCiAgICBjbGFzcyBQb3N0Z3Jlc1NhdmVyOgogICAgICAgIEBjbGFzc21ldGhvZAogICAgICAgIGRlZiBmcm9tX2Nvbm5fc3RyaW5nKGNscywgdXJsOiBzdHIpOgogICAgICAgICAgICBzZWVuWyJzZXR1cF91cmwiXSA9IHVybAogICAgICAgICAgICByZXR1cm4gU2V0dXBDb250ZXh0KCkKCiAgICBtZW1vcnlfbW9kdWxlID0gdHlwZXMuTW9kdWxlVHlwZSgibGFuZ2dyYXBoLmNoZWNrcG9pbnQubWVtb3J5IikKICAgIG1lbW9yeV9tb2R1bGUuSW5NZW1vcnlTYXZlciA9IG9iamVjdAogICAgcG9zdGdyZXNfbW9kdWxlID0gdHlwZXMuTW9kdWxlVHlwZSgibGFuZ2dyYXBoLmNoZWNrcG9pbnQucG9zdGdyZXMiKQogICAgcG9zdGdyZXNfbW9kdWxlLlBvc3RncmVzU2F2ZXIgPSBQb3N0Z3Jlc1NhdmVyCiAgICBwc3ljb3BnX21vZHVsZSA9IHR5cGVzLk1vZHVsZVR5cGUoInBzeWNvcGciKQoKICAgIGRlZiBjb25uZWN0KHVybDogc3RyLCAqKmt3YXJncyk6CiAgICAgICAgc2VlblsiY29ubmVjdF91cmwiXSA9IHVybAogICAgICAgIHNlZW5bImNvbm5lY3Rfa3dhcmdzIl0gPSBrd2FyZ3MKICAgICAgICByZXR1cm4gb2JqZWN0KCkKCiAgICBwc3ljb3BnX21vZHVsZS5jb25uZWN0ID0gY29ubmVjdAogICAgcm93c19tb2R1bGUgPSB0eXBlcy5Nb2R1bGVUeXBlKCJwc3ljb3BnLnJvd3MiKQogICAgcm93c19tb2R1bGUuZGljdF9yb3cgPSBvYmplY3QoKQogICAgbW9ua2V5cGF0Y2guc2V0aXRlbShzeXMubW9kdWxlcywgImxhbmdncmFwaC5jaGVja3BvaW50Lm1lbW9yeSIsIG1lbW9yeV9tb2R1bGUpCiAgICBtb25rZXlwYXRjaC5zZXRpdGVtKHN5cy5tb2R1bGVzLCAibGFuZ2dyYXBoLmNoZWNrcG9pbnQucG9zdGdyZXMiLCBwb3N0Z3Jlc19tb2R1bGUpCiAgICBtb25rZXlwYXRjaC5zZXRpdGVtKHN5cy5tb2R1bGVzLCAicHN5Y29wZyIsIHBzeWNvcGdfbW9kdWxlKQogICAgbW9ua2V5cGF0Y2guc2V0aXRlbShzeXMubW9kdWxlcywgInBzeWNvcGcucm93cyIsIHJvd3NfbW9kdWxlKQogICAgbW9ua2V5cGF0Y2guc2V0YXR0cihjb25maWcsICJBdG9taWNhbGx5RmVuY2VkUG9zdGdyZXNTYXZlciIsIGxhbWJkYSBjb25uOiBjb25uKQogICAgbW9ua2V5cGF0Y2guc2V0YXR0cihjb25maWcsICJGZW5jZWRDaGVja3BvaW50ZXIiLCBsYW1iZGEgc2F2ZXI6IHNhdmVyKQogICAgbW9ua2V5cGF0Y2guc2V0ZW52KCJBUFBfUFJPRklMRSIsICJsb2NhbCIpCiAgICBtb25rZXlwYXRjaC5zZXRlbnYoIkNIRUNLUE9JTlRfQkFDS0VORCIsICJwb3N0Z3JlcyIpCiAgICBtb25rZXlwYXRjaC5zZXRlbnYoCiAgICAgICAgIkNIRUNLUE9JTlRfREFUQUJBU0VfVVJMIiwKICAgICAgICAicG9zdGdyZXNxbCtwc3ljb3BnOi8vcXVhbGl0eTpzZWNyZXRAMTI3LjAuMC4xOjU1NDMyL3F1YWxpdHkiLAogICAgKQogICAgbW9ua2V5cGF0Y2guc2V0ZW52KCJDSEVDS1BPSU5UX1NFVFVQIiwgInRydWUiKQoKICAgIGNvbmZpZy5jbGVhcl9jaGVja3BvaW50ZXJfY2FjaGUoKQogICAgdHJ5OgogICAgICAgIGNvbmZpZy5idWlsZF9jaGVja3BvaW50ZXIoKQogICAgZmluYWxseToKICAgICAgICBjb25maWcuY2xlYXJfY2hlY2twb2ludGVyX2NhY2hlKCkKCiAgICBleHBlY3RlZCA9ICJwb3N0Z3Jlc3FsOi8vcXVhbGl0eTpzZWNyZXRAMTI3LjAuMC4xOjU1NDMyL3F1YWxpdHkiCiAgICBhc3NlcnQgc2Vlblsic2V0dXBfdXJsIl0gPT0gZXhwZWN0ZWQKICAgIGFzc2VydCBzZWVuWyJjb25uZWN0X3VybCJdID09IGV4cGVjdGVkCiAgICBhc3NlcnQgc2Vlblsic2V0dXBfY2FsbGVkIl0gaXMgVHJ1ZQo="))
