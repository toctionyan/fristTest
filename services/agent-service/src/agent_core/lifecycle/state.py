from typing import Annotated, Any, TypedDict

try:
    from langchain_core.messages import AnyMessage
except Exception:  # pragma: no cover
    AnyMessage = Any  # type: ignore

try:
    from langgraph.graph.message import add_messages
except Exception:  # pragma: no cover
    def add_messages(left, right):  # type: ignore
        return (left or []) + (right or [])


class State(TypedDict, total=False):
    """Lifecycle state contract.

    Authority is deliberately split:
    - ``artifact_ledger`` holds verified business facts and transaction drafts;
    - ``task_board`` holds soft, model-maintained work organization only;
    - ``conversation_event_log`` holds immutable audit evidence only.

    Neither prior plans nor task-board text may become business truth or silently
    trigger an action in a later turn.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    current_thread_id: str
    current_user_id: str
    current_role: str
    current_tenant_id: str | None
    # Authenticated/delegated business subject. It is independent from Actor.
    current_subject: str | None

    state_schema_version: int
    state_migration: dict[str, Any] | None
    legacy_compatibility_metrics: dict[str, Any]
    transaction_contract_version: int
    turn_index: int
    current_user_input: str | None
    ledger_schema_version: int
    artifact_ledger: list[dict[str, Any]]
    ledger_snapshot: dict[str, Any] | None

    # Continuous-loop runtime. Turn plans are current-turn audit evidence,
    # while the frozen plan definition and PlanRun own orchestration. It may not act as
    # business fact, target authority, form input or authorization.
    current_turn_plan: dict[str, Any] | None
    loop_plans: list[dict[str, Any]]
    # Candidate and authority are explicitly separated; production semantics
    # are owned only by ``frozen_semantic_contract``.
    semantic_proposal: dict[str, Any] | None
    frozen_semantic_contract: dict[str, Any] | None
    # Immutable planning structure and runtime progress are separate owners.
    # grounded_execution_plan is a same-turn derived compatibility view only.
    frozen_plan_definition: dict[str, Any] | None
    plan_run: dict[str, Any] | None
    grounded_execution_plan: dict[str, Any] | None
    # Pre-tool plan is diagnostic shadow evidence only. It cannot dispatch,
    # create permits, mutate semantics or replace grounded_execution_plan.
    pretool_shadow_plan: dict[str, Any] | None
    pretool_shadow_comparisons: list[dict[str, Any]]
    # Multiple goal-scoped blockers may coexist and are the only durable
    # clarification authority in State Schema v2.
    goal_blockers: list[dict[str, Any]]
    # Durable semantic goal lifecycle. It is not a business/transaction state.
    goal_records: list[dict[str, Any]]
    focus_state: dict[str, Any] | None
    # Exact per-turn capability discovery evidence. This is not semantic or
    # business authority; it must nevertheless be a declared graph channel so
    # the execution Gate consumes the same snapshot that bounded planning.
    capability_surface: dict[str, Any] | None
    # Model proposals become executable only after a runtime MatchProof
    # and short-lived ExecutionPermit for a specific effect.
    execution_permits: list[dict[str, Any]]
    turn_match_proofs: list[dict[str, Any]]
    agent_loop_step: int
    agent_loop_max_steps: int
    agent_loop_seen_calls: list[str]
    answer_protocol_retry: int
    goal_declaration_retry: int
    clarification_scope_retry: int
    deferred_terminal_calls: list[dict[str, Any]]
    # Every formal execution receives one finite disposition. Restrictions
    # control the next loop path; they never select a new user target.
    execution_dispositions: list[dict[str, Any]]
    latest_execution_disposition: dict[str, Any] | None
    model_mode_restriction: list[str] | None
    model_call_budget: dict[str, Any]
    model_call_trace: list[dict[str, Any]]

    # Soft task state for multi-task organization. It cannot dispatch tools or
    # override the newest user utterance.
    task_board: list[dict[str, Any]]
    current_turn_task_ids: list[str]

    # Transaction boundary runtime.
    action_queue: list[dict[str, Any]]
    action_gateway_result: dict[str, Any] | None
    pending_confirmation_id: str | None
    pending_confirmation_version: int | None
    # Canonical public delivery state.  It is never inferred from model prose;
    # ``authority_required`` is re-derived from the persisted Offer when needed.
    response_contract: dict[str, Any] | None
    # Separate structured UI authority consumed by commit_action.  It is never
    # derived from a model plan or free-form chat text.
    commit_authority: dict[str, Any] | None
    approval_result: dict[str, Any] | None
    offer_execution_result: dict[str, Any] | None

    conversation_event_log: list[dict[str, Any]]
    audit_snapshot: list[dict[str, Any]] | None
    history_recall_evidence_binding: dict[str, Any] | None
    # Ephemeral per-model-call projection. Recent raw conversation remains the
    # semantic source; this bundle adds verified observations/references only.
    context_bundle: dict[str, Any] | None
    context_health: dict[str, Any] | None
    runtime_outcome: dict[str, Any] | None
    presentation: dict[str, Any] | None
    transaction_context_hint: bool
    transaction_context_blocked: bool
    # Canonical UI interaction focus. Multiple durable Drafts may remain open;
    # this pointer selects only the one currently rendered/accepted by UI.
    focused_draft_id: str | None
    # Compatibility projection for older clients/checkpoints. Runtime readers
    # resolve through transaction.focus and never treat this as authority when
    # focused_draft_id is present (including explicit null).
    active_draft_id: str | None
    # Read-only reconciliation facts from the durable attempt store. They are
    # not semantic context and cannot authorize a new operation.
    transaction_reconciliation: list[dict[str, Any]]

    tool_trace: list[dict[str, Any]]
    tool_error: dict[str, Any] | None
    sources: list[dict[str, Any]]
    answer_evidence_handles: list[str]

    phase: str
    status: str
    current_final_answer: str | None
    current_ask_message: str | None
    summary: str | None

    debug_current_run_id: str | None
    debug_llm_calls: list[dict[str, Any]]
    decision_chain: list[dict[str, Any]]
    state_contract_violations: list[dict[str, Any]]
