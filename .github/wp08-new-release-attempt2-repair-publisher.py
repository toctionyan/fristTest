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


def replace_between(path: Path, start: str, end: str, new_block: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"start marker missing in {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"end marker missing in {path}: {end!r}")
    path.write_text(text[:start_index] + new_block + text[end_index:], encoding="utf-8")


# 1) Preserve unsupported user business effects as open Goals. The generic
# `open.unsupported_request:request` identity incorrectly encodes system
# capability status as user semantics. Runtime already owns absence proof and
# routes an unmatched exact effect to the registered unsupported reporter.
catalog_path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
payload = json.loads(catalog_path.read_text(encoding="utf-8"))
expected_generic = {"domain": "open", "operation": "unsupported_request", "object_type": "request"}
open_effect = {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"}
oracle_changed = 0
scripted_changed = 0
for case in payload.get("cases", []):
    if not isinstance(case, dict):
        continue
    contract = case.get("execution_contract") if isinstance(case.get("execution_contract"), dict) else {}
    for turn in contract.get("turn_contracts", []):
        if not isinstance(turn, dict):
            continue
        for goal in turn.get("goal_oracle", []):
            if not isinstance(goal, dict) or "快递员" not in str(goal.get("evidence_span") or ""):
                continue
            if goal.get("requested_effect") == expected_generic:
                goal["requested_effect"] = deepcopy(open_effect)
                goal["requested_effect_match"] = "unregistered_open"
                oracle_changed += 1
        for step in turn.get("model_steps", []):
            if not isinstance(step, dict):
                continue
            for call in step.get("tool_calls", []):
                if not isinstance(call, dict) or str(call.get("name") or "") != "declare_turn_goals":
                    continue
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                for goal in args.get("goals", []):
                    if not isinstance(goal, dict) or "快递员" not in str(goal.get("evidence_span") or ""):
                        continue
                    effect = goal.get("requested_effect") if isinstance(goal.get("requested_effect"), dict) else {}
                    if {key: effect.get(key) for key in ("domain", "operation", "object_type")} == expected_generic:
                        goal["requested_effect"] = {
                            **deepcopy(open_effect),
                            "raw_description": str(effect.get("raw_description") or goal.get("description") or ""),
                        }
                        scripted_changed += 1
if oracle_changed != 2 or scripted_changed != 2:
    raise SystemExit(
        f"unexpected courier unsupported fixtures: oracle={oracle_changed}, scripted={scripted_changed}"
    )
catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 2) The protected real-model semantic harness must not require an arbitrary
# exact spelling for an unregistered open effect. It still requires a distinct
# Goal, literal evidence, a structurally complete effect identity, and proof
# that the identity is not a registered nearby capability. The independent
# production alignment verifier remains responsible for semantic completeness.
smoke_path = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    smoke_path,
    "def _effect_identity(value: Any) -> tuple[str, str, str]:\n"
    "    source = value if isinstance(value, dict) else {}\n"
    "    return tuple(str(source.get(key) or \"\").strip().casefold() for key in _EFFECT_KEYS)  # type: ignore[return-value]\n",
    "def _effect_identity(value: Any) -> tuple[str, str, str]:\n"
    "    source = value if isinstance(value, dict) else {}\n"
    "    return tuple(str(source.get(key) or \"\").strip().casefold() for key in _EFFECT_KEYS)  # type: ignore[return-value]\n\n\n"
    "def _effect_key(value: tuple[str, str, str]) -> str:\n"
    "    domain, operation, object_type = value\n"
    "    return f\"{domain}.{operation}:{object_type}\" if domain and operation and object_type else \"\"\n",
)
replace_once(
    smoke_path,
    "def _match_oracle(*, case_id: str, oracle: list[dict[str, Any]], goals: list[dict[str, Any]]) -> None:\n",
    "def _match_oracle(\n"
    "    *,\n"
    "    case_id: str,\n"
    "    oracle: list[dict[str, Any]],\n"
    "    goals: list[dict[str, Any]],\n"
    "    registered_effect_identities: set[str],\n"
    ") -> None:\n",
)
replace_once(
    smoke_path,
    "        expected_effect = _effect_identity(expected.get(\"requested_effect\"))\n"
    "        if not all(expected_effect):\n"
    "            raise RuntimeError(\n"
    "                f\"{case_id}: oracle goal {expected.get('oracle_id')!r} lacks authoritative requested_effect identity\"\n"
    "            )\n\n"
    "        def candidate_matches(row: dict[str, Any], *, fuzzy_span: bool) -> bool:\n"
    "            span_ok = (\n"
    "                _span_matches_oracle(expected=evidence, actual=row.get(\"evidence_span\"))\n"
    "                if fuzzy_span\n"
    "                else str(row.get(\"evidence_span\") or \"\") == evidence\n"
    "            )\n"
    "            return (\n"
    "                span_ok\n"
    "                and bool(row.get(\"required\", True)) == required\n"
    "                and _effect_identity(row.get(\"requested_effect\")) == expected_effect\n"
    "            )\n",
    "        expected_effect = _effect_identity(expected.get(\"requested_effect\"))\n"
    "        match_mode = str(expected.get(\"requested_effect_match\") or \"exact\").strip().casefold()\n"
    "        if match_mode not in {\"exact\", \"unregistered_open\"}:\n"
    "            raise RuntimeError(f\"{case_id}: unsupported requested_effect_match={match_mode!r}\")\n"
    "        if not all(expected_effect):\n"
    "            raise RuntimeError(\n"
    "                f\"{case_id}: oracle goal {expected.get('oracle_id')!r} lacks requested_effect identity\"\n"
    "            )\n\n"
    "        def candidate_matches(row: dict[str, Any], *, fuzzy_span: bool) -> bool:\n"
    "            span_ok = (\n"
    "                _span_matches_oracle(expected=evidence, actual=row.get(\"evidence_span\"))\n"
    "                if fuzzy_span\n"
    "                else str(row.get(\"evidence_span\") or \"\") == evidence\n"
    "            )\n"
    "            candidate_effect = _effect_identity(row.get(\"requested_effect\"))\n"
    "            effect_ok = (\n"
    "                bool(all(candidate_effect))\n"
    "                and _effect_key(candidate_effect) not in registered_effect_identities\n"
    "                if match_mode == \"unregistered_open\"\n"
    "                else candidate_effect == expected_effect\n"
    "            )\n"
    "            return (\n"
    "                span_ok\n"
    "                and bool(row.get(\"required\", True)) == required\n"
    "                and effect_ok\n"
    "            )\n",
)
replace_once(
    smoke_path,
    "                f\"{case_id}: no unique model goal matches oracle span={evidence!r}, \"\n"
    "                f\"requested_effect={expected_effect!r}, candidates={candidates!r}\"\n",
    "                f\"{case_id}: no unique model goal matches oracle span={evidence!r}, \"\n"
    "                f\"requested_effect={expected_effect!r}, match_mode={match_mode!r}, candidates={candidates!r}\"\n",
)
replace_once(
    smoke_path,
    "        effect_vocabulary_json = json.dumps(\n"
    "            capability_effect_index(get_runtime_registry().capabilities),\n"
    "            ensure_ascii=False,\n"
    "            sort_keys=True,\n"
    "        )\n",
    "        effect_index = capability_effect_index(get_runtime_registry().capabilities)\n"
    "        effect_vocabulary_json = json.dumps(\n"
    "            effect_index,\n"
    "            ensure_ascii=False,\n"
    "            sort_keys=True,\n"
    "        )\n"
    "        registered_effect_identities = {\n"
    "            str(row.get(\"requested_effect_identity\") or \"\").strip().casefold()\n"
    "            for row in list(effect_index.get(\"effects\") or [])\n"
    "            if isinstance(row, dict) and str(row.get(\"requested_effect_identity\") or \"\").strip()\n"
    "        }\n",
)
replace_once(
    smoke_path,
    "            \"不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。\"\n",
    "            \"能力词汇中没有精确身份的分支也必须保留成独立 Goal；requested_effect 要写用户实际请求的开放业务效果，不能写 unsupported_request、能力缺失或系统不支持来替代用户语义。\"\n"
    "            \"不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。\"\n",
)
replace_once(
    smoke_path,
    "                _match_oracle(case_id=case[\"id\"], oracle=oracle, goals=goals)\n",
    "                _match_oracle(\n"
    "                    case_id=case[\"id\"],\n"
    "                    oracle=oracle,\n"
    "                    goals=goals,\n"
    "                    registered_effect_identities=registered_effect_identities,\n"
    "                )\n",
)


