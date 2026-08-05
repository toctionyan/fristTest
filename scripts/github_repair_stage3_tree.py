#!/usr/bin/env python3
"""Bind the exact validated Git tree to a Stage-3 candidate plan."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class TreeBindingError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TreeBindingError(f"JSON object required: {path}")
    return payload


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise TreeBindingError((completed.stderr or completed.stdout or "git failed").strip())
    return completed.stdout.strip()


def bind_tree(*, workspace: Path, plan_path: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    plan = _load(plan_path)
    if plan.get("schema") != "github-governed-repair-stage3@1":
        raise TreeBindingError("unsupported Stage-3 plan schema")
    if plan.get("status") != "CANDIDATE_PREPARED":
        raise TreeBindingError("Stage-3 candidate is not prepared")
    head = _git(workspace, "rev-parse", "HEAD")
    if head != str(plan.get("candidate_sha") or ""):
        raise TreeBindingError("Stage-3 candidate SHA drifted before tree binding")
    if _git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise TreeBindingError("Stage-3 candidate workspace is dirty before tree binding")
    parent = _git(workspace, "rev-parse", "HEAD^")
    if parent != str(plan.get("head_sha") or ""):
        raise TreeBindingError("Stage-3 candidate parent is not the failed source SHA")
    tree = _git(workspace, "rev-parse", "HEAD^{tree}")
    if len(tree) != 40:
        raise TreeBindingError("invalid validated Git tree identity")
    plan["validated_tree_sha"] = tree
    plan["validated_parent_sha"] = parent
    plan["tree_binding_complete"] = True
    plan["full_validation_passed"] = False
    plan["draft_pr_published"] = False
    plan["production_closed"] = False
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    try:
        bind_tree(workspace=Path(args.workspace), plan_path=Path(args.plan))
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError, TreeBindingError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
