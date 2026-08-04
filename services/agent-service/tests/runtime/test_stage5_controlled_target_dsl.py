from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agent_core.ledger import artifact_entry, result_entry
from agent_modules.ecommerce.shared import context as context_module
from agent_modules.ecommerce.target_dsl import TargetDslError, apply_pipeline


ROWS = [
    {
        "order_id": "10001",
        "product_name": "蓝牙耳机",
        "status": "运输中",
        "amount": 199.0,
        "created_at": "2026-06-28T10:00:00+00:00",
        "version": 1,
    },
    {
        "order_id": "10002",
        "product_name": "机械键盘",
        "status": "已签收",
        "amount": 399.0,
        "created_at": "2026-07-20T10:00:00+00:00",
        "version": 1,
    },
    {
        "order_id": "10003",
        "product_name": "无线鼠标",
        "status": "待发货",
        "amount": 99.0,
        "created_at": "2026-07-30T10:00:00+00:00",
        "version": 1,
    },
    {
        "order_id": "10004",
        "product_name": "定制马克杯",
        "status": "已签收",
        "amount": 59.0,
        "created_at": "2026-07-08T10:00:00+00:00",
        "version": 1,
    },
]


def _state(text: str, *, ledger: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "current_tenant_id": "default",
        "current_user_id": "u001",
        "current_thread_id": "thread-stage5",
        "current_user_input": text,
        "turn_index": 5,
        "artifact_ledger": list(ledger or []),
        "context_health": {"transactions": "ok"},
    }


def _stub_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module,
        "_list_orders",
        lambda state: ({"ok": True, "rows": [dict(row) for row in ROWS]}, []),
    )


def test_root_pipeline_composes_status_amount_sort_and_take(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("已签收且金额不低于200元的订单里最贵的1个"),
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "status",
                        "comparison": "eq",
                        "value": "已签收",
                        "source_span": "已签收",
                        "value_span": "已签收",
                    },
                },
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "gte",
                        "value": 200,
                        "source_span": "金额不低于200元",
                        "value_span": "200",
                    },
                },
                {"op": "sort", "field": "amount", "direction": "desc", "source_span": "最贵"},
                {"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"},
            ],
        },
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="read",
    )

    assert error is None
    assert target is not None
    assert [row["resource_id"] for row in target["members"]] == ["10002"]
    assert [row["op"] for row in target["pipeline_proof"]] == ["filter", "filter", "sort", "take"]
    assert target["match_proof"]["basis"] == "controlled_target_pipeline"
    assert target["match_proof"]["verified_for_write"] is True


def test_amount_value_must_match_literal_user_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("金额不低于200元"),
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "gte",
                        "value": 999,
                        "source_span": "金额不低于200元",
                        "value_span": "200",
                    },
                }
            ],
        },
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="read",
    )

    assert target is None
    assert error is not None
    assert error["code"] == "TARGET_DSL_VALUE_EVIDENCE_MISMATCH"


def test_pipeline_supports_absolute_datetime_between(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("查询2026-07-01到2026-07-31创建的订单"),
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "created_at",
                        "comparison": "between",
                        "lower": "2026-07-01",
                        "upper": "2026-07-31",
                        "source_span": "2026-07-01到2026-07-31",
                        "lower_span": "2026-07-01",
                        "upper_span": "2026-07-31",
                    },
                }
            ],
        },
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="read",
    )

    assert error is None
    assert target is not None
    assert [row["resource_id"] for row in target["members"]] == ["10002", "10003", "10004"]


def test_relative_time_filter_uses_runtime_clock_and_literal_day_count() -> None:
    records = [(str(row["order_id"]), dict(row)) for row in ROWS]
    filtered, proof = apply_pipeline(
        records,
        [
            {
                "op": "filter",
                "predicate": {
                    "field": "created_at",
                    "comparison": "within_last_days",
                    "days": 7,
                    "source_span": "最近7天",
                    "value_span": "7",
                },
            }
        ],
        user_input="最近7天的订单",
        now=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
    )

    assert [handle for handle, _ in filtered] == ["10003"]
    assert proof[0]["after_count"] == 1


def test_relative_time_filter_rejects_invented_day_count() -> None:
    with pytest.raises(TargetDslError) as exc:
        apply_pipeline(
            [(str(row["order_id"]), dict(row)) for row in ROWS],
            [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "created_at",
                        "comparison": "within_last_days",
                        "days": 30,
                        "source_span": "最近7天",
                        "value_span": "7",
                    },
                }
            ],
            user_input="最近7天的订单",
            now=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
        )
    assert exc.value.code == "TARGET_DSL_VALUE_EVIDENCE_MISMATCH"


