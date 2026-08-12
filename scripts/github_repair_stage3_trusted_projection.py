#!/usr/bin/env python3
"""Project the bound trusted Judge inputs into a Stage-3 validation workspace.

Stage 3 may validate a repair candidate whose source commit predates later control-plane
changes. The product candidate must remain the exact source+repair commit, while the
Quick Judge must come from the currently bound trusted control plane. This helper
projects only trusted-Judge manifest inputs into the disposable validation workspace.

A Stage-3 control checkout can legitimately predate regeneration of the checked-in
fingerprint manifest after trusted control files move. In GitHub Actions only, this
helper may rebuild that *derived* manifest, but only after proving the bound Judge root
is a completely clean Git checkout. The refreshed manifest and every manifest-owned
Judge file are then made read-only before candidate projection. A dirty, non-Git, or
locally invoked stale Judge root still fails closed.

The projection is validation-only. It never commits projected files, never changes the
repair patch, and never expands publication or production authority.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from trusted_judge import (  # type: ignore  # noqa: E402
    MANIFEST_REL,
    load_manifest,
    sha256,
    verify_candidate,
    verify_root,
    write_manifest,
)

SCHEMA = "github-stage3-trusted-judge-projection@1"


class ProjectionError(RuntimeError):
    """Fail-closed trusted-Judge projection error."""


def _safe_rel(raw: str) -> str:
    value = str(raw).strip().replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ProjectionError(f"invalid trusted Judge path: {raw!r}")
    normalized = pure.as_posix()
    if normalized != value:
        raise ProjectionError(f"non-canonical trusted Judge path: {raw!r}")
    return normalized


def _readonly_mode(source: Path) -> int:
    mode = stat.S_IMODE(source.stat().st_mode)
    return mode & ~0o222


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise ProjectionError(
            f"bound trusted Judge Git check failed ({' '.join(args)}): "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    return completed.stdout.strip()


def _refresh_stale_manifest_from_clean_bound_checkout(judge_root: Path) -> str:
    """Regenerate only the derived manifest from an immutable clean CI checkout."""
    if str(os.getenv("GITHUB_ACTIONS") or "").strip().lower() != "true":
        raise ProjectionError("invalid trusted Judge root: checked-in manifest is stale")
    inside = _git("rev-parse", "--is-inside-work-tree", cwd=judge_root)
    if inside != "true":
        raise ProjectionError("invalid trusted Judge root: bound Judge is not a Git worktree")
    head_sha = _git("rev-parse", "HEAD", cwd=judge_root)
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ProjectionError("invalid trusted Judge root: bound Judge HEAD is not an exact SHA")
    status = _git("status", "--porcelain=v1", "--untracked-files=all", cwd=judge_root)
    if status:
        raise ProjectionError("invalid trusted Judge root: bound Judge checkout is not clean")

    write_manifest(judge_root)
    refreshed_errors = verify_root(judge_root)
    if refreshed_errors:
        raise ProjectionError(
            "invalid trusted Judge root after deterministic manifest refresh: "
            + "; ".join(refreshed_errors)
        )
    manifest = load_manifest(judge_root)
    for raw_rel in sorted((manifest.get("files") or {})):
        rel = _safe_rel(str(raw_rel))
        path = judge_root / rel
        path.chmod(_readonly_mode(path))
    manifest_path = judge_root / MANIFEST_REL
    manifest_path.chmod(_readonly_mode(manifest_path))
    return head_sha


def _prepare_judge_root(judge_root: Path) -> tuple[dict[str, Any], str | None]:
    root_errors = verify_root(judge_root)
    refreshed_from_sha: str | None = None
    if root_errors:
        refreshed_from_sha = _refresh_stale_manifest_from_clean_bound_checkout(judge_root)
    root_errors = verify_root(judge_root)
    if root_errors:
        raise ProjectionError("invalid trusted Judge root: " + "; ".join(root_errors))
    manifest = load_manifest(judge_root)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ProjectionError("trusted Judge manifest contains no files")
    return manifest, refreshed_from_sha


def project(*, candidate_root: Path, judge_root: Path, output_path: Path) -> dict[str, Any]:
    candidate_root = candidate_root.resolve()
    judge_root = judge_root.resolve()
    if candidate_root == judge_root or candidate_root in judge_root.parents or judge_root in candidate_root.parents:
        raise ProjectionError("candidate and trusted Judge roots must be independent workspaces")
    if not candidate_root.is_dir() or not judge_root.is_dir():
        raise ProjectionError("candidate and trusted Judge roots must exist")

    manifest, refreshed_from_sha = _prepare_judge_root(judge_root)
    files = manifest["files"]

    projected: list[dict[str, str]] = []
    for raw_rel, raw_expected in sorted(files.items()):
        rel = _safe_rel(str(raw_rel))
        expected = str(raw_expected)
        source = judge_root / rel
        destination = candidate_root / rel
        if not source.is_file() or source.is_symlink():
            raise ProjectionError(f"trusted Judge source is not a regular file: {rel}")
        if sha256(source) != expected:
            raise ProjectionError(f"trusted Judge source fingerprint changed during projection: {rel}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        shutil.copy2(source, destination)
        destination.chmod(_readonly_mode(source))
        if sha256(destination) != expected:
            raise ProjectionError(f"projected trusted Judge fingerprint mismatch: {rel}")
        projected.append({"path": rel, "sha256": expected})

    candidate_errors = verify_candidate(candidate_root, judge_root)
    if candidate_errors:
        raise ProjectionError("projected candidate does not match trusted Judge: " + "; ".join(candidate_errors))

    manifest_path = judge_root / MANIFEST_REL
    payload = {
        "schema": SCHEMA,
        "status": "PROJECTED",
        "candidate_root": str(candidate_root),
        "judge_root": str(judge_root),
        "judge_manifest_sha256": sha256(manifest_path),
        "judge_manifest_refreshed_from_clean_bound_sha": refreshed_from_sha,
        "projected_file_count": len(projected),
        "projected_files": projected,
        "repair_patch_changed": False,
        "candidate_commit_changed": False,
        "publication_authority_changed": False,
        "production_closed": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--judge", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = project(
            candidate_root=Path(args.candidate),
            judge_root=Path(args.judge),
            output_path=Path(args.output),
        )
    except (OSError, ValueError, json.JSONDecodeError, ProjectionError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "production_closed": False}), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
