#!/usr/bin/env python3
"""Fail-closed preflight for real Codex/Claude host and protected release execution."""
from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

CONTRACT = "host-execution-preflight@1"
REQUIRED_HOSTS = ("codex", "claude")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_json:{path.name}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid_json_object:{path.name}")
    return payload


def _run(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=cwd, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
    )


def _tool_version(path: str, *, cwd: Path, runner: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    completed = runner([path, "--version"], cwd=cwd)
    if completed.returncode != 0:
        raise RuntimeError("version_command_failed")
    value = (completed.stdout or completed.stderr or "").strip().splitlines()
    if not value:
        raise RuntimeError("version_output_empty")
    return value[0][:240]


def _git_state(workspace: Path, *, runner: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    def git(*args: str) -> str:
        completed = runner(["git", *args], cwd=workspace)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "git_failed").strip()[:240])
        return completed.stdout.strip()

    top = Path(git("rev-parse", "--show-toplevel")).resolve()
    if top != workspace:
        raise RuntimeError("git_root_mismatch")
    branch = git("branch", "--show-current")
    head = git("rev-parse", "HEAD").casefold()
    dirty = git("status", "--porcelain=v1", "--untracked-files=all")
    origin = git("remote", "get-url", "origin")
    return {"branch": branch, "head_sha": head, "origin": origin, "clean": not bool(dirty)}


def _host_conformance_errors(workspace: Path) -> list[str]:
    namespace = runpy.run_path(str(workspace / "skill-system/controller/host_conformance.py"))
    verify = namespace.get("verify")
    if not callable(verify):
        return ["host_conformance_entrypoint_missing"]
    return [str(item) for item in verify(True)]


def evaluate(
    workspace_root: Path,
    *,
    mode: str = "host",
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run,
    host_conformance: Callable[[Path], list[str]] = _host_conformance_errors,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    source_env = dict(os.environ if env is None else env)
    blockers: list[str] = []
    errors: list[str] = []
    tools: dict[str, Any] = {}

    if mode not in {"host", "production"}:
        return {"contract": CONTRACT, "status": "FAIL", "reason": "mode_invalid", "production_closed": False}
    if not workspace.is_dir():
        return {"contract": CONTRACT, "status": "FAIL", "reason": "workspace_missing", "production_closed": False}

    ledger_path = workspace / "governance/task-ledger.json"
    try:
        ledger = _read_json(ledger_path)
        stages = {str(item.get("stage_id")): item for item in ledger.get("stages", []) if isinstance(item, dict)}
        packages = {str(item.get("work_package_id")): item for item in ledger.get("work_packages", []) if isinstance(item, dict)}
        if str(stages.get("STAGE-5", {}).get("status")) != "CLOSED_VERIFIED":
            blockers.append("stage5_not_closed")
        if str(packages.get("WP-08", {}).get("status")) != "CLOSED_VERIFIED":
            blockers.append("wp08_not_closed")
    except ValueError as exc:
        errors.append(str(exc))

    try:
        errors.extend(host_conformance(workspace))
    except Exception as exc:  # preflight must report rather than crash
        errors.append(f"host_conformance_unavailable:{exc.__class__.__name__}")

    required = list(REQUIRED_HOSTS)
    if mode == "production":
        required.append("gh")
    for name in required:
        path = which(name)
        if not path:
            blockers.append(f"host_binary_missing:{name}")
            continue
        resolved = Path(path).resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            errors.append(f"host_binary_not_executable:{name}")
            continue
        try:
            version = _tool_version(str(resolved), cwd=workspace, runner=runner)
        except Exception as exc:
            errors.append(f"host_version_unavailable:{name}:{exc}")
            continue
        tools[name] = {"path": str(resolved), "version": version}

    try:
        git = _git_state(workspace, runner=runner)
        if git["branch"] != "main":
            blockers.append("git_branch_not_main")
        if not git["clean"]:
            errors.append("git_checkout_dirty")
    except Exception as exc:
        git = {}
        blockers.append(f"git_repository_unavailable:{exc}")

    if mode == "production":
        expected = {
            "GITHUB_ACTIONS": "true",
            "CI": "true",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_REF_PROTECTED": "true",
        }
        for key, value in expected.items():
            if str(source_env.get(key) or "").strip().casefold() != value.casefold():
                blockers.append(f"protected_ci_identity_missing:{key}")
        if not str(source_env.get("GITHUB_REPOSITORY") or "").strip():
            blockers.append("github_repository_missing")

    status = "FAIL" if errors else ("BLOCKED_BY_ENVIRONMENT" if blockers else "PASS")
    return {
        "contract": CONTRACT,
        "status": status,
        "mode": mode,
        "reason": "host_preflight_passed" if status == "PASS" else ("host_preflight_failed" if errors else "host_preflight_blocked"),
        "tools": tools,
        "git": git,
        "errors": sorted(set(errors)),
        "blockers": sorted(set(blockers)),
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--mode", choices=("host", "production"), default="host")
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = evaluate(Path(args.workspace_root), mode=args.mode)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["status"] == "PASS" else (78 if payload["status"] == "BLOCKED_BY_ENVIRONMENT" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
