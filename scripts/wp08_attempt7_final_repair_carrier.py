#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected exactly one replacement target, found {count}")
    write(rel, text.replace(old, new, 1))


protocol = "services/agent-service/src/agent_core/lifecycle/protocol.py"
replace_once(
    protocol,
    '"description": "开放业务效果身份；不要求落入已有枚举。",',
    '"description": ("开放业务效果身份；domain、operation、object_type 三字段必须完整。"\n                                                "若当前部署登记的业务效果身份与用户请求精确对应，必须逐字段使用该身份；"\n                                                "没有精确对应时保留开放身份，禁止改写成相近能力或泛化类别。"),',
)
text = read(protocol)
marker = '"requested_effect": {'
start = text.index(marker)
end = text.index('"goal_type": {', start)
segment = text[start:end]
old_required = '"required": ["operation"],'
if segment.count(old_required) != 1:
    raise SystemExit("protocol.py: requested_effect required marker is not unique")
segment = segment.replace(
    old_required,
    '"required": ["domain", "operation", "object_type"],',
    1,
)
write(protocol, text[:start] + segment + text[end:])

dialogue = "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
goal_rule = (
    "- 一句话有多个目标、查询后再查询、查询后再动作、多个动作或长流程时，先按用户可以独立判断是否完成的业务效果拆 Goal；"
    "不要按接口或 Tool 数量拆 Goal。筛选、排序、数量、原因、权限检查、政策读取、Draft 与展示步骤都不是独立 Goal，"
    "除非用户明确把它们作为可单独验收的业务结果。一个 Goal 可以由多个 Tool 完成，多个 Goal 也可由一个综合 Tool 完成；"
    "每个 Goal 用开放 requested_effect 表达，系统没有能力时仍保留原 Goal。\n"
)
new_goal_rule = goal_rule + (
    "- requested_effect 必须完整填写 domain、operation、object_type。若用户业务效果与“当前部署登记的业务效果身份”中的某个身份精确对应，"
    "必须逐字段使用该精确身份；只有不存在精确对应时才保留开放身份。禁止用 query/action 等泛化类别替代已登记的精确业务效果，"
    "也禁止为了现有能力做同义词、近似或邻近能力改写。\n"
)
replace_once(dialogue, goal_rule, new_goal_rule)

smoke = "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    smoke,
    "from agent_core.composition import get_runtime_registry  # noqa: E402\n",
    "from agent_core.composition import get_runtime_registry  # noqa: E402\n"
    "from agent_core.runtime.capability_effects import capability_effect_index  # noqa: E402\n",
)
replace_once(
    smoke,
    "        model = get_model()\n"
    "        bound = model.bind_tools(planning_schemas()) if hasattr(model, \"bind_tools\") else model\n",
    "        model = get_model()\n"
    "        effect_vocabulary_json = json.dumps(\n"
    "            capability_effect_index(get_runtime_registry().capabilities),\n"
    "            ensure_ascii=False,\n"
    "            sort_keys=True,\n"
    "        )\n"
    "        bound = model.bind_tools(planning_schemas()) if hasattr(model, \"bind_tools\") else model\n",
)
replace_once(
    smoke,
    '            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"\n',
    '            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"\n'
    '            "requested_effect 必须完整填写 domain、operation、object_type；若下面登记词汇中存在与用户业务效果精确对应的身份，必须逐字段使用；"\n'
    '            "若不存在精确对应，保留开放业务效果，禁止用 query/action 等泛化类别或相近能力迁就。"\n'
    '            f"当前部署登记的业务效果身份（仅作精确身份词汇，不是自然语言分类表）：{effect_vocabulary_json}。"\n',
)

