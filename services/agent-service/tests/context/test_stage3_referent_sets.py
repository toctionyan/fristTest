from __future__ import annotations

from agent_core.composition import get_runtime_registry
from agent_core.context import ContextBundleBuilder
from agent_core.context.referent_sets import build_visible_referent_sets
from agent_core.context.visible_result_refs import mark_visible_result_refs
from agent_core.ledger import artifact_entry, result_entry
from agent_core.runtime.capability_gate import issue_execution_permit


SCOPE = {"tenant_id": "tenant-stage3", "user_id": "u-stage3", "thread_id": "thread-stage3"}


class Transactions:
    def list_drafts_for_scope(self, **_kwargs):
        return []


class ExactVerifier:
    def verify(self, **kwargs):
        return {
            "verdict": "exact",
            "evidence_span": str(kwargs.get("user_text") or ""),
            "reason_code": "stage3_referent_set_test",
        }


def _state(*, turn: int, ledger: list[dict], text: str = "它的物流呢") -> dict:
    return {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": turn,
        "artifact_ledger": list(ledger),
        "tool_trace": [],
        "semantic_capability_verifier": ExactVerifier(),
    }


def _same_turn_independent_results() -> tuple[list[dict], dict, dict]:
    earphone = artifact_entry(
        resource_type="order",
        resource_id="10001",
        label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001"},
        scope=SCOPE,
        turn=2,
        source="test",
        handle="artifact:order:10001",
    )
    mouse = artifact_entry(
        resource_type="order",
        resource_id="10003",
        label="无线鼠标（订单 10003）",
        facts={"order_id": "10003"},
        scope=SCOPE,
        turn=2,
        source="test",
        handle="artifact:order:10003",
    )
    first = result_entry(
        capability="ecommerce.order.logistics",
        member_handles=[earphone["handle"]],
        labels=[earphone["label"]],
        scope=SCOPE,
        turn=2,
        source_target={"mode": "entity_match", "attribute_span": "蓝牙耳机"},
        handle="result:earphone-logistics",
    )
    second = result_entry(
        capability="ecommerce.order.logistics",
        member_handles=[mouse["handle"]],
        labels=[mouse["label"]],
        scope=SCOPE,
        turn=2,
        source_target={"mode": "entity_match", "attribute_span": "无线鼠标"},
        handle="result:mouse-logistics",
    )
    base = [earphone, mouse, first, second]
    released = mark_visible_result_refs(
        base,
        state=_state(turn=2, ledger=base),
        evidence_handles=[first["handle"], second["handle"]],
    )
    return released, first, second


def _single_collection() -> tuple[list[dict], dict]:
    first = artifact_entry(
        resource_type="order",
        resource_id="10001",
        label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001"},
        scope=SCOPE,
        turn=1,
        source="test",
        handle="artifact:order:10001",
    )
    second = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"order_id": "10002"},
        scope=SCOPE,
        turn=1,
        source="test",
        handle="artifact:order:10002",
    )
    result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[first["handle"], second["handle"]],
        labels=[first["label"], second["label"]],
        scope=SCOPE,
        turn=1,
        source_target={"mode": "all_orders"},
        handle="result:two-orders",
    )
    base = [first, second, result]
    released = mark_visible_result_refs(
        base,
        state=_state(turn=1, ledger=base),
        evidence_handles=[result["handle"]],
    )
    return released, result


def _logistics_args(*, target: dict, text: str, context_binding: dict | None = None) -> dict:
    args = {
        "target": target,
        "expected_shape": "collection",
        "reference_span": text,
        "query": {},
    }
    if context_binding is not None:
        args["context_binding"] = context_binding
    return args


def test_context_bundle_projects_two_independent_latest_results_as_ambiguous_set() -> None:
    ledger, first, second = _same_turn_independent_results()
    bundle = ContextBundleBuilder(transactions=Transactions()).build(
        _state(turn=3, ledger=ledger)
    )

    projection = bundle["visible_referent_sets"]
    latest = projection["latest_visible_turn_set"]
    assert latest["result_count"] == 2
    assert latest["result_refs"] == [second["handle"], first["handle"]]
    assert latest["singular_reference_is_ambiguous"] is True
    assert latest["dispatchable"] is False
    assert projection["runtime_auto_select_target"] is False


