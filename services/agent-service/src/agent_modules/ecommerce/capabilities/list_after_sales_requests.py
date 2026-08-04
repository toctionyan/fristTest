"""Authoritative vertical definition for `list_after_sales_requests`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import verified_read_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "list_after_sales_requests", lambda engine: engine.execute_list_after_sales_requests(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.after_sales.list',
    tool_name='list_after_sales_requests',
    category='query',
    planner_rule='查询一个或一组订单/售后工单的办理进度；用户要求全部时使用 collection。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('after_sales.query_status:after_sales_request',),
    discovery_examples=('售后进度', '售后状态', '售后工单', '换货进度', '维修进度', '退货进度'),
    exclusion_examples=('申请售后', '申请换货', '申请维修', '退款进度'),
    schema=target_query_schema("list_after_sales_requests", "查询一个或一组订单/售后记录的办理进度。", shape=("one", "collection")),
    executor=execute,
    contract_version='2',
    planning_contract=verified_read_contract(
        resource_types=("order", "after_sales_request"), cardinality="one_or_collection", target_type="ResolvedAfterSalesQueryTarget",
        output_name="after_sales_status_snapshot", output_type="VerifiedAfterSalesStatusSnapshot",
    ),
    presentation_contract='commerce.business_status_list@1',
    public_label=None,
)