def test_text_contains_remains_read_candidate_but_cannot_be_write_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    pipeline = {
        "mode": "pipeline",
        "source_kind": "all_orders",
        "steps": [
            {
                "op": "filter",
                "predicate": {
                    "field": "product_name",
                    "comparison": "contains",
                    "value": "机械",
                    "source_span": "包含机械",
                    "value_span": "机械",
                },
            }
        ],
    }

    read_target, read_error = context_module._target_members(
        _state("查包含机械的商品"),
        pipeline,
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="read",
    )
    assert read_error is None
    assert read_target is not None
    assert [row["resource_id"] for row in read_target["members"]] == ["10002"]
    assert read_target["match_proof"]["verified_for_write"] is False

    write_target, write_error = context_module._target_members(
        _state("把包含机械的商品退掉"),
        pipeline,
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="write",
    )
    assert write_target is None
    assert write_error is not None
    assert write_error["code"] == "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE"
    assert write_error["candidates"] == [{"order_id": "10002", "label": "机械键盘"}]


def test_exact_product_name_pipeline_can_form_write_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("把商品名等于机械键盘的订单退掉"),
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "product_name",
                        "comparison": "eq",
                        "value": "机械键盘",
                        "source_span": "商品名等于机械键盘",
                        "value_span": "机械键盘",
                    },
                }
            ],
        },
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="write",
    )

    assert error is None
    assert target is not None
    assert [row["resource_id"] for row in target["members"]] == ["10002"]
    assert target["match_proof"]["verified_for_write"] is True


def test_pipeline_can_start_from_verified_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-stage5"}
    artifacts = [
        artifact_entry(
            resource_type="order",
            resource_id=str(row["order_id"]),
            label=f"{row['product_name']}（订单 {row['order_id']}）",
            facts=dict(row),
            scope=scope,
            turn=4,
            source="test",
            handle=f"artifact:order:{row['order_id']}",
        )
        for row in ROWS[:3]
    ]
    result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[row["handle"] for row in artifacts],
        labels=[row["label"] for row in artifacts],
        scope=scope,
        turn=4,
        source_target={"mode": "all_orders"},
        handle="result:visible-orders",
    )
    rows_by_handle = {f"artifact:order:{row['order_id']}": dict(row) for row in ROWS[:3]}

    def fresh(_state, handle: str, *, source: str):
        return dict(rows_by_handle[handle]), None, [], handle

    monkeypatch.setattr(context_module, "_fresh_order_from_handle", fresh)
    target, error = context_module._target_members(
        _state("刚才那些订单里金额低于200元的", ledger=[*artifacts, result]),
        {
            "mode": "pipeline",
            "source_kind": "collection",
            "source_handle": "result:visible-orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "lt",
                        "value": 200,
                        "source_span": "金额低于200元",
                        "value_span": "200",
                    },
                }
            ],
        },
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="read",
    )

    assert error is None
    assert target is not None
    assert [row["resource_id"] for row in target["members"]] == ["10001", "10003"]


def test_pipeline_between_rejects_reversed_range(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("金额从500到200元"),
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "between",
                        "lower": 500,
                        "upper": 200,
                        "source_span": "金额从500到200元",
                        "lower_span": "500",
                        "upper_span": "200",
                    },
                }
            ],
        },
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="read",
    )

    assert target is None
    assert error is not None
    assert error["code"] == "TARGET_DSL_INVALID_RANGE"


def test_empty_source_still_validates_predicate_evidence() -> None:
    with pytest.raises(TargetDslError) as exc:
        apply_pipeline(
            [],
            [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "gte",
                        "value": 999,
                        "source_span": "金额不低于200元",
                        "value_span": "200",
                    },
                }
            ],
            user_input="金额不低于200元",
        )
    assert exc.value.code == "TARGET_DSL_VALUE_EVIDENCE_MISMATCH"


def test_sort_field_and_direction_must_match_user_evidence() -> None:
    with pytest.raises(TargetDslError) as exc:
        apply_pipeline(
            [(str(row["order_id"]), dict(row)) for row in ROWS],
            [{"op": "sort", "field": "created_at", "direction": "asc", "source_span": "最贵"}],
            user_input="最贵的订单",
        )
    assert exc.value.code == "TARGET_DSL_SORT_EVIDENCE_MISMATCH"


