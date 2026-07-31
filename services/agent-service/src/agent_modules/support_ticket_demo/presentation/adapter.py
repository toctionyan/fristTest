"""Module-owned projection into the Kernel's generic resource-list contract."""
from __future__ import annotations

from typing import Any

from agent_core.presentation.contracts.runtime import RESOURCE_LIST_CONTRACT, runtime_presentation_renderer_registrations


class SupportTicketObservationAdapter:
    adapter_id = "support_ticket_demo.observations.v1"
    priority = 90

    @staticmethod
    def presentation_contracts() -> tuple[dict[str, Any], ...]:
        return (dict(RESOURCE_LIST_CONTRACT),)

    @staticmethod
    def presentation_renderers():
        return tuple(registration for registration in runtime_presentation_renderer_registrations() if registration.contract_id == "runtime.resource_list@1")

    def blocks_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in reversed(trace):
            if not isinstance(row, dict) or str(row.get("name") or "") != "list_support_tickets":
                continue
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if not result.get("ok"):
                continue
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            tickets = [dict(ticket) for ticket in data.get("tickets") or () if isinstance(ticket, dict)]
            trace_id = str(row.get("trace_id") or row.get("call_id") or "") or None
            manifest = RESOURCE_LIST_CONTRACT
            return [{
                "type": "resource_list",
                "role": "primary",
                "priority": self.priority,
                "contract_id": str(manifest["contract_id"]),
                "contract_version": int(manifest["version"]),
                "contract_owner": str(manifest["contract_owner"]),
                "projection_boundary": str(manifest["projection_boundary"]),
                "producer": str(manifest["producer"]),
                "title": "支持工单",
                "summary": f"已查询到 {len(tickets)} 条支持工单。",
                "items": [{
                    "resource_id": str(ticket.get("ticket_id") or ""),
                    "resource_label": str(ticket.get("subject") or "支持工单"),
                    "resource_type": "support_ticket",
                    "state": str(ticket.get("status") or ""),
                    "summary": str(ticket.get("summary") or ""),
                } for ticket in tickets],
                "degradation": {"level": "none", "missing_optional_semantics": []},
                "coverage": {
                    "mode": "full",
                    "source_population": "module_verified_resource_collection",
                    "status": "complete",
                    "resolved_member_count": len(tickets),
                    "presented_member_count": len(tickets),
                    "presented_population_proof": "same_member_identity_set",
                },
                "trace_id": trace_id,
            }]
        return []
