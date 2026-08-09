#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()
CATALOG = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"

payload = json.loads(CATALOG.read_text(encoding="utf-8"))
case = next(
    (row for row in payload.get("cases") or [] if row.get("id") == "semantic_order_detail_and_invoice"),
    None,
)
if not isinstance(case, dict):
    raise SystemExit("semantic_order_detail_and_invoice missing")
turns = (case.get("execution_contract") or {}).get("turn_contracts") or []
if len(turns) != 1:
    raise SystemExit("semantic_order_detail_and_invoice turn shape changed")
turn = turns[0]
steps = list(turn.get("model_steps") or [])
if not steps:
    raise SystemExit("semantic_order_detail_and_invoice model steps missing")


def one_call(name: str) -> dict:
    matches = [
        call
        for step in steps
        for call in step.get("tool_calls") or []
        if call.get("name") == name
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one {name}, found {len(matches)}")
    return matches[0]


def terminal_steps() -> list[dict]:
    return [
        step
        for step in steps[1:]
        if any(call.get("name") in {"respond_to_user", "ask_user_clarification"} for call in step.get("tool_calls") or [])
    ]

# The order-detail and invoice outcomes both bind the same explicit order id
# directly from the current user text.  They are semantically independent and
# neither needs the other tool's result.  Put both completion effects in the
# same candidate plan batch so execution scheduling does not masquerade as
# semantic depends_on.
details = one_call("get_order_details")
invoices = one_call("list_invoices")
turn["model_steps"] = [
    steps[0],
    {"tool_calls": [details, invoices]},
    *terminal_steps(),
]

CATALOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("attempt7 explicit-order independent completion batching applied")
