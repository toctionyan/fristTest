"""Structured customer transaction start through the formal lifecycle command facade."""
from __future__ import annotations

from typing import Any

from app.schemas.chat_schema import ChatResponse, TransactionStartRequest
from app.services.turn_lock import ConversationBusyError
from app.use_cases.outcome_projection import project_runtime_outcome
from agent_core.composition import get_runtime_registry
from agent_core.business import get_business_port
from agent_core.business import BusinessServiceError, business_actor_context
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.ledger import LEDGER_SCHEMA_VERSION, append_entries, artifact_entry, ledger_cards, offer_entry, scope_for_state
from agent_core.observability.trace_logger import TraceTimer
from agent_core.security.roles import normalize_role
from agent_core.transaction import transition_draft
from agent_core.transaction.availability import check_transaction_repository_available
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.active_draft import active_draft_patch
from agent_core.transaction.operation_preparation import OperationPreparationRuntime
from agent_core.runtime.outcomes import RuntimeOutcome, outcome
from agent_core.resources.targets import TargetResolver



class TransactionStartUseCase:
    """Starts a Draft through the canonical gateway runtime with zero LLM calls."""

    def __init__(self, service: Any) -> None:
        # Service supplies infrastructure seams (lock, checkpoint, trace and
        # customer response projection); business sequencing stays here.
        self._service = service

    def execute(self, request: TransactionStartRequest, *, include_debug: bool = False) -> ChatResponse:
        """Start a customer transaction from a structured UI action.

        This endpoint creates only a draft/form/authority interaction. It does
        not commit any business write and it never invokes the LLM.
        """
        service = self._service
        if service.graph is None:
            return service._runtime_unavailable_response(request.thread_id, include_debug=include_debug)
        service._claim_or_validate_thread(request.thread_id, request.user_id, request.tenant_id)
        timer = TraceTimer()
        role = normalize_role(request.role)
        plugin = get_runtime_registry().operations.get(request.action_id)
        operation = service._transaction_operation(request.action_id)
        if plugin is None or operation is None:
            return project_runtime_outcome(
                service,
                thread_id=request.thread_id,
                value=outcome(
                    "unsupported_capability",
                    correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                    customer_safe_summary="当前操作暂不支持在客户界面办理，未创建或提交任何业务申请。",
                ),
                include_debug=include_debug,
            )
        try:
            with service._serialized_turn(request.thread_id, request.user_id, request.tenant_id) as lock_meta:
                service.trace_logger.log_event(
                    request.thread_id,
                    request.user_id,
                    "transaction_start",
                    input_data={**request.model_dump(), "conversation_lock_wait_ms": lock_meta["wait_ms"]},
                )
                graph = service._require_graph()
                repository_outcome = check_transaction_repository_available(
                    getattr(service, "transactions", None),
                    scope=TransactionScope(
                        tenant_id=str(request.tenant_id or "default"),
                        user_id=str(request.user_id),
                        thread_id=str(request.thread_id),
                    ),
                    correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                    outcome_factory=outcome,
                )
                if repository_outcome is not None:
                    response = project_runtime_outcome(
                        service,
                        thread_id=request.thread_id,
                        value=repository_outcome,
                        include_debug=include_debug,
                    )
                    service.trace_logger.log_event(
                        request.thread_id,
                        request.user_id,
                        "transaction_start_repository_unavailable",
                        output_data={"outcome": repository_outcome.as_dict()},
                        latency_ms=timer.ms(),
                    )
                    return response
                config = service._config_for_request(request.thread_id, request.user_id, request.tenant_id)
                values = service._checkpoint_values(graph, thread_id=request.thread_id, user_id=request.user_id, tenant_id=request.tenant_id)
                existing = interaction_response_contract(values)
                if existing is not None:
                    response = service._normalize(request.thread_id, values, include_debug=include_debug)
                    service.trace_logger.log_event(request.thread_id, request.user_id, "transaction_start_existing_interaction", output_data=response.model_dump(), latency_ms=timer.ms())
                    return response
                base_state = {
                    **values,
                    "current_thread_id": request.thread_id,
                    "current_user_id": request.user_id,
                    "current_role": role,
                    "current_tenant_id": request.tenant_id,
                    "current_subject": request.subject or request.user_id,
                    "turn_index": max(1, int(values.get("turn_index") or 0)),
                    "ledger_schema_version": int(values.get("ledger_schema_version") or LEDGER_SCHEMA_VERSION),
                    "current_final_answer": None,
                    "response_contract": None,
                    **active_draft_patch(None),
                    "pending_confirmation_id": None,
                    "pending_confirmation_version": None,
                }
                actor_ctx = service._actor_context_for_request(request, role)
                with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, account_id=request.subject or request.user_id, permissions=getattr(request, "actor_permissions", None)):
                    ledger, target = service._resolve_transaction_target(
                        state=base_state, request=request, role=role, resource_type=plugin.target_resource_type
                    )
                    op, label = operation
                    target_set = TargetResolver(get_runtime_registry().resources).from_verified_members(
                        resource_type=plugin.target_resource_type,
                        handles=[str(target.get("handle") or "")],
                        source="verified_ui_reference_hint",
                        evidence_handles=[str(target.get("handle") or "")],
                        resolution_basis="verified_ui_reference_hint",
                        resolved_at_turn=int(base_state["turn_index"]),
                    )
                    prepared, capability_outcome = OperationPreparationRuntime(outcome_factory=outcome).prepare(
                        action_id=request.action_id,
                        target_set=target_set,
                        correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                    )
                    if capability_outcome is not None:
                        response = project_runtime_outcome(
                            service,
                            thread_id=request.thread_id,
                            value=capability_outcome,
                            include_debug=include_debug,
                        )
                        service.trace_logger.log_event(
                            request.thread_id, request.user_id, "transaction_start_capability_rejected",
                            output_data={"outcome": capability_outcome.as_dict()}, latency_ms=timer.ms(),
                        )
                        return response
                    assert prepared is not None
                    # UI hints are never authoritative transaction input.  The
                    # actual form/authority protocol is the only write path.
                    input_values: dict[str, Any] = {}
                    preview_payload = prepared.plugin.preview(
                        get_business_port(),
                        actor_ctx,
                        target={"resource_type": plugin.target_resource_type, "resource_id": str(target.get("resource_id") or "")},
                        input_values=input_values,
                    )
                if not preview_payload.get("success") or not isinstance(preview_payload.get("data"), dict):
                    unavailable = outcome(
                        "system_unavailable",
                        correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                        customer_safe_summary="当前无法完成业务预检，未创建或提交任何业务申请。请稍后重试。",
                        next_interaction="retry_later",
                        payload={"reason": "business_preview_unavailable"},
                    )
                    response = project_runtime_outcome(service, thread_id=request.thread_id, value=unavailable, include_debug=include_debug)
                    service.trace_logger.log_event(request.thread_id, request.user_id, "transaction_start_preview_unavailable", output_data={"outcome": unavailable.as_dict()}, latency_ms=timer.ms())
                    return response
                preview = dict(preview_payload.get("data") or {})
                snapshot_data = preview.get("snapshot") if isinstance(preview.get("snapshot"), dict) else {}
                if snapshot_data.get("version") is not None:
                    input_values["expected_version"] = int(snapshot_data.get("version") or 1)
                offer = offer_entry(
                    action_id=request.action_id,
                    operation=op,
                    target_handle=str(target.get("handle") or ""),
                    input_values=input_values,
                    preview=preview,
                    scope=scope_for_state(base_state),
                    turn=int(base_state["turn_index"]),
                    label=label,
                )
                offer["operation_capability_snapshot"] = dict(prepared.capability_snapshot)
                offer["operation_capability_id"] = str(prepared.capability_snapshot.get("capability_id") or "")
                offer["operation_capability_version"] = str(prepared.capability_snapshot.get("version") or "")
                offer["operation_capability_digest"] = str(prepared.capability_snapshot.get("digest") or "")
                offer = transition_draft(offer, str(offer.get("draft_state") or "READY"))
                decision = str(preview.get("decision") or "")
                if decision == "NEEDS_INPUT":
                    offer = transition_draft(offer, "NEEDS_INPUT")
                    offer["required_inputs"] = [dict(row) for row in preview.get("required_inputs") or [] if isinstance(row, dict)]
                    offer["input_schema"] = offer["required_inputs"]
                elif decision not in {"ALLOWED", "NEEDS_REVIEW"}:
                    rejected_outcome = outcome(
                        "preview_rejected",
                        correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                        customer_safe_summary=str(preview.get("message") or "当前业务状态不允许该操作。"),
                        next_interaction="none",
                        payload={"action_id": request.action_id},
                    )
                    response = project_runtime_outcome(service, thread_id=request.thread_id, value=rejected_outcome, include_debug=include_debug)
                    service.trace_logger.log_event(request.thread_id, request.user_id, "transaction_start_rejected", output_data={"outcome": rejected_outcome.as_dict()}, latency_ms=timer.ms())
                    return response
                else:
                    offer["ready_turn"] = int(base_state["turn_index"])
                    offer["ready_source_tool"] = "structured_transaction_start"
                ledger = append_entries(ledger, [offer])
                operation_outcome = outcome(
                    "input_required" if decision == "NEEDS_INPUT" else "draft_created",
                    effects="input_required" if decision == "NEEDS_INPUT" else "draft_created",
                    safe_to_continue=True,
                    correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                    evidence_handles=[offer["handle"], str(target.get("handle") or "")],
                    customer_safe_summary=(
                        "已创建业务办理草稿，尚未提交任何业务申请；请在办理卡中补充信息。"
                        if decision == "NEEDS_INPUT"
                        else "已创建业务办理草稿，尚未提交任何业务申请；请在办理卡中确认。"
                    ),
                    next_interaction="open_form" if decision == "NEEDS_INPUT" else "open_authority",
                    payload={"offer_handle": offer["handle"], "action_id": request.action_id},
                ).as_dict()
                gateway_input = {
                    **base_state,
                    "artifact_ledger": ledger,
                    "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(base_state)),
                    "action_queue": [{"offer_handle": offer["handle"], "origin_tool": "structured_transaction_start"}],
                    "tool_trace": [
                        {
                            "name": "structured_transaction_start",
                            "args": {"action_id": request.action_id, "target": request.target, "input_hints": request.input_hints},
                            "result": {"ok": True, "offer_handle": offer["handle"], "preview": preview},
                            "classification": "action_draft",
                        }
                    ],
                    "phase": "action_gateway",
                    "status": "ActionProposalReady",
                    "runtime_outcome": operation_outcome,
                }
                with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, account_id=request.subject or request.user_id, permissions=getattr(request, "actor_permissions", None)):
                    lock_meta["assert_valid"]()
                    persisted = service._lifecycle_command_runner().advance_gateway(
                        graph=graph,
                        config=config,
                        state=gateway_input,
                    )
                response = service._normalize(request.thread_id, persisted, include_debug=include_debug)
                lock_meta["assert_valid"]()
                service._persist_public_response(request.thread_id, response, persisted)
                service.thread_store.upsert_thread(request.thread_id, request.user_id, summary=persisted.get("summary"), tenant_id=request.tenant_id)
                service.trace_logger.log_event(request.thread_id, request.user_id, "graph_snapshot", node="graph", output_data=service._debug_snapshot(persisted))
                service.trace_logger.log_event(request.thread_id, request.user_id, "transaction_start_end", output_data=response.model_dump(), latency_ms=timer.ms())
                return response
        except ConversationBusyError as exc:
            service.trace_logger.log_event(request.thread_id, request.user_id, "conversation_busy", output_data={"operation": "transaction_start", "error": str(exc)}, latency_ms=timer.ms())
            return ChatResponse(type="error", thread_id=request.thread_id, error="CONVERSATION_BUSY", answer="当前会话正在处理上一条请求，请勿重复提交。")
        except HTTPException:
            raise
        except BusinessServiceError as exc:
            service.trace_logger.log_event(request.thread_id, request.user_id, "transaction_start_error", output_data={"error": exc.message, "error_type": exc.__class__.__name__}, latency_ms=timer.ms())
            return project_runtime_outcome(
                service,
                thread_id=request.thread_id,
                value=outcome(
                    "system_unavailable",
                    correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                    customer_safe_summary="当前无法完成业务预检，未创建或提交任何业务申请。请稍后重试。",
                    next_interaction="retry_later",
                    payload={"reason": "business_service_error"},
                ),
                include_debug=include_debug,
            )
        except Exception as exc:
            service.trace_logger.log_event(request.thread_id, request.user_id, "transaction_start_error", output_data={"error": str(exc), "error_type": exc.__class__.__name__}, latency_ms=timer.ms())
            return project_runtime_outcome(
                service,
                thread_id=request.thread_id,
                value=outcome(
                    "failure",
                    correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
                    customer_safe_summary="当前无法启动业务办理，未创建或提交任何业务申请。请稍后重试。",
                    next_interaction="retry_later",
                    payload={"reason": "transaction_start_failed"},
                ),
                include_debug=include_debug,
            )
