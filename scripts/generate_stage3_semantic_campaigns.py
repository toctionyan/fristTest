#!/usr/bin/env python3
from __future__ import annotations

"""Generate locked deterministic Stage-3 semantic/context evaluation fixtures.

The expected capability mapping below is intentionally independent from the
runtime registry.  The verifier compares the live registry projection against
this locked expectation, so changing a capability contract cannot silently
rewrite the expected result.
"""

import argparse
import json
from pathlib import Path
from typing import Any


EFFECTS: list[dict[str, Any]] = [
    {"identity": "order.list:order", "completion": ["list_orders"], "support": []},
    {"identity": "order.query_details:order", "completion": ["get_order_details"], "support": ["list_orders"]},
    {"identity": "order.query_logistics:order", "completion": ["get_order_logistics"], "support": ["get_order_details", "list_orders"]},
    {"identity": "order.cancel:order", "completion": ["prepare_cancel_order"], "support": ["get_order_details", "list_orders"]},
    {"identity": "refund.assess_eligibility:order", "completion": ["evaluate_refund_eligibility"], "support": ["get_order_details", "list_orders"]},
    {"identity": "refund.create:order", "completion": ["prepare_refund", "prepare_refund_from_eligibility"], "support": ["evaluate_refund_eligibility", "get_order_details", "list_active_eligibilities", "list_orders"]},
    {"identity": "refund.query_status:refund", "completion": ["list_refunds"], "support": []},
    {"identity": "refund.consult_policy:order", "completion": ["consult_refund_policy"], "support": ["get_order_details", "list_orders"]},
    {"identity": "refund.dismiss_eligibility:refund_eligibility", "completion": ["dismiss_eligibility"], "support": []},
    {"identity": "invoice.create:order", "completion": ["prepare_invoice"], "support": ["get_order_details", "list_orders"]},
    {"identity": "invoice.query_status:invoice", "completion": ["list_invoices"], "support": []},
    {"identity": "invoice.consult_policy:order", "completion": ["consult_invoice_policy"], "support": ["get_order_details", "list_orders"]},
    {"identity": "after_sales.create:order", "completion": ["prepare_after_sales_request"], "support": ["get_order_details", "list_orders"]},
    {"identity": "after_sales.query_status:after_sales_request", "completion": ["list_after_sales_requests"], "support": []},
    {"identity": "after_sales.consult_policy:order", "completion": ["consult_after_sales_policy"], "support": ["get_order_details", "list_orders"]},
    {"identity": "warranty.consult_policy:order", "completion": ["consult_warranty_policy"], "support": ["get_order_details", "list_orders"]},
    {"identity": "transaction.list_drafts:transaction_draft", "completion": ["list_active_offers"], "support": []},
    {"identity": "transaction.cancel_draft:transaction_draft", "completion": ["dismiss_offer"], "support": ["list_active_offers"]},
    {"identity": "transaction.query_status:transaction", "completion": ["query_transaction_lifecycle"], "support": ["list_active_offers"]},
]


def effect_dict(identity: str, *, raw: str) -> dict[str, str]:
    operation_identity, object_type = identity.split(":", 1)
    domain, operation = operation_identity.split(".", 1)
    return {
        "domain": domain,
        "operation": operation,
        "object_type": object_type,
        "raw_description": raw,
    }


def exact_case(case_id: str, index: int) -> dict[str, Any]:
    row = EFFECTS[index % len(EFFECTS)]
    return {
        "case_id": case_id,
        "kind": "exact_effect",
        "goal": {
            "goal_id": f"goal:{case_id}",
            "requested_effect": effect_dict(row["identity"], raw=f"locked exact {case_id}"),
        },
        "expected": {
            "status": "exact_supported",
            "completion_tools": row["completion"],
            "support_tools": row["support"],
            "candidate_tools": [*row["support"], *row["completion"]],
            "unsupported": False,
            "similarity_used": False,
        },
    }


def absent_case(case_id: str, index: int) -> dict[str, Any]:
    domains = ["courier", "payment", "identity", "warehouse", "loyalty", "subscription"]
    objects = ["driver_phone", "bank_transfer", "identity_record", "stock_bin", "points", "membership"]
    domain = domains[index % len(domains)]
    object_type = objects[index % len(objects)]
    return {
        "case_id": case_id,
        "kind": "absent_effect",
        "goal": {
            "goal_id": f"goal:{case_id}",
            "requested_effect": {
                "domain": domain,
                "operation": f"unregistered_operation_{index}",
                "object_type": object_type,
                "raw_description": f"locked absent {case_id}",
            },
        },
        "expected": {
            "status": "absent_proven",
            "completion_tools": [],
            "support_tools": [],
            "candidate_tools": ["report_unsupported_request"],
            "unsupported": True,
            "similarity_used": False,
        },
    }


