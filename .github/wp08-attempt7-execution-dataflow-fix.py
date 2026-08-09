#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()
CATALOG = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"

payload = json.loads(CATALOG.read_text(encoding="utf-8"))
by_id = {str(row.get("id") or ""): row for row in payload.get("cases") or []}


def turn_for(case_id: str) -> dict:
    case = by_id.get(case_id)
    if not isinstance(case, dict):
        raise SystemExit(f"missing semantic case: {case_id}")
    turns = (case.get("execution_contract") or {}).get("turn_contracts") or []
    if len(turns) != 1:
        raise SystemExit(f"expected one turn contract: {case_id}")
    return turns[0]


def one_call(turn: dict, name: str) -> dict:
    matches = [
        call
        for step in turn.get("model_steps") or []
        for call in step.get("tool_calls") or []
        if call.get("name") == name
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one {name}, found {len(matches)}")
    return matches[0]


def terminal_steps(turn: dict) -> list[dict]:
    return [
        step
        for step in (turn.get("model_steps") or [])[1:]
        if any(call.get("name") in {"respond_to_user", "ask_user_clarification"} for call in step.get("tool_calls") or [])
    ]


# Independent read outcomes whose targets are already explicit in the current
# user text can complete in one plan batch without inventing semantic depends_on.
detail_turn = turn_for("semantic_order_detail_and_invoice")
detail_steps = list(detail_turn.get("model_steps") or [])
if not detail_steps:
    raise SystemExit("semantic_order_detail_and_invoice model steps missing")
details = one_call(detail_turn, "get_order_details")
invoices = one_call(detail_turn, "list_invoices")
detail_turn["model_steps"] = [
    detail_steps[0],
    {"tool_calls": [details, invoices]},
    *terminal_steps(detail_turn),
]

# A write may be semantically independent while still needing a safe read to
# establish an authoritative runtime target.  Keep the query Goal independent,
# execute it first, and let the next loop use the verified artifact for the
# refund draft.  The support read is execution dataflow, not Goal dependency.
refund_turn = turn_for("semantic_query_then_refund_draft")
refund_steps = list(refund_turn.get("model_steps") or [])
if not refund_steps:
    raise SystemExit("semantic_query_then_refund_draft model steps missing")
refund_list = one_call(refund_turn, "list_orders")
refund_prepare = one_call(refund_turn, "prepare_refund")
refund_prepare.setdefault("args", {})["target"] = {
    "mode": "artifact",
    "left_handle": "artifact:fixture:order:10003",
}
refund_prepare["args"]["reference_span"] = "鼠标订单"
refund_prepare["args"].pop("context_binding", None)
refund_turn["model_steps"] = [
    refund_steps[0],
    {"tool_calls": [refund_list]},
    {"tool_calls": [refund_prepare]},
    *terminal_steps(refund_turn),
]

# Target resolution for a collection write is an execution support read, not a
# separate user-observable Goal.  Keep one cancel Goal, use list_orders as its
# exact registered support step, then prove the write cardinality boundary on
# the verified collection.  No draft or commit is authorized by the support read.
cancel_case = by_id.get("semantic_multi_target_cancel_boundary")
if not isinstance(cancel_case, dict):
    raise SystemExit("semantic_multi_target_cancel_boundary missing")
cancel_turn = turn_for("semantic_multi_target_cancel_boundary")
cancel_steps = list(cancel_turn.get("model_steps") or [])
if not cancel_steps:
    raise SystemExit("semantic_multi_target_cancel_boundary model steps missing")
prepare_cancel = one_call(cancel_turn, "prepare_cancel_order")
respond = one_call(cancel_turn, "respond_to_user")
list_support = {
    "name": "list_orders",
    "args": {
        "target": {"mode": "all_orders"},
        "expected_shape": "collection",
        "reference_span": "这些订单",
        "goal_ids": ["g1"],
    },
    "id": "semantic_multi_target_cancel_boundary:list",
}
prepare_cancel.setdefault("args", {})["target"] = {
    "mode": "collection",
    "left_handle": "$last_tool.data.result_handle",
}
prepare_cancel["args"]["reference_span"] = "这些订单"
prepare_cancel["args"]["action_span"] = "取消"
prepare_cancel["args"]["goal_ids"] = ["g1"]
respond.setdefault("args", {})["goal_ids"] = ["g1"]
cancel_turn["model_steps"] = [
    cancel_steps[0],
    {"tool_calls": [list_support]},
    {"tool_calls": [prepare_cancel]},
    {"tool_calls": [respond]},
]
for key in ("allowed_tools", "required_tools"):
    names = [str(value) for value in list(cancel_turn.get(key) or []) if str(value)]
    cancel_turn[key] = list(dict.fromkeys(["list_orders", *names]))
execution = cancel_case.get("execution_contract") if isinstance(cancel_case.get("execution_contract"), dict) else {}
preprod = [str(value) for value in list(execution.get("preproduction_allowed_tools") or []) if str(value)]
execution["preproduction_allowed_tools"] = list(dict.fromkeys(["list_orders", *preprod]))

CATALOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("attempt7 semantic-independent execution dataflow repair applied")
