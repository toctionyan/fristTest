#!/usr/bin/env python3
"""Exercise the authenticated, separately-running customer API boundary.

This integration-only smoke uses CI's disposable Business database.  It proves
the complete structured cancellation lifecycle for seeded order ``10003`` and
the public SSE event boundary; it must never be pointed at shared data.
"""
from __future__ import annotations

import json
import os
import base64
import hashlib
import hmac
import time
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AGENT_URL = os.getenv("AGENT_TEST_URL", "http://127.0.0.1:8000").rstrip("/")
BUSINESS_URL = os.getenv("BUSINESS_TEST_URL", os.getenv("BUSINESS_SERVICE_BASE_URL", "http://127.0.0.1:9000")).rstrip("/")
EPHEMERAL_DATA_FLAG = "PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA"


def _b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _protected_test_token() -> str | None:
    configured = os.getenv("AGENT_TEST_AUTH_TOKEN", "").strip()
    if configured:
        return configured
    secret = os.getenv("AGENT_TEST_JWT_SECRET", "").strip()
    if not secret:
        return None
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({
        "sub": "u001",
        "user_id": "u001",
        "role": "customer",
        "tenant_id": "default",
        "exp": int(time.time()) + 600,
    }, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64url(
        hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{signing_input}.{signature}"


def _call(base: str, path: str, *, method: str = "GET", body: dict | None = None, token: str | None = None) -> tuple[int, object]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(f"{base}{path}", data=payload, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=20) as response:  # noqa: S310 - test URLs are supplied by CI
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw) if response.headers.get_content_type() == "application/json" else raw


def _sse_events(*, token: str, thread_id: str, message: str) -> list[tuple[str, object]]:
    payload = json.dumps({"thread_id": thread_id, "message": message}).encode("utf-8")
    request = Request(f"{AGENT_URL}/api/chat/stream", data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - test URLs are supplied by CI
        assert response.status == 200
        assert response.headers.get_content_type() == "text/event-stream"
        frames = response.read().decode("utf-8").strip().split("\n\n")
    events: list[tuple[str, object]] = []
    for frame in frames:
        event = "message"
        data = ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data += line.removeprefix("data: ")
        events.append((event, json.loads(data) if data else {}))
    return events


def _unauthenticated_sse_status(*, thread_id: str, message: str) -> int:
    """Return the HTTP status without consuming a graph turn.

    The CI service uses the local ``dev_token`` provider with authentication
    required.  This negative assertion protects the formal SSE route from
    silently becoming a public alternate chat API.
    """
    payload = json.dumps({"thread_id": thread_id, "message": message}).encode("utf-8")
    request = Request(f"{AGENT_URL}/api/chat/stream", data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - test URLs are supplied by CI
            return int(response.status)
    except HTTPError as exc:
        return int(exc.code)


def _interaction(payload: object, *, lifecycle: str) -> tuple[dict, dict]:
    assert isinstance(payload, dict), payload
    assert payload.get("type") == "interaction_required", payload
    interaction = payload.get("interaction")
    assert isinstance(interaction, dict) and interaction.get("lifecycle") == lifecycle, payload
    control = interaction.get("control")
    assert isinstance(control, dict), interaction
    return interaction, control


def _input_values(interaction: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in interaction.get("fields") or []:
        if not isinstance(field, dict) or not field.get("required", True):
            continue
        name = str(field.get("name") or "")
        assert name, field
        options = [row for row in field.get("options") or [] if isinstance(row, dict) and str(row.get("value") or "")]
        values[name] = str(options[0]["value"]) if options else "integration-smoke"
    return values


def main() -> int:
    try:
        # This smoke intentionally authorizes a cancellation as part of the
        # end-to-end lifecycle.  A caller must declare that the Business data
        # is disposable before it is allowed to write anything.
        assert os.getenv(EPHEMERAL_DATA_FLAG, "").strip().lower() == "true", (
            f"{EPHEMERAL_DATA_FLAG}=true is required; this smoke writes only to disposable CI Business data"
        )
        status, health = _call(AGENT_URL, "/health")
        assert status == 200 and health == {"status": "ok"}
        status, business_health = _call(BUSINESS_URL, "/health")
        assert status == 200 and isinstance(business_health, dict) and business_health.get("success") is True

        token = _protected_test_token()
        if token is None:
            _, accounts = _call(AGENT_URL, "/api/session/dev-accounts")
            account_rows = list((accounts or {}).get("accounts") or [])
            account = next((row for row in account_rows if row.get("user_id") == "u001" and row.get("role") == "customer"), None)
            assert isinstance(account, dict), account_rows
            _, login = _call(
                AGENT_URL,
                "/api/session/dev-login",
                method="POST",
                body={"username": account["username"], "password": os.getenv("WEB_CONSOLE_DEV_PASSWORD", "123456")},
            )
            token = str((login or {}).get("token") or "")
            assert token
        _, me = _call(AGENT_URL, "/api/session/me", token=token)
        assert (me or {}).get("actor", {}).get("user_id") == "u001"
        assert _unauthenticated_sse_status(thread_id=f"integration-sse-unauth-{uuid4().hex}", message="你好") == 401

        _, orders = _call(AGENT_URL, "/api/orders", token=token)
        assert any(str(row.get("order_id") or "") == "10003" for row in (orders or {}).get("orders") or [])
        _, actions = _call(AGENT_URL, "/api/orders/10003/actions", token=token)
        assert any(row.get("action_id") == "cancel_order" for row in (actions or {}).get("actions") or [])

        normal_thread_id = f"integration-normal-chat-{uuid4().hex}"
        _, chat = _call(
            AGENT_URL,
            "/api/chat/turn",
            method="POST",
            token=token,
            body={"thread_id": normal_thread_id, "message": "你好"},
        )
        assert isinstance(chat, dict) and chat.get("thread_id") == normal_thread_id and chat.get("type") == "answer", chat

        # This must enter via the authenticated chat boundary.  The
        # deterministic model only emits a candidate; the real Lifecycle
        # Graph resolves the order, verifies capability exactness, creates the
        # Draft, and returns the first form interaction.  Calling
        # /api/transactions/start here would bypass all of those boundaries.
        thread_id = f"integration-lifecycle-{uuid4().hex}"
        _, started = _call(
            AGENT_URL,
            "/api/chat/turn",
            method="POST",
            token=token,
            body={"thread_id": thread_id, "message": "请取消订单10003"},
        )
        form, form_control = _interaction(started, lifecycle="collecting_input")
        assert form_control.get("action_id") == "cancel_order", form_control
        draft_id = str(form_control.get("offer_handle") or "")
        assert draft_id
        _, input_result = _call(
            AGENT_URL,
            "/api/transactions/input",
            method="POST",
            token=token,
            body={
                "thread_id": thread_id,
                "interaction_mode": "submit_input",
                "offer_handle": form_control["offer_handle"],
                "action_id": form_control["action_id"],
                "target_handle": form_control["target_handle"],
                "form_id": form_control["form_id"],
                "form_version": int(form_control["form_version"]),
                "form_step": int(form_control["form_step"]),
                "conversation_revision": int(form_control["conversation_revision"]),
                "client_request_id": f"input-{uuid4().hex}",
                "input_values": _input_values(form),
            },
        )
        _authority, authority_control = _interaction(input_result, lifecycle="awaiting_authority")
        _, committed = _call(
            AGENT_URL,
            "/api/transactions/authority",
            method="POST",
            token=token,
            body={
                "thread_id": thread_id,
                "decision": "approved",
                "authority_type": "ui_confirmed",
                "offer_handle": authority_control["offer_handle"],
                "action_id": authority_control["action_id"],
                "target_handle": authority_control["target_handle"],
                "confirmation_id": authority_control["confirmation_id"],
                "confirmation_version": int(authority_control["confirmation_version"]),
                "conversation_revision": int(authority_control["conversation_revision"]),
                "client_request_id": f"authority-{uuid4().hex}",
                "comment": "",
            },
        )
        assert isinstance(committed, dict) and committed.get("type") != "error", committed
        _, transaction = _call(AGENT_URL, f"/api/transactions/{draft_id}", token=token)
        assert (transaction or {}).get("transaction", {}).get("draft_state") == "COMMITTED", transaction
        _, receipt = _call(AGENT_URL, f"/api/transactions/{draft_id}/receipt", token=token)
        assert (receipt or {}).get("receipt", {}).get("receipt_state") == "SUCCESS", receipt
        _, messages = _call(AGENT_URL, f"/api/threads/{thread_id}/messages", token=token)
        assert isinstance((messages or {}).get("items"), list) and (messages or {}).get("items"), messages

        events = _sse_events(token=token, thread_id=f"integration-sse-{uuid4().hex}", message="你好")
        names = [name for name, _data in events]
        assert names[0] == "start" and "result" in names and names[-1] == "end", names
        assert set(names).issubset({"start", "public_update", "result", "end"}), events
        public_text = json.dumps(events, ensure_ascii=False)
        assert all(marker not in public_text for marker in ("tool_trace", "execution_permits", "turn_match_proofs", "confirmation_id", "command_digest")), public_text
    except (AssertionError, HTTPError, URLError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"product HTTP smoke: FAIL: {exc.__class__.__name__}: {exc!r}")
        return 1
    print("product HTTP smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
