"""Authoritative vertical definition for the demo support-ticket query."""
from __future__ import annotations

from typing import Any

from agent_core.business import ActorContext, get_business_port
from agent_core.kernel.capability import ToolCapabilityContract
from agent_core.runtime.outcomes import from_tool_result


def _actor(state: dict[str, Any]) -> ActorContext:
    return ActorContext(
        user_id=str(state.get("current_user_id") or ""),
        tenant_id=str(state.get("current_tenant_id") or "default"),
        role=str(state.get("current_role") or "customer"),
    )


def execute(state: dict[str, Any], args: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Read only module query with no ecommerce dependency or fallback."""
    try:
        payload = get_business_port().query_resources(
            _actor(state), resource_type="support_ticket", query_spec={"scope": "current_user", "answer_mode": "list"}
        )
    except Exception as exc:  # module boundary returns a classified failure rather than selecting another capability
        result = {"ok": False, "code": "SUPPORT_TICKET_BACKEND_UNAVAILABLE", "message": f"示例工单模块暂时不可用：{exc}"}
    else:
        if not bool(payload.get("success")):
            result = {"ok": False, "code": "SUPPORT_TICKET_QUERY_FAILED", "message": "示例工单模块未能返回当前用户的工单。"}
        else:
            rows = [dict(row) for row in payload.get("data") or () if isinstance(row, dict)]
            result = {
                "ok": True,
                "message": f"已查询到 {len(rows)} 条支持工单。",
                "data": {"tickets": rows, "count": len(rows)},
            }
    result["runtime_outcome"] = from_tool_result(
        tool_name="list_support_tickets", result=result, correlation_id=str(state.get("correlation_id") or "") or None
    ).as_dict()
    return result


SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_support_tickets",
        "description": "查询当前用户的支持工单。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
CONTRACT = ToolCapabilityContract(
    key="support_ticket.demo.list",
    tool_name="list_support_tickets",
    category="query",
    writes_business_data=False,
    evidence_sources=("support_ticket_demo_business_port",),
    planner_rule="查询当前用户的支持工单；该能力不替代任何其他模块能力。",
    unavailable_response="示例工单模块未启用或暂不可用。",
    execution_kind="grounding_read",
    goal_completion_types=("query",),
    completion_effects=("support_ticket.list:support_ticket",),
    discovery_examples=("支持工单", "客服工单", "我的工单", "工单列表"),
    exclusion_examples=("订单", "物流", "退款", "发票"),
)
PRESENTATION_CONTRACT = "runtime.resource_list@1"
PUBLIC_LABEL = "支持工单查询"
