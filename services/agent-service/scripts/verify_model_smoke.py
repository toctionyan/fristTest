#!/usr/bin/env python3
"""Protected real-provider smoke through the production model gateway."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from langchain_core.messages import HumanMessage  # noqa: E402
from agent_core.config import get_model, get_model_profile  # noqa: E402
from agent_core.model_calls import (  # noqa: E402
    RealModelCertificationError,
    attest_real_model_response,
    certification_session_evidence,
    classify_model_failure,
    invoke_model,
    is_environmental_model_failure_category,
    model_call_scope,
    resolve_real_model_identity,
)


def _base_smoke_response_is_valid(content: str, expected: str = "model-smoke-ok") -> bool:
    return str(content or "").strip() == str(expected or "").strip()


def _identity_failure_reason(exc: RealModelCertificationError) -> str:
    if exc.environment_blocked:
        return "real_model_environment_unavailable"
    if exc.phase == "response":
        return "real_model_attestation_invalid"
    return "real_model_identity_invalid"


def main() -> int:
    try:
        identity = resolve_real_model_identity()
        challenge = f"model-smoke-ok:{secrets.token_hex(16)}"
        with model_call_scope(max_calls=1, scope="preprod_model_smoke") as calls:
            response, trace = invoke_model(
                purpose="preprod_model_smoke",
                model=get_model(),
                payload=[HumanMessage(content=f"Respond with exactly: {challenge}")],
            )
        attestation = attest_real_model_response(
            response=response,
            identity=identity,
            expected_content=challenge,
        )
        print(json.dumps({
            "status": "PASS",
            "identity": identity,
            "certification_session": certification_session_evidence(component="smoke", identity=identity),
            "attestation": attestation,
            "model_profile": get_model_profile(),
            "trace": trace,
            "calls": calls.summary(),
        }, ensure_ascii=False))
        return 0
    except RealModelCertificationError as exc:
        reason = _identity_failure_reason(exc)
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_code": exc.code,
            "reason": reason,
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except Exception as exc:
        category = classify_model_failure(exc)
        environment_blocked = is_environmental_model_failure_category(category)
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_category": category,
            "reason": "configured_model_environment_unavailable" if environment_blocked else "configured_model_smoke_failed",
        }, ensure_ascii=False))
        return 78 if environment_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
