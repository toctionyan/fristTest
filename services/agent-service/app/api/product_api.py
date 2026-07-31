from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent_core.composition import get_runtime_registry
from agent_core.business import ActorContext, get_business_port
from agent_core.business import BusinessServiceError
from agent_core.security.auth_provider import (
    console_dev_accounts,
    console_dev_login_enabled,
    issue_console_dev_token,
)
from app.schemas.chat_schema import (
    ActionAuthorityRequest,
    ActionInputRequest,
    ChatRequest,
    ChatResponse,
    TransactionStartRequest,
)
from app.use_cases.transaction_queries import TransactionQueryService
from app.security import (
    Actor,
    actor_can_debug,
    apply_actor_to_payload,
    current_actor,
    require_api_permission,
)

router = APIRouter(prefix="/api", tags=["product-api"])


class ChatTurnPayload(BaseModel):
    thread_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=8000)


class OrderQueryPayload(BaseModel):
    product_keyword: str | None = Field(default=None, max_length=200)
    order_status: str | None = Field(default=None, max_length=64)
    amount_min: float | None = None
    amount_max: float | None = None


class DevLoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def _actor_context(actor: Actor) -> ActorContext:
    return ActorContext(
        user_id=actor.user_id,
        role=actor.role,
        tenant_id=actor.tenant_id,
        subject=actor.subject,
        permissions=tuple(actor.permissions or ()),
    )


def _business_error(exc: BusinessServiceError) -> HTTPException:
    detail: Any = exc.payload if exc.payload is not None else {"message": exc.message}
    return HTTPException(status_code=exc.status_code, detail=detail)


def _unwrap_data(payload: dict[str, Any]) -> Any:
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


@router.get("/session/me")
def session_me(actor: Actor = Depends(current_actor)):
    return {
        "actor": {
            "user_id": actor.user_id,
            "role": actor.role,
            "tenant_id": actor.tenant_id,
            "permissions": list(actor.permissions or ()),
            "source": actor.source,
            "subject": actor.subject,
        }
    }


@router.get("/session/dev-accounts")
def dev_accounts():
    if not console_dev_login_enabled():
        raise HTTPException(status_code=404, detail="local dev login is disabled")
    return {"accounts": console_dev_accounts()}


@router.post("/session/dev-login")
def dev_login(payload: DevLoginPayload):
    if not console_dev_login_enabled():
        raise HTTPException(status_code=404, detail="local dev login is disabled")
    configured_password = os.getenv("WEB_CONSOLE_DEV_PASSWORD", "123456")
    if not hmac.compare_digest(payload.password, configured_password):
        raise HTTPException(status_code=401, detail="账号或密码错误。")
    username = payload.username.strip().lower()
    account = next(
        (
            item
            for item in console_dev_accounts()
            if username in {str(item["username"]).lower(), str(item["user_id"]).lower()}
        ),
        None,
    )
    if not account:
        raise HTTPException(status_code=401, detail="账号或密码错误。")
    token = issue_console_dev_token(
        user_id=str(account["user_id"]),
        role=str(account["role"]),
        tenant_id=str(account["tenant_id"]),
        subject=str(account["user_id"]),
    )
    return {"token": token, "actor": account}


@router.get("/actions", dependencies=[Depends(require_api_permission("chat:use"))])
def list_actions():
    return {"actions": [plugin.public_metadata() for plugin in get_runtime_registry().operations.all()]}


@router.get("/orders", dependencies=[Depends(require_api_permission("chat:use"))])
def list_orders(actor: Actor = Depends(current_actor)):
    try:
        payload = get_business_port().query_resources(_actor_context(actor), resource_type="order", query_spec={"user_id": actor.user_id})
    except BusinessServiceError as exc:
        raise _business_error(exc) from exc
    return {"orders": _unwrap_data(payload) or []}


@router.post("/orders/query", dependencies=[Depends(require_api_permission("chat:use"))])
def query_orders(payload: OrderQueryPayload, actor: Actor = Depends(current_actor)):
    adapter = get_business_port()
    actor_ctx = _actor_context(actor)
    query_spec = {
        "product_keyword": payload.product_keyword,
        "order_status": payload.order_status,
        "amount_min": payload.amount_min,
        "amount_max": payload.amount_max,
    }
    try:
        # Query semantics, authorization and future pagination belong to the
        # Business Service.  The Agent API is only an authenticated projection.
        result = adapter.query_resources(actor_ctx, resource_type="order", query_spec=query_spec)
    except BusinessServiceError as exc:
        raise _business_error(exc) from exc
    data = _unwrap_data(result)
    if isinstance(data, dict):
        return data
    rows = list(data or [])
    return {"orders": rows, "summary": {"matched": len(rows)}}


