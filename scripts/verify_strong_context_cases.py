#!/usr/bin/env python3
"""Validate runtime-contract and semantic-goal conversation assets.

The evidence layers are intentionally separated:

* the runtime catalog keeps broad scenario coverage, but only its explicit
  high-signal executable matrix may count as graph evidence;
* the semantic suite adds an independent ``goal_oracle``.  Its oracle is not
  derived from ``model_steps`` and therefore catches missing or substituted
  user goals before the runtime assertions are evaluated.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

RUNTIME_SUITE_NAME = "conversation_runtime_contract_suite_v20_4"
SEMANTIC_SUITE_NAME = "semantic_goal_coverage_suite_v20_4"
RUNTIME_CATALOG_COUNT = 84
RUNTIME_EXECUTABLE_MIN_COUNT = 24
SEMANTIC_SUITE_MIN_COUNT = 12
SEMANTIC_PREPROD_COUNT = 12

REQUIRED_CATEGORIES = {
    "visible_result_filter", "pronoun_reference", "correction_override",
    "unsupported_capability", "multi_target_write_clarify", "consult_vs_commit",
    "grant_digest_guard", "submission_unknown_reconcile",
}
CONVERSATION_REQUIRED_CATEGORIES = {
    "multi_intent", "unsupported_capability", "similar_capability_rejection",
    "correction_override", "pronoun_reference", "visible_result_filter",
    "multi_task_interleaving", "conflict_intent", "vague_target",
    "consult_vs_commit", "multi_target_write_clarify", "authority_guard",
}
RUNTIME_EXECUTABLE_REQUIRED_CATEGORIES = CONVERSATION_REQUIRED_CATEGORIES - {
    # Durable pause/resume has its own generated sequence/topology contracts;
    # the current legacy catalog entries in this category are scenario ideas,
    # not semantically valid candidate scripts.
    "multi_task_interleaving",
}
REQUIRED_CASE_FIELDS = {
    "id", "category", "turns", "expected_runtime_behavior",
    "forbidden_behavior", "execution_contract",
}
CONTRACT_FIELDS = {
    "schema_version", "fixture", "turn_contracts", "forbidden_assertions",
    "preproduction_risk_prototype", "preproduction_allowed_tools",
    "preproduction_forbidden_tools",
}
TURN_FIELDS = {"user_text", "model_steps", "allowed_tools", "required_tools", "expected"}
EXPECTED_FIELDS = {
    "terminal_statuses", "workflow_levels", "workflow_statuses",
    "public_interaction", "trace", "draft", "port_calls", "result_assertions",
}
GOAL_TYPES = {"query", "consult", "action", "clarification", "unsupported", "narrative"}
FORBIDDEN_ASSERTION_KINDS = {
    "trace_absent", "trace_contains_all", "no_business_write",
    "draft_count_at_most", "workflow_level", "trace_result_path_equals",
    "visible_reference_permitted", "unsupported_result",
}
RUNTIME_MODULES = {
    "services/agent-service/tests/context/test_conversation_regression_suite_execution.py",
    "services/agent-service/tests/support/conversation_case_runner.py",
    "services/agent-service/tests/support/conversation_case_fixtures.py",
}
SEMANTIC_MODULES = {
    "services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py",
    "services/agent-service/tests/support/conversation_case_runner.py",
    "services/agent-service/tests/support/conversation_case_fixtures.py",
}
SYSTEMIC_SEQUENCE_MODULES = {
    "services/agent-service/tests/context/test_conversation_protocol.py": ("100", "test_naive_tail_mutation_is_rejected_as_orphan_tool_result"),
    "services/agent-service/tests/context/test_scenario_topology.py": ("thread-a", "thread-b", "[1, 1, 2, 2]"),
    "services/agent-service/tests/support/conversation_case_runner.py": ("_scenario_thread_aliases", "thread_ids[thread_alias]"),
}
SEMANTIC_TOOL_DIVERSITY = {
    "list_orders", "get_order_logistics", "list_refunds", "list_invoices",
    "evaluate_refund_eligibility", "prepare_refund", "prepare_cancel_order",
    "report_unsupported_request", "ask_user_clarification",
}
PROTOCOL_GOAL_COMPLETION_TYPES = {
    "ask_user_clarification": {"clarification"},
    "respond_to_user": {"narrative"},
}


def _goal_type_patterns(workspace: Path) -> dict[str, re.Pattern[str]]:
    """Read the production taxonomy regexes without importing the application."""
    path = workspace / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return {}
    expected = {"_CONSULTATIVE_MODAL", "_FACTUAL_LOOKUP", "_EXPLICIT_ACTION_REQUEST"}
    patterns: dict[str, re.Pattern[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        names = {
            target.id for target in node.targets
            if isinstance(target, ast.Name) and target.id in expected
        }
        if not names or not node.value.args:
            continue
        try:
            source = ast.literal_eval(node.value.args[0])
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(source, str):
            for name in names:
                patterns[name] = re.compile(source)
    return patterns


def _goal_type_conflicts_with_text(
    *, evidence_span: str, goal_type: str, patterns: dict[str, re.Pattern[str]],
) -> bool:
    modal = patterns.get("_CONSULTATIVE_MODAL")
    factual = patterns.get("_FACTUAL_LOOKUP")
    action = patterns.get("_EXPLICIT_ACTION_REQUEST")
    return bool(
        goal_type == "query"
        and modal is not None and modal.search(evidence_span)
        and (factual is None or not factual.search(evidence_span))
        and (action is None or not action.search(evidence_span))
    )


def _capability_goal_completion_types(workspace: Path) -> dict[str, set[str]]:
    """Read the module-owned completion contract without importing the app.

    The catalog gate runs before the application test suite and must remain a
    static check.  Parsing the authoritative ``DEFINITION`` declarations lets
    it reject a semantic oracle that asks a query-only capability to close a
    consultation (or any equivalent cross-type mismatch).
    """
    root = workspace / "services/agent-service/src/agent_modules/ecommerce/capabilities"
    contracts: dict[str, set[str]] = {
        tool_name: set(goal_types)
        for tool_name, goal_types in PROTOCOL_GOAL_COMPLETION_TYPES.items()
    }
    for path in sorted(root.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "DEFINITION" for target in node.targets
            ) or not isinstance(node.value, ast.Call):
                continue
            keywords = {str(item.arg): item.value for item in node.value.keywords if item.arg}
            try:
                tool_name = ast.literal_eval(keywords["tool_name"])
                goal_types = ast.literal_eval(keywords["goal_completion_types"])
            except (KeyError, ValueError, TypeError, SyntaxError):
                continue
            if isinstance(tool_name, str) and isinstance(goal_types, (tuple, list)):
                contracts[tool_name] = {str(value) for value in goal_types if str(value)}
    return contracts


def _error(errors: list[str], code: str, *parts: Any) -> None:
    errors.append(":".join([code, *(str(part) for part in parts)]))


def _user_turns(case: dict[str, Any]) -> list[str]:
    return [
        str(row.get("text") or "")
        for row in list(case.get("turns") or [])
        if isinstance(row, dict) and row.get("role") == "user" and str(row.get("text") or "")
    ]


def _planner_goals(steps: list[Any]) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for step in steps:
        calls = step.get("tool_calls") if isinstance(step, dict) else None
        for call in calls or []:
            if isinstance(call, dict) and str(call.get("name") or "") == "declare_turn_goals":
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                declarations.extend(row for row in list(args.get("goals") or []) if isinstance(row, dict))
    return declarations


def _verify_goal_oracle(
    *, case_id: str, turn_index: int, user_text: str, contract: dict[str, Any],
    planner_goals: list[dict[str, Any]], calls_by_goal: dict[str, set[str]], errors: list[str],
    goal_completion_types_by_tool: dict[str, set[str]] | None = None,
) -> None:
    oracle = contract.get("goal_oracle")
    if not isinstance(oracle, list) or not oracle:
        _error(errors, "semantic_goal_oracle_missing", case_id, turn_index)
        return
    oracle_ids: set[str] = set()
    for row in oracle:
        if not isinstance(row, dict):
            _error(errors, "semantic_goal_oracle_invalid", case_id, turn_index)
            continue
        oid = str(row.get("oracle_id") or "")
        evidence = str(row.get("evidence_span") or "")
        goal_type = str(row.get("goal_type") or "")
        tools = {str(v) for v in row.get("required_tools") or [] if str(v)}
        deps = {str(v) for v in row.get("depends_on") or [] if str(v)}
        if not oid or oid in oracle_ids:
            _error(errors, "semantic_goal_oracle_id_invalid", case_id, turn_index, oid)
        oracle_ids.add(oid)
        if goal_type not in GOAL_TYPES or not evidence or evidence not in user_text or not tools:
            _error(errors, "semantic_goal_oracle_contract_invalid", case_id, turn_index, oid)
        if goal_completion_types_by_tool is not None:
            for tool_name in sorted(tools):
                completion_types = goal_completion_types_by_tool.get(tool_name)
                if completion_types is None:
                    _error(errors, "semantic_goal_completion_contract_missing", case_id, turn_index, oid, tool_name)
                elif goal_type not in completion_types:
                    _error(
                        errors, "semantic_goal_type_incompatible_with_capability",
                        case_id, turn_index, oid, tool_name, goal_type,
                    )
        if oid in deps:
            _error(errors, "semantic_goal_oracle_self_dependency", case_id, turn_index, oid)
    for row in oracle:
        if isinstance(row, dict):
            for dep in row.get("depends_on") or []:
                if str(dep) not in oracle_ids:
                    _error(errors, "semantic_goal_oracle_unknown_dependency", case_id, turn_index, dep)

    planner_by_id = {str(row.get("goal_id") or ""): row for row in planner_goals}
    if set(planner_by_id) != oracle_ids:
        _error(errors, "semantic_planner_goal_ids_do_not_match_oracle", case_id, turn_index)
        return
    for expected in oracle:
        oid = str(expected["oracle_id"])
        actual = planner_by_id[oid]
        if str(actual.get("goal_type") or "") != str(expected.get("goal_type") or ""):
            _error(errors, "semantic_planner_goal_type_mismatch", case_id, turn_index, oid)
        if str(actual.get("evidence_span") or "") != str(expected.get("evidence_span") or ""):
            _error(errors, "semantic_planner_evidence_mismatch", case_id, turn_index, oid)
        required_tools = {str(v) for v in expected.get("required_tools") or [] if str(v)}
        if not required_tools.issubset(calls_by_goal.get(oid, set())):
            _error(errors, "semantic_runtime_goal_binding_mismatch", case_id, turn_index, oid)
        if {str(v) for v in actual.get("depends_on") or [] if str(v)} != {str(v) for v in expected.get("depends_on") or [] if str(v)}:
            _error(errors, "semantic_planner_dependency_mismatch", case_id, turn_index, oid)


def _verify_turn(
    *, case_id: str, turn_index: int, user_text: str, contract: Any,
    semantic: bool, errors: list[str], tool_counter: Counter[str], workflow_levels: set[str],
    goal_completion_types_by_tool: dict[str, set[str]] | None = None,
    goal_type_patterns: dict[str, re.Pattern[str]] | None = None,
) -> None:
    if not isinstance(contract, dict):
        _error(errors, "conversation_turn_contract_invalid", case_id, turn_index)
        return
    missing = sorted(TURN_FIELDS - set(contract))
    if missing:
        _error(errors, "conversation_turn_contract_missing", case_id, turn_index, ",".join(missing))
    if str(contract.get("user_text") or "") != user_text:
        _error(errors, "conversation_turn_text_mismatch", case_id, turn_index)

    steps = contract.get("model_steps")
    declared_tools: list[str] = []
    calls_by_goal: dict[str, set[str]] = {}
    declaration_count = 0
    if not isinstance(steps, list) or not steps:
        _error(errors, "conversation_turn_model_steps_invalid", case_id, turn_index)
        steps = []
    for step_index, step in enumerate(steps):
        calls = step.get("tool_calls") if isinstance(step, dict) else None
        if not isinstance(calls, list) or not calls:
            _error(errors, "conversation_turn_model_step_invalid", case_id, turn_index, step_index)
            continue
        for call in calls:
            name = str(call.get("name") or "") if isinstance(call, dict) else ""
            call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
            args = call.get("args") if isinstance(call, dict) else None
            if not name or not call_id or not isinstance(args, dict):
                _error(errors, "conversation_turn_model_call_invalid", case_id, turn_index, step_index)
                continue
            if name == "declare_turn_goals":
                declaration_count += 1
                if step_index != 0:
                    _error(errors, "goal_declaration_must_be_first_model_step", case_id, turn_index)
            else:
                declared_tools.append(name)
                tool_counter[name] += 1
    planner_goals = _planner_goals(steps)
    if declaration_count != 1 or not planner_goals:
        _error(errors, "goal_declaration_count_invalid", case_id, turn_index, declaration_count)
    planner_goal_ids = {str(row.get("goal_id") or "") for row in planner_goals if str(row.get("goal_id") or "")}
    for goal in planner_goals:
        if "expected_tools" in goal:
            _error(errors, "semantic_planner_must_not_guess_tools", case_id, turn_index, goal.get("goal_id"))
    for step in steps:
        calls = step.get("tool_calls") if isinstance(step, dict) else []
        for call in calls or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            if name in {"declare_turn_goals", "update_task_board", "inspect_audit_event"}:
                continue
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            goal_ids = [str(value) for value in list(args.get("goal_ids") or []) if str(value)]
            if not goal_ids or any(goal_id not in planner_goal_ids for goal_id in goal_ids):
                _error(errors, "runtime_goal_binding_missing_or_unknown", case_id, turn_index, name)
                continue
            if name not in {"respond_to_user", "ask_user_clarification"} and len(goal_ids) != 1:
                _error(errors, "runtime_business_call_must_bind_exactly_one_goal", case_id, turn_index, name)
                continue
            for goal_id in goal_ids:
                calls_by_goal.setdefault(goal_id, set()).add(name)

    for goal in planner_goals:
        goal_id = str(goal.get("goal_id") or "")
        goal_type = str(goal.get("goal_type") or "")
        evidence_span = str(goal.get("evidence_span") or "")
        if goal_type not in GOAL_TYPES or not evidence_span or evidence_span not in user_text:
            _error(errors, "planner_goal_contract_invalid", case_id, turn_index, goal_id)
            continue
        if goal_type_patterns is not None and _goal_type_conflicts_with_text(
            evidence_span=evidence_span,
            goal_type=goal_type,
            patterns=goal_type_patterns,
        ):
            _error(errors, "planner_goal_type_conflicts_with_user_text", case_id, turn_index, goal_id)
        if goal_completion_types_by_tool is not None:
            bound_tools = calls_by_goal.get(goal_id, set())
            if not any(
                goal_type in goal_completion_types_by_tool.get(tool_name, set())
                for tool_name in bound_tools
            ):
                _error(errors, "planner_goal_has_no_compatible_completion_capability", case_id, turn_index, goal_id)

    allowed = {str(v) for v in contract.get("allowed_tools") or [] if str(v)}
    required = {str(v) for v in contract.get("required_tools") or [] if str(v)}
    if not allowed or not required or not required.issubset(allowed):
        _error(errors, "conversation_turn_tool_boundary_invalid", case_id, turn_index)
    if not set(declared_tools).issubset(allowed) or not required.issubset(set(declared_tools)):
        _error(errors, "conversation_turn_candidate_script_boundary_invalid", case_id, turn_index)

    runtime = contract.get("expected")
    if not isinstance(runtime, dict) or not EXPECTED_FIELDS.issubset(runtime):
        _error(errors, "conversation_turn_expected_runtime_invalid", case_id, turn_index)
        return
    statuses = {str(v) for v in runtime.get("terminal_statuses") or [] if str(v)}
    levels = {str(v) for v in runtime.get("workflow_levels") or [] if str(v)}
    workflow_statuses = {str(v) for v in runtime.get("workflow_statuses") or [] if str(v)}
    public = str(runtime.get("public_interaction") or "")
    if not statuses or not levels or not workflow_statuses or public not in {"answer", "clarification", "transaction_interaction"}:
        _error(errors, "conversation_turn_terminal_contract_invalid", case_id, turn_index)
    trace = runtime.get("trace")
    draft = runtime.get("draft")
    if not isinstance(trace, dict) or not isinstance(trace.get("must_include"), list) or not isinstance(trace.get("must_not_include"), list):
        _error(errors, "conversation_turn_trace_contract_invalid", case_id, turn_index)
    if not isinstance(draft, dict) or "count" not in draft:
        _error(errors, "conversation_turn_draft_contract_invalid", case_id, turn_index)
    if not isinstance(runtime.get("port_calls"), dict) or not isinstance(runtime.get("result_assertions"), list):
        _error(errors, "conversation_turn_runtime_assertions_invalid", case_id, turn_index)
    workflow_levels.update(levels)

    if semantic:
        _verify_goal_oracle(
            case_id=case_id, turn_index=turn_index, user_text=user_text,
            contract=contract, planner_goals=planner_goals, calls_by_goal=calls_by_goal, errors=errors,
            goal_completion_types_by_tool=goal_completion_types_by_tool,
        )
        oracle = contract.get("goal_oracle") if isinstance(contract.get("goal_oracle"), list) else []
        if runtime.get("goal_count") != len(oracle):
            _error(errors, "semantic_goal_count_does_not_match_oracle", case_id, turn_index)


def _verify_suite(
    *, workspace: Path, path: Path, payload: dict[str, Any], semantic: bool,
    errors: list[str], case_ids: set[str], categories: set[str],
    goal_completion_types_by_tool: dict[str, set[str]] | None = None,
    goal_type_patterns: dict[str, re.Pattern[str]] | None = None,
) -> dict[str, Any]:
    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    expected_name = SEMANTIC_SUITE_NAME if semantic else RUNTIME_SUITE_NAME
    expected_schema = 4
    expected_scope = "independent_goal_oracle_plus_runtime_execution_with_explicit_goal_binding" if semantic else "scripted_runtime_protocol_and_boundary_contracts_not_semantic_correctness"
    expected_modules = SEMANTIC_MODULES if semantic else RUNTIME_MODULES
    if str(payload.get("suite") or "") != expected_name:
        _error(errors, "conversation_suite_name", path.relative_to(workspace), payload.get("suite"))
    if payload.get("schema_version") != expected_schema:
        _error(errors, "conversation_suite_schema_version", path.relative_to(workspace), payload.get("schema_version"))
    if str(payload.get("contract_scope") or "") != expected_scope:
        _error(errors, "conversation_suite_scope", path.relative_to(workspace))
    if payload.get("case_count") != len(cases):
        _error(errors, "conversation_suite_declared_count", path.relative_to(workspace))
    if semantic and len(cases) < SEMANTIC_SUITE_MIN_COUNT:
        _error(errors, "semantic_suite_case_count_too_small", len(cases))
    executable_ids: list[str] = []
    executable_set: set[str] = set()
    if not semantic:
        if len(cases) != RUNTIME_CATALOG_COUNT:
            _error(errors, "runtime_catalog_case_count", len(cases))
        raw_executable = payload.get("executable_case_ids")
        if not isinstance(raw_executable, list):
            _error(errors, "runtime_executable_case_ids_missing")
            raw_executable = []
        executable_ids = [str(value) for value in raw_executable if str(value)]
        executable_set = set(executable_ids)
        if len(executable_ids) != len(executable_set):
            _error(errors, "runtime_executable_case_ids_duplicate")
        if payload.get("execution_case_count") != len(executable_ids):
            _error(errors, "runtime_executable_declared_count", payload.get("execution_case_count"))
        if len(executable_ids) < RUNTIME_EXECUTABLE_MIN_COUNT:
            _error(errors, "runtime_executable_case_count_too_small", len(executable_ids))
        catalog_ids = {
            str(case.get("id") or "") for case in cases if isinstance(case, dict)
        }
        missing_executable = sorted(executable_set - catalog_ids)
        if missing_executable:
            _error(errors, "runtime_executable_case_missing", ",".join(missing_executable))
        executable_categories = {
            str(case.get("category") or "")
            for case in cases
            if isinstance(case, dict) and str(case.get("id") or "") in executable_set
        }
        missing_executable_categories = sorted(
            RUNTIME_EXECUTABLE_REQUIRED_CATEGORIES - executable_categories
        )
        if missing_executable_categories:
            _error(
                errors,
                "runtime_executable_categories_missing",
                ",".join(missing_executable_categories),
            )
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, dict) or "customer_orders_v1" not in fixtures:
        _error(errors, "conversation_suite_fixture_registry_invalid", path.relative_to(workspace))
    modules = {str(v) for v in payload.get("runtime_test_modules") or [] if str(v)}
    if modules != expected_modules:
        _error(errors, "conversation_suite_runtime_modules_invalid", path.relative_to(workspace))
    for module in modules:
        if not (workspace / module).is_file():
            _error(errors, "conversation_suite_runtime_module_missing", module)

    tool_counter: Counter[str] = Counter()
    workflow_levels: set[str] = set()
    prototypes = 0
    for case in cases:
        if not isinstance(case, dict):
            _error(errors, "conversation_case_invalid", path.relative_to(workspace))
            continue
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        case_id = str(case.get("id") or f"{path.name}:<missing-id>")
        if missing:
            _error(errors, "conversation_case_missing_fields", case_id, ",".join(missing))
        if case_id in case_ids:
            _error(errors, "duplicate_case_id", case_id)
        case_ids.add(case_id)
        categories.add(str(case.get("category") or ""))
        users = _user_turns(case)
        if not users:
            _error(errors, "conversation_case_without_user_turn", case_id)
        if not isinstance(case.get("expected_runtime_behavior"), dict) or not str(case["expected_runtime_behavior"].get("outcome") or ""):
            _error(errors, "conversation_case_expected_behavior_invalid", case_id)
        forbidden = {str(v) for v in case.get("forbidden_behavior") or [] if str(v)}
        if not forbidden:
            _error(errors, "conversation_case_forbidden_behavior_missing", case_id)

        contract = case.get("execution_contract")
        if not isinstance(contract, dict):
            _error(errors, "conversation_case_contract_invalid", case_id)
            continue
        missing_contract = sorted(CONTRACT_FIELDS - set(contract))
        if missing_contract:
            _error(errors, "conversation_case_contract_missing", case_id, ",".join(missing_contract))
        if contract.get("schema_version") != 4:
            _error(errors, "conversation_case_contract_schema", case_id)
        fixture = contract.get("fixture")
        state = fixture.get("state") if isinstance(fixture, dict) else None
        if not isinstance(fixture, dict) or fixture.get("id") != "customer_orders_v1" or not isinstance(state, dict) or not {"tenant_id", "user_id", "role", "initial_ledger"}.issubset(state):
            _error(errors, "conversation_case_fixture_invalid", case_id)
        turn_contracts = contract.get("turn_contracts")
        if not isinstance(turn_contracts, list) or len(turn_contracts) != len(users):
            _error(errors, "conversation_case_turn_contract_count", case_id)
            turn_contracts = []
        case_tool_counter: Counter[str] = Counter()
        case_workflow_levels: set[str] = set()
        evidence_case = semantic or case_id in executable_set
        for index, (text, turn) in enumerate(zip(users, turn_contracts), start=1):
            _verify_turn(
                case_id=case_id, turn_index=index, user_text=text, contract=turn,
                semantic=semantic, errors=errors, tool_counter=case_tool_counter,
                workflow_levels=case_workflow_levels,
                # Non-executable catalog rows are explicitly inventory only.
                # Their scripted candidates cannot prove semantic correctness
                # and therefore neither pass nor fail semantic evidence gates.
                goal_completion_types_by_tool=(
                    goal_completion_types_by_tool if evidence_case else None
                ),
                goal_type_patterns=(goal_type_patterns if evidence_case else None),
            )
        if evidence_case:
            tool_counter.update(case_tool_counter)
            workflow_levels.update(case_workflow_levels)

        assertions = contract.get("forbidden_assertions")
        if not isinstance(assertions, list) or not assertions:
            _error(errors, "conversation_case_forbidden_assertions_invalid", case_id)
        else:
            mapped = {str(row.get("behavior") or "") for row in assertions if isinstance(row, dict)}
            if mapped != forbidden:
                _error(errors, "conversation_case_forbidden_assertion_mapping", case_id)
            for row in assertions:
                kind = str(row.get("kind") or "") if isinstance(row, dict) else ""
                if kind not in FORBIDDEN_ASSERTION_KINDS:
                    _error(errors, "conversation_case_forbidden_assertion_kind", case_id, kind)

        prototype = contract.get("preproduction_risk_prototype")
        allowed = {str(v) for v in contract.get("preproduction_allowed_tools") or [] if str(v)}
        blocked = {str(v) for v in contract.get("preproduction_forbidden_tools") or [] if str(v)}
        if not isinstance(prototype, bool):
            _error(errors, "conversation_case_preproduction_flag_invalid", case_id)
        elif prototype:
            prototypes += 1
            if not semantic or not allowed or not blocked or allowed & blocked:
                _error(errors, "conversation_case_preproduction_tools_invalid", case_id)
        elif allowed or blocked:
            _error(errors, "conversation_case_nonprototype_preproduction_tools", case_id)

    if semantic:
        missing_tools = sorted(SEMANTIC_TOOL_DIVERSITY - set(tool_counter))
        if missing_tools:
            _error(errors, "semantic_suite_tool_diversity_missing", ",".join(missing_tools))
        if prototypes != SEMANTIC_PREPROD_COUNT:
            _error(errors, "semantic_preproduction_prototype_count", prototypes)
        if not {"L1_LIGHTWEIGHT_PLAN", "L2_WORKFLOW"}.issubset(workflow_levels):
            _error(errors, "semantic_suite_workflow_diversity_missing")
    elif prototypes:
        _error(errors, "runtime_suite_must_not_claim_preproduction_semantics", prototypes)
    return {
        "path": str(path.relative_to(workspace)),
        "kind": "semantic_goal_oracle" if semantic else "runtime_contract",
        "case_count": len(cases),
        "executable_case_count": len(cases) if semantic else len(executable_ids),
        "non_evidence_catalog_case_count": 0 if semantic else len(cases) - len(executable_ids),
        "tool_diversity": dict(sorted(tool_counter.items())),
        "workflow_levels": sorted(workflow_levels),
        "preproduction_case_count": prototypes,
    }


def verify(workspace: Path, catalog_root: str = "services/agent-service/tests/context/strong_context_cases") -> dict[str, Any]:
    root = workspace / catalog_root
    errors: list[str] = []
    categories: set[str] = set()
    case_ids: set[str] = set()
    suites: list[dict[str, Any]] = []
    seen_runtime = 0
    seen_semantic = 0
    goal_completion_types_by_tool = _capability_goal_completion_types(workspace)
    goal_type_patterns = _goal_type_patterns(workspace)
    if set(goal_type_patterns) != {"_CONSULTATIVE_MODAL", "_FACTUAL_LOOKUP", "_EXPLICIT_ACTION_REQUEST"}:
        _error(errors, "production_goal_type_taxonomy_unavailable")
    if not root.is_dir():
        return {"status": "FAIL", "errors": [f"missing_catalog:{catalog_root}"], "cases": []}

    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _error(errors, "invalid_json", path.relative_to(workspace), exc)
            continue
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list) or not cases:
            _error(errors, "empty_cases", path.relative_to(workspace))
            continue
        suite_name = str(payload.get("suite") or "")
        if suite_name == RUNTIME_SUITE_NAME:
            seen_runtime += 1
            suites.append(_verify_suite(
                workspace=workspace, path=path, payload=payload, semantic=False,
                errors=errors, case_ids=case_ids, categories=categories,
                goal_completion_types_by_tool=goal_completion_types_by_tool,
                goal_type_patterns=goal_type_patterns,
            ))
            continue
        if suite_name == SEMANTIC_SUITE_NAME:
            seen_semantic += 1
            suites.append(_verify_suite(
                workspace=workspace, path=path, payload=payload, semantic=True, errors=errors,
                case_ids=case_ids, categories=categories,
                goal_completion_types_by_tool=goal_completion_types_by_tool,
                goal_type_patterns=goal_type_patterns,
            ))
            continue

        # Small legacy counterexample assets remain structural contracts only.
        for item in cases:
            if not isinstance(item, dict):
                _error(errors, "invalid_case", path.relative_to(workspace))
                continue
            missing = sorted(REQUIRED_CASE_FIELDS - set(item))
            case_id = str(item.get("id") or f"{path.name}:<missing-id>")
            if missing:
                _error(errors, "case_missing_fields", case_id, ",".join(missing))
            if case_id in case_ids:
                _error(errors, "duplicate_case_id", case_id)
            case_ids.add(case_id)
            categories.add(str(item.get("category") or ""))
            if not _user_turns(item):
                _error(errors, "case_requires_user_turn", case_id)
            if not isinstance(item.get("expected_runtime_behavior"), dict) or not item["expected_runtime_behavior"].get("outcome"):
                _error(errors, "case_missing_expected_outcome", case_id)
            if not isinstance(item.get("forbidden_behavior"), list) or not item["forbidden_behavior"]:
                _error(errors, "case_missing_forbidden_behavior", case_id)
            if not isinstance(item.get("execution_contract"), str) or not item["execution_contract"].strip():
                _error(errors, "case_missing_execution_contract", case_id)

    if seen_runtime != 1:
        _error(errors, "runtime_suite_count", seen_runtime)
    if seen_semantic != 1:
        _error(errors, "semantic_suite_count", seen_semantic)
    missing_categories = sorted(REQUIRED_CATEGORIES - categories)
    if missing_categories:
        _error(errors, "missing_required_categories", ",".join(missing_categories))
    missing_conversation_categories = sorted(CONVERSATION_REQUIRED_CATEGORIES - categories)
    if missing_conversation_categories:
        _error(errors, "missing_conversation_categories", ",".join(missing_conversation_categories))
    for relative, markers in SYSTEMIC_SEQUENCE_MODULES.items():
        module = workspace / relative
        if not module.is_file():
            _error(errors, "systemic_sequence_module_missing", relative)
            continue
        source = module.read_text(encoding="utf-8")
        missing_markers = [marker for marker in markers if marker not in source]
        if missing_markers:
            _error(errors, "systemic_sequence_markers_missing", relative, ",".join(missing_markers))
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "case_count": len(case_ids),
        "cases": sorted(case_ids),
        "categories": sorted(categories),
        "conversation_suites": suites,
        "missing_required_categories": missing_categories,
        "missing_conversation_categories": missing_conversation_categories,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--catalog-root", default="services/agent-service/tests/context/strong_context_cases")
    args = parser.parse_args()
    result = verify(Path(args.workspace_root).resolve(), args.catalog_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
