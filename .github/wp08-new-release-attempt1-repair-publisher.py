#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Repair the stale independent semantic oracle. The production contract
# defines Goals as independently completable user business effects; filtering
# and target selection remain implementation steps rather than standalone Goals.
# The deterministic candidate must therefore also stop scripting list_orders as
# a fake Goal-owned prerequisite when the fixture already exposes verified
# single-member target handles for the two business effects.
catalog_path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
payload = json.loads(catalog_path.read_text(encoding="utf-8"))
case = next((row for row in payload.get("cases", []) if row.get("id") == "semantic_cancel_and_refund_branch"), None)
if not isinstance(case, dict):
    raise SystemExit("semantic_cancel_and_refund_branch missing")
turn = case["execution_contract"]["turn_contracts"][0]
old_oracle = list(turn.get("goal_oracle") or [])
if len(old_oracle) != 3:
    raise SystemExit(f"expected stale three-goal oracle, got {len(old_oracle)}")

cancel_goal = deepcopy(old_oracle[1])
cancel_goal.update({
    "oracle_id": "g1",
    "goal_type": "action",
    "evidence_span": "把待发货的订单取消",
    "depends_on": [],
    "required_tools": ["prepare_cancel_order"],
})
refund_goal = deepcopy(old_oracle[2])
refund_goal.update({
    "oracle_id": "g2",
    "goal_type": "consult",
    "evidence_span": "已签收的看看能不能退款",
    "depends_on": [],
    "required_tools": ["evaluate_refund_eligibility"],
})
turn["goal_oracle"] = [cancel_goal, refund_goal]

steps = list(turn.get("model_steps") or [])
if not steps:
    raise SystemExit("semantic_cancel_and_refund_branch model_steps missing")
declaration = steps[0]["tool_calls"][0]["args"]
old_goals = list(declaration.get("goals") or [])
if len(old_goals) != 3:
    raise SystemExit(f"expected stale three-goal scripted declaration, got {len(old_goals)}")
script_cancel = deepcopy(old_goals[1])
script_cancel.update({
    "goal_id": "g1",
    "description": "处理用户目标：把待发货的订单取消",
    "evidence_span": "把待发货的订单取消",
    "goal_type": "action",
    "depends_on": [],
})
script_cancel["requested_effect"]["raw_description"] = "处理用户目标：把待发货的订单取消"
script_refund = deepcopy(old_goals[2])
script_refund.update({
    "goal_id": "g2",
    "description": "处理用户目标：已签收的看看能不能退款",
    "evidence_span": "已签收的看看能不能退款",
    "goal_type": "consult",
    "depends_on": [],
})
script_refund["requested_effect"]["raw_description"] = "处理用户目标：已签收的看看能不能退款"
declaration["goals"] = [script_cancel, script_refund]

# Remove the implementation-shaped list_orders candidate. The fixture ledger
# already publishes exact order artifacts plus signed/pending single-member
# views, so the completion tools can bind those verified handles directly.
filtered_steps = [steps[0]]
for step in steps[1:]:
    calls = [call for call in list(step.get("tool_calls") or []) if isinstance(call, dict)]
    if calls and all(str(call.get("name") or "") == "list_orders" for call in calls):
        continue
    for call in calls:
        name = str(call.get("name") or "")
        if name == "prepare_cancel_order":
            call.setdefault("args", {})["goal_ids"] = ["g1"]
        elif name == "evaluate_refund_eligibility":
            call.setdefault("args", {})["goal_ids"] = ["g2"]
    filtered_steps.append(step)
turn["model_steps"] = filtered_steps

