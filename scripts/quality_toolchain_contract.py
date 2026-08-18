#!/usr/bin/env python3
"""Fail-closed toolchain contract for non-production quality workflows."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

CONTRACT = "quality-toolchain-contract@1"
LOCK_CONTRACT = "release-toolchain-lock@1"
_ACTION_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+([^\s@]+)@([^\s#]+)(?:\s+#\s*(.*))?\s*$")
_VERSION_TOKEN_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class QualityToolchainError(RuntimeError):
    def __init__(self, code: str, message: str, *, environment_blocked: bool = False):
        super().__init__(message)
        self.code = code
        self.environment_blocked = environment_blocked


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityToolchainError("quality_toolchain_lock_invalid", str(exc)) from exc
    if not isinstance(value, dict) or value.get("contract") != LOCK_CONTRACT:
        raise QualityToolchainError("quality_toolchain_lock_invalid", "release toolchain lock contract is invalid")
    return value


def _workflow_actions(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = _ACTION_RE.match(line)
        if match:
            rows.append((match.group(1), match.group(2), (match.group(3) or "").strip()))
    return rows


def _quality_action_lock(lock: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    release_actions = lock.get("github_actions")
    quality_only_actions = lock.get("quality_github_actions", {})
    if not isinstance(release_actions, Mapping) or not release_actions:
        raise QualityToolchainError("quality_action_lock_missing", "shared GitHub Action lock is missing")
    if not isinstance(quality_only_actions, Mapping):
        raise QualityToolchainError("quality_action_lock_invalid", "quality-only GitHub Action lock is invalid")

    overlap = sorted(set(str(name) for name in release_actions).intersection(str(name) for name in quality_only_actions))
    if overlap:
        raise QualityToolchainError(
            "quality_action_lock_overlap",
            "quality-only actions must not redefine release actions: " + ", ".join(overlap),
        )

    combined: dict[str, Mapping[str, Any]] = {}
    for source_name, source in (("shared", release_actions), ("quality-only", quality_only_actions)):
        for name, spec in source.items():
            if not isinstance(spec, Mapping):
                raise QualityToolchainError(
                    "quality_action_lock_invalid",
                    f"invalid {source_name} action lock: {name}",
                )
            combined[str(name)] = spec
    return combined


def _workflow_job_section(workflow: str, job_name: str, next_job: str) -> str:
    start_marker = f"\n  {job_name}:\n"
    if start_marker not in workflow:
        raise QualityToolchainError(
            "quality_toolchain_job_missing",
            f"quality workflow is missing required runtime job {job_name}",
        )
    section = workflow.split(start_marker, 1)[1]
    if next_job:
        end_marker = f"\n  {next_job}:\n"
        if end_marker not in section:
            raise QualityToolchainError(
                "quality_toolchain_job_missing",
                f"quality workflow is missing required job boundary {next_job} after {job_name}",
            )
        section = section.split(end_marker, 1)[0]
    return section


def validate_static(workspace_root: Path) -> dict[str, Any]:
    workspace = workspace_root.resolve()
    lock = _load_json(workspace / "deployment/ci/release-toolchain-lock.json")
    workflow_path = workspace / ".github/workflows/quality.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    expected_actions = _quality_action_lock(lock)

    rows = _workflow_actions(workflow)
    actual: dict[str, list[tuple[str, str]]] = {}
    for name, ref, comment in rows:
        actual.setdefault(name, []).append((ref, comment))
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise QualityToolchainError(
                "quality_action_not_sha_pinned", f"quality workflow action {name}@{ref} is not SHA pinned"
            )
    unexpected = sorted(set(actual).difference(expected_actions))
    if unexpected:
        raise QualityToolchainError("quality_action_set_unlocked", f"unlocked actions: {unexpected}")
    for name, spec in expected_actions.items():
        matches = actual.get(name, [])
        sha = str(spec.get("sha") or "")
        version = str(spec.get("version") or "")
        if not matches or any(ref != sha for ref, _ in matches):
            raise QualityToolchainError("quality_action_pin_mismatch", f"quality workflow must use {name}@{sha}")
        if any(version not in comment for _, comment in matches):
            raise QualityToolchainError(
                "quality_action_version_comment_mismatch", f"quality workflow must identify {name} {version}"
            )

    runner = str(lock.get("runner") or "")
    python_version = str(lock.get("python_version") or "")
    node_version = str(lock.get("node_version") or "")
    npm_version = str(lock.get("npm_version") or "")
    uv_version = str(lock.get("uv_version") or "")
    postgres_image = str(lock.get("postgres_image") or "")
    compact_workflow = " ".join(workflow.split())
    required_counts = {
        f"runs-on: {runner}": 4,
        f"python-version: '{python_version}'": 4,
        f"node-version: '{node_version}'": 2,
        "package-manager-cache: false": 2,
        "npm ci --ignore-scripts=false": 2,
    }
    missing: list[str] = []
    for fragment, count in required_counts.items():
        if workflow.count(fragment) < count:
            missing.append(f"{fragment} (expected >= {count})")
    compact_required = {
        "--require-hashes --only-binary=:all: -r deployment/ci/uv-requirements-linux-x86_64.txt": 2,
        "scripts/quality_toolchain_contract.py --workspace-root . --static-only": 1,
        "scripts/quality_toolchain_contract.py --workspace-root . --runtime-only": 2,
    }
    for fragment, count in compact_required.items():
        if compact_workflow.count(fragment) < count:
            missing.append(f"{fragment} (expected >= {count})")
    if missing:
        raise QualityToolchainError("quality_toolchain_workflow_unlocked", "missing controls: " + ", ".join(missing))
    if postgres_image not in workflow:
        raise QualityToolchainError("quality_postgres_image_unlocked", "integration pgvector image is not digest pinned")
    if "pgvector/pgvector:pg16" in workflow:
        raise QualityToolchainError("quality_postgres_tag_forbidden", "mutable pgvector tag is forbidden")
    if re.search(r"pip\s+install[^\n]*\buv(?:\s|$)", workflow):
        raise QualityToolchainError("quality_uv_unlocked", "unhashed uv installation is forbidden")

    runtime_jobs = (
        ("quality-quick-execution", "quality-quick-required-status"),
        ("quality-integration", "governed-failure-stage1"),
    )
    for job_name, next_job in runtime_jobs:
        section = _workflow_job_section(workflow, job_name, next_job)
        try:
            bootstrap_index = section.index("- name: Bootstrap locked uv")
            runtime_index = section.index("- name: Validate locked runtime toolchain")
            install_index = section.index("- name: Install locked Python environments")
        except ValueError as exc:
            raise QualityToolchainError(
                "quality_toolchain_step_missing", f"{job_name} is missing a locked toolchain step"
            ) from exc
        if not bootstrap_index < runtime_index < install_index:
            raise QualityToolchainError(
                "quality_toolchain_step_order_invalid",
                f"{job_name} must bootstrap uv, validate runtime, then install project dependencies",
            )

    return {
        "contract": CONTRACT,
        "status": "PASS",
        "mode": "static",
        "workflow": str(workflow_path.relative_to(workspace)),
        "runner": runner,
        "python_version": python_version,
        "node_version": node_version,
        "npm_version": npm_version,
        "uv_version": uv_version,
        "postgres_image": postgres_image,
        "action_count": len(rows),
        "shared_action_count": len(lock.get("github_actions") or {}),
        "quality_only_action_count": len(lock.get("quality_github_actions") or {}),
    }


def _run_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualityToolchainError(
            "quality_toolchain_command_unavailable", f"unable to execute {command[0]}: {exc}", environment_blocked=True
        ) from exc
    if result.returncode:
        raise QualityToolchainError(
            "quality_toolchain_command_failed",
            f"{command[0]} exited {result.returncode}: {(result.stderr or result.stdout).strip()}",
            environment_blocked=True,
        )
    return result.stdout.strip()


def _normalize_version_output(name: str, value: str) -> str:
    text = value.strip()
    if name == "node" and text.startswith("v"):
        text = text[1:]
    elif name == "uv" and text.startswith("uv "):
        text = text[3:]
    token = text.split(maxsplit=1)[0] if text else ""
    if not _VERSION_TOKEN_RE.fullmatch(token):
        raise QualityToolchainError(
            "quality_toolchain_version_unparseable",
            f"unable to parse {name} version from {value!r}",
            environment_blocked=True,
        )
    return token


def validate_runtime(workspace_root: Path) -> dict[str, Any]:
    workspace = workspace_root.resolve()
    lock = _load_json(workspace / "deployment/ci/release-toolchain-lock.json")
    commands = {
        "python": [sys.executable, "-c", "import platform; print(platform.python_version())"],
        "node": [shutil.which("node") or "node", "--version"],
        "npm": [shutil.which("npm") or "npm", "--version"],
        "uv": [shutil.which("uv") or "uv", "--version"],
    }
    raw = {name: _run_version(command) for name, command in commands.items()}
    actual = {name: _normalize_version_output(name, value) for name, value in raw.items()}
    expected = {
        "python": str(lock.get("python_version") or ""),
        "node": str(lock.get("node_version") or ""),
        "npm": str(lock.get("npm_version") or ""),
        "uv": str(lock.get("uv_version") or ""),
    }
    mismatches = {name: {"expected": expected[name], "actual": actual[name]} for name in expected if actual[name] != expected[name]}
    if mismatches:
        raise QualityToolchainError(
            "quality_toolchain_runtime_mismatch",
            json.dumps(mismatches, ensure_ascii=False, sort_keys=True),
            environment_blocked=True,
        )
    return {
        "contract": CONTRACT,
        "status": "PASS",
        "mode": "runtime",
        "runner": platform.platform(),
        "versions": actual,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--static-only", action="store_true")
    mode.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        payload = validate_static(Path(args.workspace_root)) if args.static_only else validate_runtime(Path(args.workspace_root))
        code = 0
    except QualityToolchainError as exc:
        payload = {
            "contract": CONTRACT,
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "code": exc.code,
            "message": str(exc),
        }
        code = 2 if exc.environment_blocked else 1
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