@router.get("/orders/{order_id}", dependencies=[Depends(require_api_permission("chat:use"))])
def get_order(order_id: str, actor: Actor = Depends(current_actor)):
    try:
        payload = get_business_port().read_resource(_actor_context(actor), resource_type="order", resource_id=order_id, query={"user_id": actor.user_id})
    except BusinessServiceError as exc:
        raise _business_error(exc) from exc
    return {"order": _unwrap_data(payload)}


@router.get("/orders/{order_id}/logistics", dependencies=[Depends(require_api_permission("chat:use"))])
def get_order_logistics(order_id: str, actor: Actor = Depends(current_actor)):
    try:
        payload = get_business_port().read_resource(_actor_context(actor), resource_type="logistics", resource_id=order_id)
    except BusinessServiceError as exc:
        raise _business_error(exc) from exc
    return {"logistics": _unwrap_data(payload)}


@router.get("/orders/{order_id}/actions", dependencies=[Depends(require_api_permission("chat:use"))])
def get_order_actions(order_id: str, actor: Actor = Depends(current_actor)):
    adapter = get_business_port()
    actor_ctx = _actor_context(actor)
    try:
        payload = adapter.read_resource(actor_ctx, resource_type="order", resource_id=order_id, query={"user_id": actor.user_id})
    except BusinessServiceError as exc:
        raise _business_error(exc) from exc
    order = _unwrap_data(payload) if payload else {}
    availability = order.get("operation_availability") if isinstance(order, dict) else {}
    codes: list[Any] = []
    if isinstance(availability, dict) and isinstance(availability.get("available_actions"), list):
        codes = list(availability["available_actions"])
    return {
        "actions": get_runtime_registry().operations.public_actions_for_business_codes(codes or [], resource_type="order", resource_id=order_id),
        "operation_availability": availability or None,
    }


@router.post("/chat/turn", response_model=ChatResponse, dependencies=[Depends(require_api_permission("chat:use"))])
def chat_turn(payload: ChatTurnPayload, request: Request, actor: Actor = Depends(current_actor)):
    req = ChatRequest(thread_id=payload.thread_id, user_id=actor.user_id, role=actor.role, tenant_id=actor.tenant_id, message=payload.message)
    return request.app.state.agent_service.chat(req, include_debug=actor_can_debug(actor))


