from __future__ import annotations

from typing import Any

from agent_core.observability.audit_turn_trace import append_turn_event, audit_cards, build_turn_event, create_plan_id, normalize_turn_trace
from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.transaction.interaction import explicit_interaction_response_contract
from agent_core.presentation.grounded import render_grounded_tool_answer
from agent_core.ledger import ledger_cards, scope_for_state
from agent_core.context.visible_result_refs import mark_visible_result_refs
from agent_core.lifecycle.finalizer import _safe_general_reply
from agent_core.lifecycle.task_board import complete_tasks_from_terminal, task_board_cards
from agent_core.runtime.node_support import (
    append_decision as _append_decision,
    last_human_index as _last_human_index,
    latest_human_text as _latest_human_text,
    tool_calls as _tool_calls,
)

try:
    from langchain_core.messages import AIMessage
except Exception:  # pragma: no cover
    AIMessage = None  # type: ignore
from agent_core.transaction.active_draft import get_active_draft_id
from agent_core.runtime.outcomes import coerce_runtime_outcome, fail_closed_outcome, outcome
from agent_core.lifecycle.workflow_runtime import verify_workflow_for_final_answer

def has_authority_required(state: dict[str, Any]) -> bool:
    return explicit_interaction_response_contract(state) is not None

def _render_fallback_trace(state: dict[str, Any]) -> str:
    # Authority is rendered only as a structured card.  Returning prose here
    # would recreate the old "please click a button" without an actual card.
    if has_authority_required(state):
        return ""
    trace = list(state.get("tool_trace") or [])
    if not trace:
        return _safe_general_reply(_latest_human_text(state))
    # Fail-safe path: a provider that repeatedly emits the
    # same read call instead of responding must not make the duplicate warning
    # become the user-facing answer.  Re-render the latest successful grounded
    # observation; normal providers use respond_to_user before this path.
    successful_business = [
        row for row in trace
        if isinstance(row, dict)
        and str(row.get("name") or "") not in {"action_gateway", "commit_action"}
        and isinstance(row.get("result"), dict)
        and bool((row.get("result") or {}).get("ok"))
    ]
    if successful_business:
        try:
            return render_grounded_tool_answer({**state, "tool_trace": successful_business})
        except Exception:
            pass
    # Keep the fallback deliberately factual and compact.  The normal route is
    # the model's respond_to_user tool, but a loop must terminate safely even
    # when a provider fails to follow that protocol.
    latest = trace[-1]
    result = latest.get("result") if isinstance(latest, dict) and isinstance(latest.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if latest.get("name") == "action_gateway":
        message = data.get("reason") or data.get("message")
        if message:
            return f"已获得当前查询结果：{message} 未创建或提交任何未经确认的业务申请。"
    if latest.get("name") == "commit_action":
        if data.get("decision") == "committed":
            return "业务操作已提交，结果已记录。"
        return f"未能提交业务操作：{data.get('message') or '业务服务拒绝或状态已变化'}。"
    if not result.get("ok"):
        return str(result.get("message") or "本轮工具未返回可用结果，未执行任何业务写操作。")
    return "系统未获得可继续办理的明确结果；未确认创建或提交任何业务申请。请根据当前查询结果继续说明需要处理的事项。"


def _loop_budget_fallback(state: dict[str, Any]) -> str:
    return _render_fallback_trace(state)


def route_after_agent_loop(state: dict[str, Any]) -> str:
    if has_authority_required(state):
        return "confirm"
    phase = str(state.get("phase") or "")
    if phase == "loop_execute":
        return "execute"
    # Protocol validation is deliberately allowed to request one bounded
    # repair turn (goal declaration, terminal-tool shape, evidence binding or
    # workflow completion).  Treating ``phase=agent_loop`` as final silently
    # discarded that repair request and exposed a generic fallback to users.
    # Keep the routing decision generic: the node owns retry policy/budgets;
    # the graph only honors the phase it was given.
    if phase == "agent_loop":
        return "loop"
    return "final"


def route_after_action_gateway(state: dict[str, Any]) -> str:
    if has_authority_required(state):
        return "confirm"
    phase = str(state.get("phase") or "")
    if phase == "offer_confirmation":
        return "confirm"
    if phase == "action_gateway":
        return "gateway"
    return "loop"


def route_after_confirmation(state: dict[str, Any]) -> str:
    phase = str(state.get("phase") or "")
    # A pass-through/offline interrupt adapter returns the interaction payload
    # to the node.  The node marks that invocation final so the public card can
    # be persisted instead of looping back into the confirmation node.
    if phase == "final":
        return "final"
    if has_authority_required(state):
        return "confirm"
    if phase == "commit_action":
        return "commit"
    if phase == "action_gateway":
        return "gateway"
    if phase == "agent_loop":
        return "loop"
    return "final"


def _operation_handles_from_trace(trace: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in trace:
        if not isinstance(row, dict):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        for key in ("offer_handle", "dismissed_offer", "eligibility_handle", "result_handle", "view_handle"):
            if data.get(key):
                values.append(str(data[key]))
    return list(dict.fromkeys(values))


def _source_effect_by_handle(trace: list[dict[str, Any]]) -> dict[str, str]:
    """Map released evidence back to the tool effect that created it for audit only."""
    mapping: dict[str, str] = {}
    for row in trace:
        if not isinstance(row, dict):
            continue
        effect_id = str(row.get("effect_id") or "")
        if not effect_id:
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        for key, value in data.items():
            if key.endswith("_handle") and value:
                mapping[str(value)] = effect_id
            elif key.endswith("_handles") and isinstance(value, list):
                for item in value:
                    if item:
                        mapping[str(item)] = effect_id
    return mapping


def _visible_ledger_for_interaction(
    state: dict[str, Any],
    interaction_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Record only the exact business target rendered by a structured card.

    Transaction authority and discourse visibility are deliberately separate.
    Rendering a verified transaction card makes its exact business target a
    customer-visible referent, but never grants, preserves or recreates write
    authority.  The normal VisibleResultRef release path supplies the durable
    presentation provenance used by later reference proofs.
    """
    interaction = (
        interaction_contract.get("interaction")
        if isinstance(interaction_contract.get("interaction"), dict)
        else {}
    )
    if str(interaction.get("kind") or "") != "transaction":
        return list(state.get("artifact_ledger") or [])
    control = interaction.get("control") if isinstance(interaction.get("control"), dict) else {}
    target_handle = str(control.get("target_handle") or "").strip()
    if not target_handle:
        return list(state.get("artifact_ledger") or [])
    return mark_visible_result_refs(
        state.get("artifact_ledger") or [],
        state=state,
        evidence_handles=[target_handle],
        source_effect_by_handle={},
    )


def finalize_agent_loop_turn_node(state: dict[str, Any]) -> dict[str, Any]:
    interaction_contract = explicit_interaction_response_contract(state)
    if interaction_contract is not None:
        # Defensive finalizer invariant.  A persisted transaction form or
        # authority card must never be downgraded into fallback prose.
        interaction = dict(interaction_contract.get("interaction") or {})
        prior_status = str(state.get("status") or "")
        visible_ledger = _visible_ledger_for_interaction(state, interaction_contract)
        return {
            "current_final_answer": None,
            "response_contract": interaction_contract,
            "artifact_ledger": visible_ledger,
            "ledger_snapshot": ledger_cards(visible_ledger, scope=scope_for_state(state)),
            "phase": "offer_confirmation",
            "status": (
                prior_status
                if prior_status == "ExecutionDisposition:structured_interaction"
                else "TransactionInteractionRequired"
            ),
            "decision_chain": _append_decision(
                state,
                stage="finalize_turn",
                decision="interaction_contract_preempted_final_text",
                details={
                    "offer_handle": interaction.get("interaction_id"),
                    "visible_target_handle": (
                        (interaction.get("control") or {}).get("target_handle")
                        if isinstance(interaction.get("control"), dict)
                        else None
                    ),
                    "transaction_authority_inferred_from_visibility": False,
                },
            ),
        }
    answer = str(state.get("current_final_answer") or _loop_budget_fallback(state))
    workflow_verification = verify_workflow_for_final_answer(state)
    if not workflow_verification.get("ok"):
        runtime = fail_closed_outcome(
            correlation_id=str(state.get("correlation_id") or "") or None,
            reason="workflow_required_steps_not_terminal",
        ).as_dict()
        runtime["payload"] = {**dict(runtime.get("payload") or {}), "workflow_verification": workflow_verification}
        answer = "当前任务还有必要步骤没有完成验证，系统不会把模型回答当作完成结果；请继续说明要处理的对象或稍后重试。"
        state = {**state, "runtime_outcome": runtime, "current_final_answer": answer}
    normalized_existing = coerce_runtime_outcome(
        state.get("runtime_outcome"),
        correlation_id=str(state.get("correlation_id") or "") or None,
    )
    existing_outcome = normalized_existing.as_dict() if normalized_existing is not None else None
    if existing_outcome is None:
        # No-tool replies are ordinary narrative outcomes.  A trace without an
        # outcome means a runtime boundary failed to classify an observed
        # result, so fail closed rather than letting model prose imply success.
        if state.get("tool_trace"):
            runtime_outcome = fail_closed_outcome(
                correlation_id=str(state.get("correlation_id") or "") or None,
                reason="missing_runtime_outcome_after_tool_trace",
            ).as_dict()
            answer = str(runtime_outcome["customer_safe_summary"])
        else:
            runtime_outcome = outcome(
                "narrative",
                effects="none",
                safe_to_continue=True,
                correlation_id=str(state.get("correlation_id") or "") or None,
                customer_safe_summary=answer,
                next_interaction="none",
            ).as_dict()
    else:
        runtime_outcome = existing_outcome
    plans = list(state.get("loop_plans") or [])
    summarized_plan = {
        "plan_id": str((plans[-1] if plans else {}).get("plan_id") or create_plan_id()),
        "architecture": "customer_agent.runtime",
        # Audit convenience: callers that inspect a
        # one-step plan can still see the complete flattened call sequence.
        "tool_calls": [call for loop_plan in plans for call in list(loop_plan.get("tool_calls") or [])],
        "loop_steps": plans,
        "semantic_proposal": dict(state.get("semantic_proposal") or {}) if isinstance(state.get("semantic_proposal"), dict) else None,
        "frozen_semantic_contract": dict(state.get("frozen_semantic_contract") or {}) if isinstance(state.get("frozen_semantic_contract"), dict) else None,
        "grounded_execution_plan": read_plan_projection(state),
        "goal_blockers": [dict(row) for row in list(state.get("goal_blockers") or []) if isinstance(row, dict)],
        "goal_records": [dict(row) for row in list(state.get("goal_records") or []) if isinstance(row, dict)],
        "not_future_semantic_authority": True,
    }
    event = build_turn_event(
        plan=summarized_plan,
        turn=int(state.get("turn_index") or 0),
        user_text=str(state.get("current_user_input") or _latest_human_text(state)),
        tool_trace=list(state.get("tool_trace") or []),
        answer=answer,
        status=str(state.get("status") or "Done"),
        operation_handles=_operation_handles_from_trace(list(state.get("tool_trace") or [])),
        answer_evidence_handles=list(state.get("answer_evidence_handles") or []),
    )
    audit_log = append_turn_event(state.get("conversation_event_log") or [], event)
    # A result becomes referable in future dialogue only after it has crossed a
    # customer-visible final-answer boundary with validated evidence.  No tool
    # result, focus pointer or hidden ledger item is promoted automatically.
    visible_ledger = mark_visible_result_refs(
        state.get("artifact_ledger") or [],
        state=state,
        evidence_handles=list(state.get("answer_evidence_handles") or []),
        source_effect_by_handle=_source_effect_by_handle(list(state.get("tool_trace") or [])),
    )
    messages: list[Any] = []
    # agent_loop normally already appends a trusted final AI message.  For
    # fallback/confirmation paths, append one now.
    current_messages = list(state.get("messages") or [])
    boundary = _last_human_index(current_messages)
    has_final = any(
        msg.__class__.__name__ == "AIMessage"
        and not _tool_calls(msg)
        and str(getattr(msg, "content", "") or "") == answer
        for msg in current_messages[boundary + 1:] if boundary >= 0
    )
    if not has_final and AIMessage is not None:
        messages.append(AIMessage(content=answer, additional_kwargs={"context_trust": "grounded" if state.get("tool_trace") else "safe_nonbusiness", "evidence_handles": list(state.get("answer_evidence_handles") or [])}))
    return {
        "messages": messages,
        "current_final_answer": answer,
        "runtime_outcome": runtime_outcome,
        "conversation_event_log": audit_log,
        "artifact_ledger": visible_ledger,
        "ledger_snapshot": ledger_cards(visible_ledger, scope=scope_for_state(state)),
        "audit_snapshot": audit_cards(audit_log),
        "phase": "done",
        "status": state.get("status") or "Done",
        "decision_chain": _append_decision(state, stage="finalize_turn", decision="immutable_loop_audit_committed", details={"event_id": event["event_id"], "loop_steps": len(plans), "trace_count": len(state.get("tool_trace") or []), "workflow_status": ((read_plan_projection(state) or {}).get("status"))}),
    }


def trim_agent_loop_messages_node(state: dict[str, Any]) -> dict[str, Any]:
    messages = list(state.get("messages") or [])
    if len(messages) <= 36:
        return {}
    try:
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages[-36:]], "summary": None}
    except Exception:
        return {"summary": None}
