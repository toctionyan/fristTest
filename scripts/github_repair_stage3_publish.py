#!/usr/bin/env python3
"""Recreate a validated Stage-3 tree without executing candidate code.

This trusted publisher runs only after the read-only validation job. It applies the
bound Stage-2 patch to the exact failed source commit, deterministically regenerates
control-plane-owned derived authority files, verifies the resulting Git tree matches
the independently validated tree, and creates the local commit that may be pushed to
a governed repair branch. It never runs candidate tests or model code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from refresh_product_source_baseline import (
    BaselineRefreshError,
    refresh_product_source_baseline,
)

MAX_PATCH_BYTES = 2_000_000


class PublicationError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PublicationError(f"JSON object required: {path}")
    return payload


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )


def _git(workspace: Path, *args: str) -> str:
    completed = _run(["git", *args], workspace)
    if completed.returncode:
        raise PublicationError((completed.stderr or completed.stdout or "git failed").strip())
    return completed.stdout.strip()


def _normalize(raw: str) -> str:
    value = str(raw).strip().replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise PublicationError(f"invalid repository path: {raw!r}")
    normalized = pure.as_posix()
    if normalized != value:
        raise PublicationError(f"non-canonical repository path: {raw!r}")
    return normalized


def _changed_paths(workspace: Path) -> tuple[str, ...]:
    rows = _git(workspace, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    paths: list[str] = []
    for row in rows:
        raw = row[3:] if len(row) > 3 else ""
        path = raw.split(" -> ")[-1].strip().replace("\\", "/")
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


def _validate_metadata(plan: dict[str, Any], validation: dict[str, Any]) -> None:
    if plan.get("schema") != "github-governed-repair-stage3@1":
        raise PublicationError("unsupported Stage-3 plan schema")
    if plan.get("status") != "CANDIDATE_PREPARED":
        raise PublicationError("Stage-3 plan is not a prepared candidate")
    if plan.get("tree_binding_complete") is not True:
        raise PublicationError("validated tree binding is incomplete")
    if validation.get("schema") != "github-governed-repair-stage3@1":
        raise PublicationError("unsupported Stage-3 validation schema")
    if validation.get("status") != "VALIDATED_FOR_DRAFT_PR":
        raise PublicationError("Stage-3 validation is not publishable")
    if validation.get("targeted_validation_passed") is not True:
        raise PublicationError("targeted validation did not pass")
    if validation.get("full_validation_passed") is not True:
        raise PublicationError("complete Quick validation did not pass")
    if validation.get("quick_loop_status") != "CI_VERIFIED":
        raise PublicationError("Quick validation did not reach CI_VERIFIED")
    if validation.get("draft_pr_published") is not False:
        raise PublicationError("Draft PR publication was already asserted")
    if validation.get("production_closed") is not False:
        raise PublicationError("invalid production closure authority")
    for key in (
        "source_run_id",
        "head_sha",
        "candidate_sha",
        "repair_branch",
        "repair_base_branch",
        "changed_paths",
    ):
        if validation.get(key) != plan.get(key):
            raise PublicationError(f"Stage-3 plan/validation mismatch: {key}")
    if not str(plan.get("validated_tree_sha") or ""):
        raise PublicationError("validated Git tree identity is missing")
    if str(plan.get("validated_parent_sha") or "") != str(plan.get("head_sha") or ""):
        raise PublicationError("validated parent is not the failed source SHA")


def _regenerate_derived_authorities(workspace: Path, plan: dict[str, Any]) -> list[str]:
    try:
        baseline = refresh_product_source_baseline(
            workspace,
            generated_from_sha=str(plan.get("head_sha") or ""),
            allow_missing=True,
        )
    except BaselineRefreshError as exc:
        raise PublicationError(str(exc)) from exc
    derived: list[str] = []
    if baseline.get("changed") is True and baseline.get("path"):
        derived.append(str(baseline["path"]))
    expected = [str(item) for item in plan.get("derived_paths") or []]
    if derived != expected:
        raise PublicationError(
            f"derived authority reproduction mismatch: expected={expected} actual={derived}"
        )
    return derived


def prepare_publication(
    *,
    workspace: Path,
    plan_path: Path,
    validation_path: Path,
    patch_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    plan = _load(plan_path)
    validation = _load(validation_path)
    _validate_metadata(plan, validation)
    if _git(workspace, "rev-parse", "HEAD") != str(plan.get("head_sha") or ""):
        raise PublicationError("publisher checkout does not match the failed source SHA")
    if _changed_paths(workspace):
        raise PublicationError("publisher workspace must start clean")
    if not patch_path.is_file() or patch_path.is_symlink():
        raise PublicationError("repair patch must be an existing regular file")
    patch = patch_path.read_bytes()
    if not patch or len(patch) > MAX_PATCH_BYTES or b"\x00" in patch:
        raise PublicationError("repair patch is empty, oversized, or binary")
    patch_sha = hashlib.sha256(patch).hexdigest()
    if patch_sha != str(plan.get("patch_sha256") or ""):
        raise PublicationError("publisher patch digest does not match validated evidence")

    source_paths = tuple(_normalize(str(item)) for item in plan.get("changed_paths") or [])
    if not source_paths or len(set(source_paths)) != len(source_paths):
        raise PublicationError("validated source path set is empty or duplicated")
    check = _run(
        ["git", "apply", "--check", "--whitespace=error-all", str(patch_path.resolve())],
        workspace,
    )
    if check.returncode:
        raise PublicationError((check.stderr or check.stdout or "git apply --check failed").strip())
    applied = _run(
        ["git", "apply", "--whitespace=error-all", str(patch_path.resolve())],
        workspace,
    )
    if applied.returncode:
        raise PublicationError((applied.stderr or applied.stdout or "git apply failed").strip())
    actual_source_paths = _changed_paths(workspace)
    if set(actual_source_paths) != set(source_paths) or len(actual_source_paths) != len(source_paths):
        raise PublicationError(
            f"publisher source path mismatch: expected={list(source_paths)} actual={list(actual_source_paths)}"
        )

    # Stage the model-owned patch first so git ls-files observes added/deleted
    # protected files exactly as the eventual tree will contain them.  Derived
    # authority content is then regenerated by trusted deterministic code.
    _git(workspace, "add", "-A", "--", *source_paths)
    derived_paths = _regenerate_derived_authorities(workspace, plan)
    if derived_paths:
        _git(workspace, "add", "--", *derived_paths)

    publication_paths = list(source_paths)
    for path in derived_paths:
        if path not in publication_paths:
            publication_paths.append(path)
    expected_publication_paths = [
        _normalize(str(item)) for item in plan.get("publication_paths") or list(source_paths)
    ]
    if publication_paths != expected_publication_paths:
        raise PublicationError(
            "publisher dependency closure path mismatch: "
            f"expected={expected_publication_paths} actual={publication_paths}"
        )
    actual_paths = list(_changed_paths(workspace))
    if set(actual_paths) != set(publication_paths) or len(actual_paths) != len(publication_paths):
        raise PublicationError(
            f"publisher total path mismatch: expected={publication_paths} actual={actual_paths}"
        )

    tree_sha = _git(workspace, "write-tree")
    if tree_sha != str(plan.get("validated_tree_sha") or ""):
        raise PublicationError(
            f"publisher tree mismatch: expected={plan.get('validated_tree_sha')} actual={tree_sha}"
        )
    _git(workspace, "config", "user.name", "github-actions[bot]")
    _git(
        workspace,
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    _git(
        workspace,
        "commit",
        "-m",
        f"Governed repair for workflow run {plan.get('source_run_id')}",
    )
    published_sha = _git(workspace, "rev-parse", "HEAD")
    published_tree = _git(workspace, "rev-parse", "HEAD^{tree}")
    parent = _git(workspace, "rev-parse", "HEAD^")
    if parent != str(plan.get("head_sha") or "") or published_tree != tree_sha:
        raise PublicationError("published commit identity does not preserve validated parent/tree")
    if _changed_paths(workspace):
        raise PublicationError("publisher workspace is dirty after commit")

    result = {
        "schema": "github-governed-repair-stage3-publication@1",
        "status": "PUBLICATION_COMMIT_PREPARED",
        "source_run_id": str(plan.get("source_run_id")),
        "source_head_sha": str(plan.get("head_sha")),
        "validated_candidate_sha": str(plan.get("candidate_sha")),
        "validated_tree_sha": tree_sha,
        "published_candidate_sha": published_sha,
        "repair_branch": str(plan.get("repair_branch")),
        "repair_base_branch": str(plan.get("repair_base_branch")),
        "changed_paths": list(source_paths),
        "derived_paths": derived_paths,
        "publication_paths": publication_paths,
        "full_validation_passed": True,
        "draft_pr_published": False,
        "production_closed": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _github_output(path: Path | None, values: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if "\n" in text:
                raise PublicationError(f"multiline GitHub output is not allowed: {key}")
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--validation-result", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        result = prepare_publication(
            workspace=Path(args.workspace),
            plan_path=Path(args.plan),
            validation_path=Path(args.validation_result),
            patch_path=Path(args.patch),
            output_path=Path(args.output),
        )
        _github_output(
            Path(args.github_output) if args.github_output else None,
            {
                "published_candidate_sha": result["published_candidate_sha"],
                "repair_branch": result["repair_branch"],
                "repair_base_branch": result["repair_base_branch"],
                "source_run_id": result["source_run_id"],
                "source_head_sha": result["source_head_sha"],
            },
        )
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError, PublicationError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
