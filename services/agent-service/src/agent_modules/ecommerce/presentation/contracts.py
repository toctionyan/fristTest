"""E-commerce-owned canonical structured presentation contracts.

Capability code returns verified business facts.  These projectors are the
single allowed boundary that turns those facts into customer-visible blocks.
Neither API/SSE nor browser code may rename their domain semantics afterwards.
"""

from __future__ import annotations

import json
from collections import Counter
from importlib.resources import files
from typing import Any

from agent_core.presentation.contracts.governance import PresentationContractRegistry, controlled_violation_block
from agent_core.presentation.contracts.renderer_registry import RendererRegistration


_CONTRACT_FILES = (
    "ecommerce_order_list_v1.json",
    "ecommerce_logistics_overview_v1.json",
    "ecommerce_business_status_list_v1.json",
    "ecommerce_next_actions_v1.json",
    "ecommerce_eligibility_decision_v1.json",
    "ecommerce_advisory_v1.json",
)


def _load_manifest(filename: str) -> dict[str, Any]:
    payload = files("agent_modules.ecommerce.presentation").joinpath(filename).read_text(encoding="utf-8")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"presentation contract {filename} must be an object")
    return value


_MANIFESTS = tuple(_load_manifest(filename) for filename in _CONTRACT_FILES)
_MANIFEST_BY_ID = {str(manifest["contract_id"]): manifest for manifest in _MANIFESTS}
ORDER_LIST_CONTRACT = _MANIFEST_BY_ID["commerce.order_list@1"]
LOGISTICS_OVERVIEW_CONTRACT = _MANIFEST_BY_ID["commerce.logistics_overview@1"]
BUSINESS_STATUS_LIST_CONTRACT = _MANIFEST_BY_ID["commerce.business_status_list@1"]
NEXT_ACTIONS_CONTRACT = _MANIFEST_BY_ID["commerce.next_actions@1"]
ELIGIBILITY_DECISION_CONTRACT = _MANIFEST_BY_ID["commerce.eligibility_decision@1"]
ADVISORY_CONTRACT = _MANIFEST_BY_ID["commerce.advisory@1"]
ORDER_LIST_CONTRACT_ID = str(ORDER_LIST_CONTRACT["contract_id"])
ORDER_LIST_CONTRACT_VERSION = int(ORDER_LIST_CONTRACT["version"])


def presentation_contract_manifests() -> tuple[dict[str, Any], ...]:
    return tuple(dict(manifest) for manifest in _MANIFESTS)


def presentation_renderer_registrations() -> tuple[RendererRegistration, ...]:
    rows: list[RendererRegistration] = []
    for manifest in _MANIFESTS:
        contract_id = str(manifest["contract_id"])
        for channel, renderer_id in dict(manifest.get("renderer") or {}).items():
            rows.append(RendererRegistration(contract_id=contract_id, channel=str(channel), renderer_id=str(renderer_id)))
    return tuple(rows)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional(value: Any) -> str | int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    text = _text(value)
    return text or None


def _coverage(*, mode: str, source_population: str, count: int = 0) -> dict[str, Any]:
    if mode == "full":
        return {
            "mode": "full",
            "source_population": source_population,
            "status": "complete",
            "resolved_member_count": int(count),
            "presented_member_count": int(count),
            "presented_population_proof": "same_member_identity_set",
        }
    return {
        "mode": "not_collection",
        "source_population": source_population,
        "status": "not_applicable",
        "not_collection_reason": "single_target_or_runtime_status",
    }


