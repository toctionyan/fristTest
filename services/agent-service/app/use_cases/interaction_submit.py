"""Structured transaction form and authority use cases."""
from __future__ import annotations

from typing import Any

from app.schemas.chat_schema import ActionAuthorityRequest, ActionInputRequest, ChatResponse
from app.use_cases.outcome_projection import project_runtime_outcome
from app.services.turn_lock import ConversationBusyError
from agent_core.business import business_actor_context
from agent_core.observability.trace_logger import TraceTimer
from agent_core.model_calls import model_call_scope
from agent_core.security.roles import normalize_role
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.availability import check_transaction_repository_available
from agent_core.runtime.outcomes import outcome


class InteractionSubmitUseCase:
    """Owns exact UI form/authority submission sequencing outside AgentService."""

    def __init__(self, service: Any) -> None:
        self._service = service

    @staticmethod
    def _repository_outcome(service: Any, request: Any):
        return check_transaction_repository_available(
            getattr(service, "transactions", None),
            scope=TransactionScope(
                tenant_id=str(getattr(request, "tenant_id", None) or "default"),
                user_id=str(getattr(request, "user_id", "")),
                thread_id=str(getattr(request, "thread_id", "")),
            ),
            correlation_id=str(getattr(request, "client_request_id", "") or "") or None,
            outcome_factory=outcome,
        )

    def authorize(self, request: ActionAuthorityRequest, *, include_debug: bool = False) -> ChatResponse:
        """Canonical UI authority entry: never accepts a free-text approval."""
        return self._apply_action_authority(request, include_debug=include_debug)

    def _apply_action_authority(self, request: ActionAuthorityRequest, *, include_debug: bool = False) -> ChatResponse:
        service = self._service
        if service.graph is None:
            return service._runtime_unavailable_response(request.thread_id, include_debug=include_debug)
        service._assert_thread_owner(request.thread_id, request.user_id, request.tenant_id)
        timer = TraceTimer()
        role = normalize_role(request.role)
        try:
            with service._serialized_turn(request.thread_id, request.user_id, request.tenant_id) as lock_meta:
                service.trace_logger.log_event(
                    request.thread_id,
                    request.user_id,
                    "action_authority_start",
                    input_data={**request.model_dump(), "conversation_lock_wait_ms": lock_meta["wait_ms"]},
                )
                graph = service._require_graph()
                repository_outcome = self._repository_outcome(service, request)
                if repository_outcome is not None:
                    return project_runtime_outcome(
                        service,
                        thread_id=request.thread_id,
                        value=repository_outcome,
                        include_debug=include_debug,
                    )
                with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, account_id=request.subject or request.user_id, permissions=getattr(request, "actor_permissions", None)):
                    service._reconcile_pending_transaction_attempts(
                        graph,
                        thread_id=request.thread_id,
                        user_id=request.user_id,
                        tenant_id=request.tenant_id,
                    )
                stale_reason = service._validate_action_authority(graph, request)
                if stale_reason:
                    response = service._confirmation_expired_response(
                        request.thread_id,
                        include_debug=include_debug,
                        reason=stale_reason,
                        interaction_id=request.offer_handle,
                        latest_state=service._checkpoint_values(
                            graph,
                            thread_id=request.thread_id,
                            user_id=request.user_id,
                            tenant_id=request.tenant_id,
                        ),
                    )
                    service.trace_logger.log_event(
                        request.thread_id,
                        request.user_id,
                        "action_authority_rejected",
                        output_data={"reason": stale_reason, "offer_handle": request.offer_handle, "confirmation_id": request.confirmation_id},
                        latency_ms=timer.ms(),
                    )
                    return response
                config = service._config_for_request(request.thread_id, request.user_id, request.tenant_id)
                with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, account_id=request.subject or request.user_id, permissions=getattr(request, "actor_permissions", None)):
                    with model_call_scope(scope="structured_interaction"):
                        result = graph.invoke(
                            service._resume_command({
                                "decision": request.decision,
                                "authority_type": request.authority_type,
                                "action_id": request.action_id,
                                "target_handle": request.target_handle,
                                "conversation_revision": request.conversation_revision,
                                "comment": request.comment,
                                "approved_by": request.user_id,
                                "approved_role": role,
                                "offer_handle": request.offer_handle,
                                "confirmation_id": request.confirmation_id,
                                "confirmation_version": request.confirmation_version,
                                "client_request_id": request.client_request_id,
                            }),
                            config=config,
                        )
                        # An interrupt resume can stop after authority advances the
                        # durable checkpoint to ``commit_action``.  The command
                        # facade runs the public formal node wrapper and resumes the
                        # compiled graph so Commit -> ExecutionDisposition ->
                        # routing/finalization cannot be bypassed by this UI path.
                        checkpoint_values: dict[str, Any] = {}
                        if hasattr(graph, "get_state"):
                            checkpoint = graph.get_state(config)
                            checkpoint_values = dict(getattr(checkpoint, "values", {}) or {})
                        lock_meta["assert_valid"]()
                        result = service._lifecycle_command_runner().commit_if_pending(
                            graph=graph,
                            config=config,
                            state=checkpoint_values or (result if isinstance(result, dict) else {}),
                        )
                response = service._normalize(request.thread_id, result, include_debug=include_debug)
                lock_meta["assert_valid"]()
                service._persist_public_response(request.thread_id, response, result)
                service.thread_store.upsert_thread(request.thread_id, request.user_id, summary=(result or {}).get("summary"), tenant_id=request.tenant_id)
                service.trace_logger.log_event(
                    request.thread_id,
                    request.user_id,
                    "graph_snapshot",
                    node="graph",
                    output_data=service._debug_snapshot(result or {}),
                )
                service.trace_logger.log_event(request.thread_id, request.user_id, "action_authority_end", output_data=response.model_dump(), latency_ms=timer.ms())
                return response
        except ConversationBusyError as exc:
            service.trace_logger.log_event(request.thread_id, request.user_id, "conversation_busy", output_data={"operation": "action_authority", "error": str(exc)}, latency_ms=timer.ms())
            return ChatResponse(type="error", thread_id=request.thread_id, error="CONVERSATION_BUSY", answer="当前会话正在处理上一条请求，请勿重复提交。")
        except Exception as e:
            service.trace_logger.log_event(request.thread_id, request.user_id, "action_authority_error", output_data={"error": str(e), "error_type": e.__class__.__name__}, latency_ms=timer.ms())
            return service._recover_interaction_after_exception(
                request,
                include_debug=include_debug,
                operation="action_authority",
                exception=e,
            )

    def submit_input(self, request: ActionInputRequest, *, include_debug: bool = False) -> ChatResponse:
        """Apply structured transaction form data without invoking the model."""
        service = self._service
        if service.graph is None:
            return service._runtime_unavailable_response(request.thread_id, include_debug=include_debug)
        service._assert_thread_owner(request.thread_id, request.user_id, request.tenant_id)
        timer = TraceTimer()
        role = normalize_role(request.role)
        try:
            with service._serialized_turn(request.thread_id, request.user_id, request.tenant_id) as lock_meta:
                service.trace_logger.log_event(
                    request.thread_id,
                    request.user_id,
                    "action_input_start",
                    input_data={**request.model_dump(), "conversation_lock_wait_ms": lock_meta["wait_ms"]},
                )
                graph = service._require_graph()
                repository_outcome = self._repository_outcome(service, request)
                if repository_outcome is not None:
                    return project_runtime_outcome(
                        service,
                        thread_id=request.thread_id,
                        value=repository_outcome,
                        include_debug=include_debug,
                    )
                stale_reason = service._validate_action_input(graph, request)
                if stale_reason:
                    response = service._confirmation_expired_response(
                        request.thread_id,
                        include_debug=include_debug,
                        reason=stale_reason,
                        interaction_id=request.offer_handle,
                        latest_state=service._checkpoint_values(
                            graph,
                            thread_id=request.thread_id,
                            user_id=request.user_id,
                            tenant_id=request.tenant_id,
                        ),
                    )
                    service.trace_logger.log_event(
                        request.thread_id,
                        request.user_id,
                        "action_input_rejected",
                        output_data={"reason": stale_reason, "offer_handle": request.offer_handle, "form_id": request.form_id},
                        latency_ms=timer.ms(),
                    )
                    return response
                with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, account_id=request.subject or request.user_id, permissions=getattr(request, "actor_permissions", None)):
                    with model_call_scope(scope="structured_interaction"):
                        result = graph.invoke(
                            service._resume_command({
                                "interaction_mode": request.interaction_mode,
                                "offer_handle": request.offer_handle,
                                "action_id": request.action_id,
                                "target_handle": request.target_handle,
                                "form_id": request.form_id,
                                "form_version": request.form_version,
                                "conversation_revision": request.conversation_revision,
                                "input_values": request.input_values,
                                "submitted_by": request.user_id,
                                "submitted_role": role,
                                "client_request_id": request.client_request_id,
                            }),
                            config=service._config_for_request(request.thread_id, request.user_id, request.tenant_id),
                        )
                response = service._normalize(request.thread_id, result, include_debug=include_debug)
                lock_meta["assert_valid"]()
                service._persist_public_response(request.thread_id, response, result)
                service.thread_store.upsert_thread(request.thread_id, request.user_id, summary=(result or {}).get("summary"), tenant_id=request.tenant_id)
                service.trace_logger.log_event(request.thread_id, request.user_id, "graph_snapshot", node="graph", output_data=service._debug_snapshot(result or {}))
                service.trace_logger.log_event(request.thread_id, request.user_id, "action_input_end", output_data=response.model_dump(), latency_ms=timer.ms())
                return response
        except ConversationBusyError as exc:
            service.trace_logger.log_event(request.thread_id, request.user_id, "conversation_busy", output_data={"operation": "action_input", "error": str(exc)}, latency_ms=timer.ms())
            return ChatResponse(type="error", thread_id=request.thread_id, error="CONVERSATION_BUSY", answer="当前会话正在处理上一条请求，请勿重复提交。")
        except Exception as e:
            service.trace_logger.log_event(request.thread_id, request.user_id, "action_input_error", output_data={"error": str(e), "error_type": e.__class__.__name__}, latency_ms=timer.ms())
            return service._recover_interaction_after_exception(
                request,
                include_debug=include_debug,
                operation="action_input",
                exception=e,
            )

