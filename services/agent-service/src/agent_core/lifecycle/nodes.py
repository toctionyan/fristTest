from __future__ import annotations

"""Thin LangGraph node facade.

The graph topology and public node names stay stable. Dialogue, tools and
transaction state machines live in dedicated runtimes so a context, gateway or
commit change no longer requires editing one God module.
"""

from typing import Any, Callable

from agent_core.context import ContextBundleBuilder
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.config import get_model
from agent_core.lifecycle.context_runtime import build_context_bundle_node, prepare_agent_loop_turn_node
from agent_core.lifecycle.dialogue_runtime import agent_loop_node as _dialogue_agent_loop_node
from agent_core.lifecycle.pretool_execution_policy import (
    TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY,
)
from agent_core.lifecycle.graph_routes import (
    finalize_agent_loop_turn_node,
    route_after_agent_loop,
    route_after_confirmation,
    trim_agent_loop_messages_node,
)
from agent_core.lifecycle.tool_execution_runtime import execute_agent_loop_calls_node
from agent_core.lifecycle.disposition_runtime import classify_execution_disposition_node, route_after_execution_disposition
from agent_core.lifecycle.execution_disposition import classify_execution_disposition
from agent_core.lifecycle.publish_runtime import persist_and_stream_node
from agent_core.runtime.node_support import append_decision as _append_decision, get_business_port
from agent_core.runtime.outcomes import outcome
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.transaction.commit_runtime import (
    COMMITTABLE_TRANSACTION_ACTION_IDS,
    _build_business_command_envelope,
    _execute_business_command_envelope as _transaction_execute_business_command_envelope,
    _new_resource_artifacts,
    commit_action_node as _commit_action_node,
)
from agent_core.transaction.gateway_runtime import action_gateway_node as _action_gateway_node
from agent_core.transaction.interaction_runtime import action_confirmation_node as _action_confirmation_node
from agent_core.transaction.reconciliation import reconcile_attempts
from agent_core.transaction.coordinator import record_transaction_receipt, transaction_store



def _resolve_transaction_execution(
    value: TransactionExecutionDeps | None,
) -> TransactionExecutionDeps:
    """Compatibility seam for direct node tests; production composition passes deps."""
    return value or TransactionExecutionDeps(
        business_port=get_business_port(),
        outcome_factory=outcome,
    )