def _project(
    manifest: dict[str, Any],
    *,
    title: str,
    summary: str,
    items: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    missing_optional: set[str] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    payload = manifest.get("payload") if isinstance(manifest.get("payload"), dict) else {}
    item_list = [dict(item) for item in items or () if isinstance(item, dict)]
    action_list = [dict(action) for action in actions or () if isinstance(action, dict)]
    source_population = str((manifest.get("coverage") or {}).get("source_population") or "")
    coverage_mode = str((manifest.get("coverage") or {}).get("mode") or "not_collection")
    block: dict[str, Any] = {
        "type": str(payload.get("block_type") or ""),
        "role": "primary",
        "priority": 110,
        "contract_id": str(manifest["contract_id"]),
        "contract_version": int(manifest["version"]),
        "contract_owner": str(manifest["contract_owner"]),
        "projection_boundary": str(manifest["projection_boundary"]),
        "producer": str(manifest["producer"]),
        "title": title,
        "summary": summary,
        "degradation": {
            "level": "optional_unavailable" if missing_optional else "none",
            "missing_optional_semantics": sorted(missing_optional or ()),
        },
        "coverage": _coverage(mode=coverage_mode, source_population=source_population, count=len(item_list)),
    }
    if "item_required_fields" in payload or "item_optional_fields" in payload:
        block["items"] = item_list
    if "action_required_fields" in payload or "action_optional_fields" in payload:
        block["actions"] = action_list
    block.update(dict(extra or {}))
    registry = PresentationContractRegistry(presentation_contract_manifests())
    validation = registry.validate(block, consumer=str(manifest["projection_boundary"]), trace_id=trace_id, require_contract=True)
    if validation.valid:
        return block
    return controlled_violation_block(validation.violations[0])


def project_order_list(orders: list[dict[str, Any]], *, trace_id: str | None = None) -> dict[str, Any]:
    """Project verified order facts once into ``commerce.order_list@1``."""
    missing_optional: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in orders:
        row = row if isinstance(row, dict) else {}
        status = _text(row.get("status"))
        amount = _optional(row.get("amount"))
        if not status:
            missing_optional.add("resource_state")
        if amount is None:
            missing_optional.add("supplementary_summary")
        items.append({
            "order_id": _text(row.get("order_id")),
            "product_name": _text(row.get("product_name")),
            "status": status,
            "amount": amount,
        })
    block = _project(
        ORDER_LIST_CONTRACT,
        title=f"订单（{len(items)}）",
        summary=f"已找到 {len(items)} 笔订单。",
        items=items,
        missing_optional=missing_optional,
        trace_id=trace_id,
    )
    return block


def _logistics_group(status: str) -> str:
    value = _text(status)
    if "签收" in value:
        return "delivered"
    if "待发货" in value or "备货" in value:
        return "waiting"
    return "in_transit"


def project_logistics_overview(
    items: list[dict[str, Any]],
    *,
    parameterization: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Project the requested logistics population into a formal contract.

    For a conditional server-side query, the requested population is the
    business-authoritative matched population, not the broader observation
    scope that was inspected to evaluate the condition.
    """
    missing_optional: set[str] = set()
    canonical: list[dict[str, Any]] = []
    for row in items:
        row = row if isinstance(row, dict) else {}
        estimate = _optional(row.get("estimate"))
        if estimate is None:
            missing_optional.add("delivery_estimate")
        canonical.append({
            "order_id": _text(row.get("order_id")),
            "product_name": _text(row.get("product_name")),
            "status": _text(row.get("status")),
            "latest": _text(row.get("latest")),
            "estimate": estimate,
        })
    counts = Counter(_logistics_group(str(row.get("status") or "")) for row in canonical)
    groups = [
        {"key": "in_transit", "label": "运输中", "count": int(counts.get("in_transit", 0))},
        {"key": "waiting", "label": "待发货", "count": int(counts.get("waiting", 0))},
        {"key": "delivered", "label": "已签收", "count": int(counts.get("delivered", 0))},
    ]
    groups = [group for group in groups if group["count"]]
    summary = f"共 {len(canonical)} 笔订单：" + "、".join(f"{item['count']} 笔{item['label']}" for item in groups)
    parameterization = dict(parameterization or {})
    matched_count = int(parameterization.get("matched_population_count") or len(canonical))
    source_count = int(parameterization.get("source_population_count") or matched_count)
    block = _project(
        LOGISTICS_OVERVIEW_CONTRACT,
        title="物流总览",
        summary=summary,
        items=canonical,
        extra={
            "groups": groups,
            "query_scope": {
                "source_population_count": source_count,
                "matched_population_count": matched_count,
                "presentation_population": str(parameterization.get("presentation_population") or "observed_members"),
                "applied_conditions": dict(parameterization.get("backend_applied_conditions") or {}),
            },
        },
        missing_optional=missing_optional,
        trace_id=trace_id,
    )
    if block.get("type") != "projection_contract_violation":
        block["coverage"] = {
            "mode": "full",
            "source_population": "requested_result_population",
            "status": "complete",
            "resolved_member_count": matched_count,
            "presented_member_count": len(canonical),
            "presented_population_proof": "business_matched_member_identity_set",
        }
        registry = PresentationContractRegistry(presentation_contract_manifests())
        validation = registry.validate(block, consumer=str(LOGISTICS_OVERVIEW_CONTRACT["projection_boundary"]), trace_id=trace_id, require_contract=True)
        if not validation.valid:
            return controlled_violation_block(validation.violations[0])
    return block


def project_business_status_list(
    items: list[dict[str, Any]],
    *,
    title: str = "业务进度",
    query_target: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    canonical: list[dict[str, Any]] = []
    missing_optional: set[str] = set()
    for row in items:
        row = row if isinstance(row, dict) else {}
        updated_at = _optional(row.get("updated_at"))
        order_id = _optional(row.get("order_id"))
        if updated_at is None:
            missing_optional.add("record_updated_at")
        if order_id is None:
            missing_optional.add("linked_order_identity")
        canonical.append({
            "record_reference": _text(row.get("record_reference")),
            "record_kind": _text(row.get("record_kind")),
            "status": _text(row.get("status")),
            "updated_at": updated_at,
            "order_id": order_id,
        })
    query_target = dict(query_target or {})
    target_order_id = _optional(query_target.get("order_id"))
    target_product_name = _optional(query_target.get("product_name"))
    target_label = (
        _optional(query_target.get("label"))
        or (
            f"{target_product_name}（订单 {target_order_id}）"
            if target_product_name and target_order_id
            else None
        )
        or (f"订单 {target_order_id}" if target_order_id else None)
    )
    target_fields = {
        "target_order_id": target_order_id,
        "target_product_name": target_product_name,
        "target_label": target_label,
    }
    block = _project(
        BUSINESS_STATUS_LIST_CONTRACT,
        title=title,
        summary=f"已找到 {len(canonical)} 条业务记录。" if canonical else "暂未找到业务记录。",
        items=canonical,
        extra={key: value for key, value in target_fields.items() if value is not None},
        missing_optional=missing_optional,
        trace_id=trace_id,
    )
    return block


def project_next_actions(
    *,
    order_id: str,
    product_name: str,
    actions: list[dict[str, Any]],
    title: str,
    summary: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    canonical_actions: list[dict[str, Any]] = []
    for action in actions:
        action = action if isinstance(action, dict) else {}
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        canonical_actions.append({
            "action_id": _text(action.get("action_id")),
            "label": _text(action.get("label")),
            "target": {
                "resource_type": _text(target.get("resource_type")),
                "order_id": _text(target.get("order_id")),
            },
            "input_hints": dict(action.get("input_hints") or {}),
        })
    return _project(
        NEXT_ACTIONS_CONTRACT,
        title=_text(title),
        summary=_text(summary),
        actions=canonical_actions,
        extra={"target_order_id": _text(order_id), "target_product_name": _text(product_name)},
        trace_id=trace_id,
    )


def project_eligibility_decision(
    *,
    order_id: str,
    product_name: str,
    eligible: bool,
    decision: str,
    summary: str,
    actions: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Project both positive and negative eligibility decisions.

    An empty action list is a valid negative result, not a missing primary
    presentation.  The authoritative decision and explanation remain visible.
    """
    canonical_actions: list[dict[str, Any]] = []
    for action in actions or ():
        action = action if isinstance(action, dict) else {}
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        canonical_actions.append({
            "action_id": _text(action.get("action_id")),
            "label": _text(action.get("label")),
            "target": {
                "resource_type": _text(target.get("resource_type")),
                "order_id": _text(target.get("order_id")),
            },
            "input_hints": dict(action.get("input_hints") or {}),
        })
    return _project(
        ELIGIBILITY_DECISION_CONTRACT,
        title="退款资格已通过" if eligible else "退款资格暂未通过",
        summary=_text(summary) or ("当前可以继续申请退款。" if eligible else "当前不符合退款申请条件。"),
        actions=canonical_actions,
        extra={
            "target_order_id": _text(order_id),
            "target_product_name": _text(product_name),
            "eligibility_kind": "退款资格",
            "decision": _text(decision),
            "eligible": bool(eligible),
        },
        trace_id=trace_id,
    )


def project_advisory(
    *,
    order_id: str,
    product_name: str,
    question: str,
    policy_evidence: list[dict[str, Any]],
    knowledge_available: bool,
    trace_id: str | None = None,
) -> dict[str, Any]:
    sources = [
        {
            "title": _text(row.get("title")) or "政策资料",
            "content": _text(row.get("content")),
            "source": _text(row.get("source")) or "已注册知识库",
        }
        for row in policy_evidence
        if isinstance(row, dict) and _text(row.get("content"))
    ]
    summary = (
        sources[0]["content"]
        if sources
        else f"当前知识库资料不足，无法确认“{_text(question)}”。未创建或提交任何业务申请。"
    )
    return _project(
        ADVISORY_CONTRACT,
        title="订单政策咨询",
        summary=summary,
        items=sources,
        extra={
            "target_order_id": _text(order_id),
            "target_product_name": _text(product_name),
            "question": _text(question),
            "knowledge_available": bool(knowledge_available and sources),
        },
        trace_id=trace_id,
    )