turn["allowed_tools"] = [
    name for name in list(turn.get("allowed_tools") or []) if str(name) != "list_orders"
]
turn["required_tools"] = [
    name for name in list(turn.get("required_tools") or []) if str(name) != "list_orders"
]
expected = turn.setdefault("expected", {})
expected["goal_count"] = 2
trace = expected.get("trace") if isinstance(expected.get("trace"), dict) else {}
trace["must_include"] = [
    name for name in list(trace.get("must_include") or []) if str(name) != "list_orders"
]
expected["trace"] = trace
port_calls = expected.get("port_calls") if isinstance(expected.get("port_calls"), dict) else {}
port_calls.pop("query_resources", None)
expected["port_calls"] = port_calls
catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 2) Align the independent real-model smoke with the same production Goal
# granularity rule without relaxing exact requested_effect matching.
smoke_path = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    smoke_path,
    '            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"\n'
    '            "requested_effect 必须完整填写 domain、operation、object_type；若下面登记词汇中存在与用户业务效果精确对应的身份，必须逐字段使用；"',
    '            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"\n'
    '            "Goal 只表示用户可独立判断完成与否的业务效果；筛选、选目标、输入、前置校验、政策读取、Draft 和展示都只是实现步骤，不能单独提升为 Goal。"\n'
    '            "requested_effect 必须完整填写 domain、operation、object_type；若下面登记词汇中存在与用户业务效果精确对应的身份，必须逐字段使用；"',
)


# 3) Add secret-free in-flight model-call logging. Graph snapshots only exist
# after a request reaches a checkpoint; a browser-killed in-flight provider call
# otherwise leaves no evidence about which bounded lane was waiting.
gateway_path = ROOT / "services/agent-service/src/agent_core/model_calls/gateway.py"
replace_once(
    gateway_path,
    "from dataclasses import dataclass, field\nimport os\nimport re\n",
    "from dataclasses import dataclass, field\nimport json\nimport logging\nimport os\nimport re\n",
)
replace_once(
    gateway_path,
    "ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES = frozenset({\n",
    "LOGGER = logging.getLogger(__name__)\n"
    "_MODEL_CALL_LOG_KEYS = (\n"
    "    'purpose', 'model', 'sequence', 'scope', 'lane', 'status', 'latency_ms',\n"
    "    'error_type', 'finish_reason', 'prompt_tokens', 'completion_tokens', 'total_tokens',\n"
    ")\n\n\n"
    "def _emit_model_call_log(event: str, record: dict[str, Any]) -> None:\n"
    "    \"\"\"Emit only allow-listed execution metadata; never prompt, payload or credentials.\"\"\"\n"
    "    try:\n"
    "        safe = {key: record.get(key) for key in _MODEL_CALL_LOG_KEYS if record.get(key) is not None}\n"
    "        LOGGER.info(\n"
    "            'model_call_event %s',\n"
    "            json.dumps({'event': str(event), **safe}, ensure_ascii=False, sort_keys=True),\n"
    "        )\n"
    "    except Exception:\n"
    "        # Observability is best-effort and must never affect model execution.\n"
    "        return\n\n\n"
    "ENVIRONMENTAL_MODEL_FAILURE_CATEGORIES = frozenset({\n",
)
replace_once(
    gateway_path,
    '    record: dict[str, Any] = {\n        "purpose": str(purpose),\n        "model": _model_label(model),\n        "sequence": ledger.used_calls + 1,\n        "scope": ledger.scope,\n        "lane": lane,\n    }\n    try:\n',
    '    record: dict[str, Any] = {\n        "purpose": str(purpose),\n        "model": _model_label(model),\n        "sequence": ledger.used_calls + 1,\n        "scope": ledger.scope,\n        "lane": lane,\n    }\n    _emit_model_call_log("started", record)\n    try:\n',
)
replace_once(
    gateway_path,
    '        record.update({\n            "status": "ok",\n            "latency_ms": round((perf_counter() - started) * 1000, 2),\n            **_provider_response_metadata(response),\n            **_model_usage(response),\n        })\n        return response, dict(record)\n',
    '        record.update({\n            "status": "ok",\n            "latency_ms": round((perf_counter() - started) * 1000, 2),\n            **_provider_response_metadata(response),\n            **_model_usage(response),\n        })\n        _emit_model_call_log("finished", record)\n        return response, dict(record)\n',
)
replace_once(
    gateway_path,
    '        record.update({\n            "status": "error",\n            "latency_ms": round((perf_counter() - started) * 1000, 2),\n            "error_type": exc.__class__.__name__,\n        })\n        raise\n',
    '        record.update({\n            "status": "error",\n            "latency_ms": round((perf_counter() - started) * 1000, 2),\n            "error_type": exc.__class__.__name__,\n        })\n        _emit_model_call_log("finished", record)\n        raise\n',
)