browser = "scripts/verify_product_browser_journey.py"
text = read(browser)
start_marker = "def _graph_diagnostics(database: Path, *, limit: int = 4) -> list[dict[str, Any]]:\n"
end_marker = "\ndef _configured_model_preflight(env: dict[str, str]) -> dict[str, Any]:\n"
start = text.index(start_marker)
end = text.index(end_marker, start)
replacement = """def _project_graph_diagnostic_rows(rows: list[tuple[Any, Any]]) -> list[dict[str, Any]]:\n    \"\"\"Project graph snapshots to a bounded, secret-free browser diagnostic.\"\"\"\n\n    diagnostics: list[dict[str, Any]] = []\n    for thread_id, raw in rows:\n        try:\n            payload = raw if isinstance(raw, dict) else json.loads(raw or \"{}\")\n        except (json.JSONDecodeError, TypeError):\n            continue\n        state = payload.get(\"state\") if isinstance(payload.get(\"state\"), dict) else payload\n        if not isinstance(state, dict):\n            continue\n        tool_trace = [row for row in list(state.get(\"tool_trace\") or []) if isinstance(row, dict)]\n        declared_args = next((\n            dict(row.get(\"args\") or {}) for row in tool_trace\n            if str(row.get(\"name\") or \"\") == \"declare_turn_goals\"\n            and isinstance(row.get(\"args\"), dict)\n        ), {})\n        workflow = read_plan_projection(state) or {}\n        diagnostics.append({\n            \"thread_id\": str(thread_id or \"\"),\n            \"turn\": state.get(\"turn_index\"),\n            \"input\": str(state.get(\"current_user_input\") or \"\")[:300],\n            \"status\": state.get(\"status\"),\n            \"model_failure\": _safe_model_failure(state),\n            \"final_answer\": str(state.get(\"current_final_answer\") or \"\")[:300],\n            \"tools\": [\n                {\n                    \"name\": row.get(\"name\"),\n                    \"code\": (row.get(\"result\") or {}).get(\"code\"),\n                    \"ok\": (row.get(\"result\") or {}).get(\"ok\"),\n                    \"args\": {\n                        key: (row.get(\"args\") or {}).get(key)\n                        for key in (\n                            \"target\", \"query\", \"constraint_bindings\", \"reference_span\",\n                            \"status_span\", \"question_span\", \"goal_ids\",\n                        )\n                        if key in (row.get(\"args\") or {})\n                    },\n                    \"permit_code\": ((row.get(\"result\") or {}).get(\"match_proof\") or {}).get(\"reason_code\"),\n                }\n                for row in tool_trace\n            ],\n            \"answer_release_alignment\": state.get(\"answer_release_alignment\"),\n            \"presentation_contract_violations\": state.get(\"presentation_contract_violations\"),\n            \"plan_projection\": {\n                \"status\": workflow.get(\"status\"),\n                \"goal_coverage_complete\": workflow.get(\"goal_coverage_complete\"),\n                \"goals\": list(workflow.get(\"goals\") or []),\n            },\n            \"clarification\": {\n                \"pending\": state.get(\"pending_clarification\"),\n                \"resolution\": declared_args.get(\"clarification_resolution\"),\n            },\n            \"capability_surface\": state.get(\"capability_surface\"),\n            \"workflow_status\": workflow.get(\"status\"),\n            \"model_iterations\": [\n                {\n                    \"loop_step\": call.get(\"loop_step\"),\n                    \"tool_names\": call.get(\"tool_names\"),\n                    \"response_content\": str(call.get(\"response_content\") or \"\")[:200],\n                }\n                for call in list(state.get(\"debug_llm_calls\") or [])\n                if isinstance(call, dict)\n            ],\n            \"model_call_trace\": [\n                {\n                    key: call.get(key)\n                    for key in (\n                        \"purpose\", \"model\", \"sequence\", \"lane\", \"status\", \"latency_ms\",\n                        \"prompt_tokens\", \"completion_tokens\", \"total_tokens\",\n                        \"prompt_cache_hit_tokens\", \"prompt_cache_miss_tokens\", \"prompt_cache_hit_rate\",\n                    )\n                    if call.get(key) is not None\n                }\n                for call in list(state.get(\"model_call_trace\") or [])\n                if isinstance(call, dict)\n            ],\n            \"context\": {\n                \"recent\": [\n                    {\"role\": row.get(\"role\"), \"content\": str(row.get(\"content\") or \"\")[:160]}\n                    for row in list((state.get(\"context_bundle\") or {}).get(\"recent_conversation_window\") or [])\n                    if isinstance(row, dict)\n                ],\n                \"visible_refs\": [\n                    {\n                        \"source_turn\": row.get(\"source_turn\"),\n                        \"shape\": row.get(\"shape\"),\n                        \"member_labels\": row.get(\"member_labels\"),\n                    }\n                    for row in list((state.get(\"context_bundle\") or {}).get(\"visible_result_refs\") or [])\n                    if isinstance(row, dict)\n                ],\n            },\n        })\n    return diagnostics\n\n\ndef _graph_diagnostics(database: Path, *, limit: int = 4) -> list[dict[str, Any]]:\n    if not database.is_file():\n        return []\n    with closing(sqlite3.connect(database)) as connection:\n        rows = connection.execute(\n            \"SELECT thread_id, output_json FROM trace_logs \"\n            \"WHERE event_type='graph_snapshot' ORDER BY id DESC LIMIT ?\",\n            (max(1, min(int(limit), 500)),),\n        ).fetchall()\n    return _project_graph_diagnostic_rows(rows)\n\n\ndef _postgres_graph_diagnostics(database_url: str, *, limit: int = 4) -> list[dict[str, Any]]:\n    \"\"\"Best-effort safe diagnostics from the owned protected PostgreSQL runtime.\"\"\"\n\n    normalized = str(database_url or \"\").strip()\n    for prefix in (\"postgresql+psycopg://\", \"postgresql+psycopg2://\"):\n        if normalized.startswith(prefix):\n            normalized = \"postgresql://\" + normalized[len(prefix):]\n            break\n    try:\n        import psycopg\n\n        with psycopg.connect(normalized) as connection:\n            with connection.cursor() as cursor:\n                cursor.execute(\n                    \"SELECT thread_id, output_json FROM trace_logs \"\n                    \"WHERE event_type='graph_snapshot' ORDER BY id DESC LIMIT %s\",\n                    (max(1, min(int(limit), 500)),),\n                )\n                rows = cursor.fetchall()\n    except Exception as exc:\n        # Do not expose exception text because database errors may echo credentials.\n        return [{\n            \"diagnostic_status\": \"unavailable\",\n            \"error_type\": exc.__class__.__name__,\n        }]\n    return _project_graph_diagnostic_rows(rows)\n"""
write(browser, text[:start] + replacement + text[end:])

