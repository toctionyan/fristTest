#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "skill-system" / "profiles"


def load_profile(name: str) -> dict[str, Any]:
    path = PROFILES / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown Skill profile: {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("id") != name or payload.get("schema_version") != 1:
        raise ValueError(f"invalid Skill profile: {name}")
    return payload


def expand_profiles(name: str, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = set(seen or ())
    if name in seen:
        raise ValueError(f"cyclic Skill profile include: {name}")
    seen.add(name)
    profile = load_profile(name)
    rows: list[dict[str, Any]] = []
    for child in profile.get("includes") or []:
        rows.extend(expand_profiles(str(child), seen.copy()))
    rows.append(profile)
    unique: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in rows:
        if row["id"] not in ids:
            unique.append(row)
            ids.add(row["id"])
    return unique


def _workspace_root(raw: str | Path | None = None) -> Path:
    path = Path.cwd().resolve() if raw is None else Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"profile workspace root does not exist: {path}")
    return path


def command_argv(raw: list[str], *, workspace: Path) -> list[str]:
    replacements = {
        "{python}": sys.executable,
        "{workspace_root}": str(workspace),
        "{controller_root}": str(ROOT.resolve()),
    }
    return [replacements.get(value, value) for value in raw]


def run(name: str, *, workspace: str | Path | None = None) -> dict[str, Any]:
    workspace_root = _workspace_root(workspace)
    results: list[dict[str, Any]] = []
    for profile in expand_profiles(name):
        for index, raw in enumerate(profile.get("commands") or [], start=1):
            argv = command_argv([str(v) for v in raw], workspace=workspace_root)
            completed = subprocess.run(
                argv,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            row = {
                "profile": profile["id"],
                "command_index": index,
                "argv": argv,
                "controller_cwd": str(ROOT.resolve()),
                "workspace_root": str(workspace_root),
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "status": "PASS" if completed.returncode == 0 else "FAIL",
            }
            results.append(row)
            if completed.returncode:
                return {
                    "status": "FAIL",
                    "requested_profile": name,
                    "workspace_root": str(workspace_root),
                    "results": results,
                }
    return {
        "status": "PASS",
        "requested_profile": name,
        "workspace_root": str(workspace_root),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument(
        "--workspace-root",
        help="product/workspace authority for workspace-aware profiles; defaults to the caller's cwd",
    )
    args = parser.parse_args()
    try:
        result = run(args.profile, workspace=args.workspace_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "status": "FAIL",
            "requested_profile": args.profile,
            "workspace_root": str(Path(args.workspace_root).expanduser().resolve()) if args.workspace_root else str(Path.cwd().resolve()),
            "error": str(exc),
            "results": [],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
