#!/usr/bin/env python3
"""One-time fail-closed repair for the mixed cancel/refund prototype.

After the business-goal boundary and single-Goal provenance repairs, the
prototype still declared collection cardinality for two concrete single-target
operations and expected the L2 plan to collapse to the task-level authorization
state.  This repair aligns only those exact stale expectations.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
CASE_ID = "semantic_cancel_and_refund_branch"


def main() -> int:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    matches = [row for row in payload.get("cases", []) if row.get("id") == CASE_ID]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {CASE_ID!r} case, found {len(matches)}")

    contract = matches[0]["execution_contract"]["turn_contracts"][0]
    oracle = contract.get("goal_oracle")
    if not isinstance(oracle, list) or len(oracle) != 2:
        raise SystemExit("expected the repaired two-business-Goal oracle")
    if [row.get("requested_effect", {}).get("operation") for row in oracle] != [
        "cancel",
        "assess_eligibility",
    ]:
        raise SystemExit("cancel/refund business outcomes drifted")

    declaration_calls = contract.get("model_steps", [])[0].get("tool_calls") or []
    if len(declaration_calls) != 1 or declaration_calls[0].get("name") != "declare_turn_goals":
        raise SystemExit("goal declaration step drifted")
    goals = declaration_calls[0].get("args", {}).get("goals")
    if not isinstance(goals, list) or len(goals) != 2:
        raise SystemExit("expected exactly two declared goals")
    if [row.get("goal_id") for row in goals] != ["g1", "g2"]:
        raise SystemExit("declared goal IDs drifted")
    if [row.get("expected_result_cardinality") for row in goals] != ["collection", "collection"]:
        raise SystemExit("legacy collection cardinality shape drifted")

    execution_calls = contract.get("model_steps", [])[1].get("tool_calls") or []
    list_calls = [row for row in execution_calls if row.get("name") == "list_orders"]
    if len(list_calls) != 2:
        raise SystemExit("expected two single-Goal lookup calls before cardinality repair")
    if {tuple((row.get("args") or {}).get("goal_ids") or []) for row in list_calls} != {
        ("g1",),
        ("g2",),
    }:
        raise SystemExit("lookup Goal provenance drifted")
    completion_calls = {
        row.get("name"): row
        for row in execution_calls
        if row.get("name") in {"prepare_cancel_order", "evaluate_refund_eligibility"}
    }
    if set(completion_calls) != {"prepare_cancel_order", "evaluate_refund_eligibility"}:
        raise SystemExit("completion tool set drifted")
    if completion_calls["prepare_cancel_order"].get("args", {}).get("target", {}).get("mode") != "artifact":
        raise SystemExit("cancel completion is no longer a concrete single target")
    if completion_calls["evaluate_refund_eligibility"].get("args", {}).get("target", {}).get("mode") != "artifact":
        raise SystemExit("eligibility completion is no longer a concrete single target")

    expected = contract.get("expected")
    if not isinstance(expected, dict):
        raise SystemExit("expected runtime contract missing")
    if expected.get("workflow_levels") != ["L2_WORKFLOW"]:
        raise SystemExit("mixed workflow level drifted")
    if expected.get("workflow_statuses") != ["AWAITING_AUTHORIZATION"]:
        raise SystemExit("legacy top-level authorization expectation drifted")
    if expected.get("public_interaction") != "transaction_interaction":
        raise SystemExit("public transaction interaction contract drifted")

    for goal in goals:
        goal["expected_result_cardinality"] = "one"
    expected["workflow_statuses"] = ["RUNNING"]

    CATALOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
