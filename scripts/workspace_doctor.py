#!/usr/bin/env python3
"""Read-only readiness diagnostics for every local quality-loop runtime."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _check(status: bool, detail: Any) -> dict[str, Any]:
    return {"status": "PASS" if status else "FAIL", "detail": detail}


def _python_ok(candidate: str | None) -> tuple[bool, str]:
    if not candidate:
        return False, "not found"
    try:
        result = subprocess.run(
            [candidate, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    version = result.stdout.strip()
    parts = tuple(int(value) for value in version.split(".")[:2]) if version else ()
    return bool(result.returncode == 0 and (3, 12) <= parts < (3, 14)), version or result.stderr.strip()


def _import_check(candidate: str | None, modules: tuple[str, ...]) -> tuple[bool, str]:
    valid, detail = _python_ok(candidate)
    if not valid or not candidate:
        return False, detail
    result = subprocess.run(
        [candidate, "-c", ";".join(f"import {name}" for name in modules)],
        text=True,
        capture_output=True,
        timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    return result.returncode == 0, "imports available" if result.returncode == 0 else result.stderr.strip()


def _managed_npm(workspace: Path) -> str | None:
    system = shutil.which("npm")
    if system:
        return system
    candidates = sorted((workspace / ".quality/tools").glob("node-*/bin/npm"))
    return str(candidates[-1].absolute()) if candidates else None


def _managed_uv(workspace: Path) -> str | None:
    explicit = os.getenv("UV_BIN")
    if explicit:
        return explicit
    system = shutil.which("uv")
    if system:
        return system
    managed = workspace / ".quality/tools/uv-venv/bin/uv"
    return str(managed) if managed.is_file() else None


def _uv_lock_check(workspace: Path, project: str) -> tuple[bool, dict[str, Any]]:
    uv = _managed_uv(workspace)
    if not uv:
        return False, {"project": project, "error": "uv not found"}
    command = [
        uv,
        "sync",
        "--project",
        str(workspace / project),
        "--all-groups",
        "--locked",
        "--check",
        "--offline",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, {"project": project, "error": str(exc)}
    detail = (result.stdout + result.stderr).strip()
    return result.returncode == 0, {
        "project": project,
        "command": command,
        "exit_code": result.returncode,
        "detail": detail[-4000:],
    }


def _frontend_lock_check(
    workspace: Path, npm: str | None
) -> tuple[bool, dict[str, Any]]:
    frontend = workspace / "services/agent-service/frontend"
    lock_path = frontend / "package-lock.json"
    package_path = frontend / "package.json"
    if not npm or not lock_path.is_file() or not package_path.is_file():
        return False, {"error": "npm, package.json, or package-lock.json is unavailable"}
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, {"error": str(exc)}
    declared = {
        **dict(package.get("dependencies") or {}),
        **dict(package.get("devDependencies") or {}),
    }
    locked_packages = lock.get("packages") if isinstance(lock.get("packages"), dict) else {}
    mismatches: dict[str, dict[str, str]] = {}
    for name in sorted(declared):
        locked = locked_packages.get(f"node_modules/{name}") or {}
        locked_version = str(locked.get("version") or "")
        installed_path = frontend / "node_modules" / name / "package.json"
        try:
            installed_version = str(
                json.loads(installed_path.read_text(encoding="utf-8")).get("version") or ""
            )
        except (OSError, json.JSONDecodeError):
            installed_version = ""
        if not locked_version or installed_version != locked_version:
            mismatches[name] = {
                "locked": locked_version or "<missing>",
                "installed": installed_version or "<missing>",
            }
    try:
        npm_result = subprocess.run(
            [npm, "ls", "--prefix", str(frontend), "--depth=0", "--json"],
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=60,
            env={
                **os.environ,
                "PATH": str(Path(npm).parent)
                + os.pathsep
                + os.environ.get("PATH", ""),
            },
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, {"mismatches": mismatches, "npm_error": str(exc)}
    return not mismatches and npm_result.returncode == 0, {
        "mismatches": mismatches,
        "npm_exit_code": npm_result.returncode,
        "npm_detail": (npm_result.stderr or npm_result.stdout)[-4000:],
    }


def _resolved_agent_python(workspace: Path) -> str:
    explicit = os.getenv("QUALITY_AGENT_PYTHON") or os.getenv("PYTHON_BIN")
    if explicit:
        return explicit
    local = workspace / "services/agent-service/.venv/bin/python"
    if local.is_file():
        return str(local)
    resolver = workspace / "services/agent-service/scripts/resolve_python.py"
    try:
        result = subprocess.run(
            [str(resolver)],
            text=True,
            capture_output=True,
            timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return str(local)
    return result.stdout.strip() if result.returncode == 0 else str(local)


def _template_check(workspace: Path) -> tuple[bool, dict[str, list[str]]]:
    policy = json.loads((workspace / "governance/architecture-policy.json").read_text(encoding="utf-8"))
    missing: dict[str, list[str]] = {}
    for row in (policy.get("configuration") or {}).get("templates") or []:
        relative = str(row.get("path") or "")
        path = workspace / relative
        if not path.is_file():
            missing[relative] = ["<file>"]
            continue
        text = path.read_text(encoding="utf-8")
        names = set(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)\s*=", text))
        absent = sorted(set(row.get("required_variables") or []).difference(names))
        if absent:
            missing[relative] = absent
    return not missing, missing


def inspect_workspace(workspace: Path) -> dict[str, Any]:
    """Inspect prerequisites without creating caches, environments, or evidence."""
    workspace = workspace.resolve()
    agent_python = _resolved_agent_python(workspace)
    business_python = os.getenv("QUALITY_BUSINESS_PYTHON") or str(
        workspace / "services/business-service/.venv/bin/python"
    )
    python_valid, python_detail = _python_ok(agent_python)
    agent_valid, agent_detail = _import_check(
        agent_python, ("pytest", "pytest_asyncio", "langchain_core", "fastapi")
    )
    business_valid, business_detail = _import_check(
        business_python, ("pytest", "fastapi", "pydantic")
    )
    npm = _managed_npm(workspace)
    npm_valid = bool(npm and Path(npm).exists())
    frontend = workspace / "services/agent-service/frontend"
    frontend_missing = [
        item
        for item in ("package.json", "package-lock.json", "index.html", "node_modules")
        if not (frontend / item).exists()
    ]
    templates_valid, template_missing = _template_check(workspace)
    agent_lock_valid, agent_lock_detail = _uv_lock_check(
        workspace, "services/agent-service"
    )
    business_lock_valid, business_lock_detail = _uv_lock_check(
        workspace, "services/business-service"
    )
    frontend_lock_valid, frontend_lock_detail = _frontend_lock_check(workspace, npm)
    checks = {
        "python": _check(python_valid, {"path": agent_python, "version": python_detail}),
        "agent_test_runtime": _check(agent_valid, agent_detail),
        "business_test_runtime": _check(business_valid, business_detail),
        "npm": _check(npm_valid, npm or "not found"),
        "frontend_dependencies": _check(not frontend_missing, {"missing": frontend_missing}),
        "agent_lock": _check(agent_lock_valid, agent_lock_detail),
        "business_lock": _check(business_lock_valid, business_lock_detail),
        "frontend_lock": _check(frontend_lock_valid, frontend_lock_detail),
        "required_templates": _check(templates_valid, {"missing": template_missing}),
    }
    return {
        "status": "PASS" if all(item["status"] == "PASS" for item in checks.values()) else "FAIL",
        "workspace": str(workspace),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=".")
    args = parser.parse_args()
    report = inspect_workspace(Path(args.workspace_root))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
