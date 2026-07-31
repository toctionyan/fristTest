"""Conversation turn orchestration for normal and SSE delivery."""
from __future__ import annotations

import json
from queue import Queue
from threading import Thread
from typing import Any, Generator

from fastapi import HTTPException

from app.schemas.chat_schema import ChatRequest, ChatResponse
from app.services.turn_lock import ConversationBusyError
from agent_core.business import business_actor_context
from agent_core.observability.trace_logger import TraceTimer
from agent_core.model_calls import model_call_scope
from agent_core.security.roles import normalize_role
from agent_core.lifecycle.state_schema import LegacyStateRestartRequired


class ConversationTurnService:
    """Runs authenticated chat turns while AgentService provides infrastructure seams."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def chat(self, request: ChatRequest, *, include_debug: bool = False) -> ChatResponse:
        service = self._service
        if service.graph is None:
            return service._runtime_unavailable_response(request.thread_id, include_debug=include_debug)
        service._claim_or_validate_thread(request.thread_id, request.user_id, request.tenant_id)
        timer = TraceTimer()
        role = normalize_role(request.role)
        try:
            with service._serialized_turn(request.thread_id, request.user_id, request.tenant_id) as lock_meta:
                service.message_store.add_message(request.thread_id, "user", request.message)
                service.trace_logger.log_event(
                    request.thread_id,
                    request.user_id,
                    "chat_start",
                    input_data={**request.model_dump(), "conversation_lock_wait_ms": lock_meta["wait_ms"]},
                )
                graph = service._require_graph()
                try:
                    checkpoint_reader = getattr(service, "_checkpoint_values", None)
                    if callable(checkpoint_reader):
                        checkpoint_reader(
                            graph,
                            thread_id=request.thread_id,
                            user_id=request.user_id,
                            tenant_id=request.tenant_id,
                        )
                except LegacyStateRestartRequired as exc:
                    return ChatResponse(
                        type="error",
                        thread_id=request.thread_id,
                        error=exc.code,
                        answer="该会话来自旧状态版本，缺少可安全迁移的结构化语义证据。请新建会话后继续，系统不会猜测恢复旧任务。",
                        state={"migration_error": {"reason": exc.reason, "details": exc.details}} if include_debug else None,
                    )
                with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, permissions=getattr(request, "actor_permissions", None)):
                    with model_call_scope(scope="chat_turn") as model_calls:
                        result = graph.invoke(
                            {
                                "current_thread_id": request.thread_id,
                                "current_user_id": request.user_id,
                                "current_role": role,
                                "current_tenant_id": request.tenant_id,
                                "messages": [service._human_message(request.message)],
                            },
                            config=service._config_for_request(request.thread_id, request.user_id, request.tenant_id),
                        )
                        if isinstance(result, dict):
                            # Response projection may run an independent answer
                            # release verifier.  Keep it inside the same request
                            # budget instead of hiding an extra model call.
                            result = {**result, "model_call_trace": model_calls.records, "model_call_budget": model_calls.summary()}
                        response = service._normalize(request.thread_id, result, include_debug=include_debug)
                        if isinstance(result, dict):
                            result["model_call_trace"] = list(model_calls.records)
                            result["model_call_budget"] = model_calls.summary()
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
                service.trace_logger.log_event(request.thread_id, request.user_id, "chat_end", output_data=response.model_dump(), latency_ms=timer.ms())
                return response
        except ConversationBusyError as exc:
            service.trace_logger.log_event(request.thread_id, request.user_id, "conversation_busy", output_data={"operation": "chat", "error": str(exc)}, latency_ms=timer.ms())
            return ChatResponse(type="error", thread_id=request.thread_id, error="CONVERSATION_BUSY", answer="当前会话正在处理上一条请求，请勿重复提交。")
        except Exception as e:
            service.trace_logger.log_event(request.thread_id, request.user_id, "chat_error", output_data={"error": str(e), "error_type": e.__class__.__name__}, latency_ms=timer.ms())
            return ChatResponse(
                type="error",
                thread_id=request.thread_id,
                error="CHAT_RUNTIME_FAILED",
                answer="系统处理请求时出现异常，请稍后重试或联系人工客服。",
                state={"debug_error": {"error_type": e.__class__.__name__, "error": str(e)}} if include_debug else None,
            )


    def stream(self, request: ChatRequest, *, include_debug: bool = False) -> Generator[str, None, None]:
        """Stream graph updates and then read the authoritative checkpoint state.

        ``stream_mode=updates`` yields deltas, not the final merged state.  The
        final ``get_state`` read is mandatory so streamed and non-streamed
        requests persist identical messages, summaries and trace snapshots.

        Starlette is allowed to resume a synchronous response generator on a
        different worker context after each ``yield``.  Business identity uses
        ``ContextVar`` tokens, which must be reset in the context that created
        them.  Therefore the worker owning the graph also owns those scopes;
        this generator only relays its already-projected SSE frames.  It keeps
        public updates live without leaking graph internals or corrupting the
        actor context.
        """
        service = self._service

        def sse(event: str, data: Any) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

        frames: Queue[str | object] = Queue()
        finished = object()

        def emit(event: str, data: Any) -> None:
            frames.put(sse(event, data))

        def emit_failure(data: dict[str, Any], *, message: str) -> None:
            """Finish failures without widening the ordinary SSE event API.

            Public consumers receive only ``start``, ``public_update``,
            ``result`` and ``end``.  An ordinary failure is therefore the
            same typed ``ChatResponse(type=error)`` carried by the normal
            result event; only an already-authorized debug consumer may see a
            separate diagnostic error event.
            """
            if include_debug:
                emit("error", data)
            else:
                error = str(data.get("error") or message)
                code = str(data.get("code") or "CHAT_STREAM_REJECTED")
                emit(
                    "result",
                    ChatResponse(
                        type="error",
                        thread_id=request.thread_id,
                        error=code,
                        answer=error,
                        presentation_mode="notice",
                    ).model_dump(),
                )
            emit("end", {"message": message})

        def run_turn() -> None:
            timer = TraceTimer()
            try:
                if service.graph is None:
                    response = service._runtime_unavailable_response(request.thread_id, include_debug=include_debug)
                    emit("result", response.model_dump())
                    emit("end", {"message": "Agent runtime unavailable"})
                    return
                try:
                    service._claim_or_validate_thread(request.thread_id, request.user_id, request.tenant_id)
                except HTTPException as exc:
                    emit_failure(
                        {"error": exc.detail, "status_code": exc.status_code},
                        message="stream rejected",
                    )
                    return

                role = normalize_role(request.role)
                config = service._config_for_request(request.thread_id, request.user_id, request.tenant_id)
                with service._serialized_turn(request.thread_id, request.user_id, request.tenant_id) as lock_meta:
                    emit("start", {"thread_id": request.thread_id})
                    service.message_store.add_message(request.thread_id, "user", request.message)
                    service.trace_logger.log_event(request.thread_id, request.user_id, "chat_stream_start", input_data={**request.model_dump(), "conversation_lock_wait_ms": lock_meta["wait_ms"]})
                    graph = service._require_graph()
                    try:
                        checkpoint_reader = getattr(service, "_checkpoint_values", None)
                        if callable(checkpoint_reader):
                            checkpoint_reader(
                                graph,
                                thread_id=request.thread_id,
                                user_id=request.user_id,
                                tenant_id=request.tenant_id,
                            )
                    except LegacyStateRestartRequired as exc:
                        emit(
                            "result",
                            ChatResponse(
                                type="error",
                                thread_id=request.thread_id,
                                error=exc.code,
                                answer="该会话来自旧状态版本，缺少可安全迁移的结构化语义证据。请新建会话后继续，系统不会猜测恢复旧任务。",
                                state={"migration_error": {"reason": exc.reason, "details": exc.details}} if include_debug else None,
                            ).model_dump(),
                        )
                        emit("end", {"message": "legacy checkpoint restart required"})
                        return
                    with business_actor_context(user_id=request.user_id, role=role, tenant_id=request.tenant_id, permissions=getattr(request, "actor_permissions", None)):
                        with model_call_scope(scope="chat_stream") as model_calls:
                            for update in graph.stream({
                                "current_thread_id": request.thread_id,
                                "current_user_id": request.user_id,
                                "current_role": role,
                                "current_tenant_id": request.tenant_id,
                                "messages": [service._human_message(request.message)],
                            }, config=config, stream_mode="updates"):
                                if include_debug:
                                    emit("graph_update", update)
                                else:
                                    public = service.sse_stream_adapter.project_public_update(update)
                                    if public:
                                        emit("public_update", public)
                            snapshot = graph.get_state(config)
                            result = dict(getattr(snapshot, "values", {}) or {})
                            result.update({"model_call_trace": model_calls.records, "model_call_budget": model_calls.summary()})
                            response = service._normalize(request.thread_id, result, include_debug=include_debug)
                            result.update({"model_call_trace": list(model_calls.records), "model_call_budget": model_calls.summary()})
                    lock_meta["assert_valid"]()
                    service._persist_public_response(request.thread_id, response, result)
                    service.thread_store.upsert_thread(request.thread_id, request.user_id, summary=result.get("summary"), tenant_id=request.tenant_id)
                    service.trace_logger.log_event(request.thread_id, request.user_id, "graph_snapshot", node="graph", output_data=service._debug_snapshot(result))
                    service.trace_logger.log_event(request.thread_id, request.user_id, "chat_stream_end", output_data=response.model_dump(), latency_ms=timer.ms())
                    emit("result", response.model_dump())
                    emit("end", {"message": "stream finished"})
            except ConversationBusyError as exc:
                service.trace_logger.log_event(request.thread_id, request.user_id, "conversation_busy", output_data={"operation": "chat_stream", "error": str(exc)}, latency_ms=timer.ms())
                emit_failure(
                    {"error": "当前会话正在处理上一条请求，请勿重复提交。", "code": "CONVERSATION_BUSY"},
                    message="stream busy",
                )
            except Exception as exc:
                service.trace_logger.log_event(request.thread_id, request.user_id, "chat_stream_error", output_data={"error": str(exc), "error_type": exc.__class__.__name__}, latency_ms=timer.ms())
                payload = {
                    "error": "系统处理请求时出现异常，请稍后重试或联系人工客服。",
                    "code": "CHAT_RUNTIME_FAILED",
                }
                if include_debug:
                    payload["debug_error"] = {"error_type": exc.__class__.__name__, "error": str(exc)}
                emit_failure(payload, message="stream failed")
            finally:
                frames.put(finished)

        worker = Thread(target=run_turn, name=f"chat-stream-{request.thread_id}", daemon=True)
        worker.start()
        while True:
            frame = frames.get()
            if frame is finished:
                return
            yield str(frame)
