from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate")


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"anchor_count:{relative}:{count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Publish module-owned semantic boundaries alongside exact effect identities.
replace_once(
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    'CAPABILITY_EFFECT_INDEX_VERSION = "capability-effect-index@1"',
    'CAPABILITY_EFFECT_INDEX_VERSION = "capability-effect-index@2"',
)
replace_once(
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    '''def support_effects_for_contract(contract: Any) -> tuple[str, ...]:
    return _contract_effects(getattr(contract, "support_effects", ()) or ())


def capability_effect_index(registry: CapabilityRegistry) -> dict[str, Any]:
''',
    '''def support_effects_for_contract(contract: Any) -> tuple[str, ...]:
    return _contract_effects(getattr(contract, "support_effects", ()) or ())


def _effect_semantic_guidance(contract: Any) -> dict[str, Any]:
    """Project bounded module-owned semantics without granting execution authority."""
    planning = getattr(contract, "planning_contract", None)
    target = getattr(planning, "target", None)
    bounded = lambda values: [str(value)[:96] for value in tuple(values or ())[:8] if str(value)]
    return {
        "planner_rule": str(getattr(contract, "planner_rule", "") or "")[:320],
        "target_cardinality": str(getattr(target, "cardinality", "") or "") or None,
        "target_resource_types": bounded(getattr(target, "resource_types", ()) if target is not None else ()),
        "discovery_examples": bounded(getattr(contract, "discovery_examples", ())),
        "exclusion_examples": bounded(getattr(contract, "exclusion_examples", ())),
    }


def _effect_index_row() -> dict[str, Any]:
    return {"completion_tools": [], "support_tools": [], "semantic_guidance": []}


def capability_effect_index(registry: CapabilityRegistry) -> dict[str, Any]:
''',
)
replace_once(
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    '''    grouped: dict[str, dict[str, list[str]]] = {}
    for tool_name in sorted(registry.tool_names()):
        contract = registry.contract_for_tool(tool_name)
        if contract is None or contract.execution_kind in {"unsupported", "clarification_read"}:
            continue
        for identity in completion_effects_for_contract(contract):
            grouped.setdefault(identity, {"completion_tools": [], "support_tools": []})[
                "completion_tools"
            ].append(tool_name)
        for identity in support_effects_for_contract(contract):
            grouped.setdefault(identity, {"completion_tools": [], "support_tools": []})[
                "support_tools"
            ].append(tool_name)
''',
    '''    grouped: dict[str, dict[str, Any]] = {}
    for tool_name in sorted(registry.tool_names()):
        contract = registry.contract_for_tool(tool_name)
        if contract is None or contract.execution_kind in {"unsupported", "clarification_read"}:
            continue
        for identity in completion_effects_for_contract(contract):
            row = grouped.setdefault(identity, _effect_index_row())
            row["completion_tools"].append(tool_name)
            guidance = _effect_semantic_guidance(contract)
            if guidance not in row["semantic_guidance"]:
                row["semantic_guidance"].append(guidance)
        for identity in support_effects_for_contract(contract):
            grouped.setdefault(identity, _effect_index_row())["support_tools"].append(tool_name)
''',
)
replace_once(
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    '''                "requested_effect_identity": identity,
                "completion_tool_count": len(set(row["completion_tools"])),
                "support_tool_count": len(set(row["support_tools"])),
''',
    '''                "requested_effect_identity": identity,
                "completion_tool_count": len(set(row["completion_tools"])),
                "support_tool_count": len(set(row["support_tools"])),
                "semantic_guidance": list(row.get("semantic_guidance") or []),
''',
)

# 2) Make both production and semantic smoke describe the projection accurately.
replace_once(
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    '''说明：这只是精确身份词汇，不是自然语言分类表。语义声明可使用完全匹配的身份；不匹配时保留开放 requested_effect，禁止迁就现有能力。''',
    '''说明：这是模块注册的精确业务效果身份及其语义边界，只帮助模型选择结构化 identity；Runtime 仍只按结构化 identity 精确匹配，不使用自然语言说明、示例、关键词或相似度授予能力。没有精确对应时保留开放 requested_effect。''',
)
replace_once(
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
    '''            f"当前部署登记的业务效果身份（仅作精确身份词汇，不是自然语言分类表）：{effect_vocabulary_json}。"''',
    '''            f"当前部署登记的业务效果身份及模块语义边界（只帮助模型选择结构化 identity；Runtime 仍 exact-match，不是关键词分类器）：{effect_vocabulary_json}。"''',
)

