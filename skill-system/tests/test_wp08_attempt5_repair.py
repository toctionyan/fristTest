from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
AGENT_ROOT = ROOT / "services" / "agent-service"
for path in (SCRIPTS, AGENT_ROOT, AGENT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import psycopg
import run_managed_quality_integration as managed
import verify_managed_postgres_recovery as recovery


def test_pending_interaction_reads_authoritative_live_control(monkeypatch: pytest.MonkeyPatch) -> None:
    class Harness:
        agent_url = "http://agent.test"

    seen: list[str] = []

    def fake_call(base: str, path: str, **kwargs):
        seen.append(path)
        return {
            "type": "interaction_required",
            "interaction": {
                "lifecycle": "collecting_input",
                "control": {"offer_handle": "offer-1"},
            },
        }

    monkeypatch.setattr(recovery, "_call", fake_call)
    interaction, control = recovery._pending_interaction(
        Harness(), token="token", thread_id="thread-1", lifecycle="collecting_input"
    )
    assert seen == ["/api/threads/thread-1/pending"]
    assert interaction["lifecycle"] == "collecting_input"
    assert control["offer_handle"] == "offer-1"


def test_managed_postgres_requires_host_side_database_session(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = managed.ManagedPostgres()
    calls: list[tuple[str, str]] = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def execute(self, sql: str) -> None: calls.append(("execute", sql))
        def fetchone(self): return (1,)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def cursor(self): return Cursor()

    def fake_connect(url: str, **kwargs):
        calls.append(("connect", url))
        assert url.startswith("postgresql://")
        assert "+psycopg" not in url
        assert kwargs["connect_timeout"] == 3
        return Connection()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    ready, error = postgres._host_database_ready()
    assert ready is True
    assert error == ""
    assert ("execute", "SELECT 1") in calls


def test_goal_contract_requires_unique_local_sibling_spans() -> None:
    protocol = (AGENT_ROOT / "src/agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")
    semantic = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    assert "每个证据片段必须能唯一归属到对应 Goal" in protocol
    assert "不能把整句或兄弟 Goal 的文字重复给多个 Goal" in semantic
    assert "candidates={candidates!r}" in semantic
