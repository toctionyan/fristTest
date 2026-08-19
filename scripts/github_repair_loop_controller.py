#!/usr/bin/env python3
"""Durable outer controller for governed Repair -> Verify feedback loops.

The controller owns repair-round accounting across Stage 2 and Stage 3. A
Stage-2 model/fixer cycle is not a repair round, and a GitHub workflow rerun is
not a repair round. One repair round means one source candidate was produced
and independently validated. Independent validation failures are typed before
routing so harness/environment retries do not consume repair budget and
protected-oracle disagreements cannot be "fixed" by mutating the judge.

M8.6 keeps the historical product path unchanged and adds one immutable
``CONTROL_PLANE_IMPLEMENTATION`` route. Once Stage 1 has semantically bound that
route, later rounds may only repair the exact same engineering-verifier
implementation scope. Tests/oracles remain evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_autonomy_continuation import (  # type: ignore  # noqa: E402
    AutonomyContinuationError,
    validate_autonomy_continuation,
)
from task_run import TaskRunStore  # type: ignore  # noqa: E402

LOOP_SCHEMA = "github-governed-repair-loop@1"
FEEDBACK_SCHEMA = "github-governed-repair-feedback@1"
STAGE2_SCHEMA = "github-governed-repair-stage2@1"
STAGE3_SCHEMAS = {"github-governed-repair-stage3@1", "github-governed-repair-stage3@2"}
FAILURE_SCHEMA = "github-failure-ingest@1"
SOURCE_AUTHORITY_SCHEMA = "github-stage2-source-failure-authority@2"
MAX_REPAIR_ROUNDS = 8
STAGNATION_LIMIT = 2
MAX_VALIDATION_RETRIES_PER_CANDIDATE = 3
MAX_TEXT = 80_000
PRODUCT_DOMAIN = "PRODUCT_CODE"
CONTROL_DOMAIN = "CONTROL_PLANE_IMPLEMENTATION"
PRODUCT_FAILURE = "PRODUCT_SOURCE_FAILURE"
CONTROL_FAILURE = "CONTROL_PLANE_IMPLEMENTATION_FAILURE"
REPAIRABLE_FAILURES = {PRODUCT_FAILURE, CONTROL_FAILURE}
CONTROL_COMPONENT = "skill-control-plane"

_SOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:services/agent-service/(?:src|app)/|"
    r"services/business-service/business_service/|contracts/|web/|"
    r"scripts/verify_engineering_)[A-Za-z0-9_./@+\-]+"
    r"\.(?:py|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|md|sh))(?![A-Za-z0-9_.-])"
)
_TEST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:tests/|skill-system/tests/|services/[^\s:]+/tests/|"
    r"web/[^\s:]*tests?/)[A-Za-z0-9_./@+\-]+)"
)
HARNESS_TERMS = (
    "modulenotfounderror: no module named 'agent_core'",
    "app_profile is required",
    "command not found",
    "no such file or directory",
    "targeted python runtime directory is required",
    "candidate checkout drifted",
    "validation checkout drifted",
    "playwright executable doesn't exist",
    "failed to download browser",
)
ENVIRONMENT_TERMS = (
    "could not resolve host",
    "temporary failure in name resolution",
    "connection refused",
    "service unavailable",
    "rate limit exceeded",
    "authentication failed",
    "invalid api key",
    "incorrect api key",
    "no space left on device",
    "runner lost communication",
)
ASSERTION_TERMS = (
    "assertionerror",
    "assert ",
    "differing items:",
    "full diff:",
    "failed (failures=",
)


class RepairLoopError(RuntimeError):
    """Fail-closed outer-loop routing error."""


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RepairLoopError(f"JSON object required: {path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _int(value: object, default: int = 0) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _normalize_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return ""
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return ""
    return pure.as_posix()


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _candidate_paths_digest(paths: Iterable[str]) -> str:
    normalized = [_normalize_path(value) for value in paths]
    if any(not path for path in normalized) or normalized != _unique(normalized):
        raise RepairLoopError("outer-loop repair paths are invalid")
    if not normalized:
        raise RepairLoopError("outer-loop repair paths are empty")
    canonical = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _task(path: Path) -> TaskRunStore:
    return TaskRunStore(path.resolve(), _load(path))


def _combined_result_text(rows: Iterable[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("stdout", "stderr"):
            text = str(row.get(key) or "")
            if text:
                chunks.append(text)
    return "\n".join(chunks)[-MAX_TEXT:]


def _failed_components(
    targeted: dict[str, Any], quick_summary: dict[str, Any] | None = None
) -> list[str]:
    values = [
        str(row.get("component") or "unknown")
        for row in targeted.get("results") or []
        if isinstance(row, dict) and row.get("passed") is not True
    ]
    if quick_summary is not None:
        values.extend(
            str(row.get("id") or "unknown")
            for row in quick_summary.get("results") or []
            if isinstance(row, dict) and str(row.get("status") or "") != "PASS"
        )
    return _unique(values)


def _extract_source_paths(text: str, allowed: set[str]) -> list[str]:
    found = [_normalize_path(match) for match in _SOURCE_PATH_RE.findall(text)]
    return [path for path in _unique(found) if path in allowed]


def _original_domain(original_failure: Mapping[str, Any]) -> str:
    classification = str(original_failure.get("classification") or "").strip()
    supplied = str(original_failure.get("repair_domain") or "").strip()
    if classification == "code_or_contract":
        if supplied and supplied != PRODUCT_DOMAIN:
            raise RepairLoopError("product outer-loop failure attempted repair-domain switch")
        return PRODUCT_DOMAIN
    if classification != "control_plane_implementation" or supplied != CONTROL_DOMAIN:
        raise RepairLoopError(
            f"outer-loop original failure is outside repair authority: {classification!r}/{supplied!r}"
        )
    route = original_failure.get("repair_route")
    if route is not None:
        if not isinstance(route, Mapping):
            raise RepairLoopError("control-plane semantic route must be an object")
        if (
            route.get("repair_class") != "CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE"
            or route.get("repair_domain") != CONTROL_DOMAIN
            or route.get("automatic_write_allowed") is not True
            or route.get("test_write_allowed") is not False
            or route.get("acceptance_write_allowed") is not False
            or route.get("scope_expansion_allowed") is not False
        ):
            raise RepairLoopError("control-plane semantic route crossed an authority boundary")
    candidates = [_normalize_path(item) for item in original_failure.get("candidate_paths") or []]
    if (
        not candidates
        or any(not path for path in candidates)
        or candidates != _unique(candidates)
        or any(not path.startswith("scripts/verify_engineering_") or not path.endswith(".py") for path in candidates)
    ):
        raise RepairLoopError("control-plane original candidate scope is invalid")
    if isinstance(route, Mapping):
        routed = [_normalize_path(item) for item in route.get("allowed_write_paths") or []]
        if routed != candidates:
            raise RepairLoopError("control-plane semantic route scope drifted")
    return CONTROL_DOMAIN


def _failure_fingerprint(
    *,
    failure_class: str,
    repair_paths: list[str],
    targeted: dict[str, Any],
    quick_summary: dict[str, Any] | None = None,
) -> str:
    rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = [
        row
        for row in targeted.get("results") or []
        if isinstance(row, dict) and row.get("passed") is not True
    ]
    if quick_summary is not None:
        evidence_rows.extend(
            row
            for row in quick_summary.get("results") or []
            if isinstance(row, dict) and str(row.get("status") or "") != "PASS"
        )
    for row in evidence_rows:
        text = (str(row.get("stdout") or "") + "\n" + str(row.get("stderr") or ""))[-12_000:]
        rows.append(
            {
                "component": str(row.get("component") or row.get("id") or "unknown"),
                "exit_code": row.get("exit_code"),
                "timed_out": row.get("timed_out") is True or row.get("exit_code") == 124,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    payload = {"failure_class": failure_class, "repair_paths": repair_paths, "rows": rows}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _classify_rows(
    rows: list[dict[str, Any]],
    *,
    original_failure: dict[str, Any],
    context: str,
) -> tuple[str, list[str], str]:
    if any(row.get("timed_out") is True or row.get("exit_code") == 124 for row in rows):
        return "TRANSIENT_INFRA_FAILURE", [], f"{context} timed out"

    text = _combined_result_text(rows)
    low = text.casefold()
    if any(term in low for term in ENVIRONMENT_TERMS):
        return "ENVIRONMENT_FAILURE", [], f"{context} hit an external environment failure"
    if any(term in low for term in HARNESS_TERMS):
        return "HARNESS_FAILURE", [], f"{context} harness/runtime contract failed"

    domain = _original_domain(original_failure)
    allowed = {
        path
        for path in (_normalize_path(item) for item in original_failure.get("candidate_paths") or [])
        if path
    }
    source_paths = _extract_source_paths(text, allowed)
    if source_paths:
        failure_class = CONTROL_FAILURE if domain == CONTROL_DOMAIN else PRODUCT_FAILURE
        return failure_class, source_paths, f"{context} implicated governed writable source"

    has_test_evidence = bool(_TEST_PATH_RE.search(text)) or "test_" in low
    has_assertion = any(term in low for term in ASSERTION_TERMS)
    if domain == CONTROL_DOMAIN and context == "targeted validation":
        failed_control = any(
            isinstance(row, dict)
            and str(row.get("component") or "") == CONTROL_COMPONENT
            and row.get("passed") is not True
            for row in rows
        )
        if failed_control and has_test_evidence and has_assertion:
            exact_scope = [_normalize_path(item) for item in original_failure.get("candidate_paths") or []]
            return (
                CONTROL_FAILURE,
                exact_scope,
                "targeted skill-control-plane assertion failure remains inside the immutable verifier implementation scope",
            )

    if has_test_evidence and has_assertion:
        return (
            "TEST_CONTRACT_REVIEW_REQUIRED",
            [],
            f"{context} found an oracle/semantic assertion mismatch without a governed source stack path",
        )
    return "UNKNOWN_FAILURE", [], f"{context} failed without a safe repair-path classification"


def classify_targeted_failure(
    targeted: dict[str, Any],
    *,
    original_failure: dict[str, Any],
) -> tuple[str, list[str], str]:
    if targeted.get("schema") not in STAGE3_SCHEMAS:
        raise RepairLoopError("unsupported Stage-3 targeted result schema")
    if targeted.get("status") == "TARGETED_VALIDATION_PASSED":
        return "PASS", [], "targeted validation passed"
    if targeted.get("status") != "TARGETED_VALIDATION_FAILED":
        return "HARNESS_FAILURE", [], "Stage-3 did not produce a complete targeted verdict"
    rows = [row for row in targeted.get("results") or [] if isinstance(row, dict)]
    return _classify_rows(rows, original_failure=original_failure, context="targeted validation")


def classify_independent_failure(
    targeted: dict[str, Any],
    *,
    original_failure: dict[str, Any],
    quick_summary: dict[str, Any] | None = None,
) -> tuple[str, list[str], str]:
    targeted_class, paths, reason = classify_targeted_failure(
        targeted, original_failure=original_failure
    )
    if targeted_class != "PASS":
        return targeted_class, paths, reason
    if quick_summary is None:
        return "HARNESS_FAILURE", [], "targeted validation passed but complete Quick evidence is missing"
    if (
        quick_summary.get("mode") == "quick"
        and quick_summary.get("run_kind") == "verification"
        and quick_summary.get("decision") == "PASS"
        and quick_summary.get("loop_status") == "CI_VERIFIED"
        and quick_summary.get("completion_eligible") is True
    ):
        return "PASS", [], "targeted and complete Quick validation passed"
    if (
        quick_summary.get("decision") == "BLOCKED_BY_ENVIRONMENT"
        or quick_summary.get("loop_status") == "BLOCKED_BY_ENVIRONMENT"
    ):
        return "ENVIRONMENT_FAILURE", [], "complete Quick validation was blocked by environment"
    rows = [
        row
        for row in quick_summary.get("results") or []
        if isinstance(row, dict) and str(row.get("status") or "") != "PASS"
    ]
    if not rows:
        return "HARNESS_FAILURE", [], "Quick evidence did not pass but exposed no failed gate rows"
    return _classify_rows(rows, original_failure=original_failure, context="complete Quick validation")


def _authority_digest(authority: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _resolve_original_failure(
    *,
    stage2: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Prefer the exact Stage-1 authority snapshot that Stage 2 actually consumed."""
    raw = stage2.get("source_failure_authority")
    if raw is None:
        _original_domain(fallback)
        return fallback
    if not isinstance(raw, dict):
        raise RepairLoopError("Stage-2 source failure authority must be an object")
    authority = dict(raw)
    if authority.get("authority_schema") != SOURCE_AUTHORITY_SCHEMA:
        raise RepairLoopError("Stage-2 source failure authority schema is invalid")
    expected_digest = str(stage2.get("source_failure_authority_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise RepairLoopError("Stage-2 source failure authority digest is missing or invalid")
    if _authority_digest(authority) != expected_digest:
        raise RepairLoopError("Stage-2 source failure authority digest mismatch")
    if authority.get("schema") != FAILURE_SCHEMA or authority.get("status") != "INGESTED":
        raise RepairLoopError("Stage-2 source failure authority contract is invalid")
    if authority.get("repair_allowed") is not True or authority.get("same_repository") is not True:
        raise RepairLoopError("Stage-2 source failure authority did not authorize repair")

    paths = authority.get("candidate_paths")
    if not isinstance(paths, list) or not paths:
        raise RepairLoopError("Stage-2 source failure authority has no candidate paths")
    normalized = [_normalize_path(item) for item in paths]
    if any(not path for path in normalized) or normalized != _unique(normalized):
        raise RepairLoopError("Stage-2 source failure authority candidate paths are invalid")

    expected = {
        "repository": stage2.get("repository"),
        "workflow_run_id": str(stage2.get("workflow_run_id")),
        "head_sha": stage2.get("head_sha"),
        "failure_signature": stage2.get("failure_signature"),
    }
    for key, value in expected.items():
        if not value or str(authority.get(key)) != str(value):
            raise RepairLoopError(f"Stage-2 source failure authority binding mismatch: {key}")

    classification = str(authority.get("classification") or "")
    if classification == "control_plane_implementation":
        if str(authority.get("repair_domain") or "") != CONTROL_DOMAIN:
            raise RepairLoopError("Stage-2 control-plane source authority domain drift")
        route_sha = str(authority.get("repair_route_sha256") or "")
        fallback_route = fallback.get("repair_route") if isinstance(fallback.get("repair_route"), dict) else None
        if (
            not re.fullmatch(r"[0-9a-f]{64}", route_sha)
            or fallback_route is None
            or str(fallback_route.get("route_sha256") or "") != route_sha
        ):
            raise RepairLoopError("control-plane semantic route evidence is unavailable or stale")
        authority["repair_route"] = dict(fallback_route)
    elif classification == "code_or_contract":
        if str(authority.get("repair_domain") or "") not in {"", PRODUCT_DOMAIN}:
            raise RepairLoopError("Stage-2 product source authority domain drift")
    else:
        raise RepairLoopError("Stage-2 source failure authority classification is not repairable")
    _original_domain(authority)
    return authority


def _validate_bindings(
    *,
    task: TaskRunStore,
    stage2: dict[str, Any],
    plan: dict[str, Any],
    original_failure: dict[str, Any],
) -> dict[str, Any]:
    if stage2.get("schema") != STAGE2_SCHEMA or stage2.get("status") != "REPAIR_CANDIDATE_READY":
        raise RepairLoopError("Stage-2 result is not a repair candidate")
    if plan.get("schema") not in STAGE3_SCHEMAS or plan.get("status") != "CANDIDATE_PREPARED":
        raise RepairLoopError("Stage-3 plan is not a prepared candidate")
    if original_failure.get("schema") != FAILURE_SCHEMA or original_failure.get("status") != "INGESTED":
        raise RepairLoopError("original failure-case evidence is invalid")
    binding = task.payload.get("binding") if isinstance(task.payload.get("binding"), dict) else {}
    expected = {
        "repository": stage2.get("repository"),
        "workflow_run_id": str(stage2.get("workflow_run_id")),
        "head_sha": stage2.get("head_sha"),
        "failure_signature": stage2.get("failure_signature"),
    }
    for key, value in expected.items():
        if not value or str(binding.get(key)) != str(value):
            raise RepairLoopError(f"TaskRun/Stage-2 binding mismatch: {key}")
        if str(original_failure.get(key)) != str(value):
            raise RepairLoopError(f"original failure/Stage-2 binding mismatch: {key}")
    for key in ("workflow_name", "workflow_run_attempt"):
        if str(original_failure.get(key)) != str(binding.get(key)):
            raise RepairLoopError(f"original failure/TaskRun binding mismatch: {key}")
    domain = _original_domain(original_failure)
    if str(stage2.get("repair_domain") or domain) != domain:
        raise RepairLoopError("Stage-2 repair domain differs from original failure")
    if str(plan.get("repair_domain") or domain) != domain:
        raise RepairLoopError("Stage-3 repair domain differs from original failure")
    if domain == CONTROL_DOMAIN:
        route_sha = str((original_failure.get("repair_route") or {}).get("route_sha256") or "")
        if str(binding.get("repair_domain") or "") != domain:
            raise RepairLoopError("TaskRun control-plane repair domain binding mismatch")
        if str(binding.get("repair_route_sha256") or "") != route_sha:
            raise RepairLoopError("TaskRun control-plane semantic route binding mismatch")
        if str(plan.get("repair_route_sha256") or "") != route_sha:
            raise RepairLoopError("Stage-3 control-plane semantic route binding mismatch")
    if str(plan.get("source_run_id")) != str(stage2.get("workflow_run_id")):
        raise RepairLoopError("Stage-3 plan source run does not match Stage-2")
    if str(plan.get("head_sha")) != str(stage2.get("head_sha")):
        raise RepairLoopError("Stage-3 plan head does not match Stage-2")
    if str(plan.get("patch_sha256")) != str(stage2.get("patch_sha256")):
        raise RepairLoopError("Stage-3 plan patch digest does not match Stage-2")
    return binding


def _validate_continuation_for_binding(
    raw: object,
    *,
    binding: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RepairLoopError(f"{source} autonomy continuation must be an object")
    try:
        return validate_autonomy_continuation(
            raw,
            source_run_id=binding.get("workflow_run_id"),
            source_run_attempt=binding.get("workflow_run_attempt"),
            source_head_sha=binding.get("head_sha"),
            failure_signature=binding.get("failure_signature"),
        )
    except AutonomyContinuationError as exc:
        raise RepairLoopError(str(exc)) from exc


def _assert_continuation_budget_binding(
    state: dict[str, Any],
    continuation: dict[str, Any],
    *,
    source: str,
) -> None:
    if _int(state.get("max_repair_rounds"), 0) != int(continuation["max_repair_rounds"]):
        raise RepairLoopError(f"{source} repair budget drifted from autonomy continuation")
    if _int(state.get("max_validation_retries_per_candidate"), 0) != int(
        continuation["max_validation_retries"]
    ):
        raise RepairLoopError(f"{source} validation retry budget drifted from autonomy continuation")


def _resolve_autonomy_continuation(
    *,
    stage2: dict[str, Any],
    previous: dict[str, Any],
    binding: dict[str, Any],
    task_loop: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    task_loop = task_loop or {}
    raw = stage2.get("autonomy_continuation")
    artifact_prior_raw = previous.get("autonomy_continuation") if previous else None
    task_prior_raw = task_loop.get("autonomy_continuation") if task_loop else None
    prior_started = bool(previous) or bool(task_loop)

    artifact_prior: dict[str, Any] | None = None
    task_prior: dict[str, Any] | None = None
    if artifact_prior_raw is not None:
        artifact_prior = _validate_continuation_for_binding(
            artifact_prior_raw, binding=binding, source="previous outer-loop artifact"
        )
        _assert_continuation_budget_binding(
            previous, artifact_prior, source="previous outer-loop artifact"
        )
    if task_prior_raw is not None:
        task_prior = _validate_continuation_for_binding(
            task_prior_raw, binding=binding, source="durable TaskRun metadata"
        )
        _assert_continuation_budget_binding(
            task_loop, task_prior, source="durable TaskRun metadata"
        )
    if artifact_prior is not None and task_prior is not None and artifact_prior != task_prior:
        raise RepairLoopError("previous outer-loop artifact conflicts with durable TaskRun metadata")

    prior = task_prior if task_prior is not None else artifact_prior
    if raw is None:
        if prior is not None:
            raise RepairLoopError("autonomy continuation disappeared between repair rounds")
        return None
    current = _validate_continuation_for_binding(raw, binding=binding, source="Stage-2")
    if prior_started:
        if prior is None:
            raise RepairLoopError("autonomy continuation appeared after the outer loop already started")
        if prior != current:
            raise RepairLoopError("autonomy continuation changed between repair rounds")
    return current


def _existing_loop_metadata(task: TaskRunStore) -> dict[str, Any]:
    metadata = task.payload.get("metadata") if isinstance(task.payload.get("metadata"), dict) else {}
    loop = metadata.get("repair_loop") if isinstance(metadata.get("repair_loop"), dict) else {}
    return dict(loop)


def _safe_feedback_failure(
    original: dict[str, Any],
    *,
    repair_paths: list[str],
    repair_round: int,
    verification_attempt: int,
    failure_fingerprint: str,
    autonomy_continuation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domain = _original_domain(original)
    routed_paths = [_normalize_path(item) for item in repair_paths]
    if any(not path for path in routed_paths) or routed_paths != _unique(routed_paths):
        raise RepairLoopError("feedback repair scope is malformed")
    original_paths = [_normalize_path(item) for item in original.get("candidate_paths") or []]
    if any(path not in original_paths for path in routed_paths):
        raise RepairLoopError("feedback attempted to expand original repair scope")
    if domain == CONTROL_DOMAIN:
        # The semantic route digest is immutable. Narrowing it would create a new
        # authority identity, so a later control repair round keeps the exact
        # original verifier implementation scope.
        if routed_paths != original_paths:
            raise RepairLoopError("control-plane feedback cannot change semantic route scope")
        if any(not path.startswith("scripts/verify_engineering_") or not path.endswith(".py") for path in routed_paths):
            raise RepairLoopError("control-plane feedback contains an unbounded path")
    if not routed_paths:
        raise RepairLoopError("feedback repair scope is empty")

    candidate_paths_sha256 = _candidate_paths_digest(routed_paths)
    feedback = dict(original)
    feedback.pop("authority_schema", None)
    feedback["classification"] = (
        "control_plane_implementation" if domain == CONTROL_DOMAIN else "code_or_contract"
    )
    feedback["repair_domain"] = domain
    feedback["repair_allowed"] = True
    feedback["candidate_paths"] = routed_paths
    failure_class = CONTROL_FAILURE if domain == CONTROL_DOMAIN else PRODUCT_FAILURE
    gate_id = CONTROL_COMPONENT if domain == CONTROL_DOMAIN else "governed-stage3-targeted"
    failure_kind = "control_plane_implementation" if domain == CONTROL_DOMAIN else "product_source"
    feedback["failed_gates"] = [
        {
            "gate_id": gate_id,
            "status": "FAIL",
            "category": "independent-validation",
            "owner": "governed repair outer controller",
            "failure_kind": failure_kind,
            "summary": (
                "independent validation implicated the immutable governed repair scope; "
                "protected test/oracle contents are intentionally withheld from the repair actor"
            ),
        }
    ]
    feedback["failure_summary"] = (
        "Independent Stage-3 validation failed after repair round "
        f"{repair_round}. Re-diagnose only the immutable governed paths listed in candidate_paths. "
        "Protected tests/oracles are evidence only and must not be modified. "
        f"Verification attempt={verification_attempt}; failure_fingerprint={failure_fingerprint}."
    )
    feedback["loop_feedback"] = {
        "schema": FEEDBACK_SCHEMA,
        "repair_round": repair_round,
        "next_repair_round": repair_round + 1,
        "verification_attempt": verification_attempt,
        "failure_class": failure_class,
        "repair_domain": domain,
        "failure_fingerprint": failure_fingerprint,
        "candidate_paths_sha256": candidate_paths_sha256,
        "scope_expanded": False,
    }
    if autonomy_continuation is not None:
        feedback["loop_feedback"]["autonomy_continuation_sha256"] = str(
            autonomy_continuation.get("continuation_sha256") or ""
        )
    return feedback


def route_failure(
    *,
    task_run_path: Path,
    stage2_result_path: Path,
    stage3_plan_path: Path,
    targeted_result_path: Path,
    quick_summary_path: Path | None,
    validation_result_path: Path | None,
    original_failure_path: Path,
    seed_patch_path: Path,
    output_dir: Path,
    stage3_run_id: str,
    stage3_run_attempt: int,
    previous_state_path: Path | None = None,
) -> dict[str, Any]:
    task = _task(task_run_path)
    stage2 = _load(stage2_result_path)
    plan = _load(stage3_plan_path)
    targeted = _load(targeted_result_path)
    quick_summary = _load(quick_summary_path) if quick_summary_path and quick_summary_path.is_file() else None
    validation_result = (
        _load(validation_result_path)
        if validation_result_path and validation_result_path.is_file()
        else None
    )
    fallback_failure = _load(original_failure_path)
    original_failure = _resolve_original_failure(stage2=stage2, fallback=fallback_failure)
    domain = _original_domain(original_failure)
    binding = _validate_bindings(
        task=task,
        stage2=stage2,
        plan=plan,
        original_failure=original_failure,
    )
    previous: dict[str, Any] = {}
    if previous_state_path and previous_state_path.is_file():
        previous = _load(previous_state_path)
        if previous.get("schema") != LOOP_SCHEMA:
            raise RepairLoopError("previous outer-loop state schema is invalid")
        if str(previous.get("source_run_id")) != str(binding.get("workflow_run_id")):
            raise RepairLoopError("previous outer-loop state belongs to another source run")
        if str(previous.get("repair_domain") or domain) != domain:
            raise RepairLoopError("previous outer-loop state changed repair domain")

    loop_meta = _existing_loop_metadata(task)
    if loop_meta and str(loop_meta.get("repair_domain") or domain) != domain:
        raise RepairLoopError("durable TaskRun outer-loop state changed repair domain")
    autonomy_continuation = _resolve_autonomy_continuation(
        stage2=stage2,
        previous=previous,
        task_loop=loop_meta,
        binding=binding,
    )

    event_key = f"{stage3_run_id}/{stage3_run_attempt}"
    if previous.get("last_verification_event") == event_key:
        duplicate = dict(previous)
        duplicate["action"] = "NOOP_DUPLICATE"
        duplicate["duplicate_event"] = event_key
        _write(output_dir / "loop-state.json", duplicate)
        shutil.copyfile(task_run_path, output_dir / "task-run.json")
        return duplicate

    repair_round = max(
        1,
        _int(previous.get("repair_round"), 0),
        _int(loop_meta.get("repair_round"), 0),
        _int(stage2.get("repair_round"), 0),
    )
    if autonomy_continuation is not None:
        max_rounds = int(autonomy_continuation["max_repair_rounds"])
        max_validation_retries = int(autonomy_continuation["max_validation_retries"])
        if repair_round > max_rounds:
            raise RepairLoopError("Stage-2 repair round exceeds the owner-authorized autonomy budget")
    else:
        max_rounds = max(
            1,
            min(
                MAX_REPAIR_ROUNDS,
                _int(previous.get("max_repair_rounds"), MAX_REPAIR_ROUNDS)
                or MAX_REPAIR_ROUNDS,
            ),
        )
        max_validation_retries = MAX_VALIDATION_RETRIES_PER_CANDIDATE
    prior_verifications = max(
        _int(previous.get("verification_attempt"), 0),
        _int(loop_meta.get("verification_attempt"), 0),
    )
    verification_attempt = max(prior_verifications + 1, stage3_run_attempt)

    failure_class, repair_paths, classification_reason = classify_independent_failure(
        targeted,
        original_failure=original_failure,
        quick_summary=quick_summary,
    )
    failure_fp = _failure_fingerprint(
        failure_class=failure_class,
        repair_paths=repair_paths,
        targeted=targeted,
        quick_summary=quick_summary,
    )
    stagnant_rounds = _int(previous.get("stagnant_rounds"), 0)
    if (
        failure_class in REPAIRABLE_FAILURES
        and previous.get("failure_class") == failure_class
        and previous.get("failure_fingerprint") == failure_fp
        and _int(previous.get("repair_round"), 0) < repair_round
    ):
        stagnant_rounds += 1
    elif failure_class in REPAIRABLE_FAILURES:
        stagnant_rounds = 0

    same_candidate = bool(
        previous and previous.get("candidate_sha") == str(plan.get("candidate_sha") or "")
    )
    previous_same_candidate_retries = (
        _int(previous.get("same_candidate_retry_count"), 0) if same_candidate else 0
    )
    retryable_validation_failure = failure_class in {
        "ENVIRONMENT_FAILURE",
        "TRANSIENT_INFRA_FAILURE",
    }
    same_candidate_retry_count = (
        previous_same_candidate_retries + 1 if retryable_validation_failure else 0
    )

    action = "STOP_UNKNOWN_FAILURE"
    next_repair_round: int | None = None
    stop_reason: str | None = None
    status = "BLOCKED"

    if failure_class == "PASS":
        if validation_result and validation_result.get("status") == "VALIDATED_FOR_DRAFT_PR":
            action = "PUBLISHER_REPAIR_REQUIRED"
            stop_reason = "independent validation passed but the Stage-3 workflow failed after the validation receipt"
        else:
            action = "HARNESS_REPAIR_REQUIRED"
            stop_reason = "independent validation passed but Stage-3 did not persist a publishable validation receipt"
    elif failure_class in REPAIRABLE_FAILURES:
        if repair_round >= max_rounds:
            action = "STOP_MAX_REPAIR_ROUNDS"
            stop_reason = "max governed repair rounds reached"
        elif stagnant_rounds >= STAGNATION_LIMIT:
            action = "ARCHITECTURE_REPLAN_REQUIRED"
            stop_reason = "two repair rounds repeated the same independent validation failure"
        elif not repair_paths:
            action = "TEST_CONTRACT_REVIEW_REQUIRED"
            stop_reason = "no governed writable source path can be derived without expanding authority"
        else:
            if domain == CONTROL_DOMAIN:
                expected_scope = [_normalize_path(item) for item in original_failure.get("candidate_paths") or []]
                if repair_paths != expected_scope:
                    raise RepairLoopError("control-plane repair round attempted to change immutable scope")
            action = "DISPATCH_REPAIR"
            next_repair_round = repair_round + 1
            status = "FAILED_RECOVERABLE"
    elif failure_class == "TEST_CONTRACT_REVIEW_REQUIRED":
        action = "TEST_CONTRACT_REVIEW_REQUIRED"
        stop_reason = classification_reason
    elif failure_class == "HARNESS_FAILURE":
        action = "HARNESS_REPAIR_REQUIRED"
        stop_reason = classification_reason
    elif retryable_validation_failure:
        if same_candidate_retry_count > max_validation_retries:
            action = "VALIDATION_RETRY_EXHAUSTED"
            stop_reason = "same-candidate transient/environment validation retry budget exhausted"
        else:
            action = "RETRY_VALIDATION_SAME_CANDIDATE"
            status = "FAILED_RECOVERABLE"
    else:
        action = "STOP_UNKNOWN_FAILURE"
        stop_reason = classification_reason

    state = {
        "schema": LOOP_SCHEMA,
        "source_run_id": str(binding.get("workflow_run_id")),
        "source_run_attempt": str(binding.get("workflow_run_attempt")),
        "source_head_sha": str(binding.get("head_sha")),
        "failure_signature": str(binding.get("failure_signature")),
        "repair_domain": domain,
        "repair_round": repair_round,
        "max_repair_rounds": max_rounds,
        "next_repair_round": next_repair_round,
        "verification_attempt": verification_attempt,
        "workflow_run_attempt_observed": stage3_run_attempt,
        "last_verification_event": event_key,
        "candidate_sha": str(plan.get("candidate_sha") or ""),
        "patch_sha256": str(stage2.get("patch_sha256") or ""),
        "failure_class": failure_class,
        "failure_fingerprint": failure_fp,
        "classification_reason": classification_reason,
        "repair_paths": repair_paths,
        "failed_components": _failed_components(targeted, quick_summary),
        "stagnant_rounds": stagnant_rounds,
        "same_candidate_retry_count": same_candidate_retry_count,
        "max_validation_retries_per_candidate": max_validation_retries,
        "action": action,
        "stop_reason": stop_reason,
        "repair_budget_consumed": repair_round,
        "repair_budget_remaining": max(0, max_rounds - repair_round),
        "production_closed": False,
    }
    if autonomy_continuation is not None:
        state["autonomy_continuation"] = autonomy_continuation

    task.set_metadata(repair_loop=state)
    evidence_refs = [str(stage3_plan_path), str(targeted_result_path), f"loop-state:{failure_fp}"]
    if quick_summary_path and quick_summary_path.is_file():
        evidence_refs.append(str(quick_summary_path))
    if validation_result_path and validation_result_path.is_file():
        evidence_refs.append(str(validation_result_path))
    if action == "DISPATCH_REPAIR":
        task.checkpoint(
            status="FAILED_RECOVERABLE",
            phase=failure_class,
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
            metadata={"repair_loop": state},
        )
    elif action == "RETRY_VALIDATION_SAME_CANDIDATE":
        task.checkpoint(
            status="FAILED_RECOVERABLE",
            phase=failure_class,
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
            metadata={"repair_loop": state},
        )
    else:
        task.block(
            code=action,
            reason=stop_reason or classification_reason,
            attempted_strategies=("independent-validation", "typed-failure-router"),
            next_action=(
                "review the protected contract/oracle before authorizing another repair"
                if action == "TEST_CONTRACT_REVIEW_REQUIRED"
                else "inspect outer-loop evidence and explicitly replan before another repair"
            ),
            workspace_fingerprint=str(plan.get("validated_tree_sha") or ""),
            evidence_refs=evidence_refs,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "loop-state.json", state)
    shutil.copyfile(task_run_path, output_dir / "task-run.json")
    if action == "DISPATCH_REPAIR":
        if not seed_patch_path.is_file() or seed_patch_path.is_symlink():
            raise RepairLoopError("seed repair patch is missing")
        feedback = _safe_feedback_failure(
            original_failure,
            repair_paths=repair_paths,
            repair_round=repair_round,
            verification_attempt=verification_attempt,
            failure_fingerprint=failure_fp,
            autonomy_continuation=autonomy_continuation,
        )
        _write(output_dir / "failure-case.json", feedback)
        shutil.copyfile(seed_patch_path, output_dir / "seed.patch")
    return state


def _github_output(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    values = {
        "action": state.get("action") or "",
        "source_run_id": state.get("source_run_id") or "",
        "source_run_attempt": state.get("source_run_attempt") or "",
        "repair_domain": state.get("repair_domain") or "",
        "repair_round": state.get("repair_round") or 0,
        "next_repair_round": state.get("next_repair_round") or "",
        "verification_attempt": state.get("verification_attempt") or 0,
        "failure_class": state.get("failure_class") or "",
        "repair_budget_remaining": state.get("repair_budget_remaining") or 0,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if "\n" in text:
                raise RepairLoopError(f"multiline GitHub output is not allowed: {key}")
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--stage2-result", required=True)
    parser.add_argument("--stage3-plan", required=True)
    parser.add_argument("--targeted-result", required=True)
    parser.add_argument("--quick-summary")
    parser.add_argument("--validation-result")
    parser.add_argument("--original-failure-case", required=True)
    parser.add_argument("--seed-patch", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--stage3-run-id", required=True)
    parser.add_argument("--stage3-run-attempt", required=True, type=int)
    parser.add_argument("--previous-state")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        state = route_failure(
            task_run_path=Path(args.task_run),
            stage2_result_path=Path(args.stage2_result),
            stage3_plan_path=Path(args.stage3_plan),
            targeted_result_path=Path(args.targeted_result),
            quick_summary_path=Path(args.quick_summary) if args.quick_summary else None,
            validation_result_path=Path(args.validation_result) if args.validation_result else None,
            original_failure_path=Path(args.original_failure_case),
            seed_patch_path=Path(args.seed_patch),
            output_dir=Path(args.output_dir),
            stage3_run_id=str(args.stage3_run_id),
            stage3_run_attempt=int(args.stage3_run_attempt),
            previous_state_path=Path(args.previous_state) if args.previous_state else None,
        )
        _github_output(Path(args.github_output) if args.github_output else None, state)
        return 0
    except (OSError, json.JSONDecodeError, RepairLoopError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())