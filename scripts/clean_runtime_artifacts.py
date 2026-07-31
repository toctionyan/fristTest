#!/usr/bin/env python3
"""Inspect or explicitly remove generated runtime/cache/build artifacts.

Quality validation must never erase a developer's installed dependencies or
runtime state.  Removal therefore requires ``--apply`` and is a maintenance
operation, not a quality-loop step.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

RUNTIME_DIRS = [
    "services/agent-service/runtime",
    "services/business-service/runtime",
]
REMOVE_DIR_NAMES = {"__pycache__", ".pytest_cache"}
REMOVE_FILE_SUFFIXES = {".pyc", ".pyo"}
REMOVE_PATHS = ["services/agent-service/frontend/dist"]


def artifacts(workspace: Path) -> list[str]:
    found: list[str] = []
    for rel in RUNTIME_DIRS:
        root = workspace / rel
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), reverse=True):
            if path.name == ".gitkeep":
                continue
            found.append(str(path.relative_to(workspace)))
    for rel in REMOVE_PATHS:
        path = workspace / rel
        if path.exists():
            found.append(str(path.relative_to(workspace)))
    for path in sorted(workspace.rglob("*"), reverse=True):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        if path.is_dir() and path.name in REMOVE_DIR_NAMES:
            found.append(str(path.relative_to(workspace)))
        elif path.is_file() and path.suffix in REMOVE_FILE_SUFFIXES:
            found.append(str(path.relative_to(workspace)))
    return sorted(set(found))


def clean(workspace: Path) -> dict:
    removed: list[str] = []
    for rel in artifacts(workspace):
        path = workspace / rel
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            removed.append(rel)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(rel)
    for rel in RUNTIME_DIRS:
        root = workspace / rel
        root.mkdir(parents=True, exist_ok=True)
        (root / ".gitkeep").touch(exist_ok=True)
    return {"status": "PASS", "removed": removed, "removed_count": len(removed)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--apply", action="store_true", help="remove artifacts; omitted by default for non-destructive inspection")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    if args.apply:
        result = clean(workspace)
    else:
        found = artifacts(workspace)
        result = {"status": "PASS" if not found else "DIRTY", "artifacts": found, "artifact_count": len(found)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
