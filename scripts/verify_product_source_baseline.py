#!/usr/bin/env python3
from __future__ import annotations

"""Strictly verify the protected product-source baseline against the checkout.

This verifier is read-only.  It treats the baseline as accepted-version metadata,
not semantic truth.  It fails closed when the tracked protected tree differs from
the recorded file map, the file count drifts, or the accepted source parent is not
bound by ``generated_from``.  On failure it emits a machine-readable envelope so
failure ingestion can classify protected-baseline drift without scraping prose.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

BASELINE_PATH = "skill-system/registry/product-source-baseline.json"
MACHINE_FAILURE_SCHEMA = "machine-failure-envelope@1"
IGNORED_PARTS = {".venv", "node_modules", "__pycache__", ".pytest_cache"}


class BaselineVerificationError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BaselineVerificationError(f"JSON object required: {path}")
    return payload


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        raise BaselineVerificationError(
            (completed.stderr or completed.stdout or "git failed").strip()
        )
    return completed.stdout.strip()


def _recorded_paths_under_root(recorded: dict[str, str], root_name: str) -> list[str]:
    normalized = root_name.rstrip("/")
    prefix = normalized + "/"
    return sorted(
        path
        for path in recorded
        if path == normalized or path.startswith(prefix)
    )


def _current_files(
    workspace: Path,
    roots: list[str],
    recorded: dict[str, str],
) -> dict[str, str]:
    current: dict[str, str] = {}
    for raw in roots:
        name = str(raw or "").strip().replace("\\", "/")
        if not name or name.startswith("/") or ".." in Path(name).parts:
            raise BaselineVerificationError(f"invalid protected root: {raw!r}")
        root = workspace / name
        if not root.is_dir():
            if _recorded_paths_under_root(recorded, name):
                raise BaselineVerificationError(f"protected root is missing: {name}")
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            relative = path.relative_to(workspace).as_posix()
            current[relative] = _hash_file(path)
    return current


def verify(workspace: Path, *, require_parent_binding: bool) -> dict[str, Any]:
    workspace = workspace.resolve()
    baseline = _load(workspace / BASELINE_PATH)
    if baseline.get("schema_version") != 2:
        raise BaselineVerificationError("unsupported product-source baseline schema")
    roots = baseline.get("protected_roots")
    if not isinstance(roots, list) or not roots:
        raise BaselineVerificationError("protected_roots must be a non-empty list")
    files = baseline.get("files")
    if not isinstance(files, dict):
        raise BaselineVerificationError("baseline files map is missing")

    recorded = {str(key): str(value) for key, value in files.items()}
    current = _current_files(workspace, [str(item) for item in roots], recorded)
    drift = sorted(
        path
        for path in set(recorded) | set(current)
        if recorded.get(path) != current.get(path)
    )
    errors: list[str] = []
    if int(baseline.get("file_count") or -1) != len(recorded):
        errors.append("recorded_file_count_mismatch")
    if len(current) != len(recorded):
        errors.append("current_file_count_mismatch")
    if drift:
        errors.append("protected_baseline_drift")

    generated_from = str(baseline.get("generated_from") or "")
    expected_parent: str | None = None
    if require_parent_binding:
        expected_parent = _git(workspace, "rev-parse", "HEAD^")
        if generated_from != f"git:{expected_parent}":
            errors.append("baseline_parent_binding_mismatch")

    result: dict[str, Any] = {
        "status": "PASS" if not errors else "FAIL",
        "baseline_path": BASELINE_PATH,
        "recorded_file_count": len(recorded),
        "current_file_count": len(current),
        "generated_from": generated_from,
        "expected_parent_sha": expected_parent,
        "drift_paths": drift,
        "errors": errors,
        "production_closed": False,
    }
    if errors:
        result["machine_failure"] = {
            "schema": MACHINE_FAILURE_SCHEMA,
            "gate_id": "protected-product-source-baseline",
            "status": "FAIL",
            "category": "governance",
            "owner": "skill-control-plane",
            "failure_kind": "protected_baseline_drift",
            "implicated_paths": drift,
            "detail": ";".join(errors),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--require-parent-binding", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = verify(
            Path(args.workspace),
            require_parent_binding=args.require_parent_binding,
        )
    except (OSError, json.JSONDecodeError, BaselineVerificationError) as exc:
        result = {
            "status": "FAIL",
            "baseline_path": BASELINE_PATH,
            "errors": [str(exc)],
            "machine_failure": {
                "schema": MACHINE_FAILURE_SCHEMA,
                "gate_id": "protected-product-source-baseline",
                "status": "FAIL",
                "category": "governance",
                "owner": "skill-control-plane",
                "failure_kind": "protected_baseline_drift",
                "implicated_paths": [],
                "detail": str(exc)[:2000],
            },
            "production_closed": False,
        }
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    if result.get("status") == "PASS":
        return 0
    failure = result.get("machine_failure")
    if isinstance(failure, dict):
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