def test_one_visible_collection_is_one_result_but_singular_member_reference_is_ambiguous() -> None:
    ledger, result = _single_collection()
    bundle = ContextBundleBuilder(transactions=Transactions()).build(
        _state(turn=2, ledger=ledger)
    )

    latest = bundle["visible_referent_sets"]["latest_visible_turn_set"]
    assert latest["result_refs"] == [result["handle"]]
    assert latest["result_count"] == 1
    assert latest["member_count"] == 2
    assert latest["singular_reference_is_ambiguous"] is True


def test_recent_referent_groups_are_only_contiguous_prefixes() -> None:
    refs = [
        {
            "result_ref": f"result:{rank}",
            "member_handles": [f"artifact:{rank}"],
            "member_labels": [f"对象{rank}"],
            "source_turn": 10 - rank,
            "discourse_recency_rank": rank,
        }
        for rank in range(1, 5)
    ]

    projection = build_visible_referent_sets(refs, max_recent_group_size=4)
    groups = projection["recent_contiguous_groups"]
    assert [row["discourse_recency_ranks"] for row in groups] == [
        [1, 2],
        [1, 2, 3],
        [1, 2, 3, 4],
    ]
    assert all(row["contiguous_from_latest"] for row in groups)
    assert all(row["dispatchable"] is False for row in groups)


def test_bare_singular_selection_is_rejected_when_latest_turn_has_two_independent_results() -> None:
    ledger, first, _second = _same_turn_independent_results()
    state = _state(turn=3, ledger=ledger, text="它的物流呢")

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args=_logistics_args(
            target={"mode": "collection", "left_handle": first["handle"]},
            text="它",
        ),
        effect_id="turn-plan:3:effect:ambiguous",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "VISIBLE_RESULT_REF_INVALID"
    errors = decision.match_proof["visible_result_reference"]["errors"]
    assert "latest_visible_scope_ambiguous_requires_explicit_return_or_group" in errors


def test_explicit_literal_return_can_select_one_exact_latest_result() -> None:
    ledger, first, _second = _same_turn_independent_results()
    state = _state(turn=3, ledger=ledger, text="查蓝牙耳机物流")

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args=_logistics_args(
            target={"mode": "collection", "left_handle": first["handle"]},
            text="蓝牙耳机",
            context_binding={
                "reference_kind": "explicit_return",
                "source_span": "蓝牙耳机",
            },
        ),
        effect_id="turn-plan:3:effect:explicit-return",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True, decision.match_proof
    binding = decision.match_proof["visible_result_reference"]["discourse_binding"]
    assert binding["reference_kind"] == "explicit_return"
    assert binding["latest_visible_scope_ambiguous"] is True


def test_explicit_group_rejects_group_size_that_does_not_match_selected_refs() -> None:
    ledger, first, second = _same_turn_independent_results()
    state = _state(turn=3, ledger=ledger, text="刚才两个都查物流")

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args=_logistics_args(
            target={
                "mode": "set_operation",
                "operator": "union",
                "left_handle": second["handle"],
                "right_handle": first["handle"],
            },
            text="刚才两个",
            context_binding={
                "reference_kind": "explicit_group_reference",
                "source_span": "刚才两个",
                "group_size": 3,
            },
        ),
        effect_id="turn-plan:3:effect:bad-group",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "VISIBLE_RESULT_REF_INVALID"
    errors = decision.match_proof["visible_result_reference"]["errors"]
    assert "explicit_group_reference_group_size_mismatch" in errors


def test_explicit_group_accepts_exact_recent_contiguous_pair() -> None:
    ledger, first, second = _same_turn_independent_results()
    state = _state(turn=3, ledger=ledger, text="刚才两个都查物流")

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args=_logistics_args(
            target={
                "mode": "set_operation",
                "operator": "union",
                "left_handle": second["handle"],
                "right_handle": first["handle"],
            },
            text="刚才两个",
            context_binding={
                "reference_kind": "explicit_group_reference",
                "source_span": "刚才两个",
                "group_size": 2,
            },
        ),
        effect_id="turn-plan:3:effect:good-group",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True, decision.match_proof
    binding = decision.match_proof["visible_result_reference"]["discourse_binding"]
    assert binding["explicit_group_binding_complete"] is True
    assert binding["group_size"] == 2
