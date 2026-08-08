#!/usr/bin/env python3
"""Run the live real-model bundle as one production-certification component."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "services" / "agent-service"
for path in (AGENT_ROOT, AGENT_ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import agent_core.model_calls.real_model_certification_bundle as bundle_authority  # noqa: E402
from agent_core.model_calls import run_certification_bundle  # noqa: E402
from production_certification_contract import (  # noqa: E402
    ProductionCertificationError,
    production_session_evidence,
)

_DIAGNOSTIC_FIELDS = ("reason", "error_code", "error_type", "error_category", "error")
_SECRET_NAME_TOKENS = ("key", "secret", "token", "password", "credential")


def _safe_component_diagnostic(
    payload: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, str]:
    """Project only bounded failure fields and redact known secret values."""

    secret_values = [
        str(value)
        for name, value in env.items()
        if value
        and len(str(value)) >= 6
        and any(token in str(name).casefold() for token in _SECRET_NAME_TOKENS)
    ]
    projected: dict[str, str] = {}
    for field in _DIAGNOSTIC_FIELDS:
        raw = payload.get(field)
        if raw in (None, ""):
            continue
        text = str(raw)
        for secret in secret_values:
            text = text.replace(secret, "***")
        projected[field] = text[-1600:]
    return projected


def main() -> int:
    try:
        session = production_session_evidence(component="real_model")
        captured: dict[str, dict[str, str]] = {}

        def diagnostic_runner(
            *,
            component: str,
            script_path: Path,
            env: Mapping[str, str],
            workspace_root: Path,
        ) -> Mapping[str, Any]:
            payload = bundle_authority._default_component_runner(  # noqa: SLF001 - certification harness seam
                component=component,
                script_path=script_path,
                env=env,
                workspace_root=workspace_root,
            )
            if str(payload.get("status") or "") != "PASS":
                captured[component] = _safe_component_diagnostic(payload, env)
            return payload

        bundle = run_certification_bundle(
            workspace_root=ROOT,
            component_runner=diagnostic_runner,
        )
        status = str(bundle.get("status") or "FAIL")
        failed_component = str(
            bundle.get("failed_component")
            or bundle.get("blocked_component")
            or ""
        )
        if failed_component and captured.get(failed_component):
            bundle = {
                **bundle,
                "component_diagnostic": captured[failed_component],
            }
        result = {
            "contract": "production-real-model-certification@1",
            "status": status,
            "production_session": session,
            "real_model_bundle": bundle,
        }
        if status != "PASS":
            result["reason"] = str(bundle.get("reason") or "real_model_certification_not_passed")
            result["error_code"] = str(bundle.get("error_code") or "real_model_certification_not_passed")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if status == "PASS" else (78 if status == "BLOCKED_BY_ENVIRONMENT" else 1)
    except ProductionCertificationError as exc:
        print(json.dumps({
            "contract": "production-real-model-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": exc.code,
            "error": str(exc),
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except Exception as exc:
        print(json.dumps({
            "contract": "production-real-model-certification@1",
            "status": "FAIL",
            "reason": "real_model_component_exception",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
