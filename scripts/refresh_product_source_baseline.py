#!/usr/bin/env python3
"""Deterministically refresh the protected product-source baseline.

This is trusted control-plane code.  It derives the baseline only from Git-tracked
files under the baseline's declared protected roots.  It never asks the model or a
repair actor which hashes should be accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

BASELINE_RELATIVE_PATH = Path("skill-system/registry/product-source-baseline.json")


class BaselineRefreshError(RuntimeError):
    pass


def _run_git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise BaselineRefreshError(
            (completed.stderr or completed.stdout or "git command failed").strip()
        )
    return completed.stdout.strip()


def _generated_at(workspace: Path, source_sha: str) -> str:
    value = _run_git(workspace, "show", "-s", "--format=%cI", source_sha)
    if not value:
        raise BaselineRefreshError("source commit timestamp is unavailable")
    # Git emits an ISO-8601 offset.  Preserve the instant deterministically and
    # normalize UTC to the repository's existing Z form when applicable.
    return value.replace("+00:00", "Z")


def _tracked_files(workspace: Path, protected_roots: tuple[str, ...]) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", *protected_roots],
        cwd=workspace,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise BaselineRefreshError(
            completed.stderr.decode("utf-8", errors="replace").strip()
            or "git ls-files failed"
        )
    return sorted(
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    )


def refresh_product_source_baseline(
    workspace: Path,
    *,
    generated_from_sha: str,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Refresh the declared protected snapshot and return deterministic evidence."""

    workspace = workspace.resolve()
    baseline_path = workspace / BASELINE_RELATIVE_PATH
    if not baseline_path.is_file():
        if allow_missing:
            return {
                "status": "NOT_CONFIGURED",
                "changed": False,
                "path": None,
                "file_count": 0,
                "generated_from": None,
            }
        raise BaselineRefreshError(f"product source baseline is missing: {baseline_path}")

    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BaselineRefreshError("product source baseline must be a JSON object")
    protected_roots = tuple(str(item) for item in payload.get("protected_roots") or ())
    if not protected_roots:
        raise BaselineRefreshError("product source baseline has no protected_roots")
    if len(generated_from_sha) != 40 or any(ch not in "0123456789abcdef" for ch in generated_from_sha):
        raise BaselineRefreshError("generated_from_sha must be a lowercase 40-character Git SHA")

    tracked = _tracked_files(workspace, protected_roots)
    current: dict[str, str] = {}
    for relative in tracked:
        path = workspace / relative
        if not path.is_file():
            raise BaselineRefreshError(f"tracked protected file is unavailable: {relative}")
        current[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    refreshed = dict(payload)
    refreshed["generated_from"] = f"git:{generated_from_sha}"
    refreshed["generated_at"] = _generated_at(workspace, generated_from_sha)
    refreshed["file_count"] = len(current)
    refreshed["files"] = dict(sorted(current.items()))
    serialized = json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n"
    previous = baseline_path.read_text(encoding="utf-8")
    changed = serialized != previous
    if changed:
        baseline_path.write_text(serialized, encoding="utf-8")

    return {
        "status": "REFRESHED" if changed else "CURRENT",
        "changed": changed,
        "path": BASELINE_RELATIVE_PATH.as_posix(),
        "file_count": len(current),
        "generated_from": refreshed["generated_from"],
        "generated_at": refreshed["generated_at"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--generated-from-sha", required=True)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = refresh_product_source_baseline(
            Path(args.workspace),
            generated_from_sha=args.generated_from_sha,
            allow_missing=args.allow_missing,
        )
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError, BaselineRefreshError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}))
        return 2
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
