from __future__ import annotations

"""Ecommerce HTTP transport mapping for the demo Business Service.

Order, logistics, refund and invoice endpoint paths intentionally live in the
Ecommerce Overlay rather than Agent Core.
"""

import hashlib
import hmac
import os
import time
import uuid
from functools import lru_cache
from typing import Any

import httpx

from agent_core.business.transport import BusinessServiceError, current_business_actor
from agent_core.observability.correlation import get_correlation_id

class BusinessClient:
    def __init__(self, base_url: str, token: str, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @staticmethod
    def _read_transport_retries() -> int:
        """Bound automatic retries to idempotent read requests only."""
        try:
            configured = int(os.getenv("BUSINESS_READ_TRANSPORT_RETRIES", "2"))
        except ValueError:
            configured = 2
        return max(0, min(configured, 3))

    @staticmethod
    def _retry_backoff_seconds(attempt: int) -> float:
        try:
            milliseconds = int(os.getenv("BUSINESS_READ_RETRY_BACKOFF_MS", "50"))
        except ValueError:
            milliseconds = 50
        return max(0, min(milliseconds, 1000)) / 1000.0 * max(1, attempt)

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "X-Correlation-ID": str(get_correlation_id() or "")}
        actor = current_business_actor()
        if actor:
            actor_user_id = str(actor.get("user_id") or "")
            actor_role = str(actor.get("role") or "customer")
            actor_tenant_id = str(actor.get("tenant_id") or "")
            actor_account_id = str(actor.get("account_id") or actor_user_id)
            permissions = actor.get("permissions") or []
            permissions_value = ",".join(str(p) for p in permissions)

            headers["X-Actor-User-Id"] = actor_user_id
            headers["X-Actor-Role"] = actor_role
            if actor_tenant_id:
                headers["X-Actor-Tenant-Id"] = actor_tenant_id
            if actor_account_id:
                headers["X-Actor-Account-Id"] = actor_account_id
            if permissions_value:
                headers["X-Actor-Permissions"] = permissions_value
            if actor.get("user_token"):
                # Optional on-behalf-of token forwarding.  A Spring Security/RuoYi
                # backend can validate this token instead of trusting X-Actor-* alone.
                headers["X-User-Authorization"] = f"Bearer {actor['user_token']}"

            signing_secret = os.getenv("BUSINESS_ACTOR_SIGNING_SECRET")
            if signing_secret:
                timestamp = str(int(time.time()))
                nonce = uuid.uuid4().hex
                canonical = "\n".join(
                    [
                        actor_user_id,
                        actor_role,
                        actor_tenant_id,
                        actor_account_id,
                        permissions_value,
                        timestamp,
                        nonce,
                    ]
                )
                signature = hmac.new(
                    signing_secret.encode("utf-8"),
                    canonical.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Actor-Timestamp"] = timestamp
                headers["X-Actor-Nonce"] = nonce
                headers["X-Actor-Signature"] = signature
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        retry_safe: bool = False,
    ) -> dict[str, Any]:
        """Request the business service with bounded, evidence-preserving retry.

        Runtime can automatically retry only transport-safe reads.  A write
        response that may have been lost is *not* resubmitted here, even with an
        idempotency key: it is classified as submission-unknown and must go to
        Transaction reconciliation with the original persisted command.
        """
        url = f"{self.base_url}{path}"
        normalized_method = str(method or "GET").upper()
        # Some query/preview APIs are POST only because their filters are
        # structured.  Callers must opt in explicitly; normal POST writes can
        # never inherit this retry path.
        retryable_read = normalized_method in {"GET", "HEAD", "OPTIONS"} or bool(retry_safe)
        max_retries = self._read_transport_retries() if retryable_read else 0
        attempts = 0
        while True:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        normalized_method,
                        url,
                        params={k: v for k, v in (params or {}).items() if v is not None},
                        json=json,
                        headers=self._headers(idempotency_key),
                    )
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                if attempts < max_retries:
                    attempts += 1
                    time.sleep(self._retry_backoff_seconds(attempts))
                    continue
                if not retryable_read and idempotency_key:
                    raise BusinessServiceError(
                        504,
                        "business service response is unknown; do not resubmit automatically",
                        {"submission_unknown": True, "idempotency_key": idempotency_key, "transport_error": exc.__class__.__name__},
                    ) from exc
                raise BusinessServiceError(
                    503,
                    "business service transport retry exhausted",
                    {"retry_exhausted": True, "attempts": attempts + 1, "transport_error": exc.__class__.__name__},
                ) from exc

            # Retry only idempotent reads when the upstream explicitly reports
            # a transient gateway/unavailable status.  We never retry writes on
            # an HTTP status because their side effect is business-authoritative.
            if retryable_read and response.status_code in {502, 503, 504} and attempts < max_retries:
                attempts += 1
                time.sleep(self._retry_backoff_seconds(attempts))
                continue
            break

        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text}

        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if retryable_read and response.status_code in {502, 503, 504}:
                raise BusinessServiceError(
                    503,
                    str(detail or "business service transport retry exhausted"),
                    {"retry_exhausted": True, "attempts": attempts + 1, "upstream_status": response.status_code, "payload": payload},
                )
            if not retryable_read and idempotency_key and response.status_code in {502, 503, 504}:
                raise BusinessServiceError(
                    504,
                    "business service response is unknown; do not resubmit automatically",
                    {"submission_unknown": True, "idempotency_key": idempotency_key, "upstream_status": response.status_code, "payload": payload},
                )
            raise BusinessServiceError(response.status_code, str(detail or payload), payload)
        if not isinstance(payload, dict):
            raise BusinessServiceError(502, "business service returned non-object JSON", payload)
        return payload

    def list_accounts(self) -> dict[str, Any]:
        return self._request("GET", "/accounts")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def get_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/capabilities")

    def preview_operation(
        self,
        *,
        resource_type: str | None,
        resource_id: str | None,
        operation: str,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read a business-authoritative, non-mutating command preview."""
        return self._request(
            "POST",
            "/operations/preview",
            json={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "operation": operation,
                "input": dict(input_values or {}),
            },
            retry_safe=True,
        )

    def execute_operation_command(
        self,
        *,
        command: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute a frozen business.operation.command@1 envelope.

        The Agent never chooses a REST resource route here.  The Business
        Service validates the command contract, actor scope, target type and
        final domain transition.
        """
        return self._request(
            "POST",
            "/operations/execute",
            json=dict(command or {}),
            idempotency_key=idempotency_key,
        )

    # ---- normal business read APIs ---------------------------------------
    def get_order(self, order_id: str, user_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/orders/{order_id}", params={"user_id": user_id})

    def list_orders(self, user_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/orders", params={"user_id": user_id})

    def query_orders(self, query_spec: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/orders/query", json=query_spec, retry_safe=True)

    def get_order_available_actions(
        self, order_id: str, *, reason: str = ""
    ) -> dict[str, Any]:
        return self._request(
            "GET", f"/orders/{order_id}/available-actions", params={"reason": reason}
        )

    def get_refund_eligibility(
        self, order_id: str, *, reason: str = "", reason_code: str | None = None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/refunds/eligibility",
            params={"order_id": order_id, "reason": reason, "reason_code": reason_code},
        )

    def query_logistics(self, query_spec: dict[str, Any]) -> dict[str, Any]:
        """Run a server-owned, parameterized logistics query.

        This is intentionally separate from per-order ``get_logistics``: a
        request with a delivery-state condition must be applied by the Business
        Service, not by an Agent-side post-filter.
        """
        return self._request("POST", "/logistics/query", json=dict(query_spec or {}), retry_safe=True)

    def get_logistics(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/logistics/{order_id}")

    def list_products(self, keyword: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/products", params={"keyword": keyword})

    def get_product(self, product_id: str) -> dict[str, Any]:
        return self._request("GET", f"/products/{product_id}")

    def list_coupons(self, user_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/coupons", params={"user_id": user_id})

    # ---- normal business application APIs --------------------------------
    def cancel_order(
        self,
        order_id: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/orders/{order_id}/cancel",
            json={"reason": reason, "expected_version": expected_version},
            idempotency_key=idempotency_key,
        )

    def update_order_address(
        self,
        order_id: str,
        *,
        address: str,
        expected_version: int,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/orders/{order_id}/address-change",
            json={"address": address, "expected_version": expected_version},
            idempotency_key=idempotency_key,
        )

    def create_delivery_urge(
        self,
        order_id: str,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/orders/{order_id}/delivery-urges",
            json={"expected_version": expected_version},
            idempotency_key=idempotency_key,
        )

    def create_refund_ticket(
        self,
        *,
        order_id: str,
        expected_version: int,
        reason: str,
        reason_code: str | None = None,
        subject_user_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/refunds",
            json={
                "order_id": order_id,
                "expected_version": expected_version,
                "reason": reason,
                "reason_code": reason_code,
                "subject_user_id": subject_user_id or user_id,
            },
            idempotency_key=idempotency_key,
        )

    def create_after_sales_ticket(
        self,
        *,
        order_id: str,
        expected_version: int,
        reason: str,
        reason_code: str | None = None,
        subject_user_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/after-sales/tickets",
            json={
                "order_id": order_id,
                "expected_version": expected_version,
                "reason": reason,
                "reason_code": reason_code,
                "subject_user_id": subject_user_id or user_id,
            },
            idempotency_key=idempotency_key,
        )

    def create_invoice(
        self,
        *,
        order_id: str,
        expected_version: int,
        invoice_title: str,
        subject_user_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/invoices",
            json={
                "order_id": order_id,
                "expected_version": expected_version,
                "invoice_title": invoice_title,
                "subject_user_id": subject_user_id or user_id,
            },
            idempotency_key=idempotency_key,
        )

    def create_complaint(
        self,
        *,
        reason: str,
        subject_user_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/complaints",
            json={"reason": reason, "subject_user_id": subject_user_id or user_id},
            idempotency_key=idempotency_key,
        )

    def create_human_handoff(
        self,
        *,
        reason: str,
        subject_user_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/human-handoffs",
            json={"reason": reason, "subject_user_id": subject_user_id or user_id},
            idempotency_key=idempotency_key,
        )

    # ---- normal resource query APIs. Filtering belongs to the service. ----
    def list_refunds(
        self,
        *,
        order_id: str | None = None,
        refund_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/refunds",
            params={
                "order_id": order_id,
                "refund_id": refund_id,
                "user_id": user_id,
                "status": status,
            },
        )

    def list_after_sales(
        self,
        *,
        order_id: str | None = None,
        ticket_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/after-sales/tickets",
            params={
                "order_id": order_id,
                "ticket_id": ticket_id,
                "user_id": user_id,
                "status": status,
            },
        )

    def list_invoices(
        self,
        *,
        order_id: str | None = None,
        invoice_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/invoices",
            params={
                "order_id": order_id,
                "invoice_id": invoice_id,
                "user_id": user_id,
                "status": status,
            },
        )

    def list_complaints(
        self,
        *,
        complaint_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/complaints",
            params={"complaint_id": complaint_id, "user_id": user_id, "status": status},
        )

    def list_human_handoffs(
        self,
        *,
        handoff_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/human-handoffs",
            params={"handoff_id": handoff_id, "user_id": user_id, "status": status},
        )

    # ---- command APIs. Callers cannot write status or reviewer facts. ------
    def command_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        command: str,
        expected_version: int,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        paths = {
            "refund": f"/refunds/{resource_id}/commands",
            "after_sales": f"/after-sales/tickets/{resource_id}/commands",
            "invoice": f"/invoices/{resource_id}/commands",
            "complaint": f"/complaints/{resource_id}/commands",
            "handoff": f"/human-handoffs/{resource_id}/commands",
        }
        if resource_type not in paths:
            raise ValueError(f"unknown resource_type: {resource_type}")
        return self._request(
            "POST",
            paths[resource_type],
            json={
                "command": command,
                "expected_version": expected_version,
                "note": note,
            },
            idempotency_key=idempotency_key,
        )

    def command_refund(
        self,
        refund_id: str,
        *,
        command: str,
        expected_version: int,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.command_resource(
            "refund",
            refund_id,
            command=command,
            expected_version=expected_version,
            note=note,
            idempotency_key=idempotency_key,
        )

    def command_after_sales(
        self,
        ticket_id: str,
        *,
        command: str,
        expected_version: int,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.command_resource(
            "after_sales",
            ticket_id,
            command=command,
            expected_version=expected_version,
            note=note,
            idempotency_key=idempotency_key,
        )

    def command_invoice(
        self,
        invoice_id: str,
        *,
        command: str,
        expected_version: int,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.command_resource(
            "invoice",
            invoice_id,
            command=command,
            expected_version=expected_version,
            note=note,
            idempotency_key=idempotency_key,
        )

    def command_complaint(
        self,
        complaint_id: str,
        *,
        command: str,
        expected_version: int,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.command_resource(
            "complaint",
            complaint_id,
            command=command,
            expected_version=expected_version,
            note=note,
            idempotency_key=idempotency_key,
        )

    def command_human_handoff(
        self,
        handoff_id: str,
        *,
        command: str,
        expected_version: int,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.command_resource(
            "handoff",
            handoff_id,
            command=command,
            expected_version=expected_version,
            note=note,
            idempotency_key=idempotency_key,
        )


@lru_cache(maxsize=1)
def get_business_client() -> BusinessClient:
    return BusinessClient(
        base_url=os.getenv("BUSINESS_SERVICE_BASE_URL", "http://127.0.0.1:9000"),
        token=os.getenv("BUSINESS_SERVICE_TOKEN", "dev-service-token"),
        timeout=float(os.getenv("BUSINESS_SERVICE_TIMEOUT", "8")),
    )
