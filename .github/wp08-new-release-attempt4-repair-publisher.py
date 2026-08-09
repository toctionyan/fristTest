#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: {count} for {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"

old_validator = '''def _validate_with_production_goal_contract(
    *, case_id: str, user_text: str, goals: list[dict[str, Any]]
) -> dict[str, Any]:
    result, declared = validate_goal_declaration(
        state={"current_user_input": user_text},
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
    )
    if not result.get("ok") or declared is None:
        errors = (result.get("data") or {}).get("errors") or [result.get("code")]
        raise RuntimeError(
            f"{case_id}: production goal declaration rejected model output: {errors}"
        )
    return declared
'''
new_validator = '''def _production_goal_declaration_evaluation(
    *, user_text: str, goals: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Evaluate a declaration through the exact production Runtime contract."""
    return validate_goal_declaration(
        state={"current_user_input": user_text},
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
    )


def _validate_with_production_goal_contract(
    *, case_id: str, user_text: str, goals: list[dict[str, Any]]
) -> dict[str, Any]:
    result, declared = _production_goal_declaration_evaluation(
        user_text=user_text,
        goals=goals,
    )
    if not result.get("ok") or declared is None:
        errors = (result.get("data") or {}).get("errors") or [result.get("code")]
        raise RuntimeError(
            f"{case_id}: production goal declaration rejected model output: {errors}"
        )
    return declared
'''
replace_once(smoke, old_validator, new_validator)

old_repair = '''    messages: list[Any] = [system, HumanMessage(content=user_text)]
    last_error: Exception | None = None
    for attempt in range(1, 3):
        response, trace = invoke_model(
            purpose=f"preprod_semantic_goal:{case_id}:attempt{attempt}",
            model=bound,
            payload=messages,
        )
        attestation = attest_real_model_metadata(response=response, identity=identity)
        candidates = tool_calls(response)
        if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
            raise RuntimeError(f"{case_id}: model did not emit exactly one declare_turn_goals call")
        call = candidates[0]
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
        try:
            declared = _validate_with_production_goal_contract(
                case_id=case_id,
                user_text=user_text,
                goals=goals,
            )
            return goals, declared, {"trace": trace, "attestation": attestation}, attempt
        except RuntimeError as exc:
            last_error = exc
            if attempt >= 2:
                break
            tool_call_id = str(call.get("id") or f"{case_id}:declare:{attempt}")
            messages = [
                system,
                HumanMessage(content=user_text),
                response,
                ToolMessage(
                    tool_call_id=tool_call_id,
                    content=json.dumps(
                        {
                            "ok": False,
                            "code": "GOAL_DECLARATION_RETRY_REQUIRED",
                            "message": (
                                "Runtime 未冻结当前候选。重新逐段检查同一用户原话中的每一个可独立完成业务效果；"
                                "不能删除系统没有精确能力的分支，也不能把它改写为相近能力。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
    raise RuntimeError(f"{case_id}: bounded production declaration repair exhausted: {last_error}")
'''
new_repair = '''    messages: list[Any] = [system, HumanMessage(content=user_text)]
    last_result: dict[str, Any] | None = None
    for attempt in range(1, 3):
        response, trace = invoke_model(
            purpose=f"preprod_semantic_goal:{case_id}:attempt{attempt}",
            model=bound,
            payload=messages,
        )
        attestation = attest_real_model_metadata(response=response, identity=identity)
        candidates = tool_calls(response)
        if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
            raise RuntimeError(f"{case_id}: model did not emit exactly one declare_turn_goals call")
        call = candidates[0]
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
        result, declared = _production_goal_declaration_evaluation(
            user_text=user_text,
            goals=goals,
        )
        if result.get("ok") and declared is not None:
            return goals, declared, {"trace": trace, "attestation": attestation}, attempt
        last_result = result
        if attempt >= 2:
            break
        tool_call_id = str(call.get("id") or f"{case_id}:declare:{attempt}")
        # Production execute_agent_loop_calls_node returns this exact Runtime
        # result as the ToolMessage. Keep certification behavior identical:
        # the model may see the deterministic rejection code, validation
        # errors, current_user_input and repair_contract, but no oracle count,
        # expected effect identity, expected span or expected dependency.
        messages = [
            system,
            HumanMessage(content=user_text),
            response,
            ToolMessage(
                tool_call_id=tool_call_id,
                name="declare_turn_goals",
                content=json.dumps(result, ensure_ascii=False, default=str),
            ),
        ]
    errors = ((last_result or {}).get("data") or {}).get("errors") or [(last_result or {}).get("code")]
    raise RuntimeError(
        f"{case_id}: bounded production declaration repair exhausted: "
        f"{case_id}: production goal declaration rejected model output: {errors}"
    )
'''
replace_once(smoke, old_repair, new_repair)

