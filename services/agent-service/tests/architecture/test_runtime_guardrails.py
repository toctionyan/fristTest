from __future__ import annotations

from tests.support.paths import agent_root

from tests.support.runtime_support import runtime_deps

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from agent_core.ledger import artifact_entry
from agent_core.lifecycle.finalizer import _answer_from_terminal_tool
from agent_core.lifecycle.dialogue_runtime import _loop_messages, _loop_system_prompt
from agent_core.rag.access import normalize_scope, scope_filter
from agent_core.rag.index_jobs import DocumentIndexJobStore
from agent_core.rag.providers.pgvector_provider import PgVectorRagProvider
from agent_core.rag.providers.local_sparse_provider import LocalSparseRagProvider


def _state_with_order() -> tuple[dict, str]:
    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}
    artifact = artifact_entry(
        resource_type="order",
        resource_id="10001",
        label="蓝牙耳机（订单 10001）",
        facts={"product_name": "蓝牙耳机", "status": "已发货"},
        scope=scope,
        turn=1,
        source="test",
    )
    return {
        "artifact_ledger": [artifact],
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-1",
        "turn_index": 1,
        "agent_loop_step": 0,
        "agent_loop_max_steps": 8,
        "messages": [],
        "tool_trace": [],
    }, str(artifact["handle"])


def test_context_bundle_is_the_single_dynamic_prompt_projection(monkeypatch):
    monkeypatch.setenv("APP_PROFILE", "local")
    state, _ = _state_with_order()
    prompt = _loop_system_prompt(state, context_bundle_builder=runtime_deps().context_bundle_builder)
    assert prompt.count("【ContextBundle：已验证动态上下文；原始对话已作为 provider messages 发送，不在此重复】") == 1
    assert "【已验证业务账本】" not in prompt
    assert "artifact_ledger" not in prompt
    assert "omitted_context_audit" in prompt
    assert "visible_result_refs" in prompt


def test_provider_prompt_has_stable_cache_prefix_and_no_duplicate_dialogue(monkeypatch):
    monkeypatch.setenv("APP_PROFILE", "local")
    state, _ = _state_with_order()
    state["current_user_input"] = "其中最贵的是哪个？"
    state["messages"] = [
        HumanMessage(content="查一下我的订单"),
        AIMessage(content="已展示订单列表"),
        HumanMessage(content="其中最贵的是哪个？"),
    ]
    first = _loop_messages(
        state,
        context_bundle_builder=runtime_deps().context_bundle_builder,
    )

    changed = dict(state)
    changed["current_user_input"] = "其中最便宜的是哪个？"
    changed["agent_loop_step"] = 1
    changed["messages"] = [*state["messages"][:-1], HumanMessage(content="其中最便宜的是哪个？")]
    second = _loop_messages(
        changed,
        context_bundle_builder=runtime_deps().context_bundle_builder,
    )

    assert first[0].content == second[0].content
    assert first[1].content != second[1].content
    assert "recent_conversation_window" not in first[1].content
    assert sum("查一下我的订单" in str(message.content) for message in first) == 1
    assert sum("其中最贵的是哪个？" in str(message.content) for message in first) == 1


def test_consultation_terminal_requires_explicit_insufficient_notice_or_policy_source():
    state, handle = _state_with_order()
    state["tool_trace"] = [{
        "name": "consult_contextual_information",
        "result": {"ok": True, "data": {"consultation_only": True, "knowledge_available": False, "policy_evidence": []}},
    }]
    answer, error, _ = _answer_from_terminal_tool(
        state,
        {"name": "respond_to_user", "args": {"answer": "可以按售后规则处理。", "evidence_handles": [handle]}},
    )
    assert answer is None
    assert error == "consultation_requires_explicit_insufficient_evidence_notice"

    state["tool_trace"] = [{
        "name": "consult_contextual_information",
        "result": {"ok": True, "data": {"consultation_only": True, "knowledge_available": True, "policy_evidence": []}},
    }]
    answer, error, _ = _answer_from_terminal_tool(
        state,
        {"name": "respond_to_user", "args": {"answer": "可以按售后规则处理。", "evidence_handles": [handle]}},
    )
    assert answer is None
    assert error == "consultation_requires_policy_source"


def test_pending_offer_handle_is_not_read_or_written_outside_migration_boundary():
    root = agent_root(__file__) / "src" / "agent_core"
    allowed = {
        root / "transaction" / "active_draft.py",
        root / "transaction" / "checkpoint_migration.py",
    }
    forbidden_patterns = (
        'state.get("pending_offer_handle")',
        "state['pending_offer_handle']",
        'state["pending_offer_handle"]',
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        content = path.read_text(encoding="utf-8")
        if any(pattern in content for pattern in forbidden_patterns):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


class _Embedding:
    def embed_query(self, _query: str):
        return [0.1, 0.2]


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.args = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, args):
        self.sql = str(sql)
        self.args = tuple(args)

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, cursor: _Cursor):
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