replace_once(
    browser,
    '            graph_diagnostics = (\n'
    '                []\n'
    '                if protected_preprod\n'
    '                else _graph_diagnostics(harness.runtime_dir / "agent.db", limit=diagnostic_limit)\n'
    '            )\n',
    '            graph_diagnostics = (\n'
    '                _postgres_graph_diagnostics(persistence_url, limit=diagnostic_limit)\n'
    '                if protected_preprod and persistence_url\n'
    '                else _graph_diagnostics(harness.runtime_dir / "agent.db", limit=diagnostic_limit)\n'
    '            )\n',
)

test_path = ROOT / "skill-system/tests/test_wp08_attempt7_final_repair.py"
test_path.write_text('from __future__ import annotations\n\nimport importlib.util\nimport json\nfrom pathlib import Path\nimport sys\nimport unittest\n\n\nROOT = Path(__file__).resolve().parents[2]\nAGENT_SRC = ROOT / "services" / "agent-service" / "src"\nSCRIPTS = ROOT / "scripts"\nfor path in (AGENT_SRC, SCRIPTS):\n    if str(path) not in sys.path:\n        sys.path.insert(0, str(path))\n\n\nclass Attempt7FinalRepairTests(unittest.TestCase):\n    def test_requested_effect_schema_requires_complete_identity(self) -> None:\n        from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA\n\n        goal = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]\n        effect = goal["properties"]["requested_effect"]\n        self.assertEqual(\n            set(effect["required"]),\n            {"domain", "operation", "object_type"},\n        )\n        self.assertNotIn("enum", effect["properties"]["operation"])\n\n    def test_production_prompt_keeps_exact_effect_identity_open_but_not_generic(self) -> None:\n        from agent_core.lifecycle.dialogue_runtime import _loop_static_system_prompt\n\n        prompt = _loop_static_system_prompt()\n        self.assertIn("domain、operation、object_type", prompt)\n        self.assertIn("精确对应", prompt)\n        self.assertIn("不存在精确对应时才保留开放身份", prompt)\n        self.assertIn("禁止用 query/action 等泛化类别", prompt)\n\n    def test_semantic_smoke_receives_runtime_effect_vocabulary_without_weakening_oracle(self) -> None:\n        source = (\n            ROOT / "services" / "agent-service" / "scripts" / "verify_preprod_conversation_smoke.py"\n        ).read_text(encoding="utf-8")\n        self.assertIn("capability_effect_index(get_runtime_registry().capabilities)", source)\n        self.assertIn("当前部署登记的业务效果身份", source)\n        self.assertIn(\'_effect_identity(row.get("requested_effect")) == expected_effect\', source)\n        self.assertNotIn("expected_effect in", source)\n\n    def test_browser_postgres_diagnostics_projects_only_safe_model_call_fields(self) -> None:\n        path = ROOT / "scripts" / "verify_product_browser_journey.py"\n        spec = importlib.util.spec_from_file_location("wp08_browser_diagnostic_test", path)\n        self.assertIsNotNone(spec)\n        self.assertIsNotNone(spec.loader)\n        module = importlib.util.module_from_spec(spec)\n        spec.loader.exec_module(module)\n\n        snapshot = {\n            "state": {\n                "turn_index": 1,\n                "current_user_input": "我买过什么？",\n                "status": "AgentLoop",\n                "model_call_trace": [{\n                    "purpose": "agent_loop",\n                    "model": "deepseek-v4-flash",\n                    "sequence": 1,\n                    "lane": "planner",\n                    "status": "success",\n                    "latency_ms": 61234,\n                    "prompt_tokens": 100,\n                    "completion_tokens": 20,\n                    "total_tokens": 120,\n                    "api_key": "must-not-leak",\n                    "request_headers": {"Authorization": "must-not-leak"},\n                }],\n                "tool_trace": [],\n            }\n        }\n        rows = [("thread-safe", json.dumps(snapshot, ensure_ascii=False))]\n        projected = module._project_graph_diagnostic_rows(rows)\n        self.assertEqual(projected[0]["model_call_trace"][0]["latency_ms"], 61234)\n        serialized = json.dumps(projected, ensure_ascii=False)\n        self.assertNotIn("must-not-leak", serialized)\n        self.assertNotIn("api_key", serialized)\n        self.assertNotIn("Authorization", serialized)\n\n    def test_protected_browser_uses_postgres_diagnostics_instead_of_forced_empty_list(self) -> None:\n        source = (ROOT / "scripts" / "verify_product_browser_journey.py").read_text(encoding="utf-8")\n        self.assertIn("_postgres_graph_diagnostics(persistence_url", source)\n        self.assertNotIn("[]\\n                if protected_preprod", source)\n        self.assertIn(\'"diagnostic_status": "unavailable"\', source)\n\n\nif __name__ == "__main__":\n    unittest.main()\n', encoding="utf-8")
