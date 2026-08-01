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

from locked_python import locked_project_python  # noqa: E402

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


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            payload = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    raise ProductionCertificationError("component_output_invalid", "production component emitted no JSON object")


def _default_runner(
    *,
    component: str,
    script_path: Path,
    env: Mapping[str, str],
    workspace_root: Path,
) -> Mapping[str, Any]:
    completed = subprocess.run(
        [str(locked_project_python(workspace_root, "agent", env=env)), "-B", str(script_path)],
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


def _safe_component_failure(component: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only bounded diagnostic fields; never expose credentials or model text."""

    # Emit diagnostics at the final production boundary so one protected rerun is
    # sufficient to locate a failed nested component without retaining raw logs.
    evidence: dict[str, Any] = {
        "component": component,
        "status": str(payload.get("status") or "FAIL"),
        "reason": str(payload.get("reason") or f"{component}_certification_failed"),
        "error_code": str(payload.get("error_code") or "component_failed"),
    }
    for key in ("error_type", "error_category"):
        value = str(payload.get(key) or "").strip()
        if value:
            evidence[key] = value

    nested = payload.get("real_model_bundle") if component == "real_model" else None
    if isinstance(nested, Mapping):
        failed_subcomponent = str(nested.get("failed_component") or "").strip()
        if failed_subcomponent:
            evidence["failed_subcomponent"] = failed_subcomponent
        nested_error_code = str(nested.get("error_code") or "").strip()
        if nested_error_code:
            evidence["error_code"] = nested_error_code
        nested_failure = nested.get("component_failure")
        if isinstance(nested_failure, Mapping):
            safe_nested = {
                key: str(nested_failure.get(key) or "")
                for key in (
                    "component",
                    "status",
                    "reason",
                    "error_code",
                    "error_type",
                    "error_category",
                )
                if str(nested_failure.get(key) or "").strip()
            }
            if safe_nested:
                evidence["subcomponent_failure"] = safe_nested
    return evidence


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
            failure = _safe_component_failure(component, payload)
            return {
                "contract": BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or f"{component}_environment_unavailable"),
                "blocked_component": component,
                "component_error_code": failure["error_code"],
                "component_failure": failure,
                "components_launched": launched,
                "component_launch_count": len(launched),
                "session_id": session_id,
                "workspace_fingerprint_sha256": initial_fingerprint,
                "toolchain_fingerprint_sha256": toolchain_fingerprint,
            }
        if status != "PASS":
            failure = _safe_component_failure(component, payload)
            return {
                "contract": BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": str(payload.get("reason") or f"{component}_certification_failed"),
                "failed_component": component,
                "component_error_code": failure["error_code"],
                "component_failure": failure,
                "components_launched": launched,
                "component_launch_count": len(launched),
                "session_id": session_id,
                "workspace_fingerprint_sha256": initial_fingerprint,
                "toolchain_fingerprint_sha256": toolchain_fingerprint,
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
