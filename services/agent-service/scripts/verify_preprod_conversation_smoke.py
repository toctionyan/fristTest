#!/usr/bin/env python3
"""Canonical semantic adapter for the WP08 real-model goal smoke.

The mature bounded-repair/provider-attestation implementation is preserved in
``_verify_preprod_conversation_smoke_legacy.py``. This entry point owns only the
semantic migration boundary: provider planning gets the live Runtime's
capability-independent vocabulary, new-turn declarations require canonical
``requested_outputs``, and the independent oracle certifies canonical output IDs.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_IMPL_PATH = Path(__file__).with_name("_verify_preprod_conversation_smoke_legacy.py")
_SPEC = importlib.util.spec_from_file_location("_wp08_semantic_smoke_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"unable to load semantic smoke implementation: {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_impl)

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

from agent_core.composition import get_module_registry  # noqa: E402

_ORIGINAL_PLANNING_SCHEMAS = _impl.planning_schemas
_ORIGINAL_SYSTEM_MESSAGE = _impl.SystemMessage
_LEGACY_DECLARE_WITH_BOUNDED_REPAIR = _impl._declare_with_bounded_production_repair

# This is an independent case oracle, not a capability vocabulary. Every
# registered ID below is checked against ModuleRegistry before certification.
_CANONICAL_OUTPUT_ORACLE: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "semantic_multi_orders_logistics": {
        "g1": (("order.collection",),),
        "g2": (
            ("shipment.current_status",),
            ("shipment.tracking",),
            ("shipment.current_status", "shipment.tracking"),
        ),
    },
    "semantic_multi_refunds_invoices": {
        "g1": (("refund.status",),),
        "g2": (("invoice.status",),),
    },
    "semantic_query_then_refund_consult": {
        "g1": (("order.collection",),),
        "g2": (("refund.eligibility",),),
    },
    "semantic_query_then_refund_draft": {
        "g1": (("order.collection",),),
        "g2": (("refund.request",),),
    },
    "semantic_cancel_and_refund_branch": {
        "g1": (("order.cancellation",),),
        "g2": (("refund.eligibility",),),
    },
    "semantic_supported_plus_unsupported": {
        "g1": (
            ("shipment.current_status",),
            ("shipment.tracking",),
            ("shipment.current_status", "shipment.tracking"),
        ),
        "g2": (("courier.contact.phone",),),
    },
    "semantic_unsupported_courier_phone": {"g1": (("courier.contact.phone",),)},
    "semantic_refund_arrival_query": {"g1": (("refund.eta",),)},
    "semantic_delete_record_not_cancel": {"g1": (("open",),)},
    "semantic_refund_consult_no_draft": {"g1": (("refund.eligibility",),)},
    "semantic_multi_target_cancel_boundary": {"g1": (("order.cancellation",),)},
    "semantic_conflicting_actions_clarify": {"g1": (("open",),)},
}


def _semantic_vocabulary_snapshot() -> dict[str, object]:
    snapshot = get_module_registry().semantic_vocabulary_snapshot()
    if snapshot.get("availability_exposed") is not False:
        raise RuntimeError("semantic planning vocabulary must not expose capability availability")
    if snapshot.get("tool_names_exposed") is not False:
        raise RuntimeError("semantic planning vocabulary must not expose tool names")
    return snapshot


def _semantic_output_ids() -> tuple[str, ...]:
    return get_module_registry().semantic_output_ids()


def _canonical_planning_schemas(*args: Any, **kwargs: Any):
    if args or "semantic_output_ids" in kwargs:
        return _ORIGINAL_PLANNING_SCHEMAS(*args, **kwargs)
    return _ORIGINAL_PLANNING_SCHEMAS(semantic_output_ids=_semantic_output_ids())


def _canonical_effect_index(_capabilities: Any = None) -> dict[str, object]:
    return _semantic_vocabulary_snapshot()


def _canonical_system_message(*args: Any, **kwargs: Any):
    vocabulary = json.dumps(_semantic_vocabulary_snapshot(), ensure_ascii=False, sort_keys=True)
    content = (
        "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"
        "Goal 只表示用户可独立判断完成与否的业务效果；实现步骤不能单独提升为 Goal。"
        "requested_effect.requested_outputs 是新轮语义身份的唯一权威：每个 output_id 必须从下面的"
        "能力无关 semantic vocabulary 精确选择；若用户语义不在登记词汇中，只能使用保留 output_id=open，"
        "并在 open_description 中按用户原意描述，禁止映射到相近 output、legacy effect、能力或工具。"
        f"semantic vocabulary（不包含能力可用性和工具身份）：{vocabulary}。"
        "domain、operation、object_type 如出现仅是兼容元数据，不能决定新轮语义，也不能替代 requested_outputs。"
        "能力词汇中没有精确身份的分支也必须保留；不能吞掉不支持分支，也不能用相似能力代替。"
        "evidence_span 必须来自用户当前轮原话；多目标时每个 span 只覆盖自己的局部连续原文。"
        "同轮后续目标依赖前一目标真实结果时用 depends_on；普通共享范围不制造依赖。"
        "reference_expression 只用于更早轮次已展示的历史结果，不能引用本轮未来结果。"
    )
    kwargs.pop("content", None)
    return _ORIGINAL_SYSTEM_MESSAGE(content=content, **kwargs)


def _production_goal_declaration_evaluation(
    *,
    user_text: str,
    goals: list[dict[str, Any]],
    inventory_authority: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    state: dict[str, Any] = {"current_user_input": user_text}
    if isinstance(inventory_authority, dict):
        state["current_turn_plan"] = {
            "goal_granularity_inventory_authority": dict(inventory_authority),
        }
    return validate_goal_declaration(
        state=state,
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
        require_canonical_output_identity=True,
    )


def _requested_output_identity(row: dict[str, Any]) -> tuple[str, ...]:
    effect = row.get("requested_effect") if isinstance(row.get("requested_effect"), dict) else {}
    raw_outputs = effect.get("requested_outputs") if isinstance(effect, dict) else []
    output_ids: list[str] = []
    for item in list(raw_outputs or []):
        output_id = (
            str(item.get("output_id") or "").strip()
            if isinstance(item, dict)
            else str(item or "").strip()
        )
        if output_id:
            output_ids.append(output_id)
    return tuple(sorted(dict.fromkeys(output_ids)))


def _oracle_output_sets(*, case_id: str, expected: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    explicit = expected.get("accepted_output_sets")
    if isinstance(explicit, list):
        values = tuple(
            tuple(sorted(dict.fromkeys(str(value).strip() for value in row if str(value).strip())))
            for row in explicit
            if isinstance(row, list)
        )
    else:
        values = _CANONICAL_OUTPUT_ORACLE.get(case_id, {}).get(str(expected.get("oracle_id") or ""), ())
    if not values:
        raise RuntimeError(f"{case_id}: canonical semantic oracle missing for {expected.get('oracle_id')!r}")
    registered = set(_semantic_output_ids())
    unknown = sorted({value for row in values for value in row if value != "open" and value not in registered})
    if unknown:
        raise RuntimeError(f"{case_id}: canonical oracle references unregistered outputs: {unknown}")
    if any(not row for row in values):
        raise RuntimeError(f"{case_id}: canonical oracle output set must not be empty")
    return values


def _match_oracle(
    *,
    case_id: str,
    oracle: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    registered_effect_identities: set[str] | None = None,
) -> None:
    del registered_effect_identities
    if len(goals) != len(oracle):
        raise RuntimeError(f"{case_id}: goal count mismatch, expected {len(oracle)}, got {len(goals)}")
    goal_ids = [str(row.get("goal_id") or "") for row in goals]
    duplicate_ids = sorted(goal_id for goal_id in set(goal_ids) if goal_id and goal_ids.count(goal_id) > 1)
    if duplicate_ids:
        raise RuntimeError(f"{case_id}: duplicate goal_id values are forbidden: {duplicate_ids}")

    unmatched = list(goals)
    oracle_to_goal: dict[str, str] = {}
    for expected in oracle:
        evidence = str(expected.get("evidence_span") or "")
        required = bool(expected.get("required", True))
        accepted_outputs = _oracle_output_sets(case_id=case_id, expected=expected)

        def candidate_matches(row: dict[str, Any], *, containment: bool) -> bool:
            span_ok = (
                _span_matches_oracle(expected=evidence, actual=row.get("evidence_span"))
                if containment
                else str(row.get("evidence_span") or "") == evidence
            )
            return (
                span_ok
                and bool(row.get("required", True)) == required
                and _requested_output_identity(row) in accepted_outputs
            )

        exact = [row for row in unmatched if candidate_matches(row, containment=False)]
        matches = exact or [row for row in unmatched if candidate_matches(row, containment=True)]
        if len(matches) != 1:
            candidates = [
                {
                    "goal_id": str(row.get("goal_id") or ""),
                    "evidence_span": str(row.get("evidence_span") or ""),
                    "requested_outputs": list(_requested_output_identity(row)),
                }
                for row in unmatched
            ]
            raise RuntimeError(
                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "
                f"accepted_outputs={accepted_outputs!r}, candidates={candidates!r}"
            )
        matched = matches[0]
        oracle_id = str(expected.get("oracle_id") or "")
        goal_id = str(matched.get("goal_id") or "")
        if not oracle_id or not goal_id:
            raise RuntimeError(f"{case_id}: oracle and model goals must declare stable IDs")
        oracle_to_goal[oracle_id] = goal_id
        unmatched.remove(matched)

    if unmatched:
        raise RuntimeError(f"{case_id}: model emitted undeclared extra goals")
    goals_by_id = {str(row.get("goal_id") or ""): row for row in goals}
    for expected in oracle:
        expected_dependencies = {
            oracle_to_goal.get(str(value), "<unmatched>")
            for value in expected.get("depends_on") or []
        }
        goal = goals_by_id[oracle_to_goal[str(expected["oracle_id"])]]
        actual_dependencies = {str(value) for value in goal.get("depends_on") or []}
        if actual_dependencies != expected_dependencies:
            raise RuntimeError(
                f"{case_id}: goal dependency mismatch for oracle {expected['oracle_id']}; "
                f"expected_dependencies={sorted(expected_dependencies)!r}; "
                f"actual_dependencies={sorted(actual_dependencies)!r}"
            )


def _sync_direct_repair_surface() -> None:
    # Legacy function objects resolve globals from their implementation module.
    # Mirror monkeypatched adapter dependencies before direct focused-test calls.
    for name in (
        "invoke_model",
        "tool_calls",
        "attest_real_model_metadata",
        "_validate_with_production_goal_contract",
    ):
        setattr(_impl, name, globals()[name])


def _declare_with_bounded_production_repair(**kwargs: Any):
    _sync_direct_repair_surface()
    return _LEGACY_DECLARE_WITH_BOUNDED_REPAIR(**kwargs)


def _sync_impl() -> None:
    for name in (
        "CATALOG",
        "get_model",
        "get_model_profile",
        "model_call_scope",
        "invoke_model",
        "tool_calls",
        "resolve_real_model_identity",
        "attest_real_model_metadata",
        "certification_session_evidence",
        "classify_model_failure",
        "is_environmental_model_failure_category",
        "resolve_verifier_mode",
        "get_runtime_registry",
        "validate_goal_declaration",
        "_validate_with_production_goal_contract",
        "_match_oracle",
    ):
        if name in globals():
            setattr(_impl, name, globals()[name])
    _impl.planning_schemas = _canonical_planning_schemas
    _impl.capability_effect_index = _canonical_effect_index
    _impl.SystemMessage = _canonical_system_message
    _impl._production_goal_declaration_evaluation = _production_goal_declaration_evaluation


def main() -> int:
    _sync_impl()
    return _impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