def agent_loop_node(
    state: dict[str, Any],
    *,
    context_bundle_builder: ContextBundleBuilder,
    capability_registry: CapabilityRegistry,
    model_resolver: Callable[[], Any] = get_model,
    dependency_authority_control_resolver: Callable[[], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Thin node wrapper retaining explicit model and authority-control seams.

    Checkpoint/user/model state is never trusted as dependency-activation
    authority. The private key is stripped on every call and can only be
    reintroduced as an in-process callable supplied by LifecycleRuntimeDeps.
    """
    runtime_state = dict(state)
    runtime_state.pop(TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY, None)
    if callable(dependency_authority_control_resolver):
        runtime_state[TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY] = (
            dependency_authority_control_resolver
        )
    return _dialogue_agent_loop_node(
        runtime_state,
        context_bundle_builder=context_bundle_builder,
        capability_registry=capability_registry,
        model_resolver=model_resolver,
    )


def _append_formal_disposition(
    state: dict[str, Any],
    patch: dict[str, Any],
    *,
    tool_name: str,
    tool_signature: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach the closed post-execution classification to a formal runtime step.

    The wrapper only classifies facts already produced by the transaction
    runtime; it never chooses another target, tool, or business action.
    """
    disposition = classify_execution_disposition(
        state=state,
        tool_name=tool_name,
        tool_signature=tool_signature,
        result=result,
    )
    trace = list(patch.get("tool_trace") or state.get("tool_trace") or [])
    if trace and isinstance(trace[-1], dict) and str(trace[-1].get("name") or "") == tool_name:
        latest = dict(trace[-1])
        latest_result = dict(latest.get("result") or {})
        # Preserve the transaction runtime's original observation but normalize
        # the formal runtime boundary so Trace has the same RuntimeOutcome and
        # code used for the disposition decision.
        latest_result.update(result)
        latest_result["execution_disposition"] = disposition
        latest["result"] = latest_result
        trace[-1] = latest
    return {
        **patch,
        "tool_trace": trace,
        "execution_dispositions": [*(state.get("execution_dispositions") or []), disposition],
        "latest_execution_disposition": disposition,
        "decision_chain": _append_decision(
            {**state, **patch},
            stage="classify_formal_transaction_execution",
            decision="execution_disposition_recorded",
            details={
                "tool_name": tool_name,
                "disposition": disposition.get("disposition"),
                "runtime_action": disposition.get("runtime_action"),
                "auto_target_switch": False,
            },
        ),
    }



def action_gateway_node(
    state: dict[str, Any],
    *,
    transaction_execution: TransactionExecutionDeps | None = None,
) -> dict[str, Any]:
    """Run deterministic preflight/interaction routing, then classify it.

    The gateway has no semantic authority: it only projects the already-created
    Draft through business preflight and structured interaction requirements.
    Its observable conclusion is still a formal runtime execution and must be
    audited under the same disposition contract as every other execution.
    """
    patch = _action_gateway_node(state, deps=_resolve_transaction_execution(transaction_execution))
    gateway = patch.get("action_gateway_result") if isinstance(patch.get("action_gateway_result"), dict) else {}
    decision = str(gateway.get("decision") or "no_action")
    reason = str(gateway.get("reason") or gateway.get("message") or "办理状态已更新。")
    business_conclusion = decision in {"rejected", "human_review", "review_required"}
    clarification = decision == "clarify"
    if business_conclusion:
        runtime = outcome(
            "preview_rejected",
            effects="none",
            safe_to_continue=False,
            correlation_id=str(state.get("correlation_id") or "") or None,
            customer_safe_summary=reason,
            next_interaction="none",
            payload={"gateway_decision": decision, **gateway},
        ).as_dict()
        code = "GATEWAY_BUSINESS_CONCLUSION"
    elif clarification:
        runtime = outcome(
            "clarification",
            effects="none",
            safe_to_continue=False,
            correlation_id=str(state.get("correlation_id") or "") or None,
            customer_safe_summary=reason,
            next_interaction="need_selection",
            payload={"gateway_decision": decision, **gateway},
        ).as_dict()
        code = "CAPABILITY_SEMANTIC_CLARIFICATION_REQUIRED"
    else:
        runtime = outcome(
            "authority_required" if decision in {"awaiting_structured_authority", "needs_input"} else "draft_created",
            effects="authority_required" if decision == "awaiting_structured_authority" else "input_required" if decision == "needs_input" else "none",
            safe_to_continue=True,
            correlation_id=str(state.get("correlation_id") or "") or None,
            evidence_handles=[str(gateway.get("offer_handle") or "")],
            customer_safe_summary=reason,
            next_interaction="open_authority" if decision == "awaiting_structured_authority" else "open_form" if decision == "needs_input" else "none",
            payload={"gateway_decision": decision, **gateway},
        ).as_dict()
        code = "GATEWAY_PROGRESS"
    result = {
        "ok": not business_conclusion,
        "code": code,
        "message": reason,
        "data": {**gateway, "business_conclusion": business_conclusion},
        "runtime_outcome": runtime,
    }
    signature = "action_gateway:" + str(gateway.get("offer_handle") or "none") + ":" + decision
    return _append_formal_disposition(
        state,
        {**patch, "runtime_outcome": runtime},
        tool_name="action_gateway",
        tool_signature=signature,
        result=result,
    )

def action_confirmation_node(
    state: dict[str, Any],
    *,
    transaction_execution: TransactionExecutionDeps | None = None,
) -> dict[str, Any]:
    """Run the transaction interaction state machine with explicit execution dependencies."""
    return _action_confirmation_node(state, deps=_resolve_transaction_execution(transaction_execution))


def commit_action_node(
    state: dict[str, Any],
    *,
    transaction_execution: TransactionExecutionDeps | None = None,
) -> dict[str, Any]:
    """Commit through the durable transaction runtime, then classify its result.

    A commit is a formal effect-bearing execution, so it must not bypass the
    same disposition audit/routing boundary used by model-selected tools.
    """
    patch = _commit_action_node(state, deps=_resolve_transaction_execution(transaction_execution))
    gateway = patch.get("action_gateway_result") if isinstance(patch.get("action_gateway_result"), dict) else {}
    runtime_outcome = patch.get("runtime_outcome") if isinstance(patch.get("runtime_outcome"), dict) else {}
    decision = str(gateway.get("decision") or "")
    status = str(patch.get("status") or "")
    code = "SUBMISSION_UNKNOWN" if decision == "submission_unknown" or status == "ActionSubmissionUnknown" else "COMMIT_EXECUTED"
    result = {
        "ok": decision == "committed",
        "code": code,
        "message": str(gateway.get("message") or patch.get("current_final_answer") or "提交状态已更新。"),
        "data": {
            **gateway,
            "business_conclusion": decision in {"committed", "commit_failed"} or status in {
                "ActionUnavailable",
                "ActionCapabilityReviewRequired",
                "ActionTargetUnavailable",
                "ActionCommitPreflightRejected",
                "ActionCommitEnvelopeInvalid",
                "ActionAuthorityInvalid",
            },
        },
        "runtime_outcome": runtime_outcome,
    }
    signature = "commit_action:" + str(gateway.get("attempt_id") or gateway.get("idempotency_key") or gateway.get("offer_handle") or "none")
    return _append_formal_disposition(
        state,
        patch,
        tool_name="commit_action",
        tool_signature=signature,
        result=result,
    )


def _execute_business_command_envelope(
    state: dict[str, Any],
    envelope: dict[str, Any],
    *,
    idempotency_key: str,
    transaction_execution: TransactionExecutionDeps | None = None,
) -> dict[str, Any]:
    """Compatibility facade; production graph passes explicit dependencies."""
    deps = transaction_execution or TransactionExecutionDeps(
        business_port=get_business_port(),
        outcome_factory=outcome,
    )
    return _transaction_execute_business_command_envelope(
        state, envelope, idempotency_key=idempotency_key, deps=deps
    )


def reconcile_submission_node(
    state: dict[str, Any],
    *,
    transaction_execution: TransactionExecutionDeps | None = None,
) -> dict[str, Any]:
    """Reconcile only the persisted command envelope with its original idempotency key.

    ``reconcile_attempts`` is deliberately graph-independent and may return
    ``None`` when no durable attempt requires work. The facade ensures a
    reconciliation also crosses the formal disposition and RuntimeOutcome
    boundary, without allowing the model to resume semantic planning.
    """
    # Direct unit callers may replace the legacy zero-argument seam.  The
    # production graph always supplies the explicit repository in state.
    store = transaction_store(state) if state.get("_transaction_repository") is not None else transaction_store()
    patch = reconcile_attempts(
        state,
        store=store,
        execute_envelope=lambda current, envelope, idempotency_key: (
            _execute_business_command_envelope(
                current, envelope, idempotency_key=idempotency_key
            )
            if transaction_execution is None
            else _execute_business_command_envelope(
                current,
                envelope,
                idempotency_key=idempotency_key,
                transaction_execution=transaction_execution,
            )
        ),
        new_resource_artifacts=_new_resource_artifacts,
        # Reconciliation must persist the receipt in the same repository that
        # supplied the durable Attempt. Never rediscover another store mid-run.
        record_transaction_receipt_fn=lambda **kwargs: record_transaction_receipt(store=store, **kwargs),
    )
    if patch is None:
        answer = "当前没有需要对账的提交记录；系统未发起新的业务操作。"
        result = {
            "ok": True,
            "code": "RECONCILIATION_NOT_REQUIRED",
            "message": answer,
            "data": {"business_conclusion": True, "reconciliation": []},
            "runtime_outcome": outcome(
                "transaction_status",
                effects="none",
                safe_to_continue=False,
                correlation_id=str(state.get("correlation_id") or "") or None,
                customer_safe_summary=answer,
                next_interaction="none",
                payload={"reconciliation": []},
            ).as_dict(),
        }
        final_patch = {
            "phase": "final",
            "status": "SubmissionReconciliationNoPendingAttempt",
            "current_final_answer": answer,
            "model_mode_restriction": ["explain"],
            "runtime_outcome": result["runtime_outcome"],
            "tool_trace": [*(state.get("tool_trace") or []), {
                "plan_id": str((state.get("current_turn_plan") or {}).get("plan_id") or ""),
                "loop_step": int(state.get("agent_loop_step") or 0),
                "name": "reconcile_submission",
                "args": {"mode": "same_idempotency_key_only"},
                "result": result,
                "classification": "transaction_reconciliation",
            }],
        }
    else:
        reconciled = list(patch.get("transaction_reconciliation") or [])
        unknown = any(
            str(row.get("state") or "") in {"SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED"}
            for row in reconciled
            if isinstance(row, dict)
        )
        answer = (
            "提交结果仍在业务系统对账中，系统未重复提交；请稍后查询办理状态。"
            if unknown
            else "已按原提交记录完成对账，未创建新的重复业务操作。"
        )
        result = {
            "ok": not unknown,
            "code": "SUBMISSION_UNKNOWN" if unknown else "RECONCILIATION_COMPLETE",
            "message": answer,
            "data": {"business_conclusion": not unknown, "reconciliation": reconciled},
            "runtime_outcome": outcome(
                "submission_unknown" if unknown else "transaction_status",
                effects="unknown" if unknown else "none",
                safe_to_continue=False,
                correlation_id=str(state.get("correlation_id") or "") or None,
                customer_safe_summary=answer,
                next_interaction="retry_later" if unknown else "show_status",
                payload={"reconciliation": reconciled},
            ).as_dict(),
        }
        final_patch = {
            **patch,
            "phase": "final",
            "status": "SubmissionReconciliationPending" if unknown else "SubmissionReconciled",
            "current_final_answer": answer,
            "model_mode_restriction": ["explain"],
            "runtime_outcome": result["runtime_outcome"],
            "tool_trace": [*(patch.get("tool_trace") or state.get("tool_trace") or []), {
                "plan_id": str((state.get("current_turn_plan") or {}).get("plan_id") or ""),
                "loop_step": int(state.get("agent_loop_step") or 0),
                "name": "reconcile_submission",
                "args": {"mode": "same_idempotency_key_only"},
                "result": result,
                "classification": "transaction_reconciliation",
            }],
        }
    return _append_formal_disposition(
        state,
        final_patch,
        tool_name="reconcile_submission",
        tool_signature="reconcile_submission:same_idempotency_key_only",
        result=result,
    )


__all__ = [
    "COMMITTABLE_TRANSACTION_ACTION_IDS",
    "get_business_port",
    "prepare_agent_loop_turn_node",
    "build_context_bundle_node",
    "agent_loop_node",
    "execute_agent_loop_calls_node",
    "classify_execution_disposition_node",
    "route_after_execution_disposition",
    "action_gateway_node",
    "action_confirmation_node",
    "commit_action_node",
    "finalize_agent_loop_turn_node",
    "trim_agent_loop_messages_node",
    "persist_and_stream_node",
    "route_after_agent_loop",
    "route_after_confirmation",
    "reconcile_submission_node",
]