# 3) Bridge a declaration-level `clarify` verdict to the only legal customer
# interaction: one ask_user_clarification call. The rejected candidate is never
# frozen, no business capability is exposed, and the user reply starts a fresh
# semantic declaration on the next turn.
dialogue_path = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
replace_once(
    dialogue_path,
    "from agent_core.lifecycle.protocol import TERMINAL_TOOL_NAMES, agent_loop_schemas, planning_schemas\n",
    "from agent_core.lifecycle.protocol import (\n"
    "    ASK_USER_CLARIFICATION_SCHEMA,\n"
    "    TERMINAL_TOOL_NAMES,\n"
    "    agent_loop_schemas,\n"
    "    planning_schemas,\n"
    ")\n",
)
replace_once(
    dialogue_path,
    "FINAL_PROTOCOL_MAX_RETRIES = 1\nGOAL_DECLARATION_MAX_RETRIES = 2\n\n\ndef get_model():\n",
    "FINAL_PROTOCOL_MAX_RETRIES = 1\nGOAL_DECLARATION_MAX_RETRIES = 2\n\n\ndef _declaration_clarification_required(state: dict[str, Any]) -> bool:\n"
    "    \"\"\"Return whether this same turn is paused before semantic freeze.\n\n"
    "    The authority is the Runtime result of the current turn's sole\n"
    "    declaration call. A prior-turn clarification can never reopen this\n"
    "    lane because the TurnPlan turn index must match.\n"
    "    \"\"\"\n"
    "    if goal_plan_ready(state):\n"
    "        return False\n"
    "    plan = state.get(\"current_turn_plan\") if isinstance(state.get(\"current_turn_plan\"), dict) else {}\n"
    "    if int(plan.get(\"turn\") or -1) != int(state.get(\"turn_index\") or 0):\n"
    "        return False\n"
    "    if not any(\n"
    "        isinstance(call, dict) and str(call.get(\"name\") or \"\") == \"declare_turn_goals\"\n"
    "        for call in list(plan.get(\"tool_calls\") or [])\n"
    "    ):\n"
    "        return False\n"
    "    for row in reversed(list(state.get(\"tool_trace\") or [])):\n"
    "        if not isinstance(row, dict) or str(row.get(\"name\") or \"\") != \"declare_turn_goals\":\n"
    "            continue\n"
    "        result = row.get(\"result\") if isinstance(row.get(\"result\"), dict) else {}\n"
    "        return bool(\n"
    "            not result.get(\"ok\")\n"
    "            and str(result.get(\"code\") or \"\") == \"GOAL_DECLARATION_REQUIRES_CLARIFICATION\"\n"
    "        )\n"
    "    return False\n\n\ndef get_model():\n",
)
replace_once(
    dialogue_path,
    "    budget = compute_loop_budget(state)\n    terminal_rule = (\n",
    "    budget = compute_loop_budget(state)\n"
    "    declaration_clarification_mode = _declaration_clarification_required(state)\n"
    "    terminal_rule = (\n",
)
replace_once(
    dialogue_path,
    "    if str(state.get(\"status\") or \"\") == \"ClarificationNotNeededRetry\":\n",
    "    if declaration_clarification_mode:\n"
    "        protocol_repair_rule = (\n"
    "            \"上一次 declare_turn_goals 已由独立语义验证明确判定需要用户澄清；本次不能再次声明 Goal，\"\n"
    "            \"也不能调用任何业务能力。只能调用一次 ask_user_clarification，直接询问缺失的对象、范围、条件或真实意图。\"\n"
    "        )\n"
    "    elif str(state.get(\"status\") or \"\") == \"ClarificationNotNeededRetry\":\n",
)
replace_between(
    dialogue_path,
    "    planning_rule = (\n",
    "    surface = state.get(\"capability_surface\")",
    "    if declaration_clarification_mode:\n"
    "        planning_rule = (\n"
    "            \"当前处于语义冻结前的澄清暂停阶段：独立验证已证明当前候选不能安全冻结。\"\n"
    "            \"只能向用户提出一个最小必要澄清问题；不得改写或冻结 requested_effect，不得发现、调用或暗示任何业务能力。\"\n"
    "        )\n"
    "    elif planning_phase:\n"
    "        planning_rule = (\n"
    "            \"当前处于统一语义声明阶段：只能调用 declare_turn_goals。先完整理解当前原话与公开上下文，再按用户可独立判断完成与否的业务效果拆 Goal；不要按接口、Tool 或现有能力数量拆，也不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal。每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。系统没有对应能力时仍保留原 Goal，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力。goal_type 只在旧能力合同确实需要时作为兼容提示，不是正式语义。\"\n"
    "            + (\n"
    "                \" 当前存在一个或多个 Goal Blocker：只处理本轮明确涉及的 blocker，可同时解决一个 blocker、新建独立 Goal、暂停或替换另一个 Goal。使用 blocker_resolutions/goal_changes 表达具体状态操作；已有 Goal/Focus 的 expected_revision 必须复制 ContextBundle 当前值，evidence_span 必须是本轮原话连续片段；requested_effect 变化必须新建 Goal 并 supersede，不能 PATCH 偷换。不得强迫整轮采用一个全局 disposition；只提交 goal_changes 和 blocker_resolutions。\"\n"
    "                if blocker_context is not None else \"\"\n"
    "            )\n"
    "        )\n"
    "    else:\n"
    "        planning_rule = \"本轮正式语义已冻结。能力发现和执行只能实现这些 Goal，不能因工具失败或能力缺失改写 requested_effect。每个业务工具调用必须显式绑定一个 goal_id；终止调用必须覆盖全部已处理 Goal。\"\n"
    "    surface = state.get(\"capability_surface\")",
)
replace_once(
    dialogue_path,
    "        else \"目标声明阶段不暴露业务能力。\"\n",
    "        else (\"语义冻结前澄清阶段不暴露业务能力。\" if declaration_clarification_mode else \"目标声明阶段不暴露业务能力。\")\n",
)
replace_once(
    dialogue_path,
    "        planning_phase = not goal_plan_ready(state)\n        pre_model_budget = compute_loop_budget(state)\n",
    "        planning_phase = not goal_plan_ready(state)\n"
    "        declaration_clarification_mode = planning_phase and _declaration_clarification_required(state)\n"
    "        pre_model_budget = compute_loop_budget(state)\n",
)
replace_once(
    dialogue_path,
    "        schemas = (\n            planning_schemas()\n            if planning_phase\n            else agent_loop_schemas(\n",
    "        schemas = (\n"
    "            [deepcopy(ASK_USER_CLARIFICATION_SCHEMA)]\n"
    "            if declaration_clarification_mode\n"
    "            else planning_schemas()\n"
    "            if planning_phase\n"
    "            else agent_loop_schemas(\n",
)
replace_once(
    dialogue_path,
    "                planning_phase\n                or terminal_protocol_repair\n",
    "                declaration_clarification_mode\n"
    "                or planning_phase\n"
    "                or terminal_protocol_repair\n",
)
replace_once(
    dialogue_path,
    "            required_tool_name=\"declare_turn_goals\" if planning_phase else None,\n",
    "            required_tool_name=(\n"
    "                \"ask_user_clarification\"\n"
    "                if declaration_clarification_mode\n"
    "                else \"declare_turn_goals\" if planning_phase else None\n"
    "            ),\n",
)
clarification_block = '''        if declaration_clarification_mode:\n            invalid = [\n                call for call in raw_calls\n                if str(call.get("name") or "") != "ask_user_clarification"\n            ]\n            next_step = int(state.get("agent_loop_step") or 0) + 1\n            ai = _as_ai_message(response, raw_calls)\n            debug_call = {\n                "node": "agent_loop",\n                "model_profile": get_model_profile(),\n                "loop_step": next_step,\n                "tool_call_count": len(raw_calls),\n                "tool_names": [str(call.get("name") or "") for call in raw_calls],\n                "workflow_level": None,\n                "workflow_status": "declaration_clarification",\n                "response_content": raw_content,\n                "model_call": model_call,\n            }\n            if invalid or len(raw_calls) != 1:\n                retry = int(state.get("goal_declaration_retry") or 0) + 1\n                exhausted = retry >= GOAL_DECLARATION_MAX_RETRIES\n                return {\n                    "messages": [ai],\n                    "agent_loop_step": next_step,\n                    "goal_declaration_retry": retry,\n                    "phase": "final" if exhausted else "agent_loop",\n                    "status": "GoalDeclarationClarificationUnavailable" if exhausted else "GoalDeclarationClarificationProtocolRetry",\n                    "current_final_answer": (\n                        "当前仍缺少必要信息，系统未执行任何业务操作；请重新说明要查询的对象或范围。"\n                        if exhausted else None\n                    ),\n                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],\n                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],\n                    "decision_chain": _append_decision(\n                        state,\n                        stage="agent_loop",\n                        decision="declaration_clarification_protocol_violation",\n                        details={\n                            "emitted_tools": [str(call.get("name") or "") for call in raw_calls],\n                            "retry": retry,\n                        },\n                    ),\n                }\n            call = raw_calls[0]\n            if (scope_conflict := _unnecessary_unique_scope_clarification(state, call)) is not None:\n                return {\n                    "messages": [ai],\n                    "agent_loop_step": next_step,\n                    "current_final_answer": _loop_budget_fallback(state),\n                    "phase": "final",\n                    "status": "UnnecessaryClarificationFallback",\n                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],\n                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],\n                    "decision_chain": _append_decision(\n                        state,\n                        stage="agent_loop",\n                        decision="declaration_clarification_rejected_for_unique_scope",\n                        details=scope_conflict,\n                    ),\n                }\n            answer, error, handles = _answer_from_terminal_tool(state, call)\n            if error is not None or answer is None:\n                return {\n                    "messages": [ai],\n                    "agent_loop_step": next_step,\n                    "current_final_answer": "当前仍缺少必要信息，系统未执行任何业务操作；请重新说明要查询的对象或范围。",\n                    "phase": "final",\n                    "status": "GoalDeclarationClarificationUnavailable",\n                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],\n                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],\n                    "decision_chain": _append_decision(\n                        state,\n                        stage="agent_loop",\n                        decision="declaration_clarification_terminal_rejected",\n                        details={"reason": error},\n                    ),\n                }\n            final_ai = AIMessage(\n                content=answer,\n                additional_kwargs={"context_trust": "safe_nonbusiness", "evidence_handles": handles},\n            ) if AIMessage else None\n            messages = [ai]\n            acknowledgment = _append_terminal_protocol_message(call, code="FINAL_ACCEPTED")\n            if acknowledgment is not None:\n                messages.append(acknowledgment)\n            if final_ai is not None:\n                messages.append(final_ai)\n            terminal_runtime = _terminal_runtime_outcome(\n                state, call=call, answer=answer, evidence_handles=handles\n            )\n            return {\n                "messages": messages,\n                "agent_loop_step": next_step,\n                "current_final_answer": answer,\n                "answer_evidence_handles": handles,\n                **({"runtime_outcome": terminal_runtime} if terminal_runtime is not None else {}),\n                "phase": "final",\n                "status": "GeneralFinalAnswer",\n                "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],\n                "model_call_trace": [*(state.get("model_call_trace") or []), model_call],\n                "decision_chain": _append_decision(\n                    state,\n                    stage="agent_loop",\n                    decision="declaration_clarification_accepted",\n                    details={"missing_kind": str((call.get("args") or {}).get("missing_kind") or "")},\n                ),\n            }\n'''
replace_once(
    dialogue_path,
    "        if planning_phase:\n            invalid = [call for call in raw_calls if str(call.get(\"name\") or \"\") != \"declare_turn_goals\"]\n",
    clarification_block + "        if planning_phase:\n            invalid = [call for call in raw_calls if str(call.get(\"name\") or \"\") != \"declare_turn_goals\"]\n",
)


