"""Fail-closed, live real-model certification bundle authority.

The bundle controller never accepts historical evidence paths.  It starts all
three protected certification components itself and binds their safe outputs to
one unpredictable session, one source-tree fingerprint and one official model
identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_core.model_calls.real_model_identity import (
    RealModelCertificationError,
    resolve_real_model_identity,
)

_SESSION_CONTRACT = "real-model-certification-session@1"
_BUNDLE_CONTRACT = "real-model-certification-bundle@1"
_REQUIRED_COMPONENTS = ("smoke", "semantic", "lifecycle")
_IDENTITY_FIELDS = (
    "provider",
    "endpoint",
    "model",
    "credential_fingerprint_sha256_16",
)
_SESSION_ENV = "REAL_MODEL_CERTIFICATION_SESSION_ID"
_WORKSPACE_ENV = "REAL_MODEL_CERTIFICATION_WORKSPACE_FINGERPRINT"
_STARTED_ENV = "REAL_MODEL_CERTIFICATION_SESSION_STARTED_AT"
_COMPONENT_ENV = "REAL_MODEL_CERTIFICATION_COMPONENT"
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROTECTED_VERIFIER_MODES = {
    "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
    "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
    "GOAL_GRANULARITY_VERIFIER_MODE": "model",
    "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
}


class RealModelBundleError(RuntimeError):
    """Raised when live component evidence cannot close the bundle."""

    def __init__(self, code: str, message: str, *, environment_blocked: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.environment_blocked = bool(environment_blocked)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any, *, code: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise RealModelBundleError(code, "certification session timestamp is missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RealModelBundleError(code, "certification session timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise RealModelBundleError(code, "certification session timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def certification_session_from_environment(
    *,
    component: str,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return the bundle session supplied by the live parent controller.

    Standalone smoke/prototype/lifecycle commands remain supported: when none of
    the session variables are present this returns ``None``.  Partial or forged
    session variables fail closed.
    """

    source = os.environ if env is None else env
    values = {
        "session_id": str(source.get(_SESSION_ENV) or "").strip(),
        "workspace_fingerprint_sha256": str(source.get(_WORKSPACE_ENV) or "").strip().casefold(),
        "started_at": str(source.get(_STARTED_ENV) or "").strip(),
        "component": str(source.get(_COMPONENT_ENV) or "").strip(),
    }
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise RealModelBundleError(
            "certification_session_incomplete",
            "all live certification session variables must be supplied together",
        )
    if component not in _REQUIRED_COMPONENTS:
        raise RealModelBundleError("component_invalid", "unknown certification component")
    if values["component"] != component:
        raise RealModelBundleError(
            "component_session_mismatch",
            "certification component does not match the parent session assignment",
        )
    if not _SESSION_RE.fullmatch(values["session_id"]):
        raise RealModelBundleError("session_id_invalid", "certification session id is invalid")
    if not _SHA256_RE.fullmatch(values["workspace_fingerprint_sha256"]):
        raise RealModelBundleError(
            "workspace_fingerprint_invalid",
            "certification workspace fingerprint must be a lowercase SHA-256 value",
        )
    started_at = _parse_timestamp(values["started_at"], code="session_started_at_invalid")
    now = _utc_now()
    if started_at > now + timedelta(minutes=5) or started_at < now - timedelta(hours=6):
        raise RealModelBundleError(
            "certification_session_stale",
            "certification session is outside the permitted live execution window",
        )
    return {
        "contract": _SESSION_CONTRACT,
        "mode": "bundle",
        "session_id": values["session_id"],
        "workspace_fingerprint_sha256": values["workspace_fingerprint_sha256"],
        "component": component,
        "started_at": _iso(started_at),
        "emitted_at": _iso(now),
    }


def workspace_fingerprint(workspace_root: Path) -> str:
    """Public source fingerprint used by the bundle controller and CI wrappers."""

    return _source_workspace_fingerprint(Path(workspace_root))


