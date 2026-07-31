#!/usr/bin/env python3
"""Read-only real-model canary through the complete public Lifecycle Graph.

Unlike the goal prototype smoke, this sends two dependent turns through the
authenticated product API.  The real provider must declare goals, consume
Business observations, preserve tool protocol, reuse visible evidence and
publish safe answers.  The canary snapshots transactions before/after and
fails if any Draft is created.
"""
from __future__ import annotations

from contextlib import closing

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import dotenv_values


AGENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = AGENT_ROOT.parents[1]
ROOT_SCRIPTS = WORKSPACE / "scripts"
AGENT_SRC = AGENT_ROOT / "src"
if str(ROOT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ROOT_SCRIPTS))
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.kernel.plan_projection_contract import read_plan_projection  # noqa: E402
from agent_core.model_calls import (  # noqa: E402
    RealModelCertificationError,
    attest_real_model_call_record,
    certification_session_evidence,
    resolve_real_model_identity,
)
from verify_full_lifecycle_canary import ProductRuntimeHarness  # noqa: E402


def _b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _jwt(secret: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({
        "sub": "u001", "user_id": "u001", "role": "customer", "tenant_id": "default",
        "exp": int(time.time()) + 600,
    }, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64url(hmac.new(secret.encode(), signing_input.encode("ascii"), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def _call(base: str, path: str, *, method: str = "GET", token: str = "", body: dict[str, Any] | None = None) -> Any:
    raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = Request(f"{base}{path}", data=raw, method=method)
    request.add_header("Accept", "application/json")
    if raw is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=120) as response:  # noqa: S310 - certification URL
        return json.loads(response.read().decode("utf-8"))


def _token(base: str, env: dict[str, str]) -> str:
    configured = str(env.get("AGENT_TEST_AUTH_TOKEN") or "").strip()
    if configured:
        return configured
    jwt_secret = str(env.get("AGENT_TEST_JWT_SECRET") or env.get("AGENT_JWT_SECRET") or "").strip()
    if jwt_secret:
        return _jwt(jwt_secret)
    accounts = list((_call(base, "/api/session/dev-accounts") or {}).get("accounts") or [])
    account = next((row for row in accounts if row.get("user_id") == "u001"), None)
    if not isinstance(account, dict):
        raise RuntimeError("no protected auth token/JWT secret or disposable u001 dev account")
    login = _call(base, "/api/session/dev-login", method="POST", body={
        "username": account.get("username") or account.get("user_id"),
        "password": env.get("WEB_CONSOLE_DEV_PASSWORD", "123456"),
    })
    token = str((login or {}).get("token") or "")
    if not token:
        raise RuntimeError("dev login did not return a token")
    return token


def _transaction_ids(payload: Any) -> set[str]:
    return {
        str(row.get("draft_id") or "")
        for row in list((payload or {}).get("items") or [])
        if isinstance(row, dict) and str(row.get("draft_id") or "")
    }


def _graph_diagnostics(database: Path) -> list[dict[str, Any]]:
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT output_json FROM trace_logs WHERE event_type='graph_snapshot' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    diagnostics: list[dict[str, Any]] = []
    for (raw,) in rows:
        payload = json.loads(raw or "{}")
        state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        workflow = read_plan_projection(state) or {}
        diagnostics.append({
            "phase": state.get("phase"),
            "status": state.get("status"),
            "tool_trace": [
                {
                    "name": row.get("name"),
                    "code": (row.get("result") or {}).get("code"),
                    "semantic": (((row.get("result") or {}).get("match_proof") or {}).get("semantic_verdict") or {}).get("reason_code"),
                }
                for row in list(state.get("tool_trace") or [])
                if isinstance(row, dict)
            ],
            "workflow": {
                "status": workflow.get("status"),
                "goal_coverage_complete": workflow.get("goal_coverage_complete"),
                "goals": [
                    {"id": goal.get("goal_id"), "status": goal.get("coverage_status")}
                    for goal in list(workflow.get("goals") or [])
                    if isinstance(goal, dict)
                ],
            },
            "decisions": [
                {
                    "tool": row.get("tool_name"),
                    "disposition": row.get("disposition"),
                    "reason_code": row.get("reason_code"),
                }
                for row in list(state.get("decision_chain") or [])[-6:]
                if isinstance(row, dict)
            ],
            "model_calls": [
                {
                    key: model_call.get(key)
                    for key in (
                        "purpose", "status", "model", "provider_model", "finish_reason",
                        "prompt_tokens", "completion_tokens", "total_tokens", "lane", "sequence",
                    )
                    if key in model_call
                }
                for row in list(state.get("debug_llm_calls") or [])
                if isinstance(row, dict)
                for model_call in [row.get("model_call")]
                if isinstance(model_call, dict)
            ],
            "answer_release_alignment": state.get("answer_release_alignment"),
            "state_contract_violations": state.get("state_contract_violations"),
            "ai_tool_calls": [
                {
                    "name": call.get("name"),
                    "args": {
                        key: (call.get("args") or {}).get(key)
                        for key in ("target", "goal_ids", "reference_span", "expected_shape")
                        if key in (call.get("args") or {})
                    },
                }
                for message in list(payload.get("ai_tool_calls") or [])[-5:]
                if isinstance(message, dict)
                for call in list(message.get("tool_calls") or [])
                if isinstance(call, dict)
            ],
        })
    return diagnostics


def _configured_model_environment() -> dict[str, str]:
    env = dict(os.environ)
    business_root = WORKSPACE / "services" / "business-service"
    for env_file in (AGENT_ROOT / ".env", business_root / ".env"):
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                env.setdefault(str(key), str(value))
    return env


def _attest_lifecycle_model_calls(
    *,
    diagnostics: list[dict[str, Any]],
    identity: dict[str, Any],
    turn_index: int,
) -> dict[str, Any]:
    latest = next(
        (row for row in diagnostics if isinstance(row, dict) and list(row.get("model_calls") or [])),
        None,
    )
    if latest is None:
        raise RealModelCertificationError(
            "lifecycle_model_calls_missing",
            "completed lifecycle turn did not persist any model call records",
            phase="response",
        )
    records = [row for row in list(latest.get("model_calls") or []) if isinstance(row, dict)]
    attestations = [
        attest_real_model_call_record(record=record, identity=identity)
        for record in records
    ]
    if not attestations:
        raise RealModelCertificationError(
            "lifecycle_model_calls_missing",
            "completed lifecycle turn did not contain an attestable model call",
            phase="response",
        )
    return {
        "contract": "real-model-lifecycle-turn-attestation@1",
        "turn": int(turn_index),
        "call_count": len(attestations),
        "purposes": [str(row.get("purpose") or "") for row in attestations],
        "reported_models": sorted({str(row.get("reported_model") or "") for row in attestations}),
        "finish_reasons": sorted({str(row.get("finish_reason") or "") for row in attestations}),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in attestations),
    }


def _run(
    *,
    base: str,
    env: dict[str, str],
    identity: dict[str, Any],
    diagnostic_database: Path,
) -> dict[str, Any]:
    token = _token(base, env)
    before = _transaction_ids(_call(base, "/api/transactions", token=token))
    thread_id = f"real-model-readonly-{uuid4().hex}"
    prompts = [
        "查一下我的订单",
        "其中最贵的是哪个？再查一下它的物流",
    ]
    public_results: list[dict[str, Any]] = []
    model_attestations: list[dict[str, Any]] = []
    for turn_index, prompt in enumerate(prompts, start=1):
        response = _call(base, "/api/chat/turn", method="POST", token=token, body={
            "thread_id": thread_id,
            "message": prompt,
        })
        if not isinstance(response, dict) or response.get("type") != "answer":
            raise RuntimeError(f"real-model turn did not reach a safe answer: {response}")
        if not str(response.get("answer") or "").strip() and not list(response.get("blocks") or []):
            raise RuntimeError("real-model answer has neither narrative nor structured presentation")
        outcome_summary = {
            "turn": turn_index,
            "type": response.get("type"),
            "presentation_mode": response.get("presentation_mode"),
            "answer": str(response.get("answer") or "")[:500],
            "contracts": [
                str(block.get("contract_id") or block.get("type") or "")
                for block in list(response.get("blocks") or [])
                if isinstance(block, dict)
            ],
        }
        if turn_index == 1 and not outcome_summary["contracts"]:
            raise RuntimeError(f"order-list turn did not publish structured evidence: {outcome_summary}")
        if turn_index == 2 and str(response.get("presentation_mode") or "") == "notice":
            raise RuntimeError(
                "dependent evidence turn degraded to a notice: "
                f"{outcome_summary}, blocks={list(response.get('blocks') or [])}"
            )
        encoded = json.dumps(response, ensure_ascii=False)
        forbidden = ("tool_trace", "execution_permits", "turn_match_proofs", "command_digest", "confirmation_id")
        if any(marker in encoded for marker in forbidden):
            raise RuntimeError("public response leaked internal runtime fields")
        public_results.append({**outcome_summary, "answer": "<customer-visible-verified>"})
        model_attestations.append(_attest_lifecycle_model_calls(
            diagnostics=_graph_diagnostics(diagnostic_database),
            identity=identity,
            turn_index=turn_index,
        ))
    messages = list((_call(base, f"/api/threads/{thread_id}/messages?limit=20", token=token) or {}).get("items") or [])
    if len(messages) < 4:
        raise RuntimeError(f"durable transcript is incomplete: {len(messages)} messages")
    after = _transaction_ids(_call(base, "/api/transactions", token=token))
    if after != before:
        raise RuntimeError("read-only real-model canary created or removed a transaction")
    return {
        "status": "PASS",
        "identity": identity,
        "provider_model": str(env.get("OPENAI_MODEL") or "configured"),
        "turns": len(prompts),
        "public_results": public_results,
        "model_attestations": model_attestations,
        "transcript_messages": len(messages),
        "transaction_delta": 0,
        "guarantee": "real provider -> goal declaration -> full graph -> business reads -> tool observations -> safe public answer",
    }


def main() -> int:
    harness: ProductRuntimeHarness | None = None
    try:
        # Resolve and validate the protected identity before allocating any
        # runtime resources.  This keeps missing credentials and local stubs
        # from starting Business/Agent processes at all.
        configured_env = _configured_model_environment()
        identity = resolve_real_model_identity(configured_env)
        configured_env.setdefault("OPENAI_API_BASE", str(identity.get("endpoint") or ""))

        # Always own the service process whose model identity is under test.
        # Reusing AGENT_TEST_URL could silently point this gate at CI's
        # deterministic product-smoke service while the runner environment
        # contains a real key, producing a false "real model" certification.
        original = {name: os.environ.get(name) for name in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL", "REAL_MODEL_CERTIFICATION_PROVIDER")}
        try:
            for name in original:
                value = configured_env.get(name)
                if value is not None:
                    os.environ[name] = str(value)
            harness = ProductRuntimeHarness(deterministic_model=False)
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        harness.env.update({
            "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
        })
        base = harness.agent_url
        env = harness.env
        runtime_identity = resolve_real_model_identity(env)
        identity_fields = ("provider", "endpoint", "model", "credential_fingerprint_sha256_16")
        if any(runtime_identity.get(field) != identity.get(field) for field in identity_fields):
            raise RealModelCertificationError(
                "runtime_identity_mismatch",
                "runtime model identity differs from the preflight certification identity",
            )
        identity = runtime_identity
        with harness:
            try:
                result = _run(
                    base=base,
                    env=env,
                    identity=identity,
                    diagnostic_database=harness.runtime_dir / "agent.db",
                )
            except Exception as exc:
                if harness is not None:
                    diagnostics = _graph_diagnostics(harness.runtime_dir / "agent.db")
                    raise RuntimeError(f"{exc}; graph_diagnostics={diagnostics}") from exc
                raise
        result["certification_session"] = certification_session_evidence(
            component="lifecycle", identity=identity
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RealModelCertificationError as exc:
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_code": exc.code,
            "reason": (
                "real_model_environment_unavailable"
                if exc.environment_blocked
                else ("real_model_attestation_invalid" if exc.phase == "response" else "real_model_identity_invalid")
            ),
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except (AssertionError, HTTPError, URLError, TimeoutError, OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        diagnostics = harness.diagnostic_tails() if harness is not None else {}
        print(json.dumps({
            "status": "FAIL",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "service_log_names": sorted(diagnostics),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
