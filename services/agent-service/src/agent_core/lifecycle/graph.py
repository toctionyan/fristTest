from __future__ import annotations

"""Lifecycle graph topology.

The graph owns routing only. Runtime dependencies are assembled by the
application boundary and supplied explicitly; node implementations never look
up StoreProvider while executing a model turn.
"""

from langgraph.graph import END, START, StateGraph

from agent_core.config import build_checkpointer
from agent_core.lifecycle.nodes import (
    action_confirmation_node,
    action_gateway_node,
    agent_loop_node,
    commit_action_node,
    execute_agent_loop_calls_node,
    classify_execution_disposition_node,
    route_after_execution_disposition,
    finalize_agent_loop_turn_node,
    build_context_bundle_node,
    prepare_agent_loop_turn_node,
    route_after_agent_loop,
    route_after_confirmation,
    trim_agent_loop_messages_node,
    reconcile_submission_node,
    persist_and_stream_node,
)
from agent_core.runtime.deps import LifecycleRuntimeDeps
from agent_core.lifecycle.state import State
from agent_core.lifecycle.state_contracts import validate_state_update
from agent_core.observability.flow_debug import debug_node


def build_lifecycle_graph(runtime_deps: LifecycleRuntimeDeps):
    """Build a graph with explicit transaction/context dependencies.

    There is intentionally no zero-argument fallback.  Application code and
    tests must compose dependencies before building the graph.
    """
    builder = StateGraph(State)
    nodes = {
        "prepare_turn": prepare_agent_loop_turn_node,
        "build_context_bundle": lambda state: build_context_bundle_node(
            state,
            context_bundle_builder=runtime_deps.context_bundle_builder,
        ),
        "agent_loop": lambda state: agent_loop_node(
            state,
            context_bundle_builder=runtime_deps.context_bundle_builder,
            transactions=runtime_deps.transactions,
            capability_registry=runtime_deps.capability_registry,
            model_resolver=runtime_deps.model_resolver,
            dependency_authority_control_resolver=runtime_deps.dependency_authority_control_resolver,
        ),
        "validate_and_execute": lambda state: execute_agent_loop_calls_node(
            state,
            context_bundle_builder=runtime_deps.context_bundle_builder,
            transactions=runtime_deps.transactions,
            capability_registry=runtime_deps.capability_registry,
        ),
        "classify_execution_disposition": classify_execution_disposition_node,
        "reconcile_submission": lambda state: reconcile_submission_node(
            {**state, "_transaction_repository": runtime_deps.transactions},
            transaction_execution=runtime_deps.transaction_execution,
        ),
        "action_gateway": lambda state: action_gateway_node(
            {**state, "_transaction_repository": runtime_deps.transactions},
            transaction_execution=runtime_deps.transaction_execution,
        ),
        "action_confirmation": lambda state: action_confirmation_node(
            {**state, "_transaction_repository": runtime_deps.transactions},
            transaction_execution=runtime_deps.transaction_execution,
        ),
        "commit_action": lambda state: commit_action_node(
            {**state, "_transaction_repository": runtime_deps.transactions},
            transaction_execution=runtime_deps.transaction_execution,
        ),
        "finalize_turn": finalize_agent_loop_turn_node,
        "trim_raw_messages": trim_agent_loop_messages_node,
        "persist_and_stream": persist_and_stream_node,
    }
    for name, node in nodes.items():
        builder.add_node(
            name,
            debug_node(
                name,
                node,
                state_validator=validate_state_update,
                trace_repository=runtime_deps.trace_logger,
            ),
        )

    builder.add_edge(START, "prepare_turn")
    builder.add_edge("prepare_turn", "build_context_bundle")
    builder.add_edge("build_context_bundle", "agent_loop")
    builder.add_conditional_edges(
        "agent_loop",
        route_after_agent_loop,
        {
            "execute": "validate_and_execute",
            "confirm": "action_confirmation",
            "loop": "agent_loop",
            "final": "finalize_turn",
        },
    )
    builder.add_edge("validate_and_execute", "classify_execution_disposition")
    builder.add_conditional_edges(
        "classify_execution_disposition",
        route_after_execution_disposition,
        {"gateway": "action_gateway", "reconcile": "reconcile_submission", "confirm": "action_confirmation", "loop": "agent_loop", "final": "finalize_turn"},
    )
    builder.add_edge("reconcile_submission", "finalize_turn")
    # Gateway preflight is a formal business observation. It also crosses the
    # disposition boundary; a structured interaction contract then preempts
    # model execution deterministically.
    builder.add_edge("action_gateway", "classify_execution_disposition")
    builder.add_conditional_edges(
        "action_confirmation",
        route_after_confirmation,
        {"confirm": "action_confirmation", "commit": "commit_action", "gateway": "action_gateway", "loop": "agent_loop", "final": "finalize_turn"},
    )
    # Commit is a formal effect-bearing execution. It must cross the same
    # closed disposition boundary as model-selected business tools.
    builder.add_edge("commit_action", "classify_execution_disposition")
    builder.add_edge("finalize_turn", "trim_raw_messages")
    builder.add_edge("trim_raw_messages", "persist_and_stream")
    builder.add_edge("persist_and_stream", END)
    return builder.compile(checkpointer=build_checkpointer())
