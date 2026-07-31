from __future__ import annotations

"""Terminal-answer validation and safe finalization for the Agent loop.

This module is intentionally independent from LangGraph nodes.  It validates
only user-facing terminal calls against verified Ledger evidence and the
current interaction contract; it never creates business facts, advances a
transaction, or interprets a user request.
"""

import json
import re
from typing import Any
from uuid import uuid4

try:
    from langchain_core.messages import ToolMessage
except Exception:  # pragma: no cover
    ToolMessage = None  # type: ignore

from agent_core.transaction.interaction import explicit_interaction_response_contract
from agent_core.ledger import find_handle, scope_for_state

_NON_BUSINESS_TRACE_TOOLS = {"declare_turn_goals", "update_task_board", "inspect_audit_event"}

# A model must never promise that a user can see or click a UI control unless
# the server has already created the corresponding interaction contract.  This
# is a presentation invariant, not an intent classifier: it protects users
# from prose such as “请在下方卡片确认” when no transaction card exists.
_UI_SURFACE_WORDS = ("事务卡", "卡片", "按钮", "界面", "页面", "表单", "选择器")
_UI_ACTION_WORDS = ("点击", "选择", "填写", "提交", "确认", "展示", "显示", "下方", "上方", "找到")
_UI_INTERACTION_PHRASES = ("确认提交", "点击确认", "点击下一步", "选择后提交", "系统会展示", "系统将展示")


def _claims_unbacked_interaction_ui(state: dict[str, Any], text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    if not normalized:
        return False
    # A real interaction contract preempts normal terminal prose elsewhere in
    # this node.  Here we only reject an unbacked visual promise.
    if explicit_interaction_response_contract(state) is not None:
        return False
    return (
        any(phrase in normalized for phrase in _UI_INTERACTION_PHRASES)
        or (any(word in normalized for word in _UI_SURFACE_WORDS) and any(word in normalized for word in _UI_ACTION_WORDS))
    )



def _safe_general_reply(text: str) -> str:
    normalized = str(text or "").strip()
    if normalized in {"你好", "您好", "嗨", "hello", "Hello"}:
        return "您好。我可以根据当前已注册的服务能力协助您；涉及具体业务时会先核验再回答。"
    return "我需要通过当前已注册的能力核验具体业务信息后再回答；目前未执行任何业务操作。"


def _valid_evidence_handles(state: dict[str, Any], handles: list[str]) -> bool:
    scope = scope_for_state(state)
    for handle in handles:
        item = find_handle(
            state.get("artifact_ledger") or [],
            str(handle),
            scope=scope,
            allowed_kinds={"artifact", "view", "result", "eligibility", "offer", "receipt"},
            active_only=False,
        )
        if item is None:
            return False
    return True


def _is_non_grounded_terminal_allowed(state: dict[str, Any]) -> bool:
    trace = list(state.get("tool_trace") or [])
    if not trace:
        return True
    # Unsupported, rejected or failed tool attempts may be explained without a
    # ledger handle; they are still trace-backed and cannot be business claims.
    for row in trace:
        if not isinstance(row, dict):
            continue
        # Planning, soft task organization and audit lookup are internal
        # protocol events.  Their successful execution is not a business fact
        # and must not force a customer-facing evidence handle.
        if str(row.get("name") or "") in _NON_BUSINESS_TRACE_TOOLS:
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        if result.get("ok"):
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            if data.get("supported") is False:
                continue
            return False
    return True


def _consultation_policy_requirement(state: dict[str, Any]) -> tuple[bool | None, bool]:
    """Return ``(knowledge_available, has_policy_source)`` for this turn.

    A consultation result is authoritative only for the current turn.  The
    finalizer uses this to prevent a fluent model answer from inventing a policy
    conclusion when retrieval was unavailable or source-less.
    """
    for row in reversed(list(state.get("tool_trace") or [])):
        if not isinstance(row, dict) or not bool((row.get("result") or {}).get("data", {}).get("consultation_only")):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if not result.get("ok"):
            return False, False
        available = bool(data.get("knowledge_available"))
        evidence = data.get("policy_evidence") if isinstance(data.get("policy_evidence"), list) else []
        return available, bool(evidence)
    return None, False


def _is_explicit_evidence_insufficient(answer: str) -> bool:
    normalized = str(answer or "")
    return any(token in normalized for token in ("依据不足", "无法确认", "知识库不可用", "资料不足", "无法核实"))


def _answer_from_terminal_tool(state: dict[str, Any], call: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    name = str(call.get("name") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    handles = [str(v) for v in args.get("evidence_handles") or [] if str(v)]
    if not _valid_evidence_handles(state, handles):
        return None, "final_answer_references_unknown_handle", handles
    if name == "respond_to_user":
        answer = str(args.get("answer") or "").strip()
        if not answer:
            return None, "final_answer_empty", handles
        if _claims_unbacked_interaction_ui(state, answer):
            return None, "final_answer_claims_unbacked_interaction_ui", handles
        if not handles and not _is_non_grounded_terminal_allowed(state):
            return None, "business_final_answer_requires_evidence_handles", handles
        knowledge_available, has_policy_source = _consultation_policy_requirement(state)
        if knowledge_available is False and not _is_explicit_evidence_insufficient(answer):
            return None, "consultation_requires_explicit_insufficient_evidence_notice", handles
        if knowledge_available is True and not has_policy_source:
            return None, "consultation_requires_policy_source", handles
        return answer, None, handles
    if name == "ask_user_clarification":
        question = str(args.get("question") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not question:
            return None, "clarification_question_empty", handles
        if not handles and not _is_non_grounded_terminal_allowed(state):
            # A question can be general, but if it relies on business choices it
            # must point to the observed candidates.
            return None, "clarification_requires_evidence_handles", handles
        # ``reason`` remains in the structured tool call / audit trail.  It is
        # not customer-facing copy: exposing internal uncertainty creates noisy
        # “system explains itself” chat rather than a simple question.
        return question, None, handles
    return None, "unknown_terminal_tool", handles


def _append_terminal_protocol_message(call: dict[str, Any], *, code: str) -> Any | None:
    if ToolMessage is None:
        return None
    message = (
        "该工具不在本次模型调用实际暴露的工具表中；请只使用当前工具表里的能力，不要重复历史工具调用。"
        if code == "TOOL_NOT_AVAILABLE_IN_CURRENT_WORKFLOW"
        else "当前普通承接已有唯一的最新公开范围；不要复活更旧集合制造歧义，请沿最新 ResultRef 继续。"
        if code == "CLARIFICATION_NOT_NEEDED_UNIQUE_LATEST_SCOPE"
        else "请先完成观察或使用符合证据要求的最终回答。"
    )
    return ToolMessage(
        content=json.dumps({"ok": False, "code": code, "message": message}, ensure_ascii=False),
        tool_call_id=str(call.get("id") or call.get("tool_call_id") or uuid4()),
        name=str(call.get("name") or "respond_to_user"),
    )
