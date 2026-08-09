from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
SCRIPTS = ROOT / "scripts"
for path in (AGENT_SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class Attempt7FinalRepairTests(unittest.TestCase):
    def test_requested_effect_schema_requires_complete_identity(self) -> None:
        from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA

        goal = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
        effect = goal["properties"]["requested_effect"]
        self.assertEqual(
            set(effect["required"]),
            {"domain", "operation", "object_type"},
        )
        self.assertNotIn("enum", effect["properties"]["operation"])

    def test_production_prompt_keeps_exact_effect_identity_open_but_not_generic(self) -> None:
        from agent_core.lifecycle.dialogue_runtime import _loop_static_system_prompt

        prompt = _loop_static_system_prompt()
        self.assertIn("domain、operation、object_type", prompt)
        self.assertIn("精确对应", prompt)
        self.assertIn("不存在精确对应时才保留开放身份", prompt)
        self.assertIn("禁止用 query/action 等泛化类别", prompt)

    def test_semantic_smoke_receives_runtime_effect_vocabulary_without_weakening_oracle(self) -> None:
        source = (
            ROOT / "services" / "agent-service" / "scripts" / "verify_preprod_conversation_smoke.py"
        ).read_text(encoding="utf-8")
        self.assertIn("capability_effect_index(get_runtime_registry().capabilities)", source)
        self.assertIn("当前部署登记的业务效果身份", source)
        self.assertIn('_effect_identity(row.get("requested_effect")) == expected_effect', source)
        self.assertNotIn("expected_effect in", source)

    def test_browser_postgres_diagnostics_projects_only_safe_model_call_fields(self) -> None:
        path = ROOT / "scripts" / "verify_product_browser_journey.py"
        spec = importlib.util.spec_from_file_location("wp08_browser_diagnostic_test", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        snapshot = {
            "state": {
                "turn_index": 1,
                "current_user_input": "我买过什么？",
                "status": "AgentLoop",
                "model_call_trace": [{
                    "purpose": "agent_loop",
                    "model": "deepseek-v4-flash",
                    "sequence": 1,
                    "lane": "planner",
                    "status": "success",
                    "latency_ms": 61234,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "api_key": "must-not-leak",
                    "request_headers": {"Authorization": "must-not-leak"},
                }],
                "tool_trace": [],
            }
        }
        rows = [("thread-safe", json.dumps(snapshot, ensure_ascii=False))]
        projected = module._project_graph_diagnostic_rows(rows)
        self.assertEqual(projected[0]["model_call_trace"][0]["latency_ms"], 61234)
        serialized = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("Authorization", serialized)

    def test_protected_browser_uses_postgres_diagnostics_instead_of_forced_empty_list(self) -> None:
        source = (ROOT / "scripts" / "verify_product_browser_journey.py").read_text(encoding="utf-8")
        self.assertIn("_postgres_graph_diagnostics(persistence_url", source)
        self.assertNotIn("[]\n                if protected_preprod", source)
        self.assertIn('"diagnostic_status": "unavailable"', source)


if __name__ == "__main__":
    unittest.main()
