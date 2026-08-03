#!/usr/bin/env python3
"""Fail-closed repository onboarding and workflow authority preflight.

This command is intentionally stdlib-only so it can run before project
dependencies are installed.  It never reads secret values; repository metadata
contains names/presence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

CONTRACT = "repository-onboarding-preflight@1"
REQUIRED_ENVIRONMENT = "production-certification"
REQUIRED_SECRETS = {
    "PRODUCTION_MODEL_API_KEY",
    "PRODUCTION_EMBEDDING_API_KEY",
    "QUALITY_EVIDENCE_SIGNING_KEY",
}
REQUIRED_WORKFLOWS = {
    ".github/workflows/quality.yml",
    ".github/workflows/integration-diagnostic.yml",
    ".github/workflows/wp08-certification.yml",
    ".github/workflows/release.yml",
}
REQUIRED_ROOT_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "VERSION",
    "PHASE_CANDIDATE_MANIFEST.json",
    "governance/task-ledger.json",
    "deployment/ci/release-toolchain-lock.json",
}
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RepositoryOnboardingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryOnboardingError("json_invalid", f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RepositoryOnboardingError("json_not_object", f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_identity(workspace: Path) -> dict[str, Any]:
    manifest_path = workspace / "PHASE_CANDIDATE_MANIFEST.json"
    release_path = workspace / "release/MANIFEST.json"
    manifest = _json(manifest_path)
    release = _json(release_path)
    return {
        "workspace": str(release.get("workspace") or ""),
        "version": str(release.get("version") or ""),
        "skill_version": str((release.get("skill") or {}).get("version") or ""),
        "phase": str(release.get("phase") or manifest.get("phase") or ""),
        "manifest_sha256": _sha256(manifest_path),
        "production_closed": bool(release.get("production_closed", False)),
    }


def _workspace_safety(workspace: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    blockers: list[str] = []
    for relative in sorted(REQUIRED_ROOT_FILES | REQUIRED_WORKFLOWS):
        if not (workspace / relative).is_file():
            errors.append(f"required_file_missing:{relative}")

    forbidden_dirs = {".git", ".venv", "venv", "node_modules", ".pytest_cache", "__pycache__"}
    for path in workspace.rglob("*"):
        try:
            mode = path.lstat().st_mode
        except OSError:
            errors.append(f"path_unreadable:{path.relative_to(workspace).as_posix()}")
            continue
        relative = path.relative_to(workspace).as_posix()
        if stat.S_ISLNK(mode):
            errors.append(f"symlink_forbidden:{relative}")
        if path.is_dir() and path.name in forbidden_dirs:
            errors.append(f"runtime_directory_forbidden:{relative}")
        if path.is_file() and path.name == ".env":
            errors.append(f"real_env_file_forbidden:{relative}")

    # Candidate packages are expected to remain not production-closed.
    try:
        identity = _workspace_identity(workspace)
        if identity["production_closed"]:
            blockers.append("candidate_already_production_closed")
    except RepositoryOnboardingError as exc:
        errors.append(f"workspace_identity_invalid:{exc.code}")
    return errors, blockers


def _integration_workflow_authority(workspace: Path) -> list[str]:
    errors: list[str] = []
    lock = _json(workspace / "deployment/ci/release-toolchain-lock.json")
    workflow_path = workspace / ".github/workflows/integration-diagnostic.yml"
    try:
        text = workflow_path.read_text(encoding="utf-8")
    except OSError:
        return ["integration_workflow_missing"]

    expected = {
        "runner": f"runs-on: {lock.get('runner')}",
        "python": f"python-version: '{lock.get('python_version')}'",
        "node": f"node-version: '{lock.get('node_version')}'",
        "postgres": f"image: {lock.get('postgres_image')}",
        "uv_lock": "deployment/ci/uv-requirements-linux-x86_64.txt",
        "toolchain_check": "scripts/quality_toolchain_contract.py",
    }
    for name, needle in expected.items():
        if needle not in text:
            errors.append(f"integration_workflow_unlocked:{name}")

    for action, details in (lock.get("github_actions") or {}).items():
        if action not in {"actions/checkout", "actions/setup-python", "actions/setup-node", "actions/upload-artifact"}:
            continue
        expected_use = f"uses: {action}@{details.get('sha')}"
        if expected_use not in text:
            errors.append(f"integration_action_not_sha_pinned:{action}")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("uses: actions/") and not re.search(r"@[0-9a-f]{40}(?:\s|$)", stripped):
            errors.append(f"mutable_action_reference:{stripped}")
        if "pgvector/pgvector:" in stripped:
            errors.append("mutable_pgvector_tag_forbidden")
    return errors


def _marker_matches(marker: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    keys = ("workspace", "version", "skill_version", "manifest_sha256")
    return all(str(marker.get(key) or "") == str(identity.get(key) or "") for key in keys)


def evaluate(
    workspace_root: Path,
    *,
    repository_metadata: Mapping[str, Any] | None = None,
    allow_public: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    errors: list[str] = []
    blockers: list[str] = []
    if not workspace.is_dir():
        return {
            "contract": CONTRACT,
            "status": "FAIL",
            "reason": "workspace_missing",
            "errors": ["workspace_missing"],
            "blockers": [],
            "production_closed": False,
        }

    safety_errors, safety_blockers = _workspace_safety(workspace)
    errors.extend(safety_errors)
    blockers.extend(safety_blockers)
    errors.extend(_integration_workflow_authority(workspace))

    try:
        identity = _workspace_identity(workspace)
    except RepositoryOnboardingError as exc:
        identity = {}
        errors.append(f"workspace_identity_invalid:{exc.code}")

    repository: dict[str, Any] = {}
    if repository_metadata is None:
        blockers.append("repository_metadata_missing")
    elif not isinstance(repository_metadata, Mapping):
        errors.append("repository_metadata_invalid")
    else:
        repository = dict(repository_metadata)
        full_name = str(repository.get("repository_full_name") or "")
        if not REPOSITORY_RE.fullmatch(full_name):
            errors.append("repository_full_name_invalid")
        if str(repository.get("default_branch") or "") != "main":
            blockers.append("repository_default_branch_not_main")
        visibility = str(repository.get("visibility") or "").casefold()
        if visibility == "public" and not allow_public:
            blockers.append("public_repository_requires_explicit_approval")
        if visibility not in {"private", "public", "internal"}:
            blockers.append("repository_visibility_unknown")

        permissions = repository.get("permissions") if isinstance(repository.get("permissions"), Mapping) else {}
        if not bool(permissions.get("push")):
            blockers.append("repository_push_permission_missing")
        if not (bool(permissions.get("admin")) or bool(permissions.get("maintain"))):
            blockers.append("repository_admin_or_maintain_permission_missing")

        is_empty = repository.get("is_empty")
        if is_empty is False:
            marker = repository.get("workspace_marker")
            if not isinstance(marker, Mapping) or not _marker_matches(marker, identity):
                errors.append("nonempty_repository_unrelated")
        elif is_empty is not True:
            blockers.append("repository_empty_state_unknown")

        protection = repository.get("branch_protection")
        if not isinstance(protection, Mapping) or protection.get("main") is not True:
            blockers.append("protected_main_missing")

        environments = {str(item) for item in repository.get("environments", []) if str(item)}
        if REQUIRED_ENVIRONMENT not in environments:
            blockers.append(f"environment_missing:{REQUIRED_ENVIRONMENT}")

        secrets = {str(item) for item in repository.get("secret_names", []) if str(item)}
        for name in sorted(REQUIRED_SECRETS - secrets):
            blockers.append(f"secret_missing:{name}")

    status = "FAIL" if errors else ("BLOCKED_BY_ENVIRONMENT" if blockers else "PASS")
    next_actions: list[str] = []
    for blocker in sorted(set(blockers)):
        if blocker == "repository_metadata_missing":
            next_actions.append("Export target repository metadata and rerun this preflight.")
        elif blocker == "protected_main_missing":
            next_actions.append("Protect refs/heads/main before running WP-08 or release workflows.")
        elif blocker.startswith("environment_missing:"):
            next_actions.append("Create the production-certification GitHub Environment.")
        elif blocker.startswith("secret_missing:"):
            next_actions.append(f"Add GitHub Environment secret {blocker.split(':', 1)[1]}.")
        elif blocker == "public_repository_requires_explicit_approval":
            next_actions.append("Use a private repository or rerun with explicit public-repository approval.")
    return {
        "contract": CONTRACT,
        "status": status,
        "reason": "repository_onboarding_ready" if status == "PASS" else (
            "repository_onboarding_failed" if errors else "repository_onboarding_blocked"
        ),
        "workspace_identity": identity,
        "repository": {
            "repository_full_name": repository.get("repository_full_name"),
            "default_branch": repository.get("default_branch"),
            "visibility": repository.get("visibility"),
            "is_empty": repository.get("is_empty"),
        },
        "required_environment": REQUIRED_ENVIRONMENT,
        "required_secret_names": sorted(REQUIRED_SECRETS),
        "required_workflows": sorted(REQUIRED_WORKFLOWS),
        "errors": sorted(set(errors)),
        "blockers": sorted(set(blockers)),
        "next_actions": list(dict.fromkeys(next_actions)),
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--repository-metadata")
    parser.add_argument("--allow-public", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    metadata = _json(Path(args.repository_metadata).resolve()) if args.repository_metadata else None
    payload = evaluate(Path(args.workspace_root), repository_metadata=metadata, allow_public=args.allow_public)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["status"] == "PASS" else (78 if payload["status"] == "BLOCKED_BY_ENVIRONMENT" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