# 3) Add a storage-neutral event-type read to TraceRepository and both providers.
replace_once(
    "services/agent-service/src/agent_core/storage/repositories/base.py",
    '''    def list_recent(self, limit: int = 100) -> list[dict]: ...
    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]: ...
''',
    '''    def list_recent(self, limit: int = 100) -> list[dict]: ...
    def list_recent_by_event_type(self, event_type: str, limit: int = 100) -> list[dict]: ...
    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]: ...
''',
)
replace_once(
    "services/agent-service/src/agent_core/persistence/trace_store.py",
    '''    def list_recent(self, limit: int = 100) -> list[dict]:
        return self.query_all("SELECT * FROM trace_logs ORDER BY id DESC LIMIT ?", (limit,))

    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]:
''',
    '''    def list_recent(self, limit: int = 100) -> list[dict]:
        return self.query_all("SELECT * FROM trace_logs ORDER BY id DESC LIMIT ?", (limit,))

    def list_recent_by_event_type(self, event_type: str, limit: int = 100) -> list[dict]:
        return self.query_all(
            "SELECT * FROM trace_logs WHERE event_type=? ORDER BY id DESC LIMIT ?",
            (str(event_type), max(1, int(limit))),
        )

    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]:
''',
)
replace_once(
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
    '''    def list_recent(self, limit: int = 100) -> list[dict]:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(self.sa.select(table).order_by(table.c.id.desc()).limit(limit)).fetchall()]

    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]:
''',
    '''    def list_recent(self, limit: int = 100) -> list[dict]:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(self.sa.select(table).order_by(table.c.id.desc()).limit(limit)).fetchall()]

    def list_recent_by_event_type(self, event_type: str, limit: int = 100) -> list[dict]:
        table = self.t["trace_logs"]
        stmt = self.sa.select(table).where(table.c.event_type == str(event_type)).order_by(table.c.id.desc()).limit(max(1, int(limit)))
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(stmt).fetchall()]

    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]:
''',
)

# 4) Browser diagnostics consume the repository contract instead of guessing a SQL table.
replace_once(
    "scripts/verify_product_browser_journey.py",
    '''from agent_core.kernel.plan_projection_contract import read_plan_projection  # noqa: E402
''',
    '''from agent_core.kernel.plan_projection_contract import read_plan_projection  # noqa: E402
from agent_core.persistence.database_settings import DatabaseSettings  # noqa: E402
from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider  # noqa: E402
''',
)
replace_once(
    "scripts/verify_product_browser_journey.py",
    '''def _postgres_graph_diagnostics(database_url: str, *, limit: int = 4) -> list[dict[str, Any]]:
    """Best-effort safe diagnostics from the owned protected PostgreSQL runtime."""

    normalized = str(database_url or "").strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if normalized.startswith(prefix):
            normalized = "postgresql://" + normalized[len(prefix):]
            break
    try:
        import psycopg

        with psycopg.connect(normalized) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT thread_id, output_json FROM trace_logs "
                    "WHERE event_type='graph_snapshot' ORDER BY id DESC LIMIT %s",
                    (max(1, min(int(limit), 500)),),
                )
                rows = cursor.fetchall()
    except Exception as exc:
        # Do not expose exception text because database errors may echo credentials.
        return [{
            "diagnostic_status": "unavailable",
            "error_type": exc.__class__.__name__,
        }]
    return _project_graph_diagnostic_rows(rows)
''',
    '''def _postgres_graph_diagnostics(database_url: str, *, limit: int = 4) -> list[dict[str, Any]]:
    """Read protected traces through the same repository authority as Runtime."""

    provider = None
    try:
        provider = build_sqlalchemy_store_provider(DatabaseSettings(
            backend="postgres",
            database_url=str(database_url or "").strip(),
            sqlite_path=AGENT_ROOT / "runtime/sqlite/app.db",
            create_schema=False,
        ))
        records = provider.traces.list_recent_by_event_type(
            "graph_snapshot", max(1, min(int(limit), 500))
        )
        rows = [(row.get("thread_id"), row.get("output_json")) for row in records]
    except Exception as exc:
        # Do not expose exception text because database errors may echo credentials.
        return [{"diagnostic_status": "unavailable", "error_type": exc.__class__.__name__}]
    finally:
        if provider is not None:
            provider.close()
    return _project_graph_diagnostic_rows(rows)
''',
)

