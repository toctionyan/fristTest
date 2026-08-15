#!/usr/bin/env python3
"""Execute all final production certifications live under one authority."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from production_certification_contract import (  # noqa: E402
    BUNDLE_CONTRACT,
    COMPONENT_ENV,
    ProductionCertificationError,
    SESSION_ENV,
    STARTED_ENV,
    TOOLCHAIN_ENV,
    WORKSPACE_ENV,
    iso,
    utc_now,
    validate_production_components,
    workspace_fingerprint,
)

from release_toolchain_contract import (  # noqa: E402
    EVIDENCE_ENV as TOOLCHAIN_EVIDENCE_ENV,
    FINGERPRINT_ENV as TOOLCHAIN_FINGERPRINT_ENV,
    ReleaseToolchainError,
    validate_runtime_evidence,
)

COMPONENT_SCRIPTS = {
    "real_model": SCRIPTS / "verify_production_real_model_bundle.py",
    "postgres": SCRIPTS / "verify_production_postgres_bundle.py",
    "browser": SCRIPTS / "verify_production_browser_bundle.py",
}

_DIAGNOSTIC_FIELDS = ("reason", "error_code", "error_type", "error_category", "error", "component_exit_code")
_SECRET_NAME_TOKENS = ("key", "secret", "token", "password", "credential")


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            payload = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    raise ProductionCertificationError("component_output_invalid", "production component emitted no JSON object")


def _redacted_bounded_text(value: Any, env: Mapping[str, str], *, limit: int = 1600) -> str:
    text = str(value)
    secret_values = [
        str(secret)
        for name, secret in env.items()
        if secret
        and len(str(secret)) >= 6
        and any(token in str(name).casefold() for token in _SECRET_NAME_TOKENS)
    ]
    for secret in secret_values:
        text = text.replace(secret, "***")
    return text[-limit:]


def _safe_failure_diagnostic(
    component: str,
    payload: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Project only bounded, redacted failure metadata from a component.

    The production controller intentionally does not copy arbitrary nested
    component payloads.  Real-model certification already produces a bounded
    diagnostic; this function exposes only the fields needed to identify which
    live subcomponent failed and why, while dropping identities, endpoints,
    fingerprints and arbitrary secret-bearing fields.
    """
    result: dict[str, Any] = {}
    top_error_code = payload.get("error_code")
    if top_error_code not in (None, ""):
        result["component_error_code"] = _redacted_bounded_text(top_error_code, env)

    if component != "real_model":
        return result

    bundle = payload.get("real_model_bundle")
    if not isinstance(bundle, Mapping):
        return result

    failed_component = str(
        bundle.get("failed_component")
        or bundle.get("blocked_component")
        or ""
    ).strip()
    if failed_component:
        result["real_model_failed_component"] = failed_component[:120]

    bundle_error_code = bundle.get("error_code")
    if bundle_error_code not in (None, ""):
        result["real_model_component_error_code"] = _redacted_bounded_text(bundle_error_code, env)

    diagnostic = bundle.get("component_diagnostic")
    if isinstance(diagnostic, Mapping):
        projected: dict[str, str] = {}
        if failed_component:
            projected["component"] = failed_component[:120]
        for field in _DIAGNOSTIC_FIELDS:
            value = diagnostic.get(field)
            if value in (None, ""):
                continue
            projected[field] = _redacted_bounded_text(value, env)
        if projected:
            result["real_model_component_diagnostic"] = projected
    return result


def _default_runner(
    *,
    component: str,
    script_path: Path,
    env: Mapping[str, str],
    workspace_root: Path,
) -> Mapping[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-B", str(script_path)],
        cwd=workspace_root,
        env={**os.environ, **{str(key): str(value) for key, value in env.items()}},
        text=True,
        capture_output=True,
        timeout=10800,
        check=False,
    )
    payload = _last_json(completed.stdout)
    payload.setdefault("component_exit_code", int(completed.returncode))
    if completed.returncode not in (0, 78) and payload.get("status") == "PASS":
        raise ProductionCertificationError(
            "component_exit_status_mismatch",
            f"production component {component} reported PASS with failing exit code",
        )
    return payload


