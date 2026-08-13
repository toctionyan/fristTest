from __future__ import annotations

from copy import deepcopy
import json
from typing import Any
from uuid import uuid4

try:
    from langchain_core.messages import ToolMessage
except Exception:  # pragma: no cover
    ToolMessage = None  # type: ignore

from agent_core.lifecycle.protocol import canonical_call_key, classify_tool
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.context import ContextBundleBuilder
from agent_core.context.audit_inspection import inspect_audit_event
from agent_core.ledger import append_entries, find_handle, ledger_cards, scope_for_state
from agent_core.lifecycle.task_board import apply_task_operation, normalize_task_board
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.execution_disposition import classify_execution_disposition
from agent_core.lifecycle.goal_lifecycle import (
    apply_semantic_contract_to_goal_records,
    update_goal_records_from_execution_plan,
)
from agent_core.lifecycle.semantic_state_changes import apply_focus_change
from agent_core.lifecycle.goal_blockers import apply_blocker_resolutions
from agent_core.lifecycle.goal_outputs import record_goal_outputs_from_tool_result
from agent_core.lifecycle.workflow_runtime import (
    build_workflow_plan,
    complete_plan_run_step_result,
    materialize_plan_runtime,
    project_plan_runtime,
    validate_plan_runtime_dispatch,
    validate_step_dispatch,
)
from agent_core.lifecycle.plan_execution import begin_step_attempt
from agent_core.runtime.outcomes import from_tool_result
from agent_core.runtime.target_compiler import compile_runtime_target_arguments
from agent_core.runtime.node_support import (
    append_decision as _append_decision,
    latest_human_text as _latest_human_text,
    max_same_calls as _max_same_calls,
)
from agent_core.runtime.capability_gate import issue_execution_permit, normalize_tool_arguments, permit_allows_dispatch, record_effect_decision
from agent_core.transaction.model import DRAFT_READY
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.storage.repositories.base import TransactionLifecycleRepository

def _tool_result_message(call: dict[str, Any], result: dict[str, Any]) -> Any | None:
    if ToolMessage is None:
        return None
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False, default=str),
        tool_call_id=str(call.get("id") or call.get("tool_call_id") or f"tool_{uuid4().hex[:8]}"),
        name=str(call.get("name") or "tool"),
    )


def _offer_is_ready_for_gateway(offer: dict[str, Any] | None) -> bool:
    if not isinstance(offer, dict):
        return False
    # draft_state is the sole runtime transaction authority.  Display fields
    # must not alter gateway routing.
    return str(offer.get("draft_state") or "") in {"READY", "NEEDS_INPUT"}


def _call_is_bound_to_write_capability(
    *,
    call: dict[str, Any],
    plan: dict[str, Any],
) -> bool:
    """Return whether the current grounded effect is a write-draft capability.

    Serialization is an execution-safety decision, not a language-semantics
    decision.  The runtime therefore reads only the capability-owned
    ``execution_kind`` already captured in the grounded plan.  Legacy
    ``goal_type`` metadata must never turn a read into a write or let a write
    bypass an active structured interaction.
    """
    effect_id = str(call.get("_effect_id") or "")
    effect = next((
        row for row in list(plan.get("effects") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "") == effect_id
    ), {})
    goal_ids = {
        str(value)
        for value in list(effect.get("goal_ids") or call.get("_goal_ids") or [])
        if str(value)
    }
    return bool(goal_ids) and str(effect.get("execution_kind") or "") == "action_draft"