def test_pgvector_provider_scopes_before_rank_and_top_k():
    cursor = _Cursor()
    provider = PgVectorRagProvider.__new__(PgVectorRagProvider)
    provider.collection = "knowledge"
    provider.embedding_provider = _Embedding()
    provider._connect = lambda: _Conn(cursor)  # type: ignore[method-assign]

    provider.search(
        "退款政策",
        top_k=5,
        filters=scope_filter(normalize_scope(tenant_id="tenant-a", user_id="u001")),
    )
    where_index = cursor.sql.index("WHERE")
    order_index = cursor.sql.index("ORDER BY")
    assert where_index < order_index
    assert "visibility = 'public'" in cursor.sql
    assert "tenant_id = %s" in cursor.sql
    assert cursor.args[3:6] == ("tenant-a", "tenant-a", "u001")


def test_pgvector_provider_applies_policy_domain_before_rank_and_top_k():
    cursor = _Cursor()
    provider = PgVectorRagProvider.__new__(PgVectorRagProvider)
    provider.collection = "knowledge"
    provider.embedding_provider = _Embedding()
    provider._connect = lambda: _Conn(cursor)  # type: ignore[method-assign]

    filters = scope_filter(normalize_scope(tenant_id="tenant-a", user_id="u001"))
    filters["policy_domain"] = "invoice"
    provider.search("发票政策", top_k=5, filters=filters)

    assert "metadata_json @> %s::jsonb" in cursor.sql
    assert cursor.sql.index("metadata_json @>") < cursor.sql.index("ORDER BY")
    assert '{"policy_domain": "invoice"}' in cursor.args


def test_local_rag_policy_domain_prevents_higher_scoring_cross_domain_release(tmp_path: Path):
    provider = LocalSparseRagProvider(tmp_path / "knowledge.db")
    provider.upsert_document(
        "refund-high-score",
        "退款政策",
        "test",
        ["发票 发票 发票，但这是退款政策，不能进入开票回答。"],
        metadata={"visibility": "public", "status": "published", "builtin": True, "policy_domain": "refund"},
    )
    provider.upsert_document(
        "invoice-policy",
        "发票政策",
        "test",
        ["已支付订单可以申请电子发票。"],
        metadata={"visibility": "public", "status": "published", "builtin": True, "policy_domain": "invoice"},
    )

    rows = provider.search(
        "发票",
        top_k=5,
        filters={
            **scope_filter(normalize_scope(tenant_id="tenant-a", user_id="u001")),
            "policy_domain": "invoice",
        },
    )

    assert [row["doc_id"] for row in rows] == ["invoice-policy"]


def test_document_jobs_are_scope_first(tmp_path: Path, request: pytest.FixtureRequest):
    jobs = DocumentIndexJobStore(tmp_path / "jobs.db")
    request.addfinalizer(jobs.close)
    job = jobs.enqueue(
        tenant_id="tenant-a",
        user_id="u001",
        visibility="tenant",
        file_path="/tmp/a.txt",
        title="a.txt",
        source="/tmp/a.txt",
        metadata={"tenant_id": "tenant-a", "owner_id": "u001", "visibility": "tenant"},
    )
    assert jobs.get_for_scope(job_id=job["job_id"], tenant_id="tenant-a", user_id="u001")
    assert jobs.get_for_scope(job_id=job["job_id"], tenant_id="tenant-b", user_id="u009") is None


def test_health_is_liveness_and_ready_can_fail_without_changing_liveness(monkeypatch):
    from types import SimpleNamespace
    from app.api import health_api

    assert health_api.health() == {"status": "ok"}
    monkeypatch.setattr(
        health_api,
        "readiness_report",
        lambda **_kwargs: {"status": "not_ready", "profile": "preprod", "checks": {"rag": {"status": "failed"}}},
    )
    response = health_api.ready(SimpleNamespace(app=object()))
    assert response.status_code == 503
    assert b'not_ready' in response.body


def test_rag_bootstrap_verifies_without_seeding(monkeypatch):
    from agent_core.rag.bootstrap import RagBootstrapService
    import agent_core.rag.bootstrap as bootstrap_module
    import agent_core.rag.ingest as ingest_module

    calls: list[str] = []

    class _Provider:
        backend_name = "fake"

    monkeypatch.setattr(bootstrap_module, "get_rag_provider", lambda: _Provider())
    monkeypatch.setattr(ingest_module, "seed_builtin_knowledge", lambda: calls.append("seed") or {"documents": 1})
    report = RagBootstrapService().verify_readiness(seed=False)
    assert report["ready"] is True
    assert calls == []
    RagBootstrapService().seed_builtin_knowledge()
    assert calls == ["seed"]