def test_chinese_quantity_evidence_is_supported_for_bounded_take() -> None:
    rows, proof = apply_pipeline(
        [(str(row["order_id"]), dict(row)) for row in ROWS],
        [{"op": "take", "limit": 2, "source_span": "前两个", "value_span": "两个"}],
        user_input="前两个订单",
    )
    assert [handle for handle, _ in rows] == ["10001", "10002"]
    assert proof[0]["limit"] == 2


def test_sort_keeps_missing_values_at_the_end_in_both_directions() -> None:
    records = [
        ("a", {"order_id": "a", "amount": 10}),
        ("missing", {"order_id": "missing"}),
        ("b", {"order_id": "b", "amount": 20}),
    ]
    desc, _ = apply_pipeline(
        records,
        [{"op": "sort", "field": "amount", "direction": "desc", "source_span": "金额从高到低"}],
        user_input="金额从高到低",
    )
    asc, _ = apply_pipeline(
        records,
        [{"op": "sort", "field": "amount", "direction": "asc", "source_span": "金额从低到高"}],
        user_input="金额从低到高",
    )
    assert [handle for handle, _ in desc] == ["b", "a", "missing"]
    assert [handle for handle, _ in asc] == ["a", "b", "missing"]


def test_runtime_rejects_empty_pipeline_even_if_called_below_schema_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("查订单"),
        {"mode": "pipeline", "source_kind": "all_orders", "steps": []},
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="read",
    )
    assert target is None
    assert error is not None
    assert error["code"] == "TARGET_DSL_PIPELINE_INVALID"


def test_capability_gate_classifies_pipeline_cardinality() -> None:
    from agent_core.runtime.capability_gate import _target_cardinality_hint

    base = {
        "target": {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "gte",
                        "value": 200,
                        "source_span": "200以上",
                        "value_span": "200",
                    },
                }
            ],
        }
    }
    assert _target_cardinality_hint(base) == "collection"
    assert _target_cardinality_hint({
        "target": {**base["target"], "steps": [*base["target"]["steps"], {"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"}]}
    }) == "single"
    assert _target_cardinality_hint({
        "target": {**base["target"], "steps": [*base["target"]["steps"], {"op": "ordinal", "position": 2, "source_span": "第2个", "value_span": "2"}]}
    }) == "single"


def test_capability_gate_audits_nested_pipeline_spans() -> None:
    from agent_core.runtime.capability_gate import _parameterization_proof

    args = {
        "target": {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "between",
                        "lower": 100,
                        "upper": 300,
                        "source_span": "金额从100到300元",
                        "lower_span": "100",
                        "upper_span": "300",
                    },
                },
                {"op": "sort", "field": "amount", "direction": "desc", "source_span": "金额从高到低"},
            ],
        }
    }
    proof = _parameterization_proof(_state("金额从100到300元，按金额从高到低"), args)
    assert proof["parameterization_complete"] is True
    assert not proof["errors"]
    paths = {row["parameter_path"] for row in proof["bindings"]}
    assert "target.steps[0].predicate.lower_span" in paths
    assert "target.steps[1].source_span" in paths

    rejected = _parameterization_proof(_state("金额从100到300元"), args)
    assert rejected["parameterization_complete"] is False
    assert "target_pipeline_evidence_not_current_turn:target.steps[1].source_span" in rejected["errors"]


def _visible_collection_state() -> tuple[dict[str, Any], dict[str, Any]]:
    from agent_core.context.visible_result_refs import mark_visible_result_refs

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-stage5"}
    artifacts = [
        artifact_entry(
            resource_type="order",
            resource_id=str(row["order_id"]),
            label=f"{row['product_name']}（订单 {row['order_id']}）",
            facts=dict(row),
            scope=scope,
            turn=4,
            source="test",
            handle=f"artifact:order:{row['order_id']}",
        )
        for row in ROWS[:2]
    ]
    result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[row["handle"] for row in artifacts],
        labels=[row["label"] for row in artifacts],
        scope=scope,
        turn=4,
        source_target={"mode": "all_orders"},
        handle="result:stage5-visible",
    )
    ledger = mark_visible_result_refs(
        [*artifacts, result],
        state=_state("先查订单", ledger=[*artifacts, result]),
        evidence_handles=[result["handle"]],
    )
    return _state("刚才那些金额200以上的", ledger=ledger), result


