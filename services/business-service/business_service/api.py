"""FastAPI composition for the ecommerce Business Service."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header

from .api_models import (
    AddressChangeRequest, AfterSalesCreateRequest, CancelOrderRequest,
    CommandRequest, ComplaintCreateRequest, DeliveryUrgeRequest,
    HumanHandoffCreateRequest, InvoiceCreateRequest, LegacyReviewRequest, LogisticsQueryRequest,
    OperationCommandRequest, OperationPreviewRequest, OrderQueryRequest,
    RefundCreateRequest,
)
from .application.service import BusinessService
from .config import BusinessSettings
from .database import build_business_database
from .domain import DomainError
from .security import Actor, build_actor_dependency
from .seed import seed_demo_data


def create_app(*, database_path: str | None = None) -> FastAPI:
    settings = BusinessSettings.from_env()
    if database_path:
        settings = BusinessSettings(
            profile=settings.profile,
            database_backend="sqlite",
            database_url=None,
            database_path=Path(database_path),
            service_token=settings.service_token,
            require_actor_signature=settings.require_actor_signature,
            actor_signing_secret=settings.actor_signing_secret,
            actor_signature_ttl_seconds=settings.actor_signature_ttl_seconds,
            seed_demo_data=settings.seed_demo_data,
        )
    settings.validate_security()
    database = build_business_database(settings)
    database.initialize()
    if settings.seed_demo_data:
        seed_demo_data(database)
    service = BusinessService(database)
    current_actor = build_actor_dependency(settings, database)
    app = FastAPI(title="Ecommerce Business Service", version="20.6.1")
    app.state.database = database
    app.state.business_service = service

    @app.exception_handler(DomainError)
    async def _domain_error(_request, exc: DomainError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code},
        )

    def key(
        value: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> str | None:
        return value

    @app.get("/health")
    def health():
        return {
            "success": True,
            "data": {
                "service": "business",
                "version": "20.6.1",
                "database_backend": settings.database_backend,
            },
        }

    @app.get("/capabilities")
    def capabilities(actor: Actor = Depends(current_actor)):
        return service.ok(
            {
                "orders": True,
                "refunds": True,
                "after_sales": True,
                "invoices": True,
                "complaints": True,
                "delivery_urges": True,
                "human_handoffs": True,
                "normal_business_commands": True,
                "actor": actor.user_id,
            }
        )

    @app.get("/auth/me")
    def auth_me(actor: Actor = Depends(current_actor)):
        return service.auth_me(actor)

    @app.get("/accounts")
    def accounts(actor: Actor = Depends(current_actor)):
        return service.accounts(actor)

    @app.post("/operations/preview")
    def operation_preview(
        payload: OperationPreviewRequest,
        actor: Actor = Depends(current_actor),
    ):
        return service.operation_preview(actor, payload)

    @app.post("/operations/execute")
    def execute_operation_command(
        payload: OperationCommandRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.execute_operation_command(actor, payload, key=idempotency_key)

    @app.get("/orders")
    def list_orders(user_id: str | None = None, actor: Actor = Depends(current_actor)):
        return service.list_orders(actor, user_id)

    @app.get("/orders/{order_id}")
    def get_order(
        order_id: str, user_id: str | None = None, actor: Actor = Depends(current_actor)
    ):
        return service.get_order(actor, order_id, user_id)

    @app.get("/orders/{order_id}/available-actions")
    def order_available_actions(
        order_id: str, reason: str = "", actor: Actor = Depends(current_actor)
    ):
        return service.order_available_actions(actor, order_id, reason)

    @app.post("/orders/query")
    def query_orders(payload: OrderQueryRequest, actor: Actor = Depends(current_actor)):
        return service.query_orders(actor, payload)

    @app.post("/orders/{order_id}/cancel")
    def cancel_order(
        order_id: str,
        payload: CancelOrderRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.cancel_order(
            actor,
            order_id,
            payload.reason,
            payload.expected_version,
            key=idempotency_key,
        )

    @app.post("/orders/{order_id}/address-change")
    def change_address(
        order_id: str,
        payload: AddressChangeRequest = ...,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.change_address(
            actor,
            order_id,
            payload.address,
            payload.expected_version,
            key=idempotency_key,
        )

    # URL retained only briefly as a normal-operation alias, not an Agent route.
    @app.post("/orders/{order_id}/address", deprecated=True)
    def change_address_compat(
        order_id: str,
        payload: AddressChangeRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.change_address(
            actor,
            order_id,
            payload.address,
            payload.expected_version,
            key=idempotency_key,
        )

    @app.post("/orders/{order_id}/delivery-urges")
    def urge_delivery(
        order_id: str,
        payload: DeliveryUrgeRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.urge_delivery(
            actor, order_id, payload.expected_version, key=idempotency_key
        )

    @app.post("/logistics/query")
    def query_logistics(payload: LogisticsQueryRequest, actor: Actor = Depends(current_actor)):
        return service.query_logistics(actor, payload)

    @app.get("/logistics/{order_id}")
    def logistics(order_id: str, actor: Actor = Depends(current_actor)):
        return service.logistics(actor, order_id)

    @app.get("/products")
    def products(keyword: str | None = None, actor: Actor = Depends(current_actor)):
        return service.products(actor, keyword)

    @app.get("/products/{product_id}")
    def product(product_id: str, actor: Actor = Depends(current_actor)):
        return service.product(actor, product_id)

    @app.get("/coupons")
    def coupons(user_id: str | None = None, actor: Actor = Depends(current_actor)):
        return service.coupons(actor, user_id)

    @app.post("/refunds")
    def create_refund(
        payload: RefundCreateRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.create_refund(actor, payload, key=idempotency_key)

    @app.get("/refunds")
    def list_refunds(
        order_id: str | None = None,
        refund_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        actor: Actor = Depends(current_actor),
    ):
        return service.list_resources(
            actor,
            "refund",
            order_id=order_id,
            resource_id=refund_id,
            user_id=user_id,
            status=status,
        )

    @app.get("/refunds/eligibility")
    def refund_eligibility(
        order_id: str, reason: str = "", reason_code: str | None = None, actor: Actor = Depends(current_actor)
    ):
        return service.refund_eligibility(actor, order_id, reason, reason_code)

    @app.post("/refunds/{refund_id}/commands")
    def refund_command(
        refund_id: str,
        payload: CommandRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.command_resource(
            actor, "refund", refund_id, payload, key=idempotency_key
        )

    @app.post("/after-sales/tickets")
    def create_after_sales(
        payload: AfterSalesCreateRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.create_after_sales(actor, payload, key=idempotency_key)

    @app.get("/after-sales/tickets")
    def list_after_sales(
        order_id: str | None = None,
        ticket_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        actor: Actor = Depends(current_actor),
    ):
        return service.list_resources(
            actor,
            "after_sales",
            order_id=order_id,
            resource_id=ticket_id,
            user_id=user_id,
            status=status,
        )

    @app.post("/after-sales/tickets/{ticket_id}/commands")
    def after_sales_command(
        ticket_id: str,
        payload: CommandRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.command_resource(
            actor, "after_sales", ticket_id, payload, key=idempotency_key
        )

    @app.post("/invoices")
    def create_invoice(
        payload: InvoiceCreateRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.create_invoice(actor, payload, key=idempotency_key)

    @app.get("/invoices")
    def list_invoices(
        order_id: str | None = None,
        invoice_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        actor: Actor = Depends(current_actor),
    ):
        return service.list_resources(
            actor,
            "invoice",
            order_id=order_id,
            resource_id=invoice_id,
            user_id=user_id,
            status=status,
        )

    @app.post("/invoices/{invoice_id}/commands")
    def invoice_command(
        invoice_id: str,
        payload: CommandRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.command_resource(
            actor, "invoice", invoice_id, payload, key=idempotency_key
        )

    @app.post("/complaints")
    def create_complaint(
        payload: ComplaintCreateRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.create_complaint(actor, payload, key=idempotency_key)

    @app.get("/complaints")
    def list_complaints(
        complaint_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        actor: Actor = Depends(current_actor),
    ):
        return service.list_resources(
            actor, "complaint", resource_id=complaint_id, user_id=user_id, status=status
        )

    @app.post("/complaints/{complaint_id}/commands")
    def complaint_command(
        complaint_id: str,
        payload: CommandRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.command_resource(
            actor, "complaint", complaint_id, payload, key=idempotency_key
        )

    @app.post("/human-handoffs")
    def create_handoff(
        payload: HumanHandoffCreateRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.create_handoff(actor, payload, key=idempotency_key)

    @app.get("/human-handoffs")
    def list_handoffs(
        handoff_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        actor: Actor = Depends(current_actor),
    ):
        return service.list_resources(
            actor, "handoff", resource_id=handoff_id, user_id=user_id, status=status
        )

    @app.post("/human-handoffs/{handoff_id}/commands")
    def handoff_command(
        handoff_id: str,
        payload: CommandRequest,
        actor: Actor = Depends(current_actor),
        idempotency_key: str | None = Depends(key),
    ):
        return service.command_resource(
            actor, "handoff", handoff_id, payload, key=idempotency_key
        )

    # Explicitly reject generic state editing. These endpoints exist only to give
    # current callers a migration error instead of preserving a dangerous path.
    @app.put("/refunds/{refund_id}", deprecated=True)
    def legacy_refund_update(
        refund_id: str,
        payload: LegacyReviewRequest,
        actor: Actor = Depends(current_actor),
    ):
        service.legacy_update_rejected()

    @app.put("/after-sales/tickets/{ticket_id}", deprecated=True)
    def legacy_after_sales_update(
        ticket_id: str,
        payload: LegacyReviewRequest,
        actor: Actor = Depends(current_actor),
    ):
        service.legacy_update_rejected()

    @app.put("/invoices/{invoice_id}", deprecated=True)
    def legacy_invoice_update(
        invoice_id: str,
        payload: LegacyReviewRequest,
        actor: Actor = Depends(current_actor),
    ):
        service.legacy_update_rejected()

    @app.put("/complaints/{complaint_id}", deprecated=True)
    def legacy_complaint_update(
        complaint_id: str,
        payload: LegacyReviewRequest,
        actor: Actor = Depends(current_actor),
    ):
        service.legacy_update_rejected()

    @app.put("/human-handoffs/{handoff_id}", deprecated=True)
    def legacy_handoff_update(
        handoff_id: str,
        payload: LegacyReviewRequest,
        actor: Actor = Depends(current_actor),
    ):
        service.legacy_update_rejected()

    return app


app = create_app()
