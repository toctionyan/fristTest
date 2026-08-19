"""Execute schema-v2 conversation contracts through the real lifecycle graph.

The catalog supplies deterministic model candidates, but its assertions are
made against runtime output: Trace, MatchProof, WorkflowPlan, Ledger, public
response contract and the recordable BusinessPort.  In particular, a model
candidate being present in JSON is never treated as proof that it executed.
"""
from __future__ import annotations

from agent_core.lifecycle.semantic_contract import goal_declaration_projection_from_contract
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage

from agent_core.business import configure_business_port
from agent_core.composition import get_runtime_registry
from agent_core.business import get_business_port
from agent_core.lifecycle.graph import build_lifecycle_graph
from agent_core.model_calls import model_call_scope
from agent_core.runtime.deps import lifecycle_runtime_deps
from agent_core.persistence.store_provider import get_store_provider
from tests.support.conversation_case_fixtures import (
    FIXTURE_EVIDENCE_HANDLE,
    FIXTURE_ID,
    FixtureBusinessPort,
    fixture_ledger,
)
from tests.support.scripted_chat_model import ScriptedChatModel


@dataclass(frozen=True)
class ExecutedConversationTurn:
    """One actual graph invocation retained for contract-level assertions."""

    user_text: str
    model: ScriptedChatModel
    result: dict[str, Any]
    thread_alias: str = "default"
    thread_id: str = ""


@dataclass(frozen=True)
class ExecutedConversationCase:
    """Runtime evidence produced by a complete catalog case."""

    case_id: str
    port: FixtureBusinessPort
    turns: tuple[ExecutedConversationTurn, ...]
    thread_ids: dict[str, str] | None = None

    @property
    def final(self) -> ExecutedConversationTurn:
        return self.turns[-1]

    @property
    def trace(self) -> list[dict[str, Any]]:
        return [
            row
            for turn in self.turns
            for row in list(turn.result.get("tool_trace") or [])
            if isinstance(row, dict)
        ]


class _CurrentModelResolver:
    """Keep production composition unchanged while swapping only test model turns."""

    def __init__(self) -> None:
        self.current: ScriptedChatModel | None = None

    def __call__(self) -> ScriptedChatModel:
        if self.current is None:  # pragma: no cover - runner contract guard
            raise AssertionError("conversation runner requested a model before a turn script was installed")
        return self.current


def _user_turns(case: dict[str, Any]) -> list[str]:
    return [
        str(turn["text"])
        for turn in list(case.get("turns") or [])
        if isinstance(turn, dict) and str(turn.get("role") or "") == "user" and str(turn.get("text") or "")
    ]


def _successful_tool_data(result: dict[str, Any]) -> dict[str, Any]:
    """Retain opaque outputs for the next scripted turn in the same thread."""
    collected: dict[str, Any] = {}
    for row in list(result.get("tool_trace") or []):
        if not isinstance(row, dict):
            continue
        payload = row.get("result") if isinstance(row.get("result"), dict) else {}
        if not bool(payload.get("ok")):
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        collected.update(deepcopy(data))
    blockers = [
        row for row in list(result.get("goal_blockers") or [])
        if isinstance(row, dict)
        and str(row.get("status") or "OPEN").upper() == "OPEN"
        and str(row.get("blocker_id") or "")
    ]
    if blockers:
        collected["goal_blocker_id"] = str(blockers[0]["blocker_id"])
    active_goals = [
        row for row in list(result.get("goal_records") or [])
        if isinstance(row, dict)
        and str(row.get("lifecycle") or "OPEN").upper() in {"OPEN", "ACTIVE", "BLOCKED", "PAUSED"}
        and str(row.get("goal_id") or "")
    ]
    if active_goals:
        collected["active_goal_id"] = str(active_goals[0]["goal_id"])
        collected["active_goal_revision"] = int(active_goals[0].get("revision") or 1)
    return collected