def test_capability_gate_validates_pipeline_collection_source_ref() -> None:
    from agent_core.runtime.capability_gate import _visible_reference_proof

    state, result = _visible_collection_state()
    args = {
        "target": {
            "mode": "pipeline",
            "source_kind": "collection",
            "source_handle": result["handle"],
            "steps": [
                {
                    "op": "filter",
                    "predicate": {
                        "field": "amount",
                        "comparison": "gte",
                        "value": 200,
                        "source_span": "金额200以上",
                        "value_span": "200",
                    },
                }
            ],
        }
    }
    proof = _visible_reference_proof(state, args)
    assert proof["complete"] is True
    assert proof["checks"][0]["parameter_path"] == "target.source_handle"
    assert proof["checks"][0]["valid"] is True

    invalid = _visible_reference_proof(state, {
        "target": {**args["target"], "source_handle": "result:not-visible"}
    })
    assert invalid["complete"] is False
    assert invalid["errors"]


def test_pipeline_rerank_guard_preserves_parent_collection_rule() -> None:
    from agent_core.runtime.capability_gate import _derived_collection_scope_proof

    selected_ref = {
        "result_ref": "result:derived-singleton",
        "member_handles": ["artifact:order:10002"],
        "lineage_result_refs": ["result:parent-orders"],
        "source_operation": {
            "mode": "pipeline",
            "source_kind": "collection",
            "steps": [
                {"op": "sort", "field": "amount", "direction": "desc", "source_span": "最贵"},
                {"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"},
            ],
        },
    }
    visible = {
        "complete": True,
        "checks": [{"result_ref": "result:derived-singleton", "validated_ref": selected_ref}],
    }
    proof = _derived_collection_scope_proof(
        {
            "target": {
                "mode": "pipeline",
                "source_kind": "collection",
                "source_handle": "result:derived-singleton",
                "steps": [{"op": "sort", "field": "amount", "direction": "asc", "source_span": "最便宜"}],
            }
        },
        visible,
    )
    assert proof["complete"] is False
    assert proof["errors"] == ["derived_singleton_rerank_requires_lineage_parent"]


def test_workflow_batch_detection_understands_pipeline_final_shape() -> None:
    from agent_core.lifecycle.workflow_runtime import _has_collection_target

    collection_call = [{
        "args": {
            "target": {
                "mode": "pipeline",
                "source_kind": "all_orders",
                "steps": [{"op": "filter", "predicate": {"field": "status", "comparison": "eq", "value": "已签收", "source_span": "已签收", "value_span": "已签收"}}],
            }
        }
    }]
    singleton_call = [{
        "args": {
            "target": {
                "mode": "pipeline",
                "source_kind": "all_orders",
                "steps": [{"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"}],
            }
        }
    }]
    assert _has_collection_target(collection_call) is True
    assert _has_collection_target(singleton_call) is False


def test_visible_result_projection_retains_pipeline_lineage_and_steps() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs, visible_result_refs_from_ledger

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-stage5"}
    artifact = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts=dict(ROWS[1]),
        scope=scope,
        turn=5,
        source="test",
        handle="artifact:order:10002",
    )
    target = {
        "mode": "pipeline",
        "source_kind": "collection",
        "source_handle": "result:parent-orders",
        "steps": [
            {"op": "sort", "field": "amount", "direction": "desc", "source_span": "最贵"},
            {"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"},
        ],
    }
    parent = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[artifact["handle"]],
        labels=[artifact["label"]],
        scope=scope,
        turn=4,
        source_target={"mode": "all_orders"},
        handle="result:parent-orders",
    )
    result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[artifact["handle"]],
        labels=[artifact["label"]],
        scope=scope,
        turn=5,
        source_target={"mode": "pipeline", "target": target},
        handle="result:pipeline-singleton",
    )
    ledger = mark_visible_result_refs(
        [artifact, parent, result],
        state=_state("最贵的1个", ledger=[artifact, parent, result]),
        evidence_handles=[result["handle"]],
    )
    projected = visible_result_refs_from_ledger(ledger, state=_state("继续", ledger=ledger), limit=5)
    row = next(item for item in projected if item["result_ref"] == result["handle"])
    assert row["lineage_result_refs"] == ["result:parent-orders"]
    assert row["source_operation"]["mode"] == "pipeline"
    assert row["source_operation"]["steps"][0]["op"] == "sort"
