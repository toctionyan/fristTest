#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
path = ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
text = path.read_text(encoding="utf-8")
start_marker = "def test_protected_goal_smoke_accepts_schema_compliant_goals_without_expected_tools() -> None:\n"
end_marker = "\ndef test_process_group_cleanup_handles_permission_error_after_parent_exit(\n"
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0 or end <= start:
    raise SystemExit("semantic_oracle_fixture_markers_not_found")
replacement = '''def test_protected_goal_smoke_accepts_schema_compliant_goals_without_expected_tools() -> None:
    smoke = _load_script("../services/agent-service/scripts/verify_preprod_conversation_smoke.py")
    smoke._match_oracle(
        case_id="schema-compliant",
        oracle=[
            {
                "oracle_id": "g1",
                "evidence_span": "查订单",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "order",
                    "operation": "list",
                    "object_type": "order",
                },
                "required_tools": ["list_orders"],
            }
        ],
        goals=[
            {
                "goal_id": "g1",
                "description": "查询订单",
                "evidence_span": "查订单",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "order",
                    "operation": "list",
                    "object_type": "order",
                },
            }
        ],
    )


def test_protected_goal_smoke_accepts_literal_span_with_surrounding_user_wording() -> None:
    smoke = _load_script("../services/agent-service/scripts/verify_preprod_conversation_smoke.py")
    smoke._match_oracle(
        case_id="literal-span-extension",
        oracle=[
            {
                "oracle_id": "refund-history",
                "evidence_span": "鼠标订单的退款记录",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "refund",
                    "operation": "query_status",
                    "object_type": "refund",
                },
            }
        ],
        goals=[
            {
                "goal_id": "model-goal",
                "evidence_span": "查下鼠标订单的退款记录",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "refund",
                    "operation": "query_status",
                    "object_type": "refund",
                },
            }
        ],
    )


def test_protected_goal_smoke_rejects_fuzzy_or_ambiguous_span_matching() -> None:
    smoke = _load_script("../services/agent-service/scripts/verify_preprod_conversation_smoke.py")
    with pytest.raises(RuntimeError, match="no unique model goal"):
        smoke._match_oracle(
            case_id="fuzzy-is-forbidden",
            oracle=[
                {
                    "oracle_id": "refund-history",
                    "evidence_span": "鼠标订单的退款记录",
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {
                        "domain": "refund",
                        "operation": "query_status",
                        "object_type": "refund",
                    },
                }
            ],
            goals=[
                {
                    "goal_id": "model-goal",
                    "evidence_span": "鼠标订单的物流记录",
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {
                        "domain": "refund",
                        "operation": "query_status",
                        "object_type": "refund",
                    },
                }
            ],
        )
    ambiguous_effect = {
        "domain": "order",
        "operation": "update",
        "object_type": "order",
    }
    with pytest.raises(RuntimeError, match="no unique model goal"):
        smoke._match_oracle(
            case_id="ambiguous-containment",
            oracle=[
                {
                    "oracle_id": "cancel",
                    "evidence_span": "取消",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": ambiguous_effect,
                },
                {
                    "oracle_id": "ship",
                    "evidence_span": "继续发货",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": ambiguous_effect,
                },
            ],
            goals=[
                {
                    "goal_id": "combined-a",
                    "evidence_span": "取消，然后继续发货",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": ambiguous_effect,
                },
                {
                    "goal_id": "combined-b",
                    "evidence_span": "取消并继续发货",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": ambiguous_effect,
                },
            ],
        )

'''
path.write_text(text[:start] + replacement + text[end + 1 :], encoding="utf-8")
print("semantic oracle governance fixtures upgraded to requested_effect authority")