# 4) Add focused counterexamples that lock the repair and prove the browser SLA
# was not weakened to make certification pass.
test_path = ROOT / "skill-system/tests/test_wp08_new_release_attempt1_repair.py"
test_path.write_text(r'''from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


class NewReleaseAttempt1RepairTests(unittest.TestCase):
    def _case(self) -> dict:
        path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return next(row for row in payload["cases"] if row["id"] == "semantic_cancel_and_refund_branch")

    def test_filter_is_not_promoted_to_standalone_goal(self) -> None:
        turn = self._case()["execution_contract"]["turn_contracts"][0]
        oracle = turn["goal_oracle"]
        self.assertEqual(len(oracle), 2)
        self.assertEqual(oracle[0]["goal_type"], "action")
        self.assertEqual(oracle[0]["evidence_span"], "把待发货的订单取消")
        self.assertEqual(
            oracle[0]["requested_effect"],
            {"domain": "order", "operation": "cancel", "object_type": "order"},
        )
        self.assertEqual(oracle[0]["required_tools"], ["prepare_cancel_order"])
        self.assertEqual(oracle[1]["goal_type"], "consult")
        self.assertEqual(oracle[1]["oracle_id"], "g2")
        self.assertEqual(turn["expected"]["goal_count"], 2)
        self.assertFalse(any(
            row.get("requested_effect", {}).get("operation") == "list"
            for row in oracle
        ))

    def test_scripted_execution_uses_only_business_completion_tools(self) -> None:
        turn = self._case()["execution_contract"]["turn_contracts"][0]
        calls = [
            call
            for step in turn["model_steps"][1:]
            for call in step.get("tool_calls", [])
        ]
        by_name = {call["name"]: call for call in calls}
        self.assertNotIn("list_orders", by_name)
        self.assertEqual(by_name["prepare_cancel_order"]["args"]["goal_ids"], ["g1"])
        self.assertEqual(by_name["evaluate_refund_eligibility"]["args"]["goal_ids"], ["g2"])
        self.assertNotIn("list_orders", turn["required_tools"])
        self.assertNotIn("list_orders", turn["expected"]["trace"]["must_include"])
        self.assertNotIn("query_resources", turn["expected"]["port_calls"])

    def test_independent_semantic_prompt_uses_production_granularity_rule(self) -> None:
        production = (ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        smoke = (ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn("不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal", production)
        self.assertIn("筛选、选目标、输入、前置校验、政策读取、Draft 和展示都只是实现步骤，不能单独提升为 Goal", smoke)

    def test_inflight_model_log_is_allowlisted_and_secret_free(self) -> None:
        from agent_core.model_calls import gateway

        record = {
            "purpose": "agent_loop",
            "model": "deepseek-v4-flash",
            "sequence": 2,
            "scope": "request",
            "lane": "planner",
            "payload": "must-not-leak",
            "api_key": "must-not-leak",
            "request_headers": {"Authorization": "must-not-leak"},
        }
        with patch.object(gateway.LOGGER, "info") as info:
            gateway._emit_model_call_log("started", record)
        self.assertEqual(info.call_count, 1)
        serialized = " ".join(str(value) for value in info.call_args.args)
        self.assertIn("agent_loop", serialized)
        self.assertIn("planner", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("payload", serialized)

    def test_browser_response_sla_remains_120_seconds(self) -> None:
        source = (ROOT / "services/agent-service/frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('{ timeout: 120_000 }', source)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(catalog_path.relative_to(ROOT)),
        str(smoke_path.relative_to(ROOT)),
        str(gateway_path.relative_to(ROOT)),
        str(test_path.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