# 5) Regression/counterexample coverage.  Keep it outside protected source roots.
test_path = ROOT / "skill-system/tests/test_wp08_post_attempt8_repair.py"
test_path.write_text('''from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
SCRIPTS = ROOT / "scripts"
for path in (AGENT_SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class PostAttempt8RepairTests(unittest.TestCase):
    def _registry(self):
        from agent_core.kernel.capability_registry import CapabilityBinding, CapabilityRegistry
        from agent_modules.ecommerce.capabilities.list_orders import DEFINITION as list_orders
        from agent_modules.ecommerce.capabilities.get_order_details import DEFINITION as order_details

        def noop(*args, **kwargs):
            return {"ok": True}

        return CapabilityRegistry([
            CapabilityBinding("ecommerce", list_orders.contract, list_orders.schema, noop),
            CapabilityBinding("ecommerce", order_details.contract, order_details.schema, noop),
        ])

    def test_effect_index_projects_module_semantics_without_tool_names(self) -> None:
        from agent_core.runtime.capability_effects import capability_effect_index

        index = capability_effect_index(self._registry())
        self.assertEqual(index["version"], "capability-effect-index@2")
        effects = {row["requested_effect_identity"]: row for row in index["effects"]}
        listed = effects["order.list:order"]["semantic_guidance"][0]
        details = effects["order.query_details:order"]["semantic_guidance"][0]
        self.assertEqual(listed["target_cardinality"], "collection")
        self.assertIn("查一下我的订单", listed["discovery_examples"])
        self.assertEqual(details["target_cardinality"], "exactly_one")
        self.assertIn("订单详情", details["discovery_examples"])
        serialized = json.dumps(index, ensure_ascii=False)
        self.assertNotIn("list_orders", serialized)
        self.assertNotIn("get_order_details", serialized)

    def test_effect_guidance_does_not_change_exact_runtime_matching(self) -> None:
        from agent_core.runtime.capability_effects import discover_exact_effect_surface

        registry = self._registry()
        exact = discover_exact_effect_surface(registry, [{
            "goal_id": "g1",
            "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
        }])
        self.assertEqual(exact["goals"][0]["status"], "exact_supported")
        self.assertEqual(exact["goals"][0]["completion_tools"], ["list_orders"])
        unknown = discover_exact_effect_surface(registry, [{
            "goal_id": "g1",
            "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
        }])
        self.assertEqual(unknown["goals"][0]["status"], "absent_proven")
        self.assertEqual(unknown["goals"][0]["completion_tools"], [])
        self.assertFalse(unknown["similarity_used"])

    def test_sqlite_trace_repository_filters_event_type(self) -> None:
        from agent_core.persistence.trace_store import TraceLogger

        with tempfile.TemporaryDirectory() as directory:
            logger = TraceLogger(Path(directory) / "trace.db")
            logger.init_db()
            logger.log_event("thread-a", "u001", "chat_start", output_data={"ignored": True})
            logger.log_event("thread-a", "u001", "graph_snapshot", output_data={"state": {"turn_index": 1}})
            rows = logger.list_recent_by_event_type("graph_snapshot", 10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "graph_snapshot")
            logger.close()

    def test_browser_postgres_diagnostics_uses_store_provider_not_physical_table(self) -> None:
        source = (ROOT / "scripts/verify_product_browser_journey.py").read_text(encoding="utf-8")
        self.assertIn("build_sqlalchemy_store_provider(DatabaseSettings(", source)
        self.assertIn('provider.traces.list_recent_by_event_type(', source)
        self.assertNotIn("psycopg.connect", source)
        self.assertNotIn("FROM trace_logs", source)

    def test_browser_projection_still_redacts_unapproved_model_metadata(self) -> None:
        path = ROOT / "scripts/verify_product_browser_journey.py"
        spec = importlib.util.spec_from_file_location("post_attempt8_browser", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [("thread-a", json.dumps({"state": {"turn_index": 1, "model_call_trace": [{
            "purpose": "agent_loop", "status": "success", "latency_ms": 1234,
            "api_key": "must-not-leak", "request_headers": {"Authorization": "must-not-leak"},
        }]}}))]
        projected = module._project_graph_diagnostic_rows(rows)
        serialized = json.dumps(projected, ensure_ascii=False)
        self.assertEqual(projected[0]["model_call_trace"][0]["latency_ms"], 1234)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("Authorization", serialized)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