def _scenario_thread_aliases(contract: dict[str, Any]) -> tuple[str, ...]:
    topology = contract.get("topology") if isinstance(contract.get("topology"), dict) else {}
    raw_threads = topology.get("threads") if isinstance(topology.get("threads"), list) else []
    aliases: list[str] = []
    for item in raw_threads:
        alias = str(item.get("id") or item.get("alias") or "") if isinstance(item, dict) else str(item or "")
        alias = alias.strip()
        if alias:
            aliases.append(alias)
    if not aliases:
        aliases = ["default"]
    assert len(aliases) == len(set(aliases)), "conversation topology thread aliases must be unique"
    return tuple(aliases)


def _path(value: Any, dotted_path: str) -> Any:
    current = value
    for segment in str(dotted_path or "").split("."):
        if not segment:
            continue
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _offers(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in list(result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]


def _trace_rows(executed: ExecutedConversationCase, tool_name: str | None = None) -> list[dict[str, Any]]:
    rows = executed.trace
    return rows if tool_name is None else [row for row in rows if str(row.get("name") or "") == tool_name]




def _assert_goal_oracle(*, case_id: str, contract: dict[str, Any], goal_plan: dict[str, Any], workflow: dict[str, Any]) -> None:
    """Compare the runtime declaration with an independent semantic oracle.

    ``model_steps`` are candidate inputs.  They cannot be the expected meaning
    of the user text.  A release semantic case therefore declares a separate
    ``goal_oracle`` that is not generated from the candidate tool script.
    """
    oracle = contract.get("goal_oracle")
    if oracle is None:
        return
    assert isinstance(oracle, list) and oracle, f"{case_id}: goal_oracle must be a non-empty list"
    actual = [row for row in list(goal_plan.get("goals") or []) if isinstance(row, dict)]
    assert len(actual) == len(oracle), (case_id, "goal_count_mismatch", actual, oracle)
    actual_by_id = {str(row.get("goal_id") or ""): row for row in actual}
    workflow_by_id = {
        str(row.get("goal_id") or ""): row
        for row in list(workflow.get("goals") or [])
        if isinstance(row, dict)
    }
    for expected in oracle:
        assert isinstance(expected, dict), f"{case_id}: invalid goal oracle row"
        oracle_id = str(expected.get("oracle_id") or "")
        assert oracle_id and oracle_id in actual_by_id, (case_id, "missing_oracle_goal", oracle_id, actual)
        row = actual_by_id[oracle_id]
        assert str(row.get("goal_type") or "") == str(expected.get("goal_type") or ""), (case_id, oracle_id, row, expected)
        assert str(row.get("evidence_span") or "") == str(expected.get("evidence_span") or ""), (case_id, oracle_id, row, expected)
        assert bool(row.get("required", True)) is bool(expected.get("required", True)), (case_id, oracle_id, row, expected)
        required_tools = {str(value) for value in expected.get("required_tools") or [] if str(value)}
        assert required_tools, (case_id, oracle_id, "oracle_required_tools_missing")
        dependencies = {str(value) for value in expected.get("depends_on") or [] if str(value)}
        assert dependencies == {str(value) for value in row.get("depends_on") or [] if str(value)}, (case_id, oracle_id, row, expected)
        covered = workflow_by_id.get(oracle_id)
        assert covered is not None, (case_id, "workflow_missing_goal", oracle_id, workflow)
        covered_step_ids = {str(value) for value in covered.get("covered_by_step_ids") or [] if str(value)}
        covered_tools = {
            str(step.get("tool_name") or "")
            for step in list(workflow.get("steps") or [])
            if isinstance(step, dict) and str(step.get("step_id") or "") in covered_step_ids
        }
        covered_tools.update(str(value) for value in covered.get("covered_by_terminal_tools") or [] if str(value))
        assert required_tools.issubset(covered_tools), (case_id, oracle_id, required_tools, covered_tools)
        assert str(covered.get("coverage_status") or "") != "PENDING", (case_id, "goal_not_covered", oracle_id, covered)


def _assert_turn_contract(
    *,
    case_id: str,
    contract: dict[str, Any],
    user_text: str,
    model: ScriptedChatModel,
    result: dict[str, Any],
    port: FixtureBusinessPort,
    port_call_offset: int,
) -> None:
    expected = dict(contract.get("expected") or {})
    emitted_all = [str(call.get("name") or "") for call in model.emitted_tool_calls]
    planner_tools = [name for name in emitted_all if name == "declare_turn_goals"]
    emitted = [name for name in emitted_all if name != "declare_turn_goals"]
    allowed = {str(name) for name in contract.get("allowed_tools") or [] if str(name)}
    required = {str(name) for name in contract.get("required_tools") or [] if str(name)}
    message_tail = [
        {
            "type": message.__class__.__name__,
            "content": str(getattr(message, "content", ""))[:3000],
            "tool_calls": getattr(message, "tool_calls", None),
        }
        for message in list(result.get("messages") or [])[-6:]
    ]
    assert model.remaining_steps == 0, (
        case_id,
        "model script did not reach its declared terminal step",
        {"remaining_steps": model.remaining_steps, "status": result.get("status"), "messages_tail": message_tail},
    )
    assert planner_tools == ["declare_turn_goals"], f"{case_id}: every user turn must declare goals exactly once"
    assert set(emitted).issubset(allowed), f"{case_id}: model emitted a tool outside the declared boundary: {emitted!r}"
    assert required.issubset(set(emitted)), f"{case_id}: declared required candidate was never emitted: {required!r}"
    assert len(model.emitted_tool_batches) == len(model.invoked_bound_tool_history), (
        case_id,
        "every invocation must retain its exact bound protocol surface",
        {
            "emitted_batches": len(model.emitted_tool_batches),
            "invoked_surfaces": len(model.invoked_bound_tool_history),
            "remaining_steps": model.remaining_steps,
            "invoked_bound_tools": [sorted(names) for names in model.invoked_bound_tool_history],
            "status": result.get("status"),
            "last_error": result.get("last_error"),
            "resolution": result.get("resolution"),
            "pending_reason": result.get("pending_reason"),
            "messages_tail": message_tail,
        },
    )
    for invocation_index, (batch, bound_names) in enumerate(
        zip(model.emitted_tool_batches, model.invoked_bound_tool_history), start=1
    ):
        emitted_names = {str(call.get("name") or "") for call in batch if isinstance(call, dict)}
        assert emitted_names.issubset(bound_names), (
            f"{case_id}: invocation {invocation_index} emitted a candidate outside its bound schema: "
            f"{sorted(emitted_names - bound_names)!r}"
        )

    statuses = {str(value) for value in expected.get("terminal_statuses") or [] if str(value)}
    assert statuses and str(result.get("status") or "") in statuses, (
        case_id,
        user_text,
        result.get("status"),
        sorted(statuses),
    )
    workflow = result.get("grounded_execution_plan") if isinstance(result.get("grounded_execution_plan"), dict) else {}
    levels = {str(value) for value in expected.get("workflow_levels") or [] if str(value)}
    workflow_statuses = {str(value) for value in expected.get("workflow_statuses") or [] if str(value)}
    assert levels and str(workflow.get("level") or "") in levels, (case_id, workflow, expected)
    assert workflow_statuses and str(workflow.get("status") or "") in workflow_statuses, (case_id, workflow, expected)
    formal_contract = (
        result.get("frozen_semantic_contract")
        if isinstance(result.get("frozen_semantic_contract"), dict)
        else {}
    )
    goal_plan = goal_declaration_projection_from_contract(formal_contract) if formal_contract else {}
    assert goal_plan.get("goals"), f"{case_id}: frozen goal declaration was not retained"
    assert bool(workflow.get("goal_coverage_complete")) is True, (case_id, workflow.get("goals"))
    expected_goal_count = expected.get("goal_count")
    if expected_goal_count is not None:
        assert len(goal_plan.get("goals") or []) == int(expected_goal_count), (case_id, goal_plan)
    _assert_goal_oracle(case_id=case_id, contract=contract, goal_plan=goal_plan, workflow=workflow)

    public_kind = str(expected.get("public_interaction") or "")
    response_contract = result.get("response_contract") if isinstance(result.get("response_contract"), dict) else None
    if public_kind == "answer":
        assert str(result.get("current_final_answer") or ""), f"{case_id}: answer contract did not produce customer text"
        assert response_contract is None, f"{case_id}: answer contract leaked a transaction interaction"
    elif public_kind == "clarification":
        answer = str(result.get("current_final_answer") or "")
        assert answer and "请" in answer, f"{case_id}: clarification was not released as a customer question"
        assert response_contract is None, f"{case_id}: clarification unexpectedly created an interaction"
    elif public_kind == "transaction_interaction":
        assert response_contract is not None and str(response_contract.get("kind") or "") == "interaction_required", (
            case_id,
            response_contract,
        )
        assert result.get("current_final_answer") in {None, ""}, f"{case_id}: transaction interaction was downgraded into prose"
    else:
        raise AssertionError(f"{case_id}: unknown public interaction {public_kind!r}")

    trace = [row for row in list(result.get("tool_trace") or []) if isinstance(row, dict)]
    trace_names = {str(row.get("name") or "") for row in trace}
    trace_expectation = expected.get("trace") if isinstance(expected.get("trace"), dict) else {}
    required_trace = {str(name) for name in trace_expectation.get("must_include") or [] if str(name)}
    forbidden_trace = {str(name) for name in trace_expectation.get("must_not_include") or [] if str(name)}
    assert required_trace.issubset(trace_names), f"{case_id}: required runtime Trace missing {required_trace - trace_names!r}"
    assert trace_names.isdisjoint(forbidden_trace), f"{case_id}: forbidden runtime Trace present {trace_names & forbidden_trace!r}"

    draft = expected.get("draft") if isinstance(expected.get("draft"), dict) else {}
    offers = _offers(result)
    if "count" in draft:
        assert len(offers) == int(draft["count"]), (case_id, len(offers), draft)
    if "states" in draft:
        assert {str(item.get("draft_state") or "") for item in offers} == {str(value) for value in draft["states"]}, (case_id, offers, draft)
    if "target_order_id" in draft:
        expected_order = str(draft["target_order_id"])
        assert any(expected_order in str(item.get("target_handle") or "") for item in offers), (case_id, offers, expected_order)

    turn_port_calls = list(port.calls[port_call_offset:])
    for kind, expected_count in dict(expected.get("port_calls") or {}).items():
        actual_count = sum(1 for call in turn_port_calls if call.get("kind") == str(kind))
        if isinstance(expected_count, dict):
            minimum = expected_count.get("min")
            maximum = expected_count.get("max")
            if minimum is not None:
                assert actual_count >= int(minimum), (case_id, kind, actual_count, expected_count, turn_port_calls)
            if maximum is not None:
                assert actual_count <= int(maximum), (case_id, kind, actual_count, expected_count, turn_port_calls)
        else:
            assert actual_count == int(expected_count), (case_id, kind, actual_count, turn_port_calls)

    for assertion in list(expected.get("result_assertions") or []):
        if not isinstance(assertion, dict):
            raise AssertionError(f"{case_id}: invalid result assertion")
        rows = [row for row in trace if str(row.get("name") or "") == str(assertion.get("tool") or "")]
        assert rows, f"{case_id}: no actual Trace row for result assertion {assertion!r}"
        actual = _path(rows[-1], str(assertion.get("path") or ""))
        assert actual == assertion.get("equals"), (case_id, assertion, actual)


def _assert_forbidden_behavior(case: dict[str, Any], executed: ExecutedConversationCase) -> None:
    contract = case["execution_contract"]
    declared = {str(value) for value in case.get("forbidden_behavior") or [] if str(value)}
    rows = list(contract.get("forbidden_assertions") or [])
    mapped = {str(row.get("behavior") or "") for row in rows if isinstance(row, dict)}
    assert mapped == declared, f"{case['id']}: every forbidden behavior must have one runtime assertion"
    final = executed.final.result
    trace = executed.trace
    trace_names = {str(row.get("name") or "") for row in trace}
    for assertion in rows:
        assert isinstance(assertion, dict)
        behavior = str(assertion.get("behavior") or "")
        kind = str(assertion.get("kind") or "")
        tools = {str(value) for value in assertion.get("tool_names") or [] if str(value)}
        if kind == "trace_absent":
            assert trace_names.isdisjoint(tools), (case["id"], behavior, trace_names & tools)
        elif kind == "turn_trace_absent":
            turn_index = assertion.get("turn_index")
            assert isinstance(turn_index, int) and not isinstance(turn_index, bool) and 1 <= turn_index <= len(executed.turns), (
                case["id"], behavior, "invalid_turn_index", turn_index, len(executed.turns)
            )
            turn_trace = [
                row
                for row in list(executed.turns[turn_index - 1].result.get("tool_trace") or [])
                if isinstance(row, dict)
            ]
            turn_trace_names = {str(row.get("name") or "") for row in turn_trace}
            assert turn_trace_names.isdisjoint(tools), (
                case["id"], behavior, turn_index, turn_trace_names & tools
            )
        elif kind == "trace_contains_all":
            assert tools.issubset(trace_names), (case["id"], behavior, tools - trace_names)
        elif kind == "no_business_write":
            assert executed.port.count("execute_command") == 0, (case["id"], behavior, executed.port.calls)
        elif kind == "draft_count_at_most":
            assert len(_offers(final)) <= int(assertion.get("value") or 0), (case["id"], behavior, _offers(final))
        elif kind == "workflow_level":
            workflow = final.get("grounded_execution_plan") if isinstance(final.get("grounded_execution_plan"), dict) else {}
            assert workflow.get("level") == assertion.get("value"), (case["id"], behavior, workflow)
        elif kind == "trace_result_path_equals":
            rows_for_tool = _trace_rows(executed, str(assertion.get("tool") or ""))
            assert rows_for_tool, (case["id"], behavior, "missing trace")
            assert _path(rows_for_tool[-1], str(assertion.get("path") or "")) == assertion.get("value"), (case["id"], behavior, rows_for_tool[-1])
        elif kind == "visible_reference_permitted":
            rows_for_tool = _trace_rows(executed, str(assertion.get("tool") or ""))
            assert rows_for_tool, (case["id"], behavior, "missing trace")
            proof = rows_for_tool[-1].get("match_proof") if isinstance(rows_for_tool[-1].get("match_proof"), dict) else {}
            assert bool((proof.get("visible_result_reference") or {}).get("complete")), (case["id"], behavior, proof)
        elif kind == "unsupported_result":
            rows_for_tool = _trace_rows(executed, "report_unsupported_request")
            assert rows_for_tool and (rows_for_tool[-1].get("result") or {}).get("data", {}).get("supported") is False, (case["id"], behavior, rows_for_tool)
        else:
            raise AssertionError(f"{case['id']}: unknown forbidden assertion kind {kind!r}")


def run_conversation_case(case: dict[str, Any]) -> ExecutedConversationCase:
    """Run every turn through its declared real checkpoint-thread topology."""
    contract = case.get("execution_contract") if isinstance(case.get("execution_contract"), dict) else {}
    fixture = contract.get("fixture") if isinstance(contract.get("fixture"), dict) else {}
    fixture_state = fixture.get("state") if isinstance(fixture.get("state"), dict) else {}
    assert fixture.get("id") == FIXTURE_ID, f"{case.get('id')}: unknown deterministic fixture"
    tenant_id = str(fixture_state.get("tenant_id") or "")
    user_id = str(fixture_state.get("user_id") or "")
    role = str(fixture_state.get("role") or "")
    assert tenant_id and user_id and role, f"{case.get('id')}: incomplete fixture scope"

    user_turns = _user_turns(case)
    turn_contracts = [row for row in list(contract.get("turn_contracts") or []) if isinstance(row, dict)]
    assert len(turn_contracts) == len(user_turns), f"{case.get('id')}: every user turn needs an executable contract"
    assert [str(row.get("user_text") or "") for row in turn_contracts] == user_turns, (
        case.get("id"),
        "catalog model script is not bound to the declared user text",
    )

    # Construct the module registry first: it sets up all capability schemas.
    # Only then swap the port factory, preserving the normal Composition Root.
    registry = get_runtime_registry()
    port = FixtureBusinessPort()
    configure_business_port(lambda: port)
    provider = get_store_provider()
    resolver = _CurrentModelResolver()
    runtime_deps = lifecycle_runtime_deps(
        transactions=provider.transactions,
        capability_registry=registry.capabilities,
        business_port=port,
        trace_logger=provider.traces,
        model_resolver=resolver,
    )
    graph = build_lifecycle_graph(runtime_deps)
    thread_aliases = _scenario_thread_aliases(contract)
    run_id = uuid4().hex
    thread_ids = {
        alias: f"conversation-regression:{case['id']}:{alias}:{run_id}"
        for alias in thread_aliases
    }
    thread_turns = {alias: 0 for alias in thread_aliases}
    previous_turn_data = {alias: {} for alias in thread_aliases}
    initialized_threads: set[str] = set()
    executed_turns: list[ExecutedConversationTurn] = []

    for case_turn_index, (user_text, turn_contract) in enumerate(zip(user_turns, turn_contracts), start=1):
        thread_alias = str(turn_contract.get("thread") or thread_aliases[0])
        assert thread_alias in thread_ids, (
            case.get("id"), "turn references undeclared topology thread", thread_alias, thread_aliases
        )
        thread_id = thread_ids[thread_alias]
        thread_turns[thread_alias] += 1
        thread_turn_index = thread_turns[thread_alias]
        model_steps = list(turn_contract.get("model_steps") or [])
        resolver.current = ScriptedChatModel(
            model_steps,
            previous_turn_data=previous_turn_data[thread_alias],
        )
        input_state: dict[str, Any] = {
            "messages": [HumanMessage(content=user_text)],
            "current_thread_id": thread_id,
            "current_user_id": user_id,
            "current_tenant_id": tenant_id,
            "current_role": role,
            "model_call_budget": {"max_calls": 18},
        }
        if thread_alias not in initialized_threads:
            input_state["artifact_ledger"] = fixture_ledger(tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
            input_state["fixture_evidence_handle"] = FIXTURE_EVIDENCE_HANDLE
            initialized_threads.add(thread_alias)
        port_call_offset = len(port.calls)
        # Match the production gateway: verifier and support lanes may consume
        # calls without consuming scripted planner candidates. The scripted
        # model still fails immediately on an undeclared planner invocation.
        with model_call_scope(scope=f"conversation-regression:{case['id']}:{thread_alias}:{thread_turn_index}"):
            result = graph.invoke(input_state, {"configurable": {"thread_id": thread_id}})
        assert int(result.get("turn_index") or 0) == thread_turn_index, (
            case["id"], thread_alias, case_turn_index, thread_turn_index, result.get("turn_index")
        )
        _assert_turn_contract(
            case_id=str(case["id"]),
            contract=turn_contract,
            user_text=user_text,
            model=resolver.current,
            result=result,
            port=port,
            port_call_offset=port_call_offset,
        )
        previous_turn_data[thread_alias] = _successful_tool_data(result)
        executed_turns.append(ExecutedConversationTurn(
            user_text=user_text,
            model=resolver.current,
            result=result,
            thread_alias=thread_alias,
            thread_id=thread_id,
        ))

    executed = ExecutedConversationCase(
        case_id=str(case["id"]), port=port, turns=tuple(executed_turns), thread_ids=dict(thread_ids)
    )
    _assert_forbidden_behavior(case, executed)
    return executed


__all__ = ["ExecutedConversationCase", "ExecutedConversationTurn", "run_conversation_case"]
