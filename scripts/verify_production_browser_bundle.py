#!/usr/bin/env python3
"""Run configured-model browser journeys inside one protected preprod runtime."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "services" / "agent-service"
for path in (ROOT / "scripts", AGENT_ROOT, AGENT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

def _load_real_model_identity_module():
    path = AGENT_ROOT / "src" / "agent_core" / "model_calls" / "real_model_identity.py"
    spec = importlib.util.spec_from_file_location("production_real_model_identity_lightweight", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("real-model identity module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_identity_module = _load_real_model_identity_module()
RealModelCertificationError = _identity_module.RealModelCertificationError
resolve_real_model_identity = _identity_module.resolve_real_model_identity
from production_certification_contract import (  # noqa: E402
    ProductionCertificationError,
    postgres_database_identity,
    production_session_evidence,
    safe_file_fingerprint,
)
from run_managed_quality_integration import ManagedPostgres  # noqa: E402
import verify_product_browser_journey as browser_runtime  # noqa: E402

RUNTIME_CONTRACT = "protected-browser-runtime-authority@1"
JOURNEY_CONTRACT = "protected-browser-journey-runtime@1"


def _last_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            payload = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _validate_runtime_authority(runtime: Mapping[str, Any]) -> None:
    expected = {
        "contract": RUNTIME_CONTRACT,
        "runtime_profile": "preprod",
        "auth_provider": "jwt_hs256",
        "dev_login_enabled": False,
        "actor_signature_required": True,
        "agent_db_backend": "postgres",
        "checkpoint_backend": "postgres",
        "business_db_backend": "postgres",
        "rag_backend": "pgvector",
        "document_job_backend": "sqlalchemy",
        "document_object_store_backend": "shared_filesystem",
        "strict_persistence": True,
        "state_contract_mode": "strict",
        "single_postgres_authority": True,
    }
    mismatches = {
        key: {"expected": value, "actual": runtime.get(key)}
        for key, value in expected.items()
        if runtime.get(key) != value
    }
    verifier_modes = runtime.get("verifier_modes")
    required_verifiers = {
        "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
        "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
    }
    if not isinstance(verifier_modes, Mapping) or any(
        verifier_modes.get(key) != value for key, value in required_verifiers.items()
    ):
        mismatches["verifier_modes"] = {
            "expected": required_verifiers,
            "actual": verifier_modes,
        }
    if mismatches:
        raise ProductionCertificationError(
            "browser_runtime_authority_invalid",
            f"browser journey did not run under protected authority: {mismatches}",
        )


def _run_journey(args: list[str], *, env_override: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / "verify_product_browser_journey.py"), *args],
        cwd=ROOT,
        env={**os.environ, **{str(key): str(value) for key, value in env_override.items()}},
        text=True,
        capture_output=True,
        timeout=7800,
        check=False,
    )
    payload = _last_json(completed.stdout) or {}
    if completed.returncode == 0:
        try:
            if payload.get("contract") != JOURNEY_CONTRACT or payload.get("status") != "PASS":
                raise ProductionCertificationError(
                    "browser_journey_attestation_missing",
                    "successful browser process emitted no protected runtime attestation",
                )
            runtime = payload.get("runtime_authority")
            if not isinstance(runtime, Mapping):
                raise ProductionCertificationError(
                    "browser_runtime_authority_missing",
                    "browser journey runtime authority evidence is missing",
                )
            _validate_runtime_authority(runtime)
            fingerprint = str(payload.get("database_instance_fingerprint_sha256_16") or "")
            if fingerprint != str(env_override.get("PRODUCT_BROWSER_DATABASE_INSTANCE_FINGERPRINT_SHA256_16") or ""):
                raise ProductionCertificationError(
                    "browser_database_identity_mismatch",
                    "browser journey did not attest the owned PostgreSQL instance",
                )
            return 0, {
                "status": "PASS",
                "contract": JOURNEY_CONTRACT,
                "runtime_authority": dict(runtime),
                "database_instance_fingerprint_sha256_16": fingerprint,
                "e2e_stdout_sha256_16": str(payload.get("e2e_stdout_sha256_16") or ""),
                "process_stdout_sha256_16": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()[:16],
            }
        except ProductionCertificationError as exc:
            return 1, {"status": "FAIL", "reason": exc.code, "error": str(exc)}
    if completed.returncode == 78 or payload.get("status") == "BLOCKED_BY_ENVIRONMENT":
        return 78, {
            "status": "BLOCKED_BY_ENVIRONMENT",
            "reason": str(payload.get("reason") or "browser_environment_unavailable"),
            "diagnostics": payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {},
        }
    return 1, {
        "status": "FAIL",
        "reason": str(payload.get("reason") or payload.get("error_type") or "browser_journey_failed"),
        "error": str(payload.get("error") or completed.stderr[-2000:]),
    }


def main() -> int:
    try:
        session = production_session_evidence(component="browser")
        identity = resolve_real_model_identity(os.environ)
        browser = browser_runtime._browser_executable()  # noqa: SLF001 - same certification runtime
        version_result = subprocess.run(
            [str(browser), "--version"],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        browser_version = (version_result.stdout or version_result.stderr or "").strip()
        if version_result.returncode != 0 or not browser_version:
            raise ProductionCertificationError("browser_version_unavailable", "browser version could not be read")

        journeys = [
            (
                "configured-strong-context",
                [
                    "--journey", "strong-context",
                    "--model-mode", "configured",
                    "--runtime-profile", "protected-preprod",
                ],
            ),
            (
                "configured-strong-context-campaign",
                [
                    "--journey", "strong-context-campaign",
                    "--model-mode", "configured",
                    "--runtime-profile", "protected-preprod",
                    "--campaign-seed", "20260715",
                    "--campaign-phase", "repair-retest",
                ],
            ),
        ]
        completed_names: list[str] = []
        journey_evidence: list[dict[str, Any]] = []
        with ManagedPostgres() as postgres:
            database = postgres_database_identity(postgres.url, instance_nonce=postgres.name)
            image_evidence = postgres.image_evidence
            child_env = {
                "PRODUCT_BROWSER_RUNTIME_PROFILE": "protected-preprod",
                "PRODUCT_BROWSER_POSTGRES_URL": postgres.url,
                "PRODUCT_BROWSER_DATABASE_INSTANCE_FINGERPRINT_SHA256_16": database[
                    "database_instance_fingerprint_sha256_16"
                ],
            }
            for name, args in journeys:
                exit_code, evidence = _run_journey(args, env_override=child_env)
                evidence = {"journey": name, **evidence}
                journey_evidence.append(evidence)
                if exit_code != 0:
                    status = "BLOCKED_BY_ENVIRONMENT" if exit_code == 78 else "FAIL"
                    print(json.dumps({
                        "contract": "production-browser-certification@1",
                        "status": status,
                        "reason": evidence.get("reason"),
                        "production_session": session,
                        "identity": identity,
                        "journey_evidence": journey_evidence,
                    }, ensure_ascii=False))
                    return exit_code
                completed_names.append(name)

        runtime_authority = dict(journey_evidence[0]["runtime_authority"])
        print(json.dumps({
            "contract": "production-browser-certification@1",
            "status": "PASS",
            "production_session": session,
            "identity": identity,
            "journeys": completed_names,
            "journey_count": len(completed_names),
            "protected_runtime_journey_count": len(completed_names),
            "journey_evidence": journey_evidence,
            "runtime_authority": runtime_authority,
            "database_instance_fingerprint_sha256_16": database[
                "database_instance_fingerprint_sha256_16"
            ],
            "pgvector_extension": database["pgvector_extension"],
            **image_evidence,
            "browser_version": browser_version,
            "browser_executable_sha256_16": safe_file_fingerprint(browser),
            "browser_executable_name": browser.name,
        }, ensure_ascii=False))
        return 0
    except RealModelCertificationError as exc:
        print(json.dumps({
            "contract": "production-browser-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": exc.code,
            "error": str(exc),
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except (ProductionCertificationError, browser_runtime.BrowserRuntimeEnvironmentBlocked) as exc:
        code = getattr(exc, "code", None) or getattr(exc, "reason", None) or "browser_environment_unavailable"
        blocked = isinstance(exc, browser_runtime.BrowserRuntimeEnvironmentBlocked) or getattr(exc, "environment_blocked", False)
        print(json.dumps({
            "contract": "production-browser-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if blocked else "FAIL",
            "reason": str(code),
            "error": str(exc),
        }, ensure_ascii=False))
        return 78 if blocked else 1
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        text = str(exc)
        environment = any(token in text.lower() for token in (
            "docker command", "docker daemon", "failed to start managed pgvector", "did not become ready",
        ))
        print(json.dumps({
            "contract": "production-browser-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if environment else "FAIL",
            "reason": "browser_protected_runtime_unavailable" if environment else "browser_component_failed",
            "error_type": exc.__class__.__name__,
            "error": text,
        }, ensure_ascii=False))
        return 78 if environment else 1
    except Exception as exc:
        print(json.dumps({
            "contract": "production-browser-certification@1",
            "status": "FAIL",
            "reason": "browser_component_exception",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
