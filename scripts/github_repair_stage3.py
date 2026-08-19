#!/usr/bin/env python3
"""Independently validate an RCA-bound Stage-2 repair candidate.

Stage 3 has no model and no patch-authoring authority. It verifies the immutable
failure case, read-only RCA, exact write grant, repair domain, and patch digests;
materializes that exact patch on the exact failed commit; runs fixed targeted
suites and the repository Quick quality loop; and may publish only a Draft PR.
It never changes repair contents, broadens scope, refreshes protected baselines,
merges, deploys, runs production certification, or closes production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = Path(__file__).resolve().parent
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore  # type: ignore  # noqa: E402
from github_agent_fixer import (  # noqa: E402
    FixerError,
    validate_allowed_paths,
    verify_changed_files,
)
from github_repair_authority import (  # noqa: E402
    RepairAuthorityError,
    rca_fingerprint,
    validate_write_grant,
    write_grant_fingerprint,
)
from governed_repair_path_policy import (  # noqa: E402
    REPAIR_DOMAIN_CONTROL_PLANE,
    REPAIR_DOMAIN_PRODUCT,
    RepairPathPolicyError,
    validate_repair_paths,
)
from run_python_test_suites import STANDARD_CONFIG_PREFIXES, STANDARD_ENV  # noqa: E402

STAGE2_SCHEMA = "github-governed-repair-stage2@1"
STAGE3_SCHEMA = "github-governed-repair-stage3@2"
MAX_PATCH_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 40_000
ANTI_DRIFT_REQUIRED_GATE_ID = "python-test-suites"
CONTROL_TARGETED_COMPONENT = "skill-control-plane"


class Stage3Error(RuntimeError):
    """Fail-closed Stage-3 validation error."""


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Stage3Error(f"JSON object required: {path}")
    return payload


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 1800,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=dict(env) if env is not None else os.environ.copy(),
    )


def _git(workspace: Path, *args: str, timeout: int = 180) -> str:
    completed = _run(["git", *args], cwd=workspace, timeout=timeout)
    if completed.returncode:
        message = (completed.stderr or completed.stdout or "git command failed").strip()
        raise Stage3Error(message[:MAX_OUTPUT_CHARS])
    return completed.stdout.strip()


def _changed_paths(workspace: Path) -> tuple[str, ...]:
    completed = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=workspace,
        timeout=180,
    )
    if completed.returncode:
        message = (completed.stderr or completed.stdout or "git status failed").strip()
        raise Stage3Error(message[:MAX_OUTPUT_CHARS])
    result: list[str] = []
    for row in completed.stdout.splitlines():
        raw = row[3:] if len(row) > 3 else ""
        path = raw.split(" -> ")[-1].strip().replace("\\", "/")
        if path and path not in result:
            result.append(path)
    return tuple(result)


def _diff_fingerprint(workspace: Path) -> str:
    patch = _git(workspace, "diff", "HEAD", "--no-ext-diff", "--binary")
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _normalize_path(raw: str) -> str:
    value = str(raw).strip().replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise Stage3Error(f"invalid repository path: {raw!r}")
    normalized = pure.as_posix()
    if normalized != value:
        raise Stage3Error(f"non-canonical repository path: {raw!r}")
    return normalized


def _open_task(path: Path) -> TaskRunStore:
    return TaskRunStore(path.resolve(), _load_object(path))


def _task_binding(task: TaskRunStore) -> dict[str, Any]:
    binding = task.payload.get("binding")
    if not isinstance(binding, dict):
        raise Stage3Error("TaskRun binding is missing")
    return binding


def _validate_binding(task: TaskRunStore, result: dict[str, Any]) -> None:
    binding = _task_binding(task)
    expected = {
        "repository": result.get("repository"),
        "workflow_run_id": str(result.get("workflow_run_id")),
        "head_sha": result.get("head_sha"),
        "failure_signature": result.get("failure_signature"),
    }
    mismatched = [
        key
        for key, value in expected.items()
        if not value or str(binding.get(key)) != str(value)
    ]
    if mismatched:
        raise Stage3Error(f"Stage-2 TaskRun binding mismatch: {mismatched}")
    domain = str(result.get("repair_domain") or "")
    if domain == REPAIR_DOMAIN_CONTROL_PLANE:
        if str(binding.get("repair_domain") or "") != domain:
            raise Stage3Error("TaskRun control-plane repair domain binding mismatch")
        route_sha = str(result.get("repair_route_sha256") or "")
        if not route_sha or str(binding.get("repair_route_sha256") or "") != route_sha:
            raise Stage3Error("TaskRun control-plane semantic route binding mismatch")
    elif str(binding.get("repair_domain") or "") not in {"", REPAIR_DOMAIN_PRODUCT}:
        raise Stage3Error("TaskRun product repair domain drift")
    if task.payload.get("status") != "WAITING_EXTERNAL_RESULT":
        raise Stage3Error("TaskRun is not waiting for Stage-3 validation")
    if task.payload.get("phase") != "STAGE3_VALIDATION_REQUIRED":
        raise Stage3Error("TaskRun phase is not STAGE3_VALIDATION_REQUIRED")


def _validate_stage2_result(result: dict[str, Any]) -> None:
    if result.get("schema") != STAGE2_SCHEMA:
        raise Stage3Error("unsupported Stage-2 result schema")
    if result.get("status") != "REPAIR_CANDIDATE_READY":
        raise Stage3Error("Stage 2 did not produce a repair candidate")
    if result.get("deterministic_file_verification_passed") is not True:
        raise Stage3Error("Stage-2 deterministic file verification did not pass")
    if result.get("governed_repair_state") != "INDEPENDENT_REVIEW":
        raise Stage3Error("Stage-2 result is not awaiting independent review")
    for field in ("full_validation_passed", "draft_pr_published", "production_closed"):
        if result.get(field) is not False:
            raise Stage3Error(f"invalid Stage-2 authority boundary: {field}")
    guard_ids = result.get("required_guard_ids")
    if (
        not isinstance(guard_ids, list)
        or not guard_ids
        or any(not isinstance(item, str) or not item.strip() for item in guard_ids)
        or len(set(guard_ids)) != len(guard_ids)
    ):
        raise Stage3Error("Stage-2 result lacks immutable permanent guard IDs")
    for field in (
        "repository",
        "workflow_run_id",
        "head_sha",
        "failure_signature",
        "repair_branch",
        "repair_base_branch",
        "patch_sha256",
        "rca_sha256",
        "write_grant_sha256",
        "violated_invariant",
        "authority_owner",
        "required_permanent_guard",
    ):
        if not str(result.get(field) or "").strip():
            raise Stage3Error(f"Stage-2 result is missing {field}")
    repair_branch = str(result["repair_branch"])
    base_branch = str(result["repair_base_branch"])
    if not repair_branch.startswith("governed-repair/"):
        raise Stage3Error("repair branch is outside governed-repair namespace")
    if repair_branch == base_branch or base_branch.startswith("governed-repair/"):
        raise Stage3Error("invalid repair/base branch relationship")


def _validate_authority_bundle(
    *,
    result: dict[str, Any],
    failure_case: dict[str, Any],
    rca: dict[str, Any],
    grant: dict[str, Any],
    changed_paths: tuple[str, ...],
) -> tuple[str, ...]:
    if failure_case.get("schema") != "github-failure-ingest@1":
        raise Stage3Error("normalized Stage-1 failure case is missing or invalid")
    if str(failure_case.get("head_sha") or "") != str(result.get("head_sha") or ""):
        raise Stage3Error("failure-case/Stage-2 head SHA mismatch")
    if str(failure_case.get("failure_signature") or "") != str(
        result.get("failure_signature") or ""
    ):
        raise Stage3Error("failure-case/Stage-2 failure signature mismatch")
    candidate_paths = tuple(
        _normalize_path(str(item)) for item in failure_case.get("candidate_paths") or []
    )
    if not candidate_paths:
        raise Stage3Error("failure case has no RCA candidate paths")
    try:
        granted = validate_write_grant(
            grant,
            failure_case=failure_case,
            rca=rca,
            candidate_paths=candidate_paths,
        )
    except RepairAuthorityError as exc:
        raise Stage3Error(f"invalid Stage-2 RCA/write grant: {exc}") from exc

    if str(result.get("rca_sha256") or "") != rca_fingerprint(rca):
        raise Stage3Error("Stage-2 RCA digest mismatch")
    if str(result.get("write_grant_sha256") or "") != write_grant_fingerprint(grant):
        raise Stage3Error("Stage-2 write-grant digest mismatch")
    domain = str(grant.get("repair_domain") or "")
    if domain not in {REPAIR_DOMAIN_PRODUCT, REPAIR_DOMAIN_CONTROL_PLANE}:
        raise Stage3Error("Stage-2 write grant lacks a supported repair domain")
    if str(result.get("repair_domain") or domain) != domain:
        raise Stage3Error("Stage-2 result repair domain differs from immutable write grant")
    if str(failure_case.get("repair_domain") or domain) != domain:
        raise Stage3Error("Stage-1 failure repair domain differs from immutable write grant")
    if domain == REPAIR_DOMAIN_CONTROL_PLANE:
        route = failure_case.get("repair_route") if isinstance(failure_case.get("repair_route"), dict) else {}
        route_sha = str(route.get("route_sha256") or "")
        if not route_sha or str(result.get("repair_route_sha256") or route_sha) != route_sha:
            raise Stage3Error("Stage-2 control-plane semantic route digest drift")
    result_guard_ids = tuple(str(item or "").strip() for item in result.get("required_guard_ids") or [])
    grant_guard_ids = tuple(str(item or "").strip() for item in grant.get("required_guard_ids") or [])
    if result_guard_ids != grant_guard_ids or not result_guard_ids:
        raise Stage3Error("Stage-2 permanent guard binding does not equal the write grant")
    result_scope = tuple(_normalize_path(str(item)) for item in result.get("write_scope") or [])
    if result_scope != granted:
        raise Stage3Error("Stage-2 result write_scope does not equal the immutable grant")
    unexpected = [path for path in changed_paths if path not in granted]
    if unexpected:
        raise Stage3Error(f"Stage-2 patch escaped the immutable write grant: {unexpected}")
    if str(result.get("violated_invariant") or "") != str(rca.get("violated_invariant") or ""):
        raise Stage3Error("Stage-2 violated-invariant binding mismatch")
    if str(result.get("authority_owner") or "") != str(rca.get("authority_owner") or ""):
        raise Stage3Error("Stage-2 authority-owner binding mismatch")
    if str(result.get("required_permanent_guard") or "") != str(
        rca.get("required_permanent_guard") or ""
    ):
        raise Stage3Error("Stage-2 permanent-guard binding mismatch")
    return granted


def inspect_handoff(
    *,
    result_path: Path,
    task_run_path: Path,
    patch_path: Path,
    failure_case_path: Path,
    rca_path: Path,
    write_grant_path: Path,
) -> dict[str, Any]:
    result = _load_object(result_path)
    task = _open_task(task_run_path)
    failure_case = _load_object(failure_case_path)
    rca = _load_object(rca_path)
    grant = _load_object(write_grant_path)
    _validate_stage2_result(result)
    _validate_binding(task, result)
    if not patch_path.is_file() or patch_path.is_symlink():
        raise Stage3Error("Stage-2 patch must be an existing regular file")
    data = patch_path.read_bytes()
    if not data or len(data) > MAX_PATCH_BYTES or b"\x00" in data:
        raise Stage3Error("Stage-2 patch is empty, oversized, or binary")
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(result.get("patch_sha256")):
        raise Stage3Error("Stage-2 patch digest mismatch")
    changed = tuple(_normalize_path(str(item)) for item in result.get("changed_paths") or [])
    if not changed or len(set(changed)) != len(changed):
        raise Stage3Error("Stage-2 changed path set is empty or duplicated")
    granted = _validate_authority_bundle(
        result=result,
        failure_case=failure_case,
        rca=rca,
        grant=grant,
        changed_paths=changed,
    )
    domain = str(grant.get("repair_domain") or REPAIR_DOMAIN_PRODUCT)
    route = failure_case.get("repair_route") if isinstance(failure_case.get("repair_route"), dict) else {}
    return {
        "publish_allowed": True,
        "repository": str(result["repository"]),
        "source_run_id": str(result["workflow_run_id"]),
        "head_sha": str(result["head_sha"]),
        "failure_signature": str(result["failure_signature"]),
        "repair_domain": domain,
        "repair_route_sha256": str(route.get("route_sha256") or result.get("repair_route_sha256") or ""),
        "repair_branch": str(result["repair_branch"]),
        "repair_base_branch": str(result["repair_base_branch"]),
        "changed_paths": list(changed),
        "write_scope": list(granted),
        "required_guard_ids": list(result.get("required_guard_ids") or []),
        "patch_sha256": digest,
        "rca_sha256": rca_fingerprint(rca),
        "write_grant_sha256": write_grant_fingerprint(grant),
        "violated_invariant": str(rca["violated_invariant"]),
        "authority_owner": str(rca["authority_owner"]),
        "drifted_projection": str(rca["drifted_projection"]),
        "required_permanent_guard": str(rca["required_permanent_guard"]),
        "repair_plan": list(rca["repair_plan"]),
        "governed_repair_state": "INDEPENDENT_REVIEW",
        "gates": grant.get("gates") or {},
        "production_closed": False,
    }


def _validate_stage3_paths(
    workspace: Path,
    paths: Iterable[str],
    *,
    repair_domain: str,
) -> tuple[str, ...]:
    raw = tuple(_normalize_path(str(item)) for item in paths)
    if repair_domain == REPAIR_DOMAIN_PRODUCT:
        try:
            return validate_allowed_paths(workspace, raw)
        except FixerError as exc:
            raise Stage3Error(str(exc)) from exc
    if repair_domain != REPAIR_DOMAIN_CONTROL_PLANE:
        raise Stage3Error(f"unsupported Stage-3 repair domain: {repair_domain!r}")
    try:
        normalized = validate_repair_paths(raw, repair_domain=repair_domain)
    except RepairPathPolicyError as exc:
        raise Stage3Error(str(exc)) from exc
    root = workspace.resolve()
    for path in normalized:
        candidate = root / path
        if candidate.is_symlink() or not candidate.is_file():
            raise Stage3Error(f"control-plane repair candidate must be a regular file: {path}")
        try:
            candidate.resolve().relative_to(root)
        except ValueError as exc:
            raise Stage3Error(f"control-plane repair candidate escapes workspace: {path}") from exc
    return normalized


def _verify_stage3_changed_files(
    workspace: Path,
    paths: Iterable[str],
    *,
    repair_domain: str,
) -> tuple[bool, list[dict[str, Any]]]:
    normalized = _validate_stage3_paths(workspace, paths, repair_domain=repair_domain)
    if repair_domain == REPAIR_DOMAIN_PRODUCT:
        return verify_changed_files(workspace, normalized)
    rows: list[dict[str, Any]] = []
    passed = True
    for path in normalized:
        candidate = workspace / path
        try:
            source = candidate.read_text(encoding="utf-8")
            compile(source, path, "exec")
            row = {"path": path, "kind": "python-compile", "passed": True}
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            passed = False
            row = {
                "path": path,
                "kind": "python-compile",
                "passed": False,
                "error": str(exc)[:4000],
            }
        rows.append(row)
    return passed, rows


def _target_components(paths: Iterable[str], workspace: Path) -> list[str]:
    components: set[str] = set()
    for path in paths:
        if path.startswith("scripts/verify_engineering_") and path.endswith(".py"):
            components.add(CONTROL_TARGETED_COMPONENT)
        elif path.startswith("services/agent-service/frontend/"):
            components.add("agent-frontend")
        elif path.startswith("services/agent-service/"):
            components.add("agent-python")
        elif path.startswith("services/business-service/"):
            components.add("business-python")
        elif path.startswith("contracts/"):
            components.update({"agent-python", "business-python", "agent-frontend"})
        elif path.startswith("web/"):
            if not (workspace / "web" / "package.json").is_file():
                raise Stage3Error("web source changed but web/package.json is unavailable")
            components.add("web-node")
        else:
            raise Stage3Error(f"no fixed targeted suite for changed path: {path}")
    if not components:
        raise Stage3Error("targeted validation component set is empty")
    order = (CONTROL_TARGETED_COMPONENT, "agent-python", "business-python", "agent-frontend", "web-node")
    return [name for name in order if name in components]


def prepare_candidate(
    *,
    workspace: Path,
    result_path: Path,
    task_run_path: Path,
    patch_path: Path,
    failure_case_path: Path,
    rca_path: Path,
    write_grant_path: Path,
    plan_path: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    handoff = inspect_handoff(
        result_path=result_path,
        task_run_path=task_run_path,
        patch_path=patch_path,
        failure_case_path=failure_case_path,
        rca_path=rca_path,
        write_grant_path=write_grant_path,
    )
    actual_head = _git(workspace, "rev-parse", "HEAD")
    if actual_head != handoff["head_sha"]:
        raise Stage3Error("candidate checkout does not match Stage-2 head SHA")
    if _changed_paths(workspace):
        raise Stage3Error("Stage-3 candidate workspace must start clean")

    domain = str(handoff["repair_domain"])
    expected_paths = tuple(handoff["changed_paths"])
    _validate_stage3_paths(workspace, expected_paths, repair_domain=domain)

    check = _run(
        ["git", "apply", "--check", "--whitespace=error-all", str(patch_path.resolve())],
        cwd=workspace,
        timeout=180,
    )
    if check.returncode:
        raise Stage3Error(
            (check.stderr or check.stdout or "git apply --check failed")[:MAX_OUTPUT_CHARS]
        )
    apply_result = _run(
        ["git", "apply", "--whitespace=error-all", str(patch_path.resolve())],
        cwd=workspace,
        timeout=180,
    )
    if apply_result.returncode:
        raise Stage3Error(
            (apply_result.stderr or apply_result.stdout or "git apply failed")[:MAX_OUTPUT_CHARS]
        )

    actual_paths = _changed_paths(workspace)
    if set(actual_paths) != set(expected_paths) or len(actual_paths) != len(expected_paths):
        raise Stage3Error(
            f"applied patch path mismatch: expected={list(expected_paths)} actual={list(actual_paths)}"
        )
    unexpected = [path for path in actual_paths if path not in set(handoff["write_scope"])]
    if unexpected:
        raise Stage3Error(f"applied patch escaped write grant: {unexpected}")
    _validate_stage3_paths(workspace, actual_paths, repair_domain=domain)
    deterministic_passed, verification = _verify_stage3_changed_files(
        workspace, actual_paths, repair_domain=domain
    )
    if not deterministic_passed:
        raise Stage3Error(f"Stage-3 deterministic verification failed: {verification}")

    components = _target_components(actual_paths, workspace)
    _git(workspace, "config", "user.name", "github-actions[bot]")
    _git(
        workspace,
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    _git(workspace, "add", "--", *actual_paths)
    _git(
        workspace,
        "commit",
        "-m",
        f"Governed repair candidate for workflow run {handoff['source_run_id']}",
    )
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    if _changed_paths(workspace):
        raise Stage3Error("candidate workspace is dirty after local repair commit")

    gates = dict(handoff.get("gates") or {})
    gates.update(
        {
            "G1_CONTRACT_PROJECTION": {"status": "PENDING_REQUIRED_QUICK_GATES"},
            "G2_SEMANTIC_INVARIANT": {"status": "PENDING_TARGETED_VALIDATION"},
            "G3_MUTATION": {"status": "PENDING_REQUIRED_QUICK_GATES"},
            "G4_FINAL_AUTHORITY": {"status": "PENDING_REQUIRED_QUICK_GATES"},
            "G5_INTEGRATION_CERTIFICATION": {"status": "PENDING_QUICK"},
            "G6_GOVERNANCE_EXACT_HEAD": {"status": "PENDING"},
        }
    )
    plan = {
        "schema": STAGE3_SCHEMA,
        "status": "CANDIDATE_PREPARED",
        **handoff,
        "candidate_sha": candidate_sha,
        "targeted_components": components,
        "deterministic_file_verification": verification,
        "governed_repair_state": "INDEPENDENT_REVIEW",
        "gates": gates,
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_object(plan_path, plan)
    task = _open_task(task_run_path)
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_CANDIDATE_PREPARED",
        workspace_fingerprint=_diff_fingerprint(workspace),
        evidence_refs=[
            str(result_path),
            str(patch_path),
            str(failure_case_path),
            str(rca_path),
            str(write_grant_path),
            str(plan_path),
        ],
        metadata={
            "stage": 3,
            "governed_repair_state": "INDEPENDENT_REVIEW",
            "candidate_sha": candidate_sha,
            "repair_domain": domain,
            "repair_route_sha256": handoff.get("repair_route_sha256"),
            "targeted_components": components,
            "rca_sha256": handoff["rca_sha256"],
            "write_grant_sha256": handoff["write_grant_sha256"],
        },
    )
    return plan


def _component_command(component: str, workspace: Path) -> tuple[list[str], Path]:
    python_test_args = [
        "-B",
        "-m",
        "pytest",
        "-q",
        "-ra",
        "-p",
        "no:cacheprovider",
        "-m",
        "not integration and not preprod",
        "tests",
    ]
    if component == CONTROL_TARGETED_COMPONENT:
        return (
            [
                str(workspace / "services/agent-service/.venv/bin/python"),
                "-B",
                "skill-system/controller/profile_runner.py",
                "skill-control-plane",
            ],
            workspace,
        )
    if component == "agent-python":
        return (
            [str(workspace / "services/agent-service/.venv/bin/python"), *python_test_args],
            workspace / "services/agent-service",
        )
    if component == "business-python":
        return (
            [str(workspace / "services/business-service/.venv/bin/python"), *python_test_args],
            workspace / "services/business-service",
        )
    if component == "agent-frontend":
        return (["npm", "test"], workspace / "services/agent-service/frontend")
    if component == "web-node":
        return (["npm", "test"], workspace / "web")
    raise Stage3Error(f"unknown targeted component: {component}")


def _targeted_env(component: str, *, runtime_dir: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if component not in {"agent-python", "business-python"}:
        return env
    if runtime_dir is None:
        raise Stage3Error("targeted Python runtime directory is required")

    for key in tuple(env):
        if key.startswith(STANDARD_CONFIG_PREFIXES):
            env.pop(key, None)
    env.update(STANDARD_ENV)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    runtime_dir = runtime_dir.resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "SQLITE_DB_PATH": str(runtime_dir / "agent.sqlite3"),
            "AGENT_DATABASE_URL": f"sqlite:///{runtime_dir / 'agent.sqlite3'}",
            "DATABASE_URL": f"sqlite:///{runtime_dir / 'agent.sqlite3'}",
            "CHECKPOINT_DB_PATH": str(runtime_dir / "checkpoints.sqlite3"),
            "VECTOR_DB_PATH": str(runtime_dir / "vector.sqlite3"),
            "UPLOAD_DIR": str(runtime_dir / "uploads"),
            "BUSINESS_DB_PATH": str(runtime_dir / "business.sqlite3"),
        }
    )
    if component == "agent-python":
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = "src:." + (os.pathsep + existing if existing else "")
    return env


def run_targeted(*, workspace: Path, plan_path: Path, output_path: Path) -> int:
    workspace = workspace.resolve()
    plan = _load_object(plan_path)
    if plan.get("schema") != STAGE3_SCHEMA or plan.get("status") != "CANDIDATE_PREPARED":
        raise Stage3Error("invalid Stage-3 targeted validation plan")
    if _git(workspace, "rev-parse", "HEAD") != str(plan.get("candidate_sha") or ""):
        raise Stage3Error("targeted validation checkout drifted from candidate SHA")
    rows: list[dict[str, Any]] = []
    passed = True
    for component in plan.get("targeted_components") or []:
        component_name = str(component)
        command, cwd = _component_command(component_name, workspace)
        if not cwd.is_dir():
            raise Stage3Error(f"targeted component directory is missing: {cwd}")
        runtime_dir = output_path.resolve().parent / "runtime" / component_name
        try:
            completed = _run(
                command,
                cwd=cwd,
                timeout=3600,
                env=_targeted_env(component_name, runtime_dir=runtime_dir),
            )
            row = {
                "component": component,
                "command": command,
                "cwd": str(cwd.relative_to(workspace)),
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-MAX_OUTPUT_CHARS:],
                "stderr": completed.stderr[-MAX_OUTPUT_CHARS:],
                "passed": completed.returncode == 0,
            }
        except subprocess.TimeoutExpired as exc:
            row = {
                "component": component,
                "command": command,
                "cwd": str(cwd.relative_to(workspace)),
                "exit_code": None,
                "stdout": str(exc.stdout or "")[-MAX_OUTPUT_CHARS:],
                "stderr": str(exc.stderr or "")[-MAX_OUTPUT_CHARS:],
                "passed": False,
                "timed_out": True,
            }
        rows.append(row)
        if not row["passed"]:
            passed = False
            break
    payload = {
        "schema": STAGE3_SCHEMA,
        "status": "TARGETED_VALIDATION_PASSED" if passed else "TARGETED_VALIDATION_FAILED",
        "candidate_sha": plan.get("candidate_sha"),
        "repair_domain": plan.get("repair_domain"),
        "repair_route_sha256": plan.get("repair_route_sha256"),
        "rca_sha256": plan.get("rca_sha256"),
        "write_grant_sha256": plan.get("write_grant_sha256"),
        "required_guard_ids": plan.get("required_guard_ids"),
        "governed_repair_state": "INDEPENDENT_REVIEW",
        "results": rows,
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_object(output_path, payload)
    return 0 if passed else 2


def validate_quick_evidence(summary_path: Path) -> dict[str, Any]:
    summary = _load_object(summary_path)
    if summary.get("mode") != "quick":
        raise Stage3Error("Stage-3 full validation evidence is not Quick mode")
    if summary.get("run_kind") != "verification":
        raise Stage3Error("Stage-3 Quick evidence is not a verification run")
    if summary.get("decision") != "PASS":
        raise Stage3Error("Stage-3 Quick quality decision did not pass")
    if summary.get("loop_status") != "CI_VERIFIED":
        raise Stage3Error("Stage-3 Quick quality loop did not reach CI_VERIFIED")
    if summary.get("completion_eligible") is not True:
        raise Stage3Error("Stage-3 Quick evidence is not completion eligible")
    statuses = {
        str(row.get("id")): str(row.get("status"))
        for row in summary.get("results") or []
        if isinstance(row, dict)
    }
    missing_or_failed = [
        str(gate)
        for gate in summary.get("required_gate_ids") or []
        if statuses.get(str(gate)) != "PASS"
    ]
    if missing_or_failed:
        raise Stage3Error(f"required Quick gates did not pass: {missing_or_failed}")
    return summary


def _require_permanent_guard_reverified(
    summary: dict[str, Any],
    guard_ids: Iterable[str],
    *,
    targeted: dict[str, Any] | None = None,
    repair_domain: str = REPAIR_DOMAIN_PRODUCT,
) -> dict[str, str]:
    """Require every original machine guard to be mandatory and PASS.

    Product guards remain Quick-required exactly as before. The M8.6
    ``skill-control-plane`` guard is re-executed by the independent targeted
    Stage-3 profile because it is a workflow/profile gate rather than a
    ``quality_loop.py`` Quick gate; it is never inferred from a skipped check.
    """
    required = {str(item) for item in summary.get("required_gate_ids") or []}
    statuses = {
        str(row.get("id")): str(row.get("status"))
        for row in summary.get("results") or []
        if isinstance(row, dict)
    }
    guards = tuple(str(item or "").strip() for item in guard_ids)
    if not guards or any(not item for item in guards) or len(set(guards)) != len(guards):
        raise Stage3Error("permanent_guard_not_reverified: invalid required_guard_ids")

    proof: dict[str, str] = {}
    remaining: list[str] = []
    for guard in guards:
        if repair_domain == REPAIR_DOMAIN_CONTROL_PLANE and guard == CONTROL_TARGETED_COMPONENT:
            rows = targeted.get("results") if isinstance(targeted, dict) else None
            matched = [
                row for row in (rows or [])
                if isinstance(row, dict)
                and row.get("component") == CONTROL_TARGETED_COMPONENT
                and row.get("passed") is True
            ]
            if not matched:
                raise Stage3Error(
                    "permanent_guard_not_reverified: skill-control-plane targeted profile did not PASS"
                )
            proof[guard] = "PASS"
        else:
            remaining.append(guard)

    missing_required = [item for item in remaining if item not in required]
    failed = [item for item in remaining if statuses.get(item) != "PASS"]
    if missing_required or failed:
        raise Stage3Error(
            "permanent_guard_not_reverified: "
            f"not_required={missing_required} not_pass={failed}"
        )
    proof.update({item: statuses[item] for item in remaining})
    return proof


def _require_anti_drift_proof(summary: dict[str, Any]) -> dict[str, str]:
    """Require the permanent mutation-kill proof to be mandatory and PASS in Quick.

    The python-test-suites gate executes
    tests/architecture/test_governed_repair_mutation_proof.py, which deliberately
    drifts a secondary lifecycle projection and requires the architecture verifier
    to turn RED. Removing that gate from Quick is therefore itself fail-closed.
    """
    required = {str(item) for item in summary.get("required_gate_ids") or []}
    statuses = {
        str(row.get("id")): str(row.get("status"))
        for row in summary.get("results") or []
        if isinstance(row, dict)
    }
    gate = ANTI_DRIFT_REQUIRED_GATE_ID
    if gate not in required:
        raise Stage3Error(
            f"anti_drift_proof_not_reverified: required Quick gate missing: {gate}"
        )
    if statuses.get(gate) != "PASS":
        raise Stage3Error(
            f"anti_drift_proof_not_reverified: Quick gate did not PASS: {gate}"
        )
    return {gate: "PASS"}


def record_validation(
    *,
    workspace: Path,
    plan_path: Path,
    targeted_result_path: Path,
    quick_summary_path: Path,
    task_run_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    plan = _load_object(plan_path)
    targeted = _load_object(targeted_result_path)
    if targeted.get("status") != "TARGETED_VALIDATION_PASSED":
        raise Stage3Error("targeted validation did not pass")
    if targeted.get("candidate_sha") != plan.get("candidate_sha"):
        raise Stage3Error("targeted evidence candidate SHA mismatch")
    for field in ("rca_sha256", "write_grant_sha256", "repair_domain"):
        if targeted.get(field) != plan.get(field):
            raise Stage3Error(f"targeted evidence {field} mismatch")
    if str(targeted.get("repair_route_sha256") or "") != str(plan.get("repair_route_sha256") or ""):
        raise Stage3Error("targeted evidence repair-route digest mismatch")
    summary = validate_quick_evidence(quick_summary_path)
    domain = str(plan.get("repair_domain") or REPAIR_DOMAIN_PRODUCT)
    guard_proof = _require_permanent_guard_reverified(
        summary,
        plan.get("required_guard_ids") or [],
        targeted=targeted,
        repair_domain=domain,
    )
    anti_drift_proof = _require_anti_drift_proof(summary)
    workspace = workspace.resolve()
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    if candidate_sha != str(plan.get("candidate_sha") or ""):
        raise Stage3Error("Quick validation checkout drifted from candidate SHA")
    snapshot = str(summary.get("workspace_snapshot_fingerprint") or "")
    if not snapshot:
        raise Stage3Error("Quick evidence lacks workspace snapshot fingerprint")

    gates = dict(plan.get("gates") or {})
    gates.update(
        {
            "G1_CONTRACT_PROJECTION": {
                "status": "PASS",
                "evidence": [str(quick_summary_path), f"quick-snapshot:{snapshot}"],
            },
            "G2_SEMANTIC_INVARIANT": {
                "status": "PASS",
                "evidence": [
                    str(targeted_result_path),
                    str(quick_summary_path),
                    f"candidate-sha:{candidate_sha}",
                    f"repair-domain:{domain}",
                    *[f"permanent-guard:{gate}:PASS" for gate in guard_proof],
                ],
            },
            "G3_MUTATION": {
                "status": "PASS",
                "governed_repair_state": "ANTI_DRIFT_PROOF",
                "evidence": [
                    str(quick_summary_path),
                    f"quick-snapshot:{snapshot}",
                    *[f"anti-drift-gate:{gate}:PASS" for gate in anti_drift_proof],
                ],
            },
            "G4_FINAL_AUTHORITY": {
                "status": "PASS",
                "evidence": [str(quick_summary_path), f"quick-snapshot:{snapshot}"],
            },
            "G5_INTEGRATION_CERTIFICATION": {
                "status": "PASS",
                "evidence": [str(quick_summary_path), f"candidate-sha:{candidate_sha}"],
            },
            "G6_GOVERNANCE_EXACT_HEAD": {"status": "PENDING"},
        }
    )
    for gate in (
        "G0_SCOPE_AUTHORITY",
        "G1_CONTRACT_PROJECTION",
        "G2_SEMANTIC_INVARIANT",
        "G3_MUTATION",
        "G4_FINAL_AUTHORITY",
        "G5_INTEGRATION_CERTIFICATION",
    ):
        if not isinstance(gates.get(gate), dict) or gates[gate].get("status") != "PASS":
            raise Stage3Error(f"governed repair gate did not pass: {gate}")

    task = _open_task(task_run_path)
    task.mark_condition(
        "validation_passed",
        evidence_refs=[
            str(targeted_result_path),
            str(quick_summary_path),
            f"candidate-sha:{candidate_sha}",
            f"quick-snapshot:{snapshot}",
        ],
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE3_DRAFT_PR_REQUIRED",
        workspace_fingerprint=snapshot,
        evidence_refs=[str(targeted_result_path), str(quick_summary_path)],
        metadata={
            "candidate_sha": candidate_sha,
            "repair_domain": domain,
            "repair_route_sha256": plan.get("repair_route_sha256"),
            "quick_loop_status": "CI_VERIFIED",
            "governed_repair_state": "PR_CERTIFICATION",
            "gates": gates,
            "rca_sha256": plan.get("rca_sha256"),
            "write_grant_sha256": plan.get("write_grant_sha256"),
            "anti_drift_proof": anti_drift_proof,
        },
    )
    result = {
        "schema": STAGE3_SCHEMA,
        "status": "VALIDATED_FOR_DRAFT_PR",
        "source_run_id": plan.get("source_run_id"),
        "head_sha": plan.get("head_sha"),
        "candidate_sha": candidate_sha,
        "repair_domain": domain,
        "repair_route_sha256": plan.get("repair_route_sha256"),
        "repair_branch": plan.get("repair_branch"),
        "repair_base_branch": plan.get("repair_base_branch"),
        "changed_paths": plan.get("changed_paths"),
        "write_scope": plan.get("write_scope"),
        "rca_sha256": plan.get("rca_sha256"),
        "write_grant_sha256": plan.get("write_grant_sha256"),
        "required_guard_ids": plan.get("required_guard_ids"),
        "permanent_guard_reverification": guard_proof,
        "anti_drift_proof": {
            "governed_repair_state": "ANTI_DRIFT_PROOF",
            "required_gate_id": ANTI_DRIFT_REQUIRED_GATE_ID,
            "gate_statuses": anti_drift_proof,
            "status": "PASS",
        },
        "violated_invariant": plan.get("violated_invariant"),
        "authority_owner": plan.get("authority_owner"),
        "required_permanent_guard": plan.get("required_permanent_guard"),
        "targeted_components": plan.get("targeted_components"),
        "targeted_validation_passed": True,
        "full_validation_passed": True,
        "quick_loop_status": "CI_VERIFIED",
        "quick_workspace_snapshot_fingerprint": snapshot,
        "governed_repair_state": "PR_CERTIFICATION",
        "gates": gates,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_object(output_path, result)
    return result


def complete_publication(
    *,
    workspace: Path,
    validation_result_path: Path,
    task_run_path: Path,
    pr_url: str,
    output_path: Path,
) -> dict[str, Any]:
    raise Stage3Error("deprecated completion path: use github_repair_stage3_record_publication.py, governance closure, baseline acceptance, and exact-head G6")


def _github_output(path: Path | None, values: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = "true" if value is True else "false" if value is False else str(value)
            if "\n" in text:
                raise Stage3Error(f"multiline GitHub output is not allowed: {key}")
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--result", required=True)
    inspect_parser.add_argument("--task-run", required=True)
    inspect_parser.add_argument("--patch", required=True)
    inspect_parser.add_argument("--failure-case", required=True)
    inspect_parser.add_argument("--rca", required=True)
    inspect_parser.add_argument("--write-grant", required=True)
    inspect_parser.add_argument("--output", required=True)
    inspect_parser.add_argument("--github-output")

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--workspace", required=True)
    prepare_parser.add_argument("--result", required=True)
    prepare_parser.add_argument("--task-run", required=True)
    prepare_parser.add_argument("--patch", required=True)
    prepare_parser.add_argument("--failure-case", required=True)
    prepare_parser.add_argument("--rca", required=True)
    prepare_parser.add_argument("--write-grant", required=True)
    prepare_parser.add_argument("--plan", required=True)
    prepare_parser.add_argument("--github-output")

    targeted_parser = sub.add_parser("targeted")
    targeted_parser.add_argument("--workspace", required=True)
    targeted_parser.add_argument("--plan", required=True)
    targeted_parser.add_argument("--output", required=True)

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--workspace", required=True)
    validate_parser.add_argument("--plan", required=True)
    validate_parser.add_argument("--targeted-result", required=True)
    validate_parser.add_argument("--quick-summary", required=True)
    validate_parser.add_argument("--task-run", required=True)
    validate_parser.add_argument("--output", required=True)
    validate_parser.add_argument("--github-output")

    complete_parser = sub.add_parser("complete")
    complete_parser.add_argument("--workspace", required=True)
    complete_parser.add_argument("--validation-result", required=True)
    complete_parser.add_argument("--task-run", required=True)
    complete_parser.add_argument("--pr-url", required=True)
    complete_parser.add_argument("--output", required=True)

    args = parser.parse_args()
    try:
        if args.command == "inspect":
            payload = inspect_handoff(
                result_path=Path(args.result),
                task_run_path=Path(args.task_run),
                patch_path=Path(args.patch),
                failure_case_path=Path(args.failure_case),
                rca_path=Path(args.rca),
                write_grant_path=Path(args.write_grant),
            )
            _write_object(Path(args.output), {"schema": STAGE3_SCHEMA, **payload})
            _github_output(Path(args.github_output) if args.github_output else None, payload)
            return 0
        if args.command == "prepare":
            payload = prepare_candidate(
                workspace=Path(args.workspace),
                result_path=Path(args.result),
                task_run_path=Path(args.task_run),
                patch_path=Path(args.patch),
                failure_case_path=Path(args.failure_case),
                rca_path=Path(args.rca),
                write_grant_path=Path(args.write_grant),
                plan_path=Path(args.plan),
            )
            _github_output(
                Path(args.github_output) if args.github_output else None,
                {
                    "candidate_sha": payload["candidate_sha"],
                    "source_run_id": payload["source_run_id"],
                    "repair_domain": payload["repair_domain"],
                    "repair_branch": payload["repair_branch"],
                    "repair_base_branch": payload["repair_base_branch"],
                    "rca_sha256": payload["rca_sha256"],
                    "write_grant_sha256": payload["write_grant_sha256"],
                },
            )
            return 0
        if args.command == "targeted":
            return run_targeted(
                workspace=Path(args.workspace),
                plan_path=Path(args.plan),
                output_path=Path(args.output),
            )
        if args.command == "validate":
            payload = record_validation(
                workspace=Path(args.workspace),
                plan_path=Path(args.plan),
                targeted_result_path=Path(args.targeted_result),
                quick_summary_path=Path(args.quick_summary),
                task_run_path=Path(args.task_run),
                output_path=Path(args.output),
            )
            _github_output(
                Path(args.github_output) if args.github_output else None,
                {
                    "candidate_sha": payload["candidate_sha"],
                    "repair_domain": payload["repair_domain"],
                    "repair_branch": payload["repair_branch"],
                    "repair_base_branch": payload["repair_base_branch"],
                    "source_run_id": payload["source_run_id"],
                    "rca_sha256": payload["rca_sha256"],
                    "write_grant_sha256": payload["write_grant_sha256"],
                },
            )
            return 0
        if args.command == "complete":
            complete_publication(
                workspace=Path(args.workspace),
                validation_result_path=Path(args.validation_result),
                task_run_path=Path(args.task_run),
                pr_url=args.pr_url,
                output_path=Path(args.output),
            )
            return 0
    except (
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        Stage3Error,
        RepairAuthorityError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "production_closed": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())