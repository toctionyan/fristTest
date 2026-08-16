#!/usr/bin/env python3
"""Bind the exact independently validated Stage-3 source tree.

This controller has no baseline-acceptance authority.  It binds only the exact
RCA/write-grant product-source patch that Stage 3 prepared.  Protected baseline
refresh is intentionally deferred until an explicit governance-closed receipt
exists; therefore validation cannot turn a protected-baseline alarm green by
rewriting the alarm's expected hash.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

STAGE3_SCHEMA = "github-governed-repair-stage3@2"


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
        raise TreeBindingError(
            (completed.stderr or completed.stdout or "git failed").strip()
        )
    return completed.stdout.strip()


def _status_paths(workspace: Path) -> list[str]:
    rows = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).splitlines()
    paths: list[str] = []
    for row in rows:
        raw = row[3:] if len(row) > 3 else ""
        path = raw.split(" -> ")[-1].strip().replace("\\", "/")
        if path and path not in paths:
            paths.append(path)
    return paths


def bind_tree(*, workspace: Path, plan_path: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    plan = _load(plan_path)
    if plan.get("schema") != STAGE3_SCHEMA:
        raise TreeBindingError("unsupported Stage-3 plan schema")
    if plan.get("status") != "CANDIDATE_PREPARED":
        raise TreeBindingError("Stage-3 candidate is not prepared")
    if plan.get("governed_repair_state") != "INDEPENDENT_REVIEW":
        raise TreeBindingError("Stage-3 candidate is not in independent review")
    if plan.get("production_closed") is not False:
        raise TreeBindingError("Stage-3 cannot assert production closure")

    head = _git(workspace, "rev-parse", "HEAD")
    if head != str(plan.get("candidate_sha") or ""):
        raise TreeBindingError("Stage-3 candidate SHA drifted before tree binding")
    if _status_paths(workspace):
        raise TreeBindingError("Stage-3 candidate workspace is dirty before tree binding")
    parent = _git(workspace, "rev-parse", "HEAD^")
    if parent != str(plan.get("head_sha") or ""):
        raise TreeBindingError("Stage-3 candidate parent is not the failed source SHA")

    source_paths = [str(item) for item in plan.get("changed_paths") or []]
    write_scope = [str(item) for item in plan.get("write_scope") or []]
    if not source_paths or any(path not in write_scope for path in source_paths):
        raise TreeBindingError("Stage-3 source paths escape the immutable write grant")

    tree = _git(workspace, "rev-parse", "HEAD^{tree}")
    if len(tree) != 40:
        raise TreeBindingError("invalid validated Git tree identity")

    plan["derived_paths"] = []
    plan["publication_paths"] = list(source_paths)
    plan["validated_tree_sha"] = tree
    plan["validated_parent_sha"] = parent
    plan["tree_binding_complete"] = True
    plan["governance_closed"] = False
    plan["baseline_accepted"] = False
    plan["exact_head_certified"] = False
    plan["ready_for_review"] = False
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
    except (
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        TreeBindingError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "baseline_accepted": False,
                    "production_closed": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
