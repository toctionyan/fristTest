#!/usr/bin/env python3
"""One-time fail-closed repair for shared lookup Goal provenance.

The cancel/refund prototype has two user business Goals.  Its two internal
order lookups must remain execution steps, but every runtime business call is
required to bind to exactly one Goal.  This script replaces the single
multi-Goal ``list_orders`` call with two equivalent single-Goal calls.
"""
from __future__ import annotations

from copy import deepcopy
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
        raise SystemExit("expected repaired two-Goal oracle before lookup provenance repair")
    if [row.get("oracle_id") for row in oracle] != ["g1", "g2"]:
        raise SystemExit("two-Goal oracle IDs drifted")
    if [row.get("requested_effect", {}).get("operation") for row in oracle] != [
        "cancel",
        "assess_eligibility",
    ]:
        raise SystemExit("two-Goal oracle operations drifted")

    steps = contract.get("model_steps")
    if not isinstance(steps, list) or len(steps) < 2:
        raise SystemExit("execution steps drifted")
    calls = steps[1].get("tool_calls")
    if not isinstance(calls, list):
        raise SystemExit("execution tool calls missing")

    list_indexes = [index for index, row in enumerate(calls) if row.get("name") == "list_orders"]
    if len(list_indexes) != 1:
        raise SystemExit(f"expected one legacy shared list_orders call, found {len(list_indexes)}")
    list_index = list_indexes[0]
    shared = calls[list_index]
    args = shared.get("args") if isinstance(shared.get("args"), dict) else {}
    if args.get("goal_ids") != ["g1", "g2"]:
        raise SystemExit("legacy shared lookup Goal binding drifted")
    if args.get("target") != {"mode": "all_orders"}:
        raise SystemExit("legacy shared lookup target drifted")

    cancel_lookup = deepcopy(shared)
    cancel_lookup["id"] = "s5:orders:cancel"
    cancel_lookup["args"]["goal_ids"] = ["g1"]
    cancel_lookup["args"]["reference_span"] = "待发货的订单"

    refund_lookup = deepcopy(shared)
    refund_lookup["id"] = "s5:orders:refund"
    refund_lookup["args"]["goal_ids"] = ["g2"]
    refund_lookup["args"]["reference_span"] = "已签收的"

    calls[list_index : list_index + 1] = [cancel_lookup, refund_lookup]

    business_calls = [
        row
        for row in calls
        if row.get("name") in {
            "list_orders",
            "prepare_cancel_order",
            "evaluate_refund_eligibility",
        }
    ]
    invalid = [
        row.get("id")
        for row in business_calls
        if len((row.get("args") or {}).get("goal_ids") or []) != 1
    ]
    if invalid:
        raise SystemExit(f"business calls still lack exact single-Goal binding: {invalid}")

    CATALOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