def certification_session_evidence(
    *,
    component: str,
    identity: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Emit safe session evidence for a component.

    A parent bundle supplies protected session variables.  Standalone component
    runs receive a deliberately non-upgradable ``mode=standalone`` record so
    diagnostic PASS output can never be confused with final bundle evidence.
    """

    bundle_session = certification_session_from_environment(component=component, env=env)
    if bundle_session is not None:
        return bundle_session
    module_path = Path(__file__).resolve()
    try:
        workspace_root = module_path.parents[5]
        fingerprint = _source_workspace_fingerprint(workspace_root)
    except (IndexError, OSError, RealModelBundleError):
        fingerprint = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()
    safe_identity = _identity_tuple(identity or {})
    return {
        "contract": _SESSION_CONTRACT,
        "mode": "standalone",
        "session_id": "standalone-" + secrets.token_hex(16),
        "workspace_fingerprint_sha256": fingerprint,
        "component": component,
        "emitted_at": _iso(_utc_now()),
        "identity_bound": bool(all(safe_identity)),
    }


def _identity_tuple(identity: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(identity.get(field) or "").strip() for field in _IDENTITY_FIELDS)


def _validate_identity(identity: Any) -> tuple[str, ...]:
    if not isinstance(identity, Mapping):
        raise RealModelBundleError("component_identity_missing", "component identity is missing")
    values = _identity_tuple(identity)
    if any(not value for value in values):
        raise RealModelBundleError(
            "component_identity_missing",
            "component identity is missing a required safe field",
        )
    if identity.get("official_endpoint") is not True or identity.get("https") is not True:
        raise RealModelBundleError(
            "component_identity_unofficial",
            "component identity is not an official HTTPS provider identity",
        )
    return values


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _successful_calls(payload: Mapping[str, Any]) -> int:
    calls = payload.get("calls")
    calls = calls if isinstance(calls, Mapping) else {}
    for key in ("successful_calls", "used_calls", "total_calls"):
        value = _positive_int(calls.get(key))
        if value:
            return value
    return 0


def _validate_smoke(component: Mapping[str, Any]) -> int:
    attestation = component.get("attestation")
    if not isinstance(attestation, Mapping) or str(attestation.get("contract") or "") != "real-model-response-attestation@1":
        raise RealModelBundleError("smoke_attestation_missing", "smoke response attestation is missing")
    calls = _successful_calls(component)
    if calls < 1:
        raise RealModelBundleError("smoke_call_evidence_missing", "smoke call evidence is missing")
    return calls


def _validate_semantic(component: Mapping[str, Any]) -> int:
    prototype_count = _positive_int(component.get("prototype_count"))
    cases = component.get("cases")
    cases = list(cases) if isinstance(cases, list) else []
    if prototype_count < 12 or len(cases) < 12:
        raise RealModelBundleError(
            "semantic_coverage_insufficient",
            "semantic certification must cover all twelve protected prototypes",
        )
    for row in cases:
        attestation = row.get("provider_attestation") if isinstance(row, Mapping) else None
        if not isinstance(attestation, Mapping) or str(attestation.get("contract") or "") != "real-model-metadata-attestation@1":
            raise RealModelBundleError(
                "semantic_attestation_missing",
                "every semantic prototype must include provider metadata attestation",
            )
    calls = _successful_calls(component)
    if calls < prototype_count:
        raise RealModelBundleError(
            "semantic_call_evidence_insufficient",
            "semantic certification call evidence is incomplete",
        )
    return calls


def _validate_lifecycle(component: Mapping[str, Any]) -> int:
    if _positive_int(component.get("turns")) < 2:
        raise RealModelBundleError(
            "lifecycle_coverage_insufficient",
            "full lifecycle certification must attest at least two public turns",
        )
    try:
        transaction_delta = int(component.get("transaction_delta"))
    except (TypeError, ValueError):
        transaction_delta = -1
    if transaction_delta != 0:
        raise RealModelBundleError(
            "lifecycle_transaction_delta_invalid",
            "read-only real-model lifecycle changed transaction state",
        )
    rows = component.get("model_attestations")
    rows = list(rows) if isinstance(rows, list) else []
    if len(rows) < 2:
        raise RealModelBundleError(
            "lifecycle_attestation_missing",
            "full lifecycle model-call attestations are incomplete",
        )
    calls = 0
    for row in rows:
        row = row if isinstance(row, Mapping) else {}
        call_count = _positive_int(row.get("call_count"))
        total_tokens = _positive_int(row.get("total_tokens"))
        if call_count < 1 or total_tokens < 1:
            raise RealModelBundleError(
                "lifecycle_attestation_invalid",
                "every lifecycle turn must include positive call and token evidence",
            )
        calls += call_count
    return calls


def validate_certification_components(
    *,
    components: Mapping[str, Mapping[str, Any]],
    session_id: str,
    workspace_fingerprint: str,
) -> dict[str, Any]:
    """Validate three safe component outputs from one live parent session."""

    if not _SESSION_RE.fullmatch(str(session_id or "")):
        raise RealModelBundleError("session_id_invalid", "certification session id is invalid")
    fingerprint = str(workspace_fingerprint or "").strip().casefold()
    if not _SHA256_RE.fullmatch(fingerprint):
        raise RealModelBundleError(
            "workspace_fingerprint_invalid",
            "workspace fingerprint must be a lowercase SHA-256 value",
        )
    if not isinstance(components, Mapping):
        raise RealModelBundleError("components_invalid", "certification components must be a mapping")
    missing = [name for name in _REQUIRED_COMPONENTS if name not in components]
    if missing:
        raise RealModelBundleError(
            "required_component_missing",
            "required certification component is missing: " + ",".join(missing),
        )
    unexpected = sorted(set(components) - set(_REQUIRED_COMPONENTS))
    if unexpected:
        raise RealModelBundleError(
            "unexpected_component",
            "unexpected certification component: " + ",".join(unexpected),
        )

    canonical_identity: tuple[str, ...] | None = None
    safe_identity: dict[str, str] | None = None
    call_counts: dict[str, int] = {}
    emitted_at: list[str] = []
    now = _utc_now()
    for name in _REQUIRED_COMPONENTS:
        payload = components[name]
        if not isinstance(payload, Mapping) or str(payload.get("status") or "") != "PASS":
            raise RealModelBundleError(
                "component_not_passed",
                f"certification component {name} did not pass",
            )
        identity_values = _validate_identity(payload.get("identity"))
        if canonical_identity is None:
            canonical_identity = identity_values
            safe_identity = dict(zip(_IDENTITY_FIELDS, identity_values, strict=True))
        elif identity_values != canonical_identity:
            raise RealModelBundleError(
                "component_identity_mismatch",
                "certification component provider identities do not match",
            )

        session = payload.get("certification_session")
        if not isinstance(session, Mapping) or str(session.get("contract") or "") != _SESSION_CONTRACT:
            raise RealModelBundleError(
                "component_session_missing",
                f"certification component {name} has no live session evidence",
            )
        if str(session.get("mode") or "") != "bundle":
            raise RealModelBundleError("component_session_mode_invalid", "component session mode is not bundle")
        if str(session.get("session_id") or "") != session_id:
            raise RealModelBundleError(
                "component_session_mismatch",
                "certification component was produced by another session",
            )
        if str(session.get("workspace_fingerprint_sha256") or "").casefold() != fingerprint:
            raise RealModelBundleError(
                "component_workspace_mismatch",
                "certification component was produced from another workspace snapshot",
            )
        if str(session.get("component") or "") != name:
            raise RealModelBundleError(
                "component_name_mismatch",
                "certification component session label is inconsistent",
            )
        emitted = _parse_timestamp(session.get("emitted_at"), code="component_emitted_at_invalid")
        if emitted > now + timedelta(minutes=5) or emitted < now - timedelta(hours=6):
            raise RealModelBundleError(
                "component_evidence_stale",
                "certification component evidence is outside the live execution window",
            )
        emitted_at.append(_iso(emitted))

        if name == "smoke":
            call_counts[name] = _validate_smoke(payload)
        elif name == "semantic":
            call_counts[name] = _validate_semantic(payload)
        else:
            call_counts[name] = _validate_lifecycle(payload)

    return {
        "contract": _BUNDLE_CONTRACT,
        "status": "PASS",
        "session_id": session_id,
        "workspace_fingerprint_sha256": fingerprint,
        "identity": safe_identity or {},
        "component_count": len(_REQUIRED_COMPONENTS),
        "components": list(_REQUIRED_COMPONENTS),
        "component_emitted_at": emitted_at,
        "attested_model_calls_by_component": call_counts,
        "total_attested_model_calls": sum(call_counts.values()),
        "completed_at": _iso(now),
    }


def _source_workspace_fingerprint(workspace_root: Path) -> str:
    """Fingerprint source/governance inputs while excluding mutable evidence."""

    root = workspace_root.resolve()
    excluded_parts = {
        ".git", ".quality", ".pytest_cache", "__pycache__", "node_modules",
        ".venv", "venv", "dist", "build", "coverage", "runtime",
    }
    allowed_suffixes = {
        ".py", ".json", ".md", ".toml", ".yaml", ".yml", ".js", ".jsx",
        ".ts", ".tsx", ".css", ".html", ".sh", ".txt", ".lock",
    }
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or any(part in excluded_parts for part in path.relative_to(root).parts):
            continue
        if path.name not in {"VERSION", "Dockerfile", "Makefile"} and path.suffix.casefold() not in allowed_suffixes:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    if count == 0:
        raise RealModelBundleError("workspace_empty", "workspace has no certifiable source files")
    return digest.hexdigest()


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RealModelBundleError("component_output_invalid", "component did not emit a JSON object")


def _default_component_runner(
    *,
    component: str,
    script_path: Path,
    env: Mapping[str, str],
    workspace_root: Path,
) -> dict[str, Any]:
    command = [sys.executable, "-B", str(script_path)]
    completed = subprocess.run(
        command,
        cwd=str(workspace_root / "services" / "agent-service"),
        env={**os.environ, **{str(key): str(value) for key, value in env.items()}},
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
    )
    payload = _last_json_line(completed.stdout)
    payload.setdefault("component_exit_code", completed.returncode)
    if completed.returncode not in (0, 78) and str(payload.get("status") or "") == "PASS":
        raise RealModelBundleError(
            "component_exit_status_mismatch",
            f"component {component} reported PASS with a failing process exit code",
        )
    return payload


def run_certification_bundle(
    *,
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
    component_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute all protected components live; never consume evidence paths."""

    source = dict(os.environ if env is None else env)
    try:
        identity = resolve_real_model_identity(source)
    except RealModelCertificationError as exc:
        return {
            "contract": _BUNDLE_CONTRACT,
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": "real_model_environment_unavailable" if exc.environment_blocked else "real_model_identity_invalid",
            "error_code": exc.code,
            "component_launch_count": 0,
            "components_started": 0,
        }

    workspace = Path(workspace_root).resolve()
    fingerprint = _source_workspace_fingerprint(workspace)
    started_at = _utc_now()
    session_id = "rmcert-" + secrets.token_hex(24)
    scripts = {
        "smoke": workspace / "services" / "agent-service" / "scripts" / "verify_model_smoke.py",
        "semantic": workspace / "services" / "agent-service" / "scripts" / "verify_preprod_conversation_smoke.py",
        "lifecycle": workspace / "services" / "agent-service" / "scripts" / "verify_preprod_full_lifecycle.py",
    }
    if any(not path.is_file() for path in scripts.values()):
        raise RealModelBundleError("component_script_missing", "one or more certification component scripts are missing")
    runner = component_runner or _default_component_runner
    components: dict[str, Mapping[str, Any]] = {}
    launched = 0
    for component in _REQUIRED_COMPONENTS:
        component_env = dict(source)
        component_env.update({
            # Real-model certification must exercise the same independent-verifier
            # authority as protected Runtime.  Without an explicit protected profile,
            # resolve_verifier_mode(auto) degrades to candidate-only local evidence.
            "APP_PROFILE": "preprod",
            **_PROTECTED_VERIFIER_MODES,
            _SESSION_ENV: session_id,
            _WORKSPACE_ENV: fingerprint,
            _STARTED_ENV: _iso(started_at),
            _COMPONENT_ENV: component,
        })
        payload = runner(
            component=component,
            script_path=scripts[component],
            env=component_env,
            workspace_root=workspace,
        )
        launched += 1
        if not isinstance(payload, Mapping):
            raise RealModelBundleError("component_output_invalid", f"component {component} returned no mapping")
        status = str(payload.get("status") or "")
        if status == "BLOCKED_BY_ENVIRONMENT":
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or "real_model_environment_unavailable"),
                "error_code": str(payload.get("error_code") or "component_environment_blocked"),
                "blocked_component": component,
                "component_launch_count": launched,
            }
        if status != "PASS":
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "failed_component": component,
                "error_code": str(payload.get("error_code") or "component_failed"),
                "component_launch_count": launched,
            }
        components[component] = payload

    result = validate_certification_components(
        components=components,
        session_id=session_id,
        workspace_fingerprint=fingerprint,
    )
    if _identity_tuple(result.get("identity") or {}) != _identity_tuple(identity):
        raise RealModelBundleError(
            "bundle_preflight_identity_mismatch",
            "component identity differs from bundle preflight identity",
        )
    result["component_launch_count"] = launched
    result["components_started"] = launched
    return result


__all__ = [
    "RealModelBundleError",
    "certification_session_from_environment",
    "certification_session_evidence",
    "workspace_fingerprint",
    "validate_certification_components",
    "run_certification_bundle",
]