def _queue_ready_action_transition(
    *,
    state: dict[str, Any],
    ledger: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    board: list[dict[str, Any]],
    task_ids: list[str],
    call: dict[str, Any],
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Queue every draft that a tool transitions into a ready state.

    This is deliberately result/state based rather than tool-name based.  A
    future module may have a different tool for adding documents, selecting a
    date, or completing a form; if it turns an existing ActionDraft into
    ``ready``, it receives the same gateway/authority lifecycle.
    """
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    handle = str(data.get("offer_handle") or "")
    if not handle:
        return ledger, queue, board, task_ids
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False)
    if not _offer_is_ready_for_gateway(offer):
        return ledger, queue, board, task_ids
    # Stamp complete drafts for a later freshness preflight.  A needs-input
    # draft deliberately keeps its form state and is routed to the same
    # gateway so the client can render fields immediately.
    if str((offer or {}).get("draft_state") or "").upper() == DRAFT_READY and int((offer or {}).get("ready_turn") or 0) != int(state.get("turn_index") or 0):
        transitioned = deepcopy(offer)
        transitioned["ready_turn"] = int(state.get("turn_index") or 0)
        transitioned["updated_turn"] = int(state.get("turn_index") or 0)
        transitioned["ready_source_tool"] = str(call.get("name") or "")
        ledger = append_entries(ledger, [transitioned])
        offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False) or transitioned
    if any(str(item.get("offer_handle") or "") == handle for item in queue):
        return ledger, queue, board, task_ids
    queue.append({
        "offer_handle": handle,
        "origin_call_id": str(call.get("id") or call.get("tool_call_id") or ""),
        "origin_tool": str(call.get("name") or ""),
        "loop_step": int(state.get("agent_loop_step") or 0),
        "transition": "draft_ready",
    })
    existing_task = next((row for row in board if handle in [str(v) for v in row.get("action_handles") or []]), None)
    if existing_task is None:
        task = {
            "task_id": f"task_action_{handle[-10:]}",
            "title": str((offer or {}).get("label") or data.get("offer_label") or "业务动作草稿"),
            "status": "active",
            "target_handles": [str((offer or {}).get("target_handle") or "")],
            "action_handles": [handle],
            "created_turn": int(state.get("turn_index") or 0),
            "updated_turn": int(state.get("turn_index") or 0),
            "status_note": "动作草稿已就绪，等待动作网关决定下一步。",
            "soft_state": True,
        }
        board = normalize_task_board([*board, task])
        task_ids.append(task["task_id"])
    return ledger, queue, board, task_ids


def execute_agent_loop_calls_node(
    state: dict[str, Any],
    *,
    context_bundle_builder: ContextBundleBuilder,
    transactions: TransactionLifecycleRepository,
    capability_registry: CapabilityRegistry,
) -> dict[str, Any]:
    plan = state.get("current_turn_plan") if isinstance(state.get("current_turn_plan"), dict) else {}
    calls = list(plan.get("tool_calls") or [])
    ledger = list(state.get("artifact_ledger") or [])
    trace = list(state.get("tool_trace") or [])
    sources = list(state.get("sources") or [])
    seen = list(state.get("agent_loop_seen_calls") or [])
    board = normalize_task_board(state.get("task_board") or [])
    task_ids = list(state.get("current_turn_task_ids") or [])
    tool_messages: list[Any] = []
    action_queue = list(state.get("action_queue") or [])
    execution_permits = list(state.get("execution_permits") or [])
    turn_match_proofs = list(state.get("turn_match_proofs") or [])
    latest_runtime_outcome = state.get("runtime_outcome") if isinstance(state.get("runtime_outcome"), dict) else None
    workflow_plan = read_plan_projection(state)
    frozen_plan_definition = deepcopy(state.get("frozen_plan_definition")) if isinstance(state.get("frozen_plan_definition"), dict) else None
    plan_run = deepcopy(state.get("plan_run")) if isinstance(state.get("plan_run"), dict) else None
    if isinstance(workflow_plan, dict) and (frozen_plan_definition is None or plan_run is None):
        frozen_plan_definition, plan_run, workflow_plan = materialize_plan_runtime(
            state=state,
            workflow_plan=workflow_plan,
        )
    grounded_execution_plan = deepcopy(workflow_plan)
    frozen_semantic_contract = deepcopy(state.get("frozen_semantic_contract")) if isinstance(state.get("frozen_semantic_contract"), dict) else None
    semantic_proposal = deepcopy(state.get("semantic_proposal")) if isinstance(state.get("semantic_proposal"), dict) else None
    goal_declaration: dict[str, Any] | None = None
    goal_blockers = [deepcopy(row) for row in list(state.get("goal_blockers") or []) if isinstance(row, dict)]
    goal_records = [deepcopy(row) for row in list(state.get("goal_records") or []) if isinstance(row, dict)]
    goal_output_refs = [deepcopy(row) for row in list(state.get("goal_output_refs") or []) if isinstance(row, dict)]
    focus_state = deepcopy(state.get("focus_state")) if isinstance(state.get("focus_state"), dict) else None
    execution_dispositions = list(state.get("execution_dispositions") or [])
    latest_execution_disposition = state.get("latest_execution_disposition") if isinstance(state.get("latest_execution_disposition"), dict) else None
    redirected_interaction_contract: dict[str, Any] | None = None
    maximum_same = _max_same_calls()

    # Resolve any response that was incorrectly attempted in the same model
    # call as live tools; the model receives this observation next loop.
    for deferred in state.get("deferred_terminal_calls") or []:
        msg = _append_terminal_protocol_message(deferred, code="FINAL_DEFERRED_UNTIL_OBSERVATIONS")
        if msg is not None:
            tool_messages.append(msg)
    deferred_terminal_calls: list[dict[str, Any]] = []

    for index, call in enumerate(calls):
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        category = classify_tool(name, capability_registry).category
        compiled_runtime_target: dict[str, Any] | None = None
        if (
            category in {"observation", "action_draft"}
            and isinstance(frozen_semantic_contract, dict)
            and any(str(value) for value in list(call.get("_goal_ids") or []))
        ):
            capability_contract = capability_registry.contract_for_tool(name)
            planning_contract = capability_contract.planning_contract if capability_contract is not None else None
            target_contract = planning_contract.target if planning_contract is not None else None
            if target_contract is not None and target_contract.argument_projection is not None:
                args, compiled_runtime_target = compile_runtime_target_arguments(
                    frozen_semantic_contract,
                    goal_ids=[str(value) for value in list(call.get("_goal_ids") or []) if str(value)],
                    target_contract=target_contract,
                    arguments=args,
                )
                call["args"] = args
        preview_args, _preview_normalization = normalize_tool_arguments(args) if category in {"observation", "action_draft"} else (dict(args), {})
        key = canonical_call_key(name, preview_args)
        active_step_attempt_id: str | None = None
        if (
            isinstance(compiled_runtime_target, dict)
            and str(compiled_runtime_target.get("status") or "") == "REJECTED"
        ):
            result = {
                "ok": False,
                "code": "DETERMINISTIC_TARGET_AUTHORITY_REJECTED",
                "message": "冻结目标证据未通过确定性运行态目标编译，系统不会回退到模型选择的目标。",
                "data": {"target_authority": deepcopy(compiled_runtime_target)},
                "match_proof": None,
                "execution_permit": None,
            }
        elif seen.count(key) >= maximum_same:
            result = {"ok": False, "code": "DUPLICATE_OBSERVATION_SUPPRESSED", "message": "同一回合已获得该工具观察，请根据已有结果继续判断或回答。"}
        elif category == "internal":
            if name == "declare_turn_goals":
                result, declared = validate_goal_declaration(
                    state=state,
                    args=args,
                    capability_registry=capability_registry,
                    require_canonical_output_identity=True,
                )
                result_data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
                granularity_proof = result_data.get("granularity_proof") if isinstance(result_data.get("granularity_proof"), dict) else {}
                granularity_details = granularity_proof.get("details") if isinstance(granularity_proof.get("details"), dict) else {}
                inventory_authority = granularity_details.get("inventory_authority")
                if isinstance(inventory_authority, dict):
                    # Freeze the final candidate-blind authority that produced
                    # this ToolMessage. _build_loop_plan preserves prior plan
                    # metadata across the bounded declaration-repair loop.
                    plan = {
                        **dict(plan),
                        "goal_granularity_inventory_authority": deepcopy(inventory_authority),
                    }
                if declared is not None:
                    declaration_projection = deepcopy(declared)
                    candidate_contract = declaration_projection.pop("_frozen_semantic_contract", None)
                    candidate_proposal = declaration_projection.pop("_semantic_proposal", None)
                    candidate_records = goal_records
                    candidate_blockers = goal_blockers
                    candidate_focus = focus_state
                    try:
                        if not isinstance(candidate_contract, dict):
                            raise ValueError("frozen_semantic_contract_required")
                        candidate_records = apply_semantic_contract_to_goal_records(
                            goal_records,
                            candidate_contract,
                            turn=int(state.get("turn_index") or 0),
                        )
                        candidate_blockers = apply_blocker_resolutions(
                            goal_blockers,
                            list(candidate_contract.get("blocker_resolutions") or []),
                            turn=int(state.get("turn_index") or 0),
                        )
                        focus_change = candidate_contract.get("focus_change")
                        if isinstance(focus_change, dict):
                            candidate_focus = apply_focus_change(
                                focus_state,
                                focus_change,
                                turn=int(state.get("turn_index") or 0),
                            )
                    except ValueError as exc:
                        result = {
                            "ok": False,
                            "code": "SEMANTIC_STATE_TRANSITION_REJECTED",
                            "message": "正式语义引用的目标或待补信息状态已变化，请重新声明当前请求。",
                            "data": {"reason": str(exc)},
                        }
                    else:
                        # Publish candidate-derived state atomically only after
                        # all deterministic lifecycle/blocker checks pass.
                        frozen_semantic_contract = candidate_contract
                        semantic_proposal = candidate_proposal
                        goal_declaration = declaration_projection
                        goal_records = candidate_records
                        goal_blockers = candidate_blockers
                        focus_state = candidate_focus
                        plan = {**dict(plan), "goal_declaration": deepcopy(goal_declaration)}
                        candidate_workflow_plan = build_workflow_plan(
                            state={
                                **state,
                                "frozen_semantic_contract": frozen_semantic_contract,
                                                        "frozen_plan_definition": frozen_plan_definition,
                                "plan_run": plan_run,
                                "grounded_execution_plan": grounded_execution_plan,
                                "goal_records": goal_records,
                            },
                            turn_plan=plan,
                            user_text=_latest_human_text(state),
                        )
                        frozen_plan_definition, plan_run, workflow_plan = materialize_plan_runtime(
                            state={
                                **state,
                                "frozen_plan_definition": frozen_plan_definition,
                                "plan_run": plan_run,
                            },
                            workflow_plan=candidate_workflow_plan,
                        )
                        grounded_execution_plan = deepcopy(workflow_plan)
            elif name == "update_task_board":
                result, board, changed = apply_task_operation(
                    tasks=board,
                    args=args,
                    user_text=_latest_human_text(state),
                    ledger=list(state.get("artifact_ledger") or []),
                    scope=scope_for_state(state),
                    turn=int(state.get("turn_index") or 0),
                )
                task_ids.extend(changed)
            elif name == "inspect_audit_event":
                result = inspect_audit_event({**state, "context_bundle": state.get("context_bundle") or context_bundle_builder.build(state)}, trace_handle=str(args.get("trace_handle") or ""), reason_span=str(args.get("reason_span") or ""))
            else:
                result = {"ok": False, "code": "UNKNOWN_INTERNAL_TOOL", "message": "未注册内部工具。"}
        elif category == "disallowed":
            result = {"ok": False, "code": "CONFIRMATION_IS_GATEWAY_CONTROLLED", "message": "确认由动作网关依据风险策略处理，Planner 不能直接请求确认。"}
        elif _call_is_bound_to_write_capability(
            call=call,
            plan=plan,
        ) and (
            active_interaction := interaction_response_contract(
                {**state, "artifact_ledger": ledger}
            )
        ) is not None:
            # The durable pending card serializes every write-like chat turn.
            # Re-present it before MatchProof/Capability dispatch: chat text is
            # never allowed to populate, confirm, cancel, replace or submit a
            # structured transaction. Read-only calls take the normal path.
            redirected_interaction_contract = dict(active_interaction)
            interaction = dict(active_interaction.get("interaction") or {})
            result = {
                "ok": False,
                "code": "INTERACTION_REDIRECT",
                "message": "当前已有待办理事项；聊天文字不会修改、不会提交，也不会取消草稿。请在办理卡中补充、确认或取消。",
                "data": {"interaction_id": interaction.get("interaction_id")},
                "match_proof": None,
                "execution_permit": None,
            }
        elif category in {"observation", "action_draft"}:
            effect_id = str(call.get("_effect_id") or "")
            workflow_check = (
                validate_plan_runtime_dispatch(
                    definition=frozen_plan_definition,
                    plan_run=plan_run,
                    effect_id=effect_id,
                    semantic_contract=frozen_semantic_contract,
                )
                if isinstance(frozen_plan_definition, dict) and isinstance(plan_run, dict)
                else validate_step_dispatch(
                    workflow_plan=workflow_plan,
                    effect_id=effect_id,
                    semantic_contract=frozen_semantic_contract,
                )
            )
            if not workflow_check["ok"]:
                result = workflow_check
            else:
                # Each call sees only runtime-owned outputs from earlier calls.
                # This permits a verified same-turn result pipeline while the
                # reference gate still rejects an injected ledger handle.
                permit_state = {**state, "artifact_ledger": ledger, "tool_trace": trace}
                decision = issue_execution_permit(
                    state=permit_state,
                    tool_name=name,
                    args=args,
                    effect_id=effect_id,
                    capability_registry=capability_registry,
                )
                args = dict(decision.normalized_arguments or args)
                call["args"] = args
                if effect_id:
                    plan = record_effect_decision(plan, effect_id=effect_id, decision=decision)
                turn_match_proofs.append(dict(decision.match_proof))
                if decision.execution_permit:
                    execution_permits.append(dict(decision.execution_permit))
                if (
                    effect_id
                    and isinstance(frozen_plan_definition, dict)
                    and isinstance(plan_run, dict)
                ):
                    plan_run, step_attempt = begin_step_attempt(
                        definition=frozen_plan_definition,
                        plan_run=plan_run,
                        effect_id=effect_id,
                        tool_name=name,
                        args=args,
                        execution_permit=decision.execution_permit,
                    )
                    active_step_attempt_id = str(step_attempt.get("attempt_id") or "") or None
                if not decision.permitted or not permit_allows_dispatch(
                    state=permit_state,
                    permit=decision.execution_permit,
                    tool_name=name,
                    effect_id=effect_id,
                    args=args,
                ):
                    rejection = dict(decision.rejection or {})
                    result = {
                        "ok": False,
                        "code": str(rejection.get("code") or "CAPABILITY_EXACT_MATCH_REQUIRED"),
                        "message": str(rejection.get("message") or "当前请求没有通过精确能力校验。"),
                        "data": {"match_proof": decision.match_proof},
                        "match_proof": decision.match_proof,
                        "execution_permit": None,
                    }
                else:
                    # The domain plugin receives the permit as audit metadata;
                    # it cannot manufacture or reuse one for another effect.
                    result = capability_registry.dispatch_permitted(
                        {**permit_state, "execution_permit": decision.execution_permit},
                        name,
                        args,
                        execution_permit=decision.execution_permit,
                        effect_id=effect_id,
                        transactions=transactions,
                    )
                    if isinstance(result, dict):
                        result["match_proof"] = decision.match_proof
                        result["execution_permit"] = decision.execution_permit
                additions = result.pop("ledger_entries", []) if isinstance(result, dict) else []
                ledger = append_entries(ledger, additions)
                if isinstance(result, dict) and result.get("ok"):
                    goal_output_refs = record_goal_outputs_from_tool_result(
                        goal_output_refs,
                        state={
                            **state,
                            "artifact_ledger": ledger,
                            "frozen_semantic_contract": frozen_semantic_contract,
                        },
                        capability_registry=capability_registry,
                        tool_name=name,
                        goal_ids=[str(value) for value in list(call.get("_goal_ids") or []) if str(value)],
                        effect_id=str(call.get("_effect_id") or ""),
                        result=result,
                        ledger_additions=additions,
                        merged_ledger=ledger,
                    )
                for source in list(result.get("sources") or []) if isinstance(result, dict) else []:
                    if isinstance(source, dict) and source not in sources:
                        sources.append(source)
                if isinstance(result, dict) and result.get("ok"):
                    ledger, action_queue, board, task_ids = _queue_ready_action_transition(
                        state=state,
                        ledger=ledger,
                        queue=action_queue,
                        board=board,
                        task_ids=task_ids,
                        call=call,
                        result=result,
                    )
        else:
            # Unknown tools still receive a rejected MatchProof so Trace can
            # distinguish an explicit exact-match denial from a generic error.
            effect_id = str(call.get("_effect_id") or "")
            decision = issue_execution_permit(
                state=state,
                tool_name=name,
                args=args,
                effect_id=effect_id,
                capability_registry=capability_registry,
            )
            if effect_id:
                plan = record_effect_decision(plan, effect_id=effect_id, decision=decision)
            turn_match_proofs.append(dict(decision.match_proof))
            result = {
                "ok": False,
                "code": "UNKNOWN_OR_UNSUPPORTED_TOOL",
                "message": f"当前 Agent Loop 未注册工具：{name}。系统不会查找相近工具代替。",
                "match_proof": decision.match_proof,
            }

        # Every externally observable tool result crosses the closed RuntimeOutcome
        # boundary before it can influence final customer presentation.  Internal
        # orchestration tools are deliberately excluded: they never constitute a
        # customer-facing business conclusion by themselves.
        if (
            isinstance(result, dict)
            and category != "internal"
            and not isinstance(result.get("runtime_outcome"), dict)
        ):
            result["runtime_outcome"] = from_tool_result(
                tool_name=name,
                result=result,
                correlation_id=str(state.get("correlation_id") or "") or None,
            ).as_dict()

        if isinstance(result, dict) and category != "internal":
            disposition = classify_execution_disposition(
                state=state,
                tool_name=name,
                tool_signature=key,
                result=result,
            )
            result["execution_disposition"] = disposition
            execution_dispositions.append(disposition)
            latest_execution_disposition = disposition
        seen.append(key)
        tool_message = _tool_result_message(call, result)
        if tool_message is not None:
            tool_messages.append(tool_message)
        if isinstance(result, dict) and isinstance(result.get("runtime_outcome"), dict):
            latest_runtime_outcome = dict(result["runtime_outcome"])
        if isinstance(result, dict):
            effect_id = str(call.get("_effect_id") or "")
            if (
                not active_step_attempt_id
                and category in {"observation", "action_draft"}
                and effect_id
                and isinstance(frozen_plan_definition, dict)
                and isinstance(plan_run, dict)
            ):
                boundary_check = validate_plan_runtime_dispatch(
                    definition=frozen_plan_definition,
                    plan_run=plan_run,
                    effect_id=effect_id,
                    semantic_contract=frozen_semantic_contract,
                )
                if boundary_check.get("ok"):
                    plan_run, boundary_attempt = begin_step_attempt(
                        definition=frozen_plan_definition,
                        plan_run=plan_run,
                        effect_id=effect_id,
                        tool_name=name,
                        args=args,
                        execution_permit=(
                            result.get("execution_permit")
                            if isinstance(result.get("execution_permit"), dict)
                            else None
                        ),
                    )
                    active_step_attempt_id = str(boundary_attempt.get("attempt_id") or "") or None
            if (
                active_step_attempt_id
                and effect_id
                and isinstance(frozen_plan_definition, dict)
                and isinstance(plan_run, dict)
            ):
                # PlanRun is the only mutable execution authority. Result
                # classification and candidate-repair supersession are derived
                # directly from Definition + PlanRun, then the compatibility
                # projection is regenerated one-way.
                plan_run, _step_outcome = complete_plan_run_step_result(
                    definition=frozen_plan_definition,
                    plan_run=plan_run,
                    attempt_id=active_step_attempt_id,
                    effect_id=effect_id,
                    result=result,
                )
                workflow_plan = project_plan_runtime(
                    definition=frozen_plan_definition,
                    plan_run=plan_run,
                )
        trace.append({
            "plan_id": str(plan.get("plan_id") or ""),
            "loop_step": int(state.get("agent_loop_step") or 0),
            "call_index": index,
            "name": name,
            "args": deepcopy(args),
            "result": result,
            "classification": category,
            "effect_id": str(call.get("_effect_id") or "") or None,
            # Goal binding is already validated by the grounded execution plan.
            # Keep it on the trace so presentation can release one canonical
            # primary view per independent user Goal instead of collapsing the
            # entire turn to one block.  This is execution provenance, not a
            # second semantic interpretation.
            "goal_ids": [
                str(value)
                for value in list(call.get("_goal_ids") or [])
                if str(value)
            ],
            "match_proof": (result.get("match_proof") if isinstance(result, dict) else None),
            "execution_permit": (result.get("execution_permit") if isinstance(result, dict) else None),
            "compiled_runtime_target": (
                deepcopy(compiled_runtime_target)
                if isinstance(compiled_runtime_target, dict)
                else None
            ),
        })

    # Runtime progress is owned by PlanRun. The old grounded/workflow keys are
    # regenerated compatibility projections for consumers not yet migrated.
    if isinstance(frozen_plan_definition, dict) and isinstance(plan_run, dict):
        workflow_plan = project_plan_runtime(
            definition=frozen_plan_definition,
            plan_run=plan_run,
        )
    grounded_execution_plan = deepcopy(workflow_plan) if isinstance(workflow_plan, dict) else None
    goal_records = update_goal_records_from_execution_plan(
        goal_records,
        grounded_execution_plan,
        turn=int(state.get("turn_index") or 0),
    )

    return {
        "messages": tool_messages,
        "current_turn_plan": plan,
        "semantic_proposal": semantic_proposal,
        "frozen_semantic_contract": frozen_semantic_contract,
        "frozen_plan_definition": frozen_plan_definition,
        "plan_run": plan_run,
        "grounded_execution_plan": grounded_execution_plan,
        "goal_blockers": goal_blockers,
        "goal_records": goal_records,
        "goal_output_refs": goal_output_refs,
        "focus_state": focus_state,
        "execution_permits": execution_permits,
        "turn_match_proofs": turn_match_proofs,
        "artifact_ledger": ledger,
        "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
        "task_board": board,
        "current_turn_task_ids": list(dict.fromkeys(task_ids)),
        "tool_trace": trace,
        "sources": sources,
        "agent_loop_seen_calls": seen,
        "deferred_terminal_calls": deferred_terminal_calls,
        "action_queue": action_queue,
        "runtime_outcome": latest_runtime_outcome,
        "execution_dispositions": execution_dispositions,
        "latest_execution_disposition": latest_execution_disposition,
        "response_contract": redirected_interaction_contract,
        "phase": "action_gateway" if action_queue else "agent_loop",
        "status": "ActionProposalReady" if action_queue else "ObservationsRecorded",
        "decision_chain": _append_decision(state, stage="execute_loop_calls", decision="tools_observed", details={"tool_count": len(calls), "action_proposals": len(action_queue), "workflow_status": (workflow_plan or {}).get("status") if isinstance(workflow_plan, dict) else None}),
    }