def mixed_case(case_id: str, index: int) -> dict[str, Any]:
    exact = exact_case(f"{case_id}:exact", index)
    absent = absent_case(f"{case_id}:absent", index)
    return {
        "case_id": case_id,
        "kind": "mixed_effects",
        "goals": [exact["goal"], absent["goal"]],
        "expected": [exact["expected"], absent["expected"]],
    }


def refs_for_case(*, result_count: int, members_each: int, same_latest_turn: bool) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for rank in range(1, result_count + 1):
        source_turn = 20 if same_latest_turn else 21 - rank
        refs.append({
            "result_ref": f"result:{rank}",
            "member_handles": [f"artifact:{rank}:{member}" for member in range(1, members_each + 1)],
            "member_labels": [f"对象{rank}-{member}" for member in range(1, members_each + 1)],
            "source_turn": source_turn,
            "discourse_recency_rank": rank,
        })
    return refs


def context_case(case_id: str, index: int, mode: str) -> dict[str, Any]:
    if mode == "unique":
        refs = refs_for_case(result_count=1, members_each=1, same_latest_turn=True)
        expected = {"latest_result_count": 1, "latest_member_count": 1, "singular_ambiguous": False}
    elif mode == "collection_members":
        members = 2 + index % 3
        refs = refs_for_case(result_count=1, members_each=members, same_latest_turn=True)
        expected = {"latest_result_count": 1, "latest_member_count": members, "singular_ambiguous": True}
    elif mode == "latest_multi":
        count = 2 + index % 3
        refs = refs_for_case(result_count=count, members_each=1, same_latest_turn=True)
        expected = {"latest_result_count": count, "latest_member_count": count, "singular_ambiguous": True}
    else:
        count = 2 + index % 3
        refs = refs_for_case(result_count=count, members_each=1, same_latest_turn=False)
        expected = {
            "latest_result_count": 1,
            "latest_member_count": 1,
            "singular_ambiguous": False,
            "group_ranks": [list(range(1, size + 1)) for size in range(2, count + 1)],
        }
    return {
        "case_id": case_id,
        "kind": "context_projection",
        "mode": mode,
        "refs": refs,
        "expected": expected,
    }


def build_campaign(name: str, count: int, seed_offset: int) -> dict[str, Any]:
    # Strong-context and holdout sets intentionally contain more context and
    # mixed-goal cases than the initial locked set.
    if count == 50:
        pattern = ["exact"] * 20 + ["absent"] * 10 + ["mixed"] * 5 + ["unique"] * 4 + ["collection_members"] * 4 + ["latest_multi"] * 4 + ["contiguous"] * 3
    elif count == 100:
        pattern = ["exact"] * 35 + ["absent"] * 20 + ["mixed"] * 15 + ["unique"] * 5 + ["collection_members"] * 8 + ["latest_multi"] * 9 + ["contiguous"] * 8
    else:
        pattern = ["exact"] * 50 + ["absent"] * 35 + ["mixed"] * 35 + ["unique"] * 15 + ["collection_members"] * 20 + ["latest_multi"] * 25 + ["contiguous"] * 20
    assert len(pattern) == count
    rows: list[dict[str, Any]] = []
    for index, kind in enumerate(pattern):
        case_id = f"{name}:{index + 1:03d}"
        rotated = seed_offset + index
        if kind == "exact":
            row = exact_case(case_id, rotated)
        elif kind == "absent":
            row = absent_case(case_id, rotated)
        elif kind == "mixed":
            row = mixed_case(case_id, rotated)
        else:
            row = context_case(case_id, rotated, kind)
        rows.append(row)
    return {
        "campaign_version": "stage3-semantic-context-campaign@1",
        "name": name,
        "locked": True,
        "case_count": len(rows),
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="services/agent-service/quality/stage3_campaigns")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    specs = [
        ("locked_50", 50, 0),
        ("expanded_100", 100, 37),
        ("strong_context_200", 200, 113),
        ("holdout_200", 200, 997),
    ]
    manifest = {"version": "stage3-campaign-manifest@1", "campaigns": []}
    for name, count, seed in specs:
        payload = build_campaign(name, count, seed)
        path = output / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["campaigns"].append({"name": name, "case_count": count, "file": path.name})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