# 4) Focused counterexamples lock both attempt-2 repairs and keep the browser
# response SLA unchanged.
test_path = ROOT / "skill-system/tests/test_wp08_new_release_attempt2_repair.py"
test_path.write_text(r'''from __future__ import annotations

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


class NewReleaseAttempt2RepairTests(unittest.TestCase):
    def _catalog(self) -> dict:
        path = AGENT_ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _semantic_smoke(self):
        path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
        spec = importlib.util.spec_from_file_location("wp08_attempt2_semantic_smoke", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_courier_contact_remains_open_effect_not_generic_unsupported_semantics(self) -> None:
        goals = []
        for case in self._catalog()["cases"]:
            contract = case.get("execution_contract") or {}
            for turn in contract.get("turn_contracts") or []:
                goals.extend(
                    goal for goal in turn.get("goal_oracle") or []
                    if "快递员" in str(goal.get("evidence_span") or "")
                    and "电话" in str(goal.get("evidence_span") or "") or "手机号" in str(goal.get("evidence_span") or "")
                )
        courier = [goal for goal in goals if "快递员" in str(goal.get("evidence_span") or "")]
        self.assertEqual(len(courier), 2)
        for goal in courier:
            self.assertEqual(goal.get("requested_effect_match"), "unregistered_open")
            self.assertEqual(
                goal.get("requested_effect"),
                {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"},
            )
            self.assertNotEqual((goal.get("requested_effect") or {}).get("operation"), "unsupported_request")
            self.assertEqual(goal.get("required_tools"), ["report_unsupported_request"])

    def test_unregistered_open_oracle_accepts_open_spelling_but_rejects_registered_nearby_effect(self) -> None:
        smoke = self._semantic_smoke()
        oracle = [{
            "oracle_id": "g1",
            "evidence_span": "快递员手机号",
            "required": True,
            "depends_on": [],
            "requested_effect_match": "unregistered_open",
            "requested_effect": {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"},
        }]
        smoke._match_oracle(
            case_id="open-effect",
            oracle=oracle,
            goals=[{
                "goal_id": "m1",
                "evidence_span": "快递员手机号",
                "required": True,
                "depends_on": [],
                "requested_effect": {"domain": "shipping", "operation": "courier_phone", "object_type": "courier"},
            }],
            registered_effect_identities={"order.query_logistics:order"},
        )
        with self.assertRaises(RuntimeError):
            smoke._match_oracle(
                case_id="nearby-effect",
                oracle=oracle,
                goals=[{
                    "goal_id": "m1",
                    "evidence_span": "快递员手机号",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
                }],
                registered_effect_identities={"order.query_logistics:order"},
            )

    def test_unregistered_branch_still_cannot_be_dropped(self) -> None:
        smoke = self._semantic_smoke()
        with self.assertRaisesRegex(RuntimeError, "goal count mismatch"):
            smoke._match_oracle(
                case_id="drop-open-branch",
                oracle=[
                    {"oracle_id": "g1", "evidence_span": "查物流", "required": True, "depends_on": [], "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"}},
                    {"oracle_id": "g2", "evidence_span": "快递员手机号", "required": True, "depends_on": [], "requested_effect_match": "unregistered_open", "requested_effect": {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"}},
                ],
                goals=[{"goal_id": "m1", "evidence_span": "查物流", "required": True, "depends_on": [], "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"}}],
                registered_effect_identities={"order.query_logistics:order"},
            )

    def test_declaration_clarification_is_same_turn_runtime_authority(self) -> None:
        from agent_core.lifecycle.dialogue_runtime import _declaration_clarification_required
        state = {
            "turn_index": 2,
            "current_turn_plan": {"turn": 2, "tool_calls": [{"name": "declare_turn_goals"}]},
            "tool_trace": [{
                "name": "declare_turn_goals",
                "result": {"ok": False, "code": "GOAL_DECLARATION_REQUIRES_CLARIFICATION"},
            }],
        }
        self.assertTrue(_declaration_clarification_required(state))
        self.assertFalse(_declaration_clarification_required({**state, "turn_index": 3}))
        self.assertFalse(_declaration_clarification_required({
            **state,
            "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": True, "code": "TURN_SEMANTICS_FROZEN"}}],
        }))

    def test_declaration_clarification_surface_is_ask_only_and_skips_workflow_plan(self) -> None:
        source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        self.assertIn("[deepcopy(ASK_USER_CLARIFICATION_SCHEMA)]", source)
        self.assertIn('"ask_user_clarification"\n                if declaration_clarification_mode', source)
        self.assertIn("decision=\"declaration_clarification_accepted\"", source)
        accepted = source.index('decision="declaration_clarification_accepted"')
        workflow_build = source.index("candidate_workflow_plan = build_workflow_plan", accepted)
        self.assertLess(accepted, workflow_build)
        self.assertIn("不得发现、调用或暗示任何业务能力", source)

    def test_semantic_prompt_explicitly_preserves_unregistered_branch(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn("能力词汇中没有精确身份的分支也必须保留成独立 Goal", source)
        self.assertIn("registered_effect_identities", source)

    def test_browser_response_sla_remains_120_seconds(self) -> None:
        source = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('{ timeout: 120_000 }', source)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(catalog_path.relative_to(ROOT)),
        str(smoke_path.relative_to(ROOT)),
        str(dialogue_path.relative_to(ROOT)),
        str(test_path.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