# Focused governance regression file. It deliberately tests deterministic
# Runtime payload parity without making a provider call.
test_file = ROOT / "skill-system/tests/test_wp08_new_release_attempt4_repair.py"
test_file.write_text(r'''from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_smoke():
    path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_attempt4_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Attempt4RepairTests(unittest.TestCase):
    def test_production_rejection_payload_preserves_exact_evidence_rule_and_current_input(self) -> None:
        smoke = _load_smoke()
        user_text = "查一下我的订单，再查下物流到哪了"
        result, declared = smoke._production_goal_declaration_evaluation(
            user_text=user_text,
            goals=[
                {
                    "goal_id": "g1",
                    "description": "查订单",
                    "evidence_span": "查一下我的订单",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
                },
                {
                    "goal_id": "g2",
                    "description": "查物流",
                    "evidence_span": "查询订单物流位置",
                    "required": True,
                    "depends_on": ["g1"],
                    "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
                },
            ],
        )
        self.assertIsNone(declared)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "GOAL_DECLARATION_INVALID")
        self.assertIn("evidence_not_in_current_turn:g2", result["data"]["errors"])
        self.assertEqual(result["data"]["current_user_input"], user_text)
        self.assertEqual(
            result["data"]["repair_contract"]["evidence_span_rule"],
            "literal_contiguous_substring",
        )

    def test_bounded_repair_forwards_exact_runtime_result_without_oracle_material(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        start = source.index("def _declare_with_bounded_production_repair")
        end = source.index("def _identity_failure_reason", start)
        helper = source[start:end]
        self.assertIn("_production_goal_declaration_evaluation", helper)
        self.assertIn("content=json.dumps(result, ensure_ascii=False, default=str)", helper)
        self.assertIn('name="declare_turn_goals"', helper)
        self.assertIn("for attempt in range(1, 3)", helper)
        self.assertNotIn("goal_oracle", helper)
        self.assertNotIn("_match_oracle", helper)
        self.assertNotIn("expected_effect", helper)
        self.assertNotIn("expected span", helper.lower())

    def test_oracle_still_runs_only_after_production_declaration_is_accepted(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        loop = source.index("for case in cases:")
        declaration = source.index("_declare_with_bounded_production_repair(", loop)
        oracle = source.index("_match_oracle(", declaration)
        self.assertLess(declaration, oracle)

    def test_runtime_itself_returns_full_repair_context_as_tool_result(self) -> None:
        tool_runtime = (AGENT_SRC / "agent_core/lifecycle/tool_execution_runtime.py").read_text(encoding="utf-8")
        goal_planning = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
        self.assertIn("result, declared = validate_goal_declaration", tool_runtime)
        self.assertIn("tool_message = _tool_result_message(call, result)", tool_runtime)
        self.assertIn('"current_user_input": user_text', goal_planning)
        self.assertIn('"evidence_span_rule": "literal_contiguous_substring"', goal_planning)

    def test_provider_budget_and_browser_response_gate_remain_bounded(self) -> None:
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(smoke.relative_to(ROOT)),
        str(test_file.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
