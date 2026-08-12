#!/usr/bin/env python3
"""Project the bound trusted Judge inputs into a Stage-3 validation workspace.

Stage 3 may validate a repair candidate whose source commit predates later control-plane
changes.  The product candidate must remain the exact source+repair commit, while the
Quick Judge must come from the currently bound trusted control plane.  This helper
copies only files listed by the trusted-Judge manifest into the disposable validation
workspace, records the projection, and verifies byte-for-byte equality before Quick.

The projection is validation-only.  It never commits projected files, never changes the
repair patch, and never expands publication or production authority.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from trusted_judge import MANIFEST_REL, load_manifest, sha256, verify_candidate, verify_root  # type: ignore  # noqa: E402

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


def project(*, candidate_root: Path, judge_root: Path, output_path: Path) -> dict[str, Any]:
    candidate_root = candidate_root.resolve()
    judge_root = judge_root.resolve()
    if candidate_root == judge_root or candidate_root in judge_root.parents or judge_root in candidate_root.parents:
        raise ProjectionError("candidate and trusted Judge roots must be independent workspaces")
    if not candidate_root.is_dir() or not judge_root.is_dir():
        raise ProjectionError("candidate and trusted Judge roots must exist")

    root_errors = verify_root(judge_root)
    if root_errors:
        raise ProjectionError("invalid trusted Judge root: " + "; ".join(root_errors))
    manifest = load_manifest(judge_root)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ProjectionError("trusted Judge manifest contains no files")

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
    except (OSError, ValueError, json.JSONDecodeError, ProjectionError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "production_closed": False}), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
