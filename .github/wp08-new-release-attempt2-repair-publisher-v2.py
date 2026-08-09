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
        raise SystemExit(f"expected one replacement in {path}: {count} for {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_region(path: Path, start_marker: str, end_marker: str, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(f"region markers missing in {path}: {start_marker!r} -> {end_marker!r}")
    path.write_text(text[:start] + body + text[end:], encoding="utf-8")


# A. Semantic authority: user effects that are unsupported by the deployment
# remain open business effects. `report_unsupported_request` is the later
# closed-world absence reporter; it is not the user's semantic intent.
catalog = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
payload = json.loads(catalog.read_text(encoding="utf-8"))
generic = {"domain": "open", "operation": "unsupported_request", "object_type": "request"}
open_effect = {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"}
oracle_count = 0
fixture_count = 0
for case in payload.get("cases", []):
    if not isinstance(case, dict):
        continue
    execution = case.get("execution_contract") if isinstance(case.get("execution_contract"), dict) else {}
    for turn in execution.get("turn_contracts", []):
        if not isinstance(turn, dict):
            continue
        for goal in turn.get("goal_oracle", []):
            if not isinstance(goal, dict):
                continue
            if "快递员" in str(goal.get("evidence_span") or "") and goal.get("requested_effect") == generic:
                goal["requested_effect"] = deepcopy(open_effect)
                goal["requested_effect_match"] = "unregistered_open"
                oracle_count += 1
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
                    if {key: effect.get(key) for key in ("domain", "operation", "object_type")} == generic:
                        goal["requested_effect"] = {
                            **deepcopy(open_effect),
                            "raw_description": str(effect.get("raw_description") or goal.get("description") or ""),
                        }
                        fixture_count += 1
if (oracle_count, fixture_count) != (2, 2):
    raise SystemExit(f"unexpected courier fixture counts: oracle={oracle_count}, scripted={fixture_count}")
catalog.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# B. Independent real-model semantic oracle: exact registered effects still
# match exactly. An unregistered open effect is allowed to use different open
# vocabulary only when its structured identity is complete and is provably not
# one of the deployment's registered nearby effects. Goal count, literal span,
# dependency semantics and the production alignment verifier remain mandatory.
smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    smoke,
    'def _effect_identity(value: Any) -> tuple[str, str, str]:\n    source = value if isinstance(value, dict) else {}\n    return tuple(str(source.get(key) or "").strip().casefold() for key in _EFFECT_KEYS)  # type: ignore[return-value]\n',
    'def _effect_identity(value: Any) -> tuple[str, str, str]:\n    source = value if isinstance(value, dict) else {}\n    return tuple(str(source.get(key) or "").strip().casefold() for key in _EFFECT_KEYS)  # type: ignore[return-value]\n\n\ndef _effect_key(value: tuple[str, str, str]) -> str:\n    domain, operation, object_type = value\n    return f"{domain}.{operation}:{object_type}" if domain and operation and object_type else ""\n',
)
replace_once(
    smoke,
    'def _match_oracle(*, case_id: str, oracle: list[dict[str, Any]], goals: list[dict[str, Any]]) -> None:\n',
    'def _match_oracle(\n    *,\n    case_id: str,\n    oracle: list[dict[str, Any]],\n    goals: list[dict[str, Any]],\n    registered_effect_identities: set[str],\n) -> None:\n',
)
old_match = '''        expected_effect = _effect_identity(expected.get("requested_effect"))
        if not all(expected_effect):
            raise RuntimeError(
                f"{case_id}: oracle goal {expected.get('oracle_id')!r} lacks authoritative requested_effect identity"
            )

        def candidate_matches(row: dict[str, Any], *, fuzzy_span: bool) -> bool:
            span_ok = (
                _span_matches_oracle(expected=evidence, actual=row.get("evidence_span"))
                if fuzzy_span
                else str(row.get("evidence_span") or "") == evidence
            )
            return (
                span_ok
                and bool(row.get("required", True)) == required
                and _effect_identity(row.get("requested_effect")) == expected_effect
            )
'''
new_match = '''        expected_effect = _effect_identity(expected.get("requested_effect"))
        match_mode = str(expected.get("requested_effect_match") or "exact").strip().casefold()
        if match_mode not in {"exact", "unregistered_open"}:
            raise RuntimeError(f"{case_id}: unsupported requested_effect_match={match_mode!r}")
        if not all(expected_effect):
            raise RuntimeError(
                f"{case_id}: oracle goal {expected.get('oracle_id')!r} lacks requested_effect identity"
            )

        def candidate_matches(row: dict[str, Any], *, fuzzy_span: bool) -> bool:
            span_ok = (
                _span_matches_oracle(expected=evidence, actual=row.get("evidence_span"))
                if fuzzy_span
                else str(row.get("evidence_span") or "") == evidence
            )
            candidate_effect = _effect_identity(row.get("requested_effect"))
            if match_mode == "unregistered_open":
                effect_ok = bool(all(candidate_effect)) and _effect_key(candidate_effect) not in registered_effect_identities
            else:
                effect_ok = candidate_effect == expected_effect
            return (
                span_ok
                and bool(row.get("required", True)) == required
                and effect_ok
            )
'''
replace_once(smoke, old_match, new_match)
replace_once(
    smoke,
    '                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "\n                f"requested_effect={expected_effect!r}, candidates={candidates!r}"\n',
    '                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "\n                f"requested_effect={expected_effect!r}, match_mode={match_mode!r}, candidates={candidates!r}"\n',
)
replace_once(
    smoke,
    '        effect_vocabulary_json = json.dumps(\n            capability_effect_index(get_runtime_registry().capabilities),\n            ensure_ascii=False,\n            sort_keys=True,\n        )\n',
    '        effect_index = capability_effect_index(get_runtime_registry().capabilities)\n        effect_vocabulary_json = json.dumps(\n            effect_index,\n            ensure_ascii=False,\n            sort_keys=True,\n        )\n        registered_effect_identities = {\n            str(row.get("requested_effect_identity") or "").strip().casefold()\n            for row in list(effect_index.get("effects") or [])\n            if isinstance(row, dict) and str(row.get("requested_effect_identity") or "").strip()\n        }\n',
)
replace_once(
    smoke,
    '            "不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。"\n',
    '            "能力词汇中没有精确身份的分支也必须保留成独立 Goal；requested_effect 要写用户实际请求的开放业务效果，不能写 unsupported_request、能力缺失或系统不支持来替代用户语义。"\n            "不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。"\n',
)
replace_once(
    smoke,
    '                _match_oracle(case_id=case["id"], oracle=oracle, goals=goals)\n',
    '                _match_oracle(\n                    case_id=case["id"],\n                    oracle=oracle,\n                    goals=goals,\n                    registered_effect_identities=registered_effect_identities,\n                )\n',
)


# C. Runtime bridge: once the current-turn declaration result is explicitly
# GOAL_DECLARATION_REQUIRES_CLARIFICATION, semantic freeze remains blocked but
# the next model call receives only ask_user_clarification. No business schema,
# workflow plan, MatchProof, Draft or transaction authority is reachable.
dialogue = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
replace_once(
    dialogue,
    'from agent_core.lifecycle.protocol import TERMINAL_TOOL_NAMES, agent_loop_schemas, planning_schemas\n',
    'from agent_core.lifecycle.protocol import (\n    ASK_USER_CLARIFICATION_SCHEMA,\n    TERMINAL_TOOL_NAMES,\n    agent_loop_schemas,\n    planning_schemas,\n)\n',
)
replace_once(
    dialogue,
    'FINAL_PROTOCOL_MAX_RETRIES = 1\nGOAL_DECLARATION_MAX_RETRIES = 2\n\n\ndef get_model():\n',
    '''FINAL_PROTOCOL_MAX_RETRIES = 1
GOAL_DECLARATION_MAX_RETRIES = 2


def _declaration_clarification_required(state: dict[str, Any]) -> bool:
    """Detect a same-turn declaration rejection that explicitly requires input."""
    if goal_plan_ready(state):
        return False
    plan = state.get("current_turn_plan") if isinstance(state.get("current_turn_plan"), dict) else {}
    if int(plan.get("turn") or -1) != int(state.get("turn_index") or 0):
        return False
    if not any(
        isinstance(call, dict) and str(call.get("name") or "") == "declare_turn_goals"
        for call in list(plan.get("tool_calls") or [])
    ):
        return False
    for row in reversed(list(state.get("tool_trace") or [])):
        if not isinstance(row, dict) or str(row.get("name") or "") != "declare_turn_goals":
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        return bool(
            not result.get("ok")
            and str(result.get("code") or "") == "GOAL_DECLARATION_REQUIRES_CLARIFICATION"
        )
    return False


def get_model():
''',
)
replace_once(
    dialogue,
    '    budget = compute_loop_budget(state)\n    terminal_rule = (\n',
    '    budget = compute_loop_budget(state)\n    declaration_clarification_mode = _declaration_clarification_required(state)\n    terminal_rule = (\n',
)
replace_once(
    dialogue,
    '    if str(state.get("status") or "") == "ClarificationNotNeededRetry":\n',
    '''    if declaration_clarification_mode:
        protocol_repair_rule = (
            "上一次 declare_turn_goals 已由独立语义验证明确判定需要用户澄清；本次不能再次声明 Goal，"
            "也不能调用任何业务能力。只能调用一次 ask_user_clarification，直接询问缺失的对象、范围、条件或真实意图。"
        )
    elif str(state.get("status") or "") == "ClarificationNotNeededRetry":
''',
)
new_planning_rule = '''    if declaration_clarification_mode:
        planning_rule = (
            "当前处于语义冻结前的澄清暂停阶段：独立验证已证明当前候选不能安全冻结。"
            "只能向用户提出一个最小必要澄清问题；不得改写或冻结 requested_effect，不得发现、调用或暗示任何业务能力。"
        )
    elif planning_phase:
        planning_rule = (
            "当前处于统一语义声明阶段：只能调用 declare_turn_goals。先完整理解当前原话与公开上下文，再按用户可独立判断完成与否的业务效果拆 Goal；不要按接口、Tool 或现有能力数量拆，也不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal。每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。系统没有对应能力时仍保留原 Goal，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力。goal_type 只在旧能力合同确实需要时作为兼容提示，不是正式语义。"
            + (
                " 当前存在一个或多个 Goal Blocker：只处理本轮明确涉及的 blocker，可同时解决一个 blocker、新建独立 Goal、暂停或替换另一个 Goal。使用 blocker_resolutions/goal_changes 表达具体状态操作；已有 Goal/Focus 的 expected_revision 必须复制 ContextBundle 当前值，evidence_span 必须是本轮原话连续片段；requested_effect 变化必须新建 Goal 并 supersede，不能 PATCH 偷换。不得强迫整轮采用一个全局 disposition；只提交 goal_changes 和 blocker_resolutions。"
                if blocker_context is not None else ""
            )
        )
    else:
        planning_rule = "本轮正式语义已冻结。能力发现和执行只能实现这些 Goal，不能因工具失败或能力缺失改写 requested_effect。每个业务工具调用必须显式绑定一个 goal_id；终止调用必须覆盖全部已处理 Goal。"
'''
replace_region(
    dialogue,
    '    planning_rule = (\n',
    '    surface = state.get("capability_surface")',
    new_planning_rule,
)
replace_once(
    dialogue,
    '        else "目标声明阶段不暴露业务能力。"\n',
    '        else ("语义冻结前澄清阶段不暴露业务能力。" if declaration_clarification_mode else "目标声明阶段不暴露业务能力。")\n',
)
replace_once(
    dialogue,
    '        planning_phase = not goal_plan_ready(state)\n        pre_model_budget = compute_loop_budget(state)\n',
    '        planning_phase = not goal_plan_ready(state)\n        declaration_clarification_mode = planning_phase and _declaration_clarification_required(state)\n        pre_model_budget = compute_loop_budget(state)\n',
)
replace_once(
    dialogue,
    '        schemas = (\n            planning_schemas()\n            if planning_phase\n            else agent_loop_schemas(\n',
    '        schemas = (\n            [deepcopy(ASK_USER_CLARIFICATION_SCHEMA)]\n            if declaration_clarification_mode\n            else planning_schemas()\n            if planning_phase\n            else agent_loop_schemas(\n',
)
replace_once(
    dialogue,
    '                planning_phase\n                or terminal_protocol_repair\n',
    '                declaration_clarification_mode\n                or planning_phase\n                or terminal_protocol_repair\n',
)
replace_once(
    dialogue,
    '            required_tool_name="declare_turn_goals" if planning_phase else None,\n',
    '            required_tool_name=(\n                "ask_user_clarification"\n                if declaration_clarification_mode\n                else "declare_turn_goals" if planning_phase else None\n            ),\n',
)
clarify_handler = '''        if declaration_clarification_mode:
            invalid = [call for call in raw_calls if str(call.get("name") or "") != "ask_user_clarification"]
            next_step = int(state.get("agent_loop_step") or 0) + 1
            ai = _as_ai_message(response, raw_calls)
            debug_call = {
                "node": "agent_loop",
                "model_profile": get_model_profile(),
                "loop_step": next_step,
                "tool_call_count": len(raw_calls),
                "tool_names": [str(call.get("name") or "") for call in raw_calls],
                "workflow_level": None,
                "workflow_status": "declaration_clarification",
                "response_content": raw_content,
                "model_call": model_call,
            }
            if invalid or len(raw_calls) != 1:
                retry = int(state.get("goal_declaration_retry") or 0) + 1
                exhausted = retry >= GOAL_DECLARATION_MAX_RETRIES
                return {
                    "messages": [ai],
                    "agent_loop_step": next_step,
                    "goal_declaration_retry": retry,
                    "phase": "final" if exhausted else "agent_loop",
                    "status": "GoalDeclarationClarificationUnavailable" if exhausted else "GoalDeclarationClarificationProtocolRetry",
                    "current_final_answer": (
                        "当前仍缺少必要信息，系统未执行任何业务操作；请重新说明要查询的对象或范围。"
                        if exhausted else None
                    ),
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="declaration_clarification_protocol_violation",
                        details={"emitted_tools": [str(call.get("name") or "") for call in raw_calls], "retry": retry},
                    ),
                }
            call = raw_calls[0]
            if (scope_conflict := _unnecessary_unique_scope_clarification(state, call)) is not None:
                return {
                    "messages": [ai],
                    "agent_loop_step": next_step,
                    "current_final_answer": _loop_budget_fallback(state),
                    "phase": "final",
                    "status": "UnnecessaryClarificationFallback",
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="declaration_clarification_rejected_for_unique_scope",
                        details=scope_conflict,
                    ),
                }
            answer, error, handles = _answer_from_terminal_tool(state, call)
            if error is not None or answer is None:
                return {
                    "messages": [ai],
                    "agent_loop_step": next_step,
                    "current_final_answer": "当前仍缺少必要信息，系统未执行任何业务操作；请重新说明要查询的对象或范围。",
                    "phase": "final",
                    "status": "GoalDeclarationClarificationUnavailable",
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="declaration_clarification_terminal_rejected",
                        details={"reason": error},
                    ),
                }
            final_ai = AIMessage(
                content=answer,
                additional_kwargs={"context_trust": "safe_nonbusiness", "evidence_handles": handles},
            ) if AIMessage else None
            messages = [ai]
            acknowledgment = _append_terminal_protocol_message(call, code="FINAL_ACCEPTED")
            if acknowledgment is not None:
                messages.append(acknowledgment)
            if final_ai is not None:
                messages.append(final_ai)
            terminal_runtime = _terminal_runtime_outcome(state, call=call, answer=answer, evidence_handles=handles)
            return {
                "messages": messages,
                "agent_loop_step": next_step,
                "current_final_answer": answer,
                "answer_evidence_handles": handles,
                **({"runtime_outcome": terminal_runtime} if terminal_runtime is not None else {}),
                "phase": "final",
                "status": "GeneralFinalAnswer",
                "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                "decision_chain": _append_decision(
                    state,
                    stage="agent_loop",
                    decision="declaration_clarification_accepted",
                    details={"missing_kind": str((call.get("args") or {}).get("missing_kind") or "")},
                ),
            }
'''
replace_once(
    dialogue,
    '        if planning_phase:\n            invalid = [call for call in raw_calls if str(call.get("name") or "") != "declare_turn_goals"]\n',
    clarify_handler + '        if planning_phase:\n            invalid = [call for call in raw_calls if str(call.get("name") or "") != "declare_turn_goals"]\n',
)


# D. Focused governance regressions.
test_file = ROOT / "skill-system/tests/test_wp08_new_release_attempt2_repair.py"
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


class NewReleaseAttempt2RepairTests(unittest.TestCase):
    def _catalog(self) -> dict:
        path = AGENT_ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _smoke(self):
        path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
        spec = importlib.util.spec_from_file_location("wp08_attempt2_semantic_smoke", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_courier_contact_is_open_effect_not_generic_unsupported_semantics(self) -> None:
        courier = []
        for case in self._catalog()["cases"]:
            execution = case.get("execution_contract") or {}
            for turn in execution.get("turn_contracts") or []:
                courier.extend(
                    goal for goal in turn.get("goal_oracle") or []
                    if "快递员" in str(goal.get("evidence_span") or "")
                    and ("电话" in str(goal.get("evidence_span") or "") or "手机号" in str(goal.get("evidence_span") or ""))
                )
        self.assertEqual(len(courier), 2)
        for goal in courier:
            self.assertEqual(goal.get("requested_effect_match"), "unregistered_open")
            self.assertEqual(
                goal.get("requested_effect"),
                {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"},
            )
            self.assertEqual(goal.get("required_tools"), ["report_unsupported_request"])

    def test_open_effect_match_accepts_unregistered_spelling_and_rejects_registered_nearby_effect(self) -> None:
        smoke = self._smoke()
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

    def test_open_effect_branch_cannot_be_dropped(self) -> None:
        smoke = self._smoke()
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

    def test_declaration_clarification_detector_is_same_turn_only(self) -> None:
        from agent_core.lifecycle.dialogue_runtime import _declaration_clarification_required
        state = {
            "turn_index": 2,
            "current_turn_plan": {"turn": 2, "tool_calls": [{"name": "declare_turn_goals"}]},
            "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": False, "code": "GOAL_DECLARATION_REQUIRES_CLARIFICATION"}}],
        }
        self.assertTrue(_declaration_clarification_required(state))
        self.assertFalse(_declaration_clarification_required({**state, "turn_index": 3}))
        self.assertFalse(_declaration_clarification_required({
            **state,
            "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": True, "code": "TURN_SEMANTICS_FROZEN"}}],
        }))

    def test_declaration_clarification_surface_is_ask_only_and_terminal_before_workflow_build(self) -> None:
        source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        self.assertIn("[deepcopy(ASK_USER_CLARIFICATION_SCHEMA)]", source)
        self.assertIn('"ask_user_clarification"\n                if declaration_clarification_mode', source)
        handler = source.index("if declaration_clarification_mode:", source.index("raw_calls = _tool_calls(response)"))
        accepted = source.index('decision="declaration_clarification_accepted"', handler)
        workflow = source.index("candidate_workflow_plan = build_workflow_plan", accepted)
        self.assertLess(accepted, workflow)
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
        str(catalog.relative_to(ROOT)),
        str(smoke.relative_to(ROOT)),
        str(dialogue.relative_to(ROOT)),
        str(test_file.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
