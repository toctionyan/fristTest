import os
from contextlib import contextmanager
from threading import Lock, RLock
from time import monotonic
from typing import Any, Generator
from uuid import uuid4
from fastapi import HTTPException
from agent_core.composition import get_runtime_registry
from agent_core.resources.runtime import ResourceTargetRuntime
from agent_core.business import ActorContext, get_business_port
from agent_core.persistence.thread_store import ThreadOwnershipError, ThreadTenantMismatchError, UnboundThreadTenantError
from agent_core.observability.retention import prune_observability
from agent_core.security.roles import normalize_role
from agent_core.security.thread_security import secure_checkpoint_thread_id
from agent_core.persistence.store_provider import get_store_provider, reset_store_provider_cache
from agent_core.storage.repositories.base import TransactionScope
from app.services.sse_stream_adapter import SseStreamAdapter
from app.services.turn_lock import ConversationBusyError, ConversationLease
from agent_core.runtime.turn_fencing import TurnFence, activate_turn_fence
from app.services.checkpoint_hydrator import CheckpointHydrator
from app.services.response_projector import ResponseProjector
from app.services.stale_interaction import build_stale_interaction_response
from app.services.lifecycle_command_runner import LifecycleCommandRunner
from app.use_cases.transaction_start import TransactionStartUseCase
from app.use_cases.interaction_submit import InteractionSubmitUseCase
from app.use_cases.conversation_turn import ConversationTurnService
from app.schemas.chat_schema import ActionAuthorityRequest, ActionInputRequest, ChatRequest, ChatResponse, TransactionStartRequest
from agent_core.transaction.interaction import INTERACTION_SCHEMA_VERSION
from agent_core.ledger import append_entries, artifact_entry, find_handle, scope_for_state
from agent_core.transaction.active_draft import get_active_draft_id
from agent_core.transaction.focus import get_focused_draft_id
from agent_core.transaction.interaction import interaction_response_contract, pending_transaction_summaries_from_state
from agent_core.runtime.dependency_authority_control import dependency_authority_control_resolver
from app.services.dependency_authority_composition import (
    build_dependency_authority_control_composition,
)
from agent_core.runtime.deps import lifecycle_runtime_deps
from agent_core.runtime.typed_goal_evidence_ingress import disabled_typed_goal_evidence_resolver
from agent_core.config import clear_checkpointer_cache
class AgentService:
    # Process-local locks preserve request arrival order within one ASGI process.
    # The durable lock below protects the same invariant across worker processes.
    _turn_locks_guard = Lock()
    _turn_locks: dict[str, RLock] = {}

    def _compose_runtime_deps(self):
        """Compose dependency authority once; the customer-serving default stays disabled."""
        composition = build_dependency_authority_control_composition(
            store_provider=self.store_provider,
        )
        provider = composition.provider
        self.dependency_authority_control_composition = composition
        self.dependency_authority_control_provider = provider
        return lifecycle_runtime_deps(
            transactions=self.transactions,
            capability_registry=self.runtime_registry.capabilities,
            business_port=get_business_port(),
            trace_logger=self.trace_logger,
            dependency_authority_control_resolver=dependency_authority_control_resolver(provider),
            typed_goal_evidence_resolver=disabled_typed_goal_evidence_resolver,
        )

    def __init__(self):
        # Compose persistent dependencies before constructing the graph.  The
        # graph receives explicit ContextBundle/transaction dependencies and
        # never discovers StoreProvider during a model call.
        self.store_provider = get_store_provider()
        self.thread_store = self.store_provider.threads
        self.message_store = self.store_provider.messages
        self.action_audit_store = self.store_provider.action_audits
        self.conversation_lock_store = self.store_provider.locks
        self.trace_logger = self.store_provider.traces
        self.transactions = self.store_provider.transactions
        self.response_projector = ResponseProjector(message_store=self.message_store)
        self.checkpoint_hydrator = CheckpointHydrator(
            config_for_request=self._config_for_request,
            transactions=self.transactions,
            trace_logger=self.trace_logger,
        )
        self.transaction_start_use_case = TransactionStartUseCase(self)
        self.interaction_submit_use_case = InteractionSubmitUseCase(self)
        self.conversation_turn_service = ConversationTurnService(self)
        self.lifecycle_command_runner = LifecycleCommandRunner(self)
        self.runtime_registry = get_runtime_registry()
        self.runtime_deps = self._compose_runtime_deps()
        # Keep the Customer Portal API available even when optional LangGraph
        # runtime dependencies are absent. Chat endpoints fail clearly at call
        # time; they never pretend to run without the declared dependencies.
        self.graph = None
        self.agent_runtime_error: str | None = None
        try:
            from agent_core.lifecycle.graph import build_lifecycle_graph
            self.graph = build_lifecycle_graph(self.runtime_deps)
        except ModuleNotFoundError as exc:
            self.agent_runtime_error = f"缺少 Agent 运行依赖：{exc.name or str(exc)}"
        except Exception as exc:
            self.agent_runtime_error = f"Agent 图初始化失败：{exc.__class__.__name__}: {exc}"
        # Bounded retention runs at a controlled process boundary, never inside
        # graph nodes or user turns. It keeps trace/audit storage finite without
        # making retention part of business semantics.
        self.observability_retention = prune_observability(self.store_provider)
        # Bootstrap is read-only in customer-serving processes. Seed writes are
        # explicit migration/management commands, never hidden startup writes.
        self.rag_bootstrap_error: str | None = None
        try:
            from agent_core.rag.bootstrap import RagBootstrapService
            readiness = RagBootstrapService().verify_readiness(seed=False)
            if not readiness.get("ready"):
                self.rag_bootstrap_error = str(readiness.get("error") or "RAG unavailable")
        except Exception as exc:
            self.rag_bootstrap_error = f"{exc.__class__.__name__}: {exc}"

    @property
    def sse_stream_adapter(self) -> SseStreamAdapter:
        # The projector is bound lazily because tests may construct a minimal
        # AgentService shell without running __init__.
        return SseStreamAdapter(self._response_projector().public_state)

    def _require_graph(self):
        if self.graph is None:
            raise RuntimeError(self.agent_runtime_error or "Agent 运行图不可用。")
        return self.graph

    @staticmethod
    def _human_message(content: str):
        from langchain_core.messages import HumanMessage
        return HumanMessage(content=content)

    @staticmethod
    def _resume_command(payload: dict[str, Any]):
        from langgraph.types import Command
        return Command(resume=payload)

    def _runtime_unavailable_response(self, thread_id: str, *, include_debug: bool) -> ChatResponse:
        detail = self.agent_runtime_error or "Agent 运行图不可用。"
        return ChatResponse(
            type="answer",
            thread_id=thread_id,
            answer="Agent 对话运行依赖未安装或初始化失败；网页后台和业务数据功能仍可使用。请安装 Agent 项目声明的依赖后再试。",
            state={"debug_error": {"error_type": "AgentRuntimeUnavailable", "error": detail}} if include_debug else None,
        )

    def _secure_checkpoint_thread_id(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> str:
        return secure_checkpoint_thread_id(thread_id, user_id, tenant_id)

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _config_for_request(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict:
        return self._config(self._secure_checkpoint_thread_id(thread_id, user_id, tenant_id))

    def _checkpoint_values(self, graph: Any, *, thread_id: str, user_id: str, tenant_id: str | None) -> dict[str, Any]:
        hydrator = getattr(self, "checkpoint_hydrator", None)
        if hydrator is None:
            transactions = getattr(self, "transactions", None)
            if transactions is None:
                provider = getattr(self, "store_provider", None)
                transactions = getattr(provider, "transactions", None)
            if transactions is None:
                # Minimal read-only test shells deliberately bypass service
                # construction. They cannot safely migrate, so return exactly
                # the persisted values rather than inventing a transaction.
                snapshot = graph.get_state(self._config_for_request(thread_id, user_id, tenant_id))
                return dict(getattr(snapshot, "values", {}) or {})
            hydrator = CheckpointHydrator(
                config_for_request=self._config_for_request,
                transactions=transactions,
                trace_logger=getattr(self, "trace_logger", None),
            )
            self.checkpoint_hydrator = hydrator
        return hydrator.values(graph, thread_id=thread_id, user_id=user_id, tenant_id=tenant_id)

    def _response_projector(self) -> ResponseProjector:
        projector = getattr(self, "response_projector", None)
        if projector is None:
            projector = ResponseProjector(message_store=getattr(self, "message_store", None))
            self.response_projector = projector
        return projector

    def _transaction_start_use_case(self) -> TransactionStartUseCase:
        use_case = getattr(self, "transaction_start_use_case", None)
        if use_case is None:
            use_case = TransactionStartUseCase(self)
            self.transaction_start_use_case = use_case
        return use_case

    def _interaction_submit_use_case(self) -> InteractionSubmitUseCase:
        use_case = getattr(self, "interaction_submit_use_case", None)
        if use_case is None:
            use_case = InteractionSubmitUseCase(self)
            self.interaction_submit_use_case = use_case
        return use_case

    def _lifecycle_command_runner(self) -> LifecycleCommandRunner:
        runner = getattr(self, "lifecycle_command_runner", None)
        if runner is None:
            runner = LifecycleCommandRunner(self)
            self.lifecycle_command_runner = runner
        return runner

    def _conversation_turn_service(self) -> ConversationTurnService:
        use_case = getattr(self, "conversation_turn_service", None)
        if use_case is None:
            use_case = ConversationTurnService(self)
            self.conversation_turn_service = use_case
        return use_case

    @classmethod
    def _local_turn_lock(cls, identity: str) -> RLock:
        with cls._turn_locks_guard:
            lock = cls._turn_locks.get(identity)
            if lock is None:
                lock = RLock()
                cls._turn_locks[identity] = lock
            return lock

    @staticmethod
    def _turn_lock_identity(thread_id: str, user_id: str, tenant_id: str | None) -> str:
        return f"{tenant_id or 'default'}:{user_id}:{thread_id}"

    @contextmanager
    def _serialized_turn(self, thread_id: str, user_id: str, tenant_id: str | None = None):
        """Serialize every graph mutation for one authenticated conversation.

        LangGraph checkpoints persist state but do not provide a business-level
        single-writer transaction across concurrent HTTP requests.  A local
        re-entrant lock preserves order in this process; the durable lock guards
        against a second worker process.  A request waits briefly for a local
        turn, while a live lock owned by a different worker fails closed instead
        of resuming or planning against a moving checkpoint.
        """
        identity = self._turn_lock_identity(thread_id, user_id, tenant_id)
        local_lock = self._local_turn_lock(identity)
        started = monotonic()
        if not local_lock.acquire(timeout=30):
            raise ConversationBusyError("会话正在处理上一条请求，请勿重复提交。")
        owner = f"turn:{uuid4()}"
        lock_key = f"conversation-turn:{identity}"
        lease: ConversationLease | None = None
        try:
            ttl_seconds = max(30, min(int(os.getenv("CONVERSATION_LOCK_TTL_SECONDS", "300")), 3600))
            acquired_result = self.conversation_lock_store.acquire(
                lock_key, owner=owner, ttl_seconds=ttl_seconds
            )
            if not bool(acquired_result.get("acquired")):
                raise ConversationBusyError("会话正在由其他服务实例处理，请勿重复提交。")
            fencing_token = int(acquired_result.get("fencing_token") or 0)
            if fencing_token <= 0:
                raise RuntimeError("conversation lock store did not issue a fencing token")
            lease = ConversationLease(
                self.conversation_lock_store,
                lock_key=lock_key,
                owner=owner,
                fencing_token=fencing_token,
                ttl_seconds=ttl_seconds,
            )
            lease.start()
            fence = TurnFence(
                lock_key=lock_key, owner=owner, fencing_token=fencing_token,
                assert_valid=lease.assert_valid,
            )
            with activate_turn_fence(fence):
                yield {
                    "wait_ms": int((monotonic() - started) * 1000),
                    "lock_key": lock_key,
                    "fencing_token": fencing_token,
                    "assert_valid": lease.assert_valid,
                }
                lease.assert_valid()
        finally:
            if lease is not None:
                lease.close()
            local_lock.release()

    def _confirmation_expired_response(
        self,
        thread_id: str,
        *,
        include_debug: bool,
        reason: str,
        interaction_id: str | None = None,
        latest_state: dict[str, Any] | None = None,
    ) -> ChatResponse:
        return build_stale_interaction_response(
            thread_id,
            include_debug=include_debug,
            reason=reason,
            interaction_id=interaction_id,
            latest_state=latest_state,
        )

    def _recover_interaction_after_exception(
        self,
        request: ActionAuthorityRequest | ActionInputRequest,
        *,
        include_debug: bool,
        operation: str,
        exception: Exception,
    ) -> ChatResponse:
        """Read the checkpoint after an interrupted transaction request.

        A request may fail after LangGraph has durably advanced the action, for
        example when the response path raises after a business commit.  The
        browser must not guess that the action expired or that it is safe to
        retry.  Recover the authoritative checkpoint and return it only when it
        contains an active interaction or an explicit terminal lifecycle update.
        Otherwise return an explicit uncertainty error; the client keeps the
        existing card read-only until a refresh reconciles server state.
        """
        debug_error = {
            "error_type": exception.__class__.__name__,
            "error": str(exception),
            "operation": operation,
        }
        try:
            graph = self._require_graph()
            snapshot = graph.get_state(self._config_for_request(request.thread_id, request.user_id, request.tenant_id))
            result = dict(getattr(snapshot, "values", {}) or {})
            recovered = self._normalize(request.thread_id, result, include_debug=include_debug)
            # Recovery follows a direct structured UI request, not a free chat
            # turn.  If an earlier transport failure lost only the projection,
            # re-derive a live card from the durable Draft for this scoped
            # recovery response.  Normal chat responses never use this path.
            if recovered.type != "interaction_required":
                contract = interaction_response_contract(result)
                if contract is not None:
                    result = {**result, "response_contract": contract}
                    recovered = self._normalize(request.thread_id, result, include_debug=include_debug)
            is_authoritative = (
                recovered.type == "interaction_required"
                or isinstance(recovered.interaction_update, dict)
            )
            if is_authoritative:
                # An active form/authority card was already persisted when it was
                # first shown; do not append a duplicate history message.  A
                # recovered terminal state, however, may be the only visible
                # record of a commit that finished before the response failed.
                if isinstance(recovered.interaction_update, dict):
                    self._persist_public_response(request.thread_id, recovered, result)
                    self.thread_store.upsert_thread(
                        request.thread_id,
                        request.user_id,
                        summary=result.get("summary"),
                        tenant_id=request.tenant_id,
                    )
                self.trace_logger.log_event(
                    request.thread_id,
                    request.user_id,
                    f"{operation}_checkpoint_recovered",
                    output_data={
                        "response_type": recovered.type,
                        "interaction_lifecycle": (recovered.interaction or {}).get("lifecycle"),
                        "interaction_update": recovered.interaction_update,
                        "original_error": debug_error,
                    },
                )
                return recovered
        except Exception as recovery_error:
            debug_error["recovery_error_type"] = recovery_error.__class__.__name__
            debug_error["recovery_error"] = str(recovery_error)

        return ChatResponse(
            type="error",
            thread_id=request.thread_id,
            error="TRANSACTION_RESULT_UNCERTAIN",
            answer="暂时无法确认此次操作是否已处理。请刷新后查看最新状态，不要重复提交。",
            state={"debug_error": debug_error} if include_debug else None,
        )

    @staticmethod
    def _transaction_operation(action_id: str) -> tuple[str, str] | None:
        plugin = get_runtime_registry().operations.get(action_id)
        if plugin is None:
            return None
        return plugin.business_operation, plugin.label

    @staticmethod
    def _actor_context_for_request(request: Any, role: str) -> ActorContext:
        return ActorContext(
            user_id=str(request.user_id or ""),
            role=role,
            tenant_id=request.tenant_id,
            subject_user_id=str(getattr(request, "subject", "") or request.user_id or ""),
            subject=str(getattr(request, "subject", "") or request.user_id or ""),
            permissions=tuple(str(item) for item in (getattr(request, "actor_permissions", None) or []) if str(item)),
        )

    def _resolve_transaction_target(
        self,
        *,
        state: dict[str, Any],
        request: TransactionStartRequest,
        role: str,
        resource_type: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Resolve one structured UI target through the registered resource plugin."""
        scope_state = {
            **state,
            "current_thread_id": request.thread_id,
            "current_user_id": request.user_id,
            "current_tenant_id": request.tenant_id,
            "current_subject": request.subject or request.user_id,
        }
        try:
            hydrated = ResourceTargetRuntime(get_runtime_registry().resources).resolve_structured_target(
                ledger=list(state.get("artifact_ledger") or []),
                scope=scope_for_state(scope_state),
                turn=max(1, int(state.get("turn_index") or 0)),
                expected_resource_type=resource_type,
                raw_target=dict(request.target or {}),
                adapter=get_business_port(),
                actor=self._actor_context_for_request(request, role),
            )
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"unsupported transaction resource type: {resource_type}") from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return hydrated.ledger, hydrated.artifact

    def start_transaction(self, request: TransactionStartRequest, *, include_debug: bool = False) -> ChatResponse:
        return self._transaction_start_use_case().execute(request, include_debug=include_debug)

    def _reconcile_pending_transaction_attempts(self, graph: Any, *, thread_id: str, user_id: str, tenant_id: str | None) -> dict[str, Any] | None:
        """Run safe idempotent reconciliation before another transaction action.

        It only replays an existing command with its fixed idempotency key.  No
        model decision and no new business payload is created here.
        """
        config = self._config_for_request(thread_id, user_id, tenant_id)
        values = self._checkpoint_values(graph, thread_id=thread_id, user_id=user_id, tenant_id=tenant_id)
        if not values:
            return None
        # Do not inject the reconciliation node into a live form/authority
        # interrupt when there is no durable Attempt to reconcile.  Resuming
        # an empty reconciliation branch schedules its final edge and can
        # discard the pending interrupt before the browser submits its exact
        # authority envelope.  The durable repository is the only source of
        # truth for whether reconciliation is necessary.
        finder = getattr(self.transactions, "list_reconcilable_attempts", None)
        if not callable(finder):
            return None
        scope = TransactionScope(
            tenant_id=str(tenant_id or "default"),
            user_id=str(user_id),
            thread_id=str(thread_id),
        )
        if not finder(scope=scope, limit=20):
            return None
        values.update({"current_thread_id": thread_id, "current_user_id": user_id, "current_tenant_id": tenant_id})
        result = self._lifecycle_command_runner().reconcile_submission(
            graph=graph,
            config=config,
            state=values,
        )
        self.trace_logger.log_event(
            thread_id,
            user_id,
            "transaction_reconciled",
            output_data={"reconciliation": result.get("transaction_reconciliation"), "ledger_snapshot": result.get("ledger_snapshot")},
        )
        return result

    @staticmethod
    def _draft_state_for_validation(offer: dict[str, Any]) -> str:
        # Structured commands may advance only an explicit canonical Draft state.
        explicit = str(offer.get("draft_state") or "").strip().upper()
        return explicit if explicit else "UNKNOWN"

    def _validate_action_authority(self, graph: Any, request: ActionAuthorityRequest) -> str | None:
        """Compatibility delegate; structured submission owns validation sequencing."""
        return self._interaction_submit_use_case().validate_action_authority(graph, request)

    def _validate_action_input(self, graph: Any, request: ActionInputRequest) -> str | None:
        """Validate a structured transaction-input form while the turn lock is held."""
        config = self._config_for_request(request.thread_id, request.user_id, request.tenant_id)
        values = self._checkpoint_values(graph, thread_id=request.thread_id, user_id=request.user_id, tenant_id=request.tenant_id)
        expected_handle = str(get_active_draft_id(values) or "")
        if not expected_handle:
            return "no_pending_transaction_interaction"
        if request.offer_handle != expected_handle:
            return "offer_handle_mismatch"
        ledger = values.get("artifact_ledger") or []
        offer = next((item for item in ledger if isinstance(item, dict) and item.get("handle") == expected_handle), None)
        if not offer or self._draft_state_for_validation(offer) != "NEEDS_INPUT":
            return "offer_not_collecting_input"
        if str(offer.get("action_id") or "") != request.action_id:
            return "action_id_mismatch"
        if str(offer.get("target_handle") or "") != request.target_handle:
            return "target_handle_mismatch"
        if str(offer.get("input_form_id") or "") != request.form_id:
            return "form_id_mismatch"
        if int(offer.get("input_form_version") or 0) != int(request.form_version):
            return "form_version_mismatch"
        if int(offer.get("input_step") or 1) != int(getattr(request, "form_step", 1) or 1):
            return "form_step_mismatch"
        expected_revision = int(offer.get("interaction_revision") or values.get("turn_index") or 0)
        if int(request.conversation_revision) != expected_revision:
            return "conversation_revision_mismatch"
        if int(values.get("turn_index") or 0) != expected_revision:
            return "current_conversation_revision_mismatch"
        return None

    def _claim_or_validate_thread(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> None:
        try:
            self.thread_store.claim_or_validate_thread(thread_id, user_id, tenant_id)
        except ThreadOwnershipError as e:
            raise HTTPException(status_code=403, detail="thread does not belong to current authenticated user") from e
        except ThreadTenantMismatchError as e:
            raise HTTPException(status_code=403, detail="thread does not belong to current authenticated tenant") from e
        except UnboundThreadTenantError as e:
            raise HTTPException(status_code=409, detail="thread tenant binding is missing; an administrator must bind the thread before reuse") from e

    def _assert_thread_owner(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> None:
        try:
            self.thread_store.assert_thread_owner(thread_id, user_id, tenant_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="thread not found") from e
        except ThreadOwnershipError as e:
            raise HTTPException(status_code=403, detail="thread does not belong to current authenticated user") from e
        except ThreadTenantMismatchError as e:
            raise HTTPException(status_code=403, detail="thread does not belong to current authenticated tenant") from e
        except UnboundThreadTenantError as e:
            raise HTTPException(status_code=409, detail="thread tenant binding is missing; an administrator must bind the thread before reuse") from e

    def _extract_interrupt(self, result: dict[str, Any]) -> dict[str, Any] | None:
        return self._response_projector()._extract_interrupt(result)

    def _message_to_debug(self, message: Any) -> dict[str, Any]:
        return self._response_projector()._message_to_debug(message)

    def _debug_snapshot(self, result: dict[str, Any]) -> dict[str, Any]:
        return self._response_projector().debug_snapshot(result)

    def _safe_state(self, result: dict[str, Any]) -> dict[str, Any]:
        return self._response_projector().safe_state(result)

    def _public_state(self, result: dict[str, Any]) -> dict[str, Any] | None:
        return self._response_projector().public_state(result)

    def _response_state(self, result: dict[str, Any], *, include_debug: bool) -> dict[str, Any] | None:
        return self._response_projector().response_state(result, include_debug=include_debug)

    @staticmethod
    def _fallback_interaction_snapshot(response: ChatResponse) -> dict[str, Any] | None:
        return ResponseProjector.fallback_interaction_snapshot(response)

    def _persist_public_response(self, thread_id: str, response: ChatResponse, result: dict[str, Any] | None = None) -> None:
        self._response_projector().persist_public_response(thread_id, response, result)

    def _dedupe_debug_llm_calls(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._response_projector().dedupe_debug_llm_calls(calls)

    def _normalize(self, thread_id: str, result: dict[str, Any], *, include_debug: bool = False) -> ChatResponse:
        return self._response_projector().normalize(thread_id, result, include_debug=include_debug)

    def reconcile_transactions(
        self,
        thread_id: str,
        user_id: str,
        tenant_id: str | None = None,
        *,
        role: str = "customer",
        actor_permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Reconcile only pre-existing ambiguous transaction attempts.

        This endpoint deliberately does not create a Draft, Grant, or new
        Business command.  It can only replay the exact canonical payload held
        by a persisted ``STARTED``/``SUBMISSION_UNKNOWN`` attempt with its
        original idempotency key.  It gives the read-only UI state a safe
        recovery action after a timeout or Agent-side crash.
        """
        self._assert_thread_owner(thread_id, user_id, tenant_id)
        graph = self._require_graph()
        normalized_role = normalize_role(role)
        try:
            with self._serialized_turn(thread_id, user_id, tenant_id):
                with business_actor_context(
                    user_id=user_id,
                    role=normalized_role,
                    tenant_id=tenant_id,
                    permissions=actor_permissions,
                ):
                    update = self._reconcile_pending_transaction_attempts(
                        graph,
                        thread_id=thread_id,
                        user_id=user_id,
                        tenant_id=tenant_id,
                    )
                values = self._checkpoint_values(graph, thread_id=thread_id, user_id=user_id, tenant_id=tenant_id)
                attempts = self.store_provider.transactions.list_by_thread(
                    tenant_id=str(tenant_id or "default"),
                    user_id=str(user_id),
                    thread_id=str(thread_id),
                    limit=100,
                )
                return {
                    "thread_id": thread_id,
                    "reconciliation": (update or {}).get("transaction_reconciliation") or {"status": "nothing_to_reconcile"},
                    "items": pending_transaction_summaries_from_state(values),
                    "transaction_lifecycle": attempts,
                }
        except ConversationBusyError:
            return {
                "thread_id": thread_id,
                "reconciliation": {"status": "conversation_busy"},
                "items": [],
                "transaction_lifecycle": [],
            }

    def pending_transactions(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        """List active and queued transaction summaries for the product drawer.

        This is intentionally read-only and does not let a client activate an
        arbitrary queued draft.  The graph remains the sole state machine that
        promotes the next draft after the active interaction terminates.
        """
        self._assert_thread_owner(thread_id, user_id, tenant_id)
        graph = self._require_graph()
        values = self._checkpoint_values(graph, thread_id=thread_id, user_id=user_id, tenant_id=tenant_id)
        attempts = self.store_provider.transactions.list_by_thread(
            tenant_id=str(tenant_id or "default"), user_id=str(user_id), thread_id=str(thread_id), limit=100
        )
        return {
            "thread_id": thread_id,
            "items": pending_transaction_summaries_from_state(values),
            "transaction_lifecycle": attempts,
        }

    def pending_interaction(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        """Return the persisted current transaction interaction after reload/navigation.

        The payload is generic: it can represent either a structured input
        form or a final structured authority card.
        """
        self._assert_thread_owner(thread_id, user_id, tenant_id)
        graph = self._require_graph()
        values = self._checkpoint_values(graph, thread_id=thread_id, user_id=user_id, tenant_id=tenant_id)
        contract = interaction_response_contract(values)
        if contract is None:
            return {"thread_id": thread_id, "type": "none"}
        interaction = dict(contract.get("interaction") or {})
        payload = {
            "thread_id": thread_id,
            "type": "interaction_required",
            "message": str(contract.get("message") or "请继续处理该操作。"),
            "interaction": interaction,
        }
        return payload

    def chat(self, request: ChatRequest, *, include_debug: bool = False) -> ChatResponse:
        return self._conversation_turn_service().chat(request, include_debug=include_debug)

    def authorize_action(self, request: ActionAuthorityRequest, *, include_debug: bool = False) -> ChatResponse:
        return self._interaction_submit_use_case().authorize(request, include_debug=include_debug)

    def _apply_action_authority(self, request: ActionAuthorityRequest, *, include_debug: bool = False) -> ChatResponse:
        return self._interaction_submit_use_case()._apply_action_authority(request, include_debug=include_debug)

    def submit_action_input(self, request: ActionInputRequest, *, include_debug: bool = False) -> ChatResponse:
        return self._interaction_submit_use_case().submit_input(request, include_debug=include_debug)
    def close(self) -> None:
        """Release process-owned persistence and checkpoint resources."""
        try:
            reset_store_provider_cache()
        finally:
            clear_checkpointer_cache()

    def chat_stream(self, request: ChatRequest, *, include_debug: bool = False) -> Generator[str, None, None]:
        yield from self._conversation_turn_service().stream(request, include_debug=include_debug)
