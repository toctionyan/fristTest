"""Core transaction-outcome presentation contract.

This is intentionally generic: it only protects that a transaction status is
projected from a RuntimeOutcome/Receipt payload.  It does not define domain
resource fields.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .governance import PresentationContractRegistry, controlled_violation_block
from .renderer_registry import RendererRegistration


def _manifest(filename: str) -> dict[str, Any]:
    value = json.loads(files("agent_core.presentation.contracts").joinpath(filename).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"runtime presentation contract must be an object: {filename}")
    return value


TRANSACTION_STATUS_CONTRACT = _manifest("runtime_transaction_status_v1.json")
INTERACTION_TIMELINE_CONTRACT = _manifest("runtime_interaction_timeline_v1.json")
RESOURCE_LIST_CONTRACT = _manifest("runtime_resource_list_v1.json")


def runtime_presentation_contract_manifests() -> tuple[dict[str, Any], ...]:
    return (dict(TRANSACTION_STATUS_CONTRACT), dict(INTERACTION_TIMELINE_CONTRACT), dict(RESOURCE_LIST_CONTRACT))


def runtime_presentation_renderer_registrations() -> tuple[RendererRegistration, ...]:
    rows: list[RendererRegistration] = []
    for manifest in runtime_presentation_contract_manifests():
        for channel, renderer_id in dict(manifest.get("renderer") or {}).items():
            rows.append(RendererRegistration(contract_id=str(manifest["contract_id"]), channel=str(channel), renderer_id=str(renderer_id)))
    return tuple(rows)


def project_transaction_status(*, summary: str, data: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
    manifest = TRANSACTION_STATUS_CONTRACT
    block = {
        "type": "transaction_status",
        "role": "primary",
        "priority": 110,
        "contract_id": str(manifest["contract_id"]),
        "contract_version": int(manifest["version"]),
        "contract_owner": str(manifest["contract_owner"]),
        "projection_boundary": str(manifest["projection_boundary"]),
        "producer": str(manifest["producer"]),
        "summary": str(summary or "").strip(),
        "data": dict(data or {}),
        "degradation": {"level": "none", "missing_optional_semantics": []},
        "coverage": {
            "mode": "not_collection",
            "source_population": "runtime_transaction_outcome",
            "status": "not_applicable",
            "not_collection_reason": "runtime_outcome",
        },
    }
    registry = PresentationContractRegistry(runtime_presentation_contract_manifests())
    validation = registry.validate(block, consumer="runtime_transaction_status_projector", trace_id=trace_id, require_contract=True)
    return block if validation.valid else controlled_violation_block(validation.violations[0])


def project_interaction_timeline(*, interaction: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
    """Create a read-only chronological event for a live interaction.

    The actual input/authority controls remain in the live interaction card.
    This block only records what happened and what the user should do next.
    """
    manifest = INTERACTION_TIMELINE_CONTRACT
    value = dict(interaction or {})
    lifecycle = str(value.get("lifecycle") or "draft")
    if lifecycle == "collecting_input":
        next_step = "请在当前办理卡补充所需信息。"
    elif lifecycle == "awaiting_authority":
        next_step = "请在当前办理卡确认或暂不提交。"
    elif lifecycle in {"submission_unknown", "retryable_failure"}:
        next_step = "请查看当前办理状态或执行对账。"
    else:
        next_step = "请查看当前办理卡中的下一步。"
    block = {
        "type": "interaction_timeline",
        "role": "primary",
        "priority": 111,
        "contract_id": str(manifest["contract_id"]),
        "contract_version": int(manifest["version"]),
        "contract_owner": str(manifest["contract_owner"]),
        "projection_boundary": str(manifest["projection_boundary"]),
        "producer": str(manifest["producer"]),
        "interaction_id": str(value.get("interaction_id") or ""),
        "lifecycle": lifecycle,
        "summary": str(value.get("summary") or value.get("title") or "已创建办理事项。"),
        "target": str(value.get("target") or ""),
        "next_step": next_step,
        "read_only": True,
        "degradation": {"level": "none", "missing_optional_semantics": []},
        "coverage": {
            "mode": "not_collection",
            "source_population": "runtime_transaction_interaction",
            "status": "not_applicable",
            "not_collection_reason": "live_interaction_timeline_event",
        },
    }
    registry = PresentationContractRegistry(runtime_presentation_contract_manifests())
    validation = registry.validate(block, consumer="runtime_interaction_timeline_projector", trace_id=trace_id, require_contract=True)
    return block if validation.valid else controlled_violation_block(validation.violations[0])
