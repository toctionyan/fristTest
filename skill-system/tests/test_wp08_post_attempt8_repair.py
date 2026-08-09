from __future__ import annotations

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
        postgres_source = source.split("def _postgres_graph_diagnostics", 1)[1].split(
            "def _configured_model_preflight", 1
        )[0]
        self.assertIn("build_sqlalchemy_store_provider(DatabaseSettings(", postgres_source)
        self.assertIn('provider.traces.list_recent_by_event_type(', postgres_source)
        self.assertNotIn("psycopg.connect", postgres_source)
        self.assertNotIn("FROM trace_logs", postgres_source)

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
