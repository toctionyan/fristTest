#!/usr/bin/env python3
"""Certify public transaction recovery across PostgreSQL-backed service restarts."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from verify_full_lifecycle_canary import ProductRuntimeHarness


def _call(base: str, path: str, *, method: str = "GET", body: dict | None = None, token: str | None = None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(f"{base}{path}", data=payload, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - owned local integration service
        raw = response.read().decode("utf-8")
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            raise RuntimeError(f"unexpected public response at {path}: {type(parsed).__name__}")
        return parsed


def _call_result(base: str, path: str, *, method: str, body: dict, token: str) -> tuple[int, dict]:
    try:
        return 200, _call(base, path, method=method, body=body, token=token)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return int(exc.code), payload if isinstance(payload, dict) else {"error": payload}


def _login(harness: ProductRuntimeHarness) -> str:
    accounts = list((_call(harness.agent_url, "/api/session/dev-accounts") or {}).get("accounts") or [])
    account = next((row for row in accounts if isinstance(row, dict) and row.get("user_id") == "u001"), None)
    if not isinstance(account, dict):
        raise RuntimeError("managed recovery requires disposable u001 dev account")
    payload = _call(harness.agent_url, "/api/session/dev-login", method="POST", body={
        "username": account.get("username") or account.get("user_id"),
        "password": harness.env.get("WEB_CONSOLE_DEV_PASSWORD", "123456"),
    })
    token = str(payload.get("token") or "")
    if not token:
        raise RuntimeError("managed recovery login did not return a token")
    return token


def _interaction(payload: dict, lifecycle: str) -> tuple[dict, dict]:
    if payload.get("type") != "interaction_required":
        raise RuntimeError(f"expected {lifecycle} interaction: {payload}")
    interaction = payload.get("interaction")
    if not isinstance(interaction, dict) or interaction.get("lifecycle") != lifecycle:
        raise RuntimeError(f"unexpected interaction lifecycle: {payload}")
    control = interaction.get("control")
    if not isinstance(control, dict):
        raise RuntimeError(f"interaction has no control: {payload}")
    return interaction, control


def _pending_interaction(
    harness: ProductRuntimeHarness,
    *,
    token: str,
    thread_id: str,
    lifecycle: str,
) -> tuple[dict, dict]:
    payload = _call(
        harness.agent_url,
        f"/api/threads/{thread_id}/pending",
        token=token,
    )
    return _interaction(payload, lifecycle)


def _input_values(interaction: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in list(interaction.get("fields") or []):
        if not isinstance(field, dict) or not field.get("required", True):
            continue
        name = str(field.get("name") or "")
        if not name:
            continue
        options = [row for row in list(field.get("options") or []) if isinstance(row, dict) and row.get("value")]
        if options:
            values[name] = str(options[0]["value"])
        elif "reason" in name:
            values[name] = "managed-postgres-restart-recovery"
        else:
            values[name] = "managed-integration"
    return values


def run_managed_postgres_recovery(harness: ProductRuntimeHarness) -> dict[str, Any]:
    if not harness.persistence_url:
        raise RuntimeError("managed PostgreSQL recovery requires persistence_url")
    if str(harness.env.get("PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA") or "").lower() != "true":
        raise RuntimeError("managed PostgreSQL recovery requires disposable Business data")

    token = _login(harness)
    thread_id = f"postgres-recovery-{uuid4().hex}"
    started = _call(harness.agent_url, "/api/transactions/start", method="POST", token=token, body={
        "thread_id": thread_id,
        "action_id": "create_refund",
        "target": {"resource_type": "order", "order_id": "10002"},
        "input_hints": {},
        "client_request_id": f"start-{uuid4().hex}",
    })
    form, form_control = _pending_interaction(
        harness, token=token, thread_id=thread_id, lifecycle="collecting_input"
    )
    draft_id = str(form_control.get("offer_handle") or "")
    if not draft_id:
        raise RuntimeError("managed recovery draft has no durable id")
    input_result = _call(harness.agent_url, "/api/transactions/input", method="POST", token=token, body={
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
    })
    _authority, authority_control = _pending_interaction(
        harness, token=token, thread_id=thread_id, lifecycle="awaiting_authority"
    )
    before_restart = _call(harness.agent_url, f"/api/transactions/{draft_id}", token=token)
    if ((before_restart.get("transaction") or {}).get("draft_state")) != "AWAITING_AUTHORIZATION":
        raise RuntimeError(f"draft did not reach AWAITING_AUTHORIZATION: {before_restart}")

    harness.restart_product_services()
    token = _login(harness)
    recovered = _call(harness.agent_url, f"/api/transactions/{draft_id}", token=token)
    if ((recovered.get("transaction") or {}).get("draft_state")) != "AWAITING_AUTHORIZATION":
        raise RuntimeError(f"draft was not recovered after restart: {recovered}")
    transcript = _call(harness.agent_url, f"/api/threads/{thread_id}/messages?limit=100", token=token)
    if not list(transcript.get("items") or []):
        raise RuntimeError("conversation transcript was not recovered after restart")

    secondary_agent_url = harness.start_secondary_agent()
    authority_request = {
        "thread_id": thread_id,
        "decision": "approved",
        "authority_type": "ui_confirmed",
        "offer_handle": authority_control["offer_handle"],
        "action_id": authority_control["action_id"],
        "target_handle": authority_control["target_handle"],
        "confirmation_id": authority_control["confirmation_id"],
        "confirmation_version": int(authority_control["confirmation_version"]),
        "conversation_revision": int(authority_control["conversation_revision"]),
        "client_request_id": f"authority-idempotent-{uuid4().hex}",
        "comment": "",
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _call_result,
                base,
                "/api/transactions/authority",
                method="POST",
                body=authority_request,
                token=token,
            )
            for base in (harness.agent_url, secondary_agent_url)
        ]
        concurrent_results = [future.result(timeout=45) for future in futures]
    if not any(status < 400 and payload.get("type") != "error" for status, payload in concurrent_results):
        raise RuntimeError(f"both concurrent authority attempts failed: {concurrent_results}")
    transaction = _call(harness.agent_url, f"/api/transactions/{draft_id}", token=token)
    assert ((transaction.get("transaction") or {}).get("draft_state")) == "COMMITTED", transaction
    receipt = _call(harness.agent_url, f"/api/transactions/{draft_id}/receipt", token=token)
    assert ((receipt.get("receipt") or {}).get("receipt_state")) == "SUCCESS", receipt
    receipt_identity = json.dumps(receipt.get("receipt") or {}, ensure_ascii=False, sort_keys=True)
    replay_status, _replay_payload = _call_result(
        harness.agent_url,
        "/api/transactions/authority",
        method="POST",
        body=authority_request,
        token=token,
    )
    if replay_status >= 500:
        raise RuntimeError(f"idempotent authority replay returned server failure: {replay_status}")
    stable_receipt = _call(harness.agent_url, f"/api/transactions/{draft_id}/receipt", token=token)
    if json.dumps(stable_receipt.get("receipt") or {}, ensure_ascii=False, sort_keys=True) != receipt_identity:
        raise RuntimeError("idempotent authority replay changed the committed receipt")

    harness.restart_product_services()
    token = _login(harness)
    transaction_after_second_restart = _call(harness.agent_url, f"/api/transactions/{draft_id}", token=token)
    assert ((transaction_after_second_restart.get("transaction") or {}).get("draft_state")) == "COMMITTED"
    receipt_after_second_restart = _call(harness.agent_url, f"/api/transactions/{draft_id}/receipt", token=token)
    assert ((receipt_after_second_restart.get("receipt") or {}).get("receipt_state")) == "SUCCESS"
    return {
        "schema_version": 1,
        "status": "PASS",
        "contract": "managed-postgres-public-restart-recovery@1",
        "draft_id": draft_id,
        "thread_id": thread_id,
        "restart_count": 2,
        "agent_instance_count": 2,
        "concurrent_authority_attempts": 2,
        "idempotency_replay": True,
        "recovered_states": ["AWAITING_AUTHORIZATION", "COMMITTED", "SUCCESS"],
        "persistence": "owned_postgresql",
    }


if __name__ == "__main__":
    raise SystemExit("run through scripts/run_managed_quality_integration.py")