def run_production_certification_bundle(
    *,
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
    component_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    source_env = dict(os.environ if env is None else env)
    toolchain_evidence_path = str(source_env.get(TOOLCHAIN_EVIDENCE_ENV) or "").strip()
    toolchain_fingerprint = str(source_env.get(TOOLCHAIN_FINGERPRINT_ENV) or "").strip().casefold()
    if not toolchain_evidence_path or not toolchain_fingerprint:
        raise ProductionCertificationError(
            "release_toolchain_evidence_missing",
            "protected release toolchain evidence and fingerprint are required",
            environment_blocked=True,
        )
    try:
        validate_runtime_evidence(
            workspace,
            Path(toolchain_evidence_path),
            expected_fingerprint=toolchain_fingerprint,
        )
    except ReleaseToolchainError as exc:
        raise ProductionCertificationError(
            exc.code,
            str(exc),
            environment_blocked=exc.environment_blocked,
        ) from exc
    initial_fingerprint = workspace_fingerprint(workspace)
    started_at = utc_now()
    session_id = "prodcert-" + secrets.token_hex(24)
    runner = component_runner or _default_runner
    components: dict[str, Mapping[str, Any]] = {}
    launched: list[str] = []

    for component in ("real_model", "postgres", "browser"):
        script = COMPONENT_SCRIPTS[component]
        if not script.is_file():
            raise ProductionCertificationError("component_script_missing", f"missing production component: {component}")
        component_env = dict(source_env)
        component_env.update({
            SESSION_ENV: session_id,
            WORKSPACE_ENV: initial_fingerprint,
            STARTED_ENV: iso(started_at),
            COMPONENT_ENV: component,
            TOOLCHAIN_ENV: toolchain_fingerprint,
        })
        payload = runner(
            component=component,
            script_path=script,
            env=component_env,
            workspace_root=workspace,
        )
        launched.append(component)
        if not isinstance(payload, Mapping):
            raise ProductionCertificationError("component_output_invalid", f"{component} returned no mapping")
        status = str(payload.get("status") or "FAIL")
        if status == "BLOCKED_BY_ENVIRONMENT":
            return {
                "contract": BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or f"{component}_environment_unavailable"),
                "blocked_component": component,
                "components_launched": launched,
                "component_launch_count": len(launched),
                "session_id": session_id,
                "workspace_fingerprint_sha256": initial_fingerprint,
                "toolchain_fingerprint_sha256": toolchain_fingerprint,
                **_safe_failure_diagnostic(component, payload, source_env),
            }
        if status != "PASS":
            return {
                "contract": BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": str(payload.get("reason") or f"{component}_certification_failed"),
                "failed_component": component,
                "components_launched": launched,
                "component_launch_count": len(launched),
                "session_id": session_id,
                "workspace_fingerprint_sha256": initial_fingerprint,
                "toolchain_fingerprint_sha256": toolchain_fingerprint,
                **_safe_failure_diagnostic(component, payload, source_env),
            }
        components[component] = payload

    completed_fingerprint = workspace_fingerprint(workspace)
    result = validate_production_components(
        components=components,
        session_id=session_id,
        workspace_fingerprint_sha256=initial_fingerprint,
        toolchain_fingerprint_sha256=toolchain_fingerprint,
        started_at=started_at,
        completed_workspace_fingerprint_sha256=completed_fingerprint,
    )
    result["component_launch_count"] = len(launched)
    result["components_launched"] = launched
    result["component_evidence"] = components
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=str(ROOT))
    parser.add_argument("--evidence-out")
    args = parser.parse_args()
    try:
        result = run_production_certification_bundle(workspace_root=Path(args.workspace_root))
    except ProductionCertificationError as exc:
        result = {
            "contract": BUNDLE_CONTRACT,
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        result = {
            "contract": BUNDLE_CONTRACT,
            "status": "FAIL",
            "reason": "production_certification_exception",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }

    if args.evidence_out:
        output = Path(args.evidence_out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "PASS" else (78 if result.get("status") == "BLOCKED_BY_ENVIRONMENT" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