@router.post("/chat/stream", dependencies=[Depends(require_api_permission("chat:use"))])
def chat_stream(payload: ChatTurnPayload, request: Request, actor: Actor = Depends(current_actor)):
    """Expose the existing Lifecycle SSE projection through the formal API.

    The stream generator already filters public events for non-debug actors;
    this endpoint only supplies authenticated identity and transport headers.
    """
    req = ChatRequest(thread_id=payload.thread_id, user_id=actor.user_id, role=actor.role, tenant_id=actor.tenant_id, message=payload.message)
    return StreamingResponse(
        request.app.state.agent_service.chat_stream(req, include_debug=actor_can_debug(actor)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/transactions", dependencies=[Depends(require_api_permission("chat:use"))])
def list_transactions(
    request: Request,
    actor: Actor = Depends(current_actor),
    thread_id: str | None = None,
    limit: int = 50,
):
    service = TransactionQueryService(request.app.state.agent_service.store_provider.transactions)
    return service.list_for_customer(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        thread_id=thread_id,
        limit=min(max(limit, 1), 200),
    )


@router.get("/transactions/{draft_id}", dependencies=[Depends(require_api_permission("chat:use"))])
def get_transaction(draft_id: str, request: Request, actor: Actor = Depends(current_actor)):
    service = TransactionQueryService(request.app.state.agent_service.store_provider.transactions)
    return service.get_for_customer(tenant_id=actor.tenant_id, user_id=actor.user_id, draft_id=draft_id)


@router.get("/transactions/{draft_id}/receipt", dependencies=[Depends(require_api_permission("chat:use"))])
def get_transaction_receipt(draft_id: str, request: Request, actor: Actor = Depends(current_actor)):
    service = TransactionQueryService(request.app.state.agent_service.store_provider.transactions)
    return service.receipt_for_customer(tenant_id=actor.tenant_id, user_id=actor.user_id, draft_id=draft_id)


@router.post("/transactions/start", response_model=ChatResponse, dependencies=[Depends(require_api_permission("chat:use"))])
def start_transaction(req: TransactionStartRequest, request: Request, actor: Actor = Depends(current_actor)):
    trusted_req = apply_actor_to_payload(req, actor)
    return request.app.state.agent_service.start_transaction(trusted_req, include_debug=actor_can_debug(actor))


@router.post("/transactions/input", response_model=ChatResponse, dependencies=[Depends(require_api_permission("chat:use"))])
def submit_transaction_input(req: ActionInputRequest, request: Request, actor: Actor = Depends(current_actor)):
    trusted_req = apply_actor_to_payload(req, actor)
    return request.app.state.agent_service.submit_action_input(trusted_req, include_debug=actor_can_debug(actor))


@router.post("/transactions/authority", response_model=ChatResponse, dependencies=[Depends(require_api_permission("chat:use"))])
def authorize_transaction(req: ActionAuthorityRequest, request: Request, actor: Actor = Depends(current_actor)):
    trusted_req = apply_actor_to_payload(req, actor)
    return request.app.state.agent_service.authorize_action(trusted_req, include_debug=actor_can_debug(actor))


@router.get("/threads/{thread_id}/messages", dependencies=[Depends(require_api_permission("chat:use"))])
def list_thread_messages(
    thread_id: str,
    request: Request,
    actor: Actor = Depends(current_actor),
    limit: int = 100,
):
    """Return a safe, chronological customer conversation timeline.

    The message repository may retain a live interaction snapshot for recovery,
    but this public endpoint deliberately exposes only the immutable text and
    released presentation blocks.  Authority tokens and live form controls are
    recovered through the dedicated pending-interaction endpoint instead.
    """
    service = request.app.state.agent_service
    service._assert_thread_owner(thread_id, actor.user_id, actor.tenant_id)
    rows = service.message_store.list_messages(thread_id, limit=min(max(limit, 1), 200))
    items: list[dict[str, Any]] = []
    for row in rows:
        blocks = [dict(block) for block in row.get("presentation") or [] if isinstance(block, dict)]
        role = "user" if str(row.get("role") or "") == "user" else "agent"
        message_type = str(row.get("message_type") or "")
        if blocks:
            presentation_mode = "structured"
        elif message_type == "error":
            presentation_mode = "notice"
        else:
            presentation_mode = "narrative"
        items.append({
            "id": f"history-{row.get('id')}",
            "role": role,
            "text": str(row.get("content") or ""),
            "blocks": blocks,
            "presentation_mode": presentation_mode,
            "created_at": row.get("created_at"),
        })
    return {"thread_id": thread_id, "items": items}


@router.get("/threads", dependencies=[Depends(require_api_permission("chat:use"))])
def list_threads(request: Request, actor: Actor = Depends(current_actor), limit: int = 100):
    safe_limit = min(max(limit, 1), 200)
    threads = request.app.state.agent_service.thread_store.list_threads(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        limit=safe_limit,
    )
    return {"threads": threads}


@router.get("/threads/{thread_id}/pending", dependencies=[Depends(require_api_permission("chat:use"))])
def pending_transactions(thread_id: str, request: Request, actor: Actor = Depends(current_actor)):
    payload = request.app.state.agent_service.pending_transactions(thread_id, actor.user_id, actor.tenant_id)
    try:
        live = request.app.state.agent_service.pending_interaction(thread_id, actor.user_id, actor.tenant_id)
    except Exception:
        live = {}
    if isinstance(live, dict) and isinstance(live.get("interaction"), dict):
        payload = {**payload, "type": live.get("type"), "message": live.get("message"), "interaction": live.get("interaction")}
    return payload


@router.post("/threads/{thread_id}/reconcile", dependencies=[Depends(require_api_permission("chat:use"))])
def reconcile_transactions(thread_id: str, request: Request, actor: Actor = Depends(current_actor)):
    return request.app.state.agent_service.reconcile_transactions(
        thread_id,
        actor.user_id,
        actor.tenant_id,
        role=actor.role,
        actor_permissions=list(actor.permissions or []),
    )
