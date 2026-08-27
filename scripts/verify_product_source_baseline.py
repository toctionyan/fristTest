#!/usr/bin/env python3
from __future__ import annotations

"""Strictly verify the v3 Git-object product-source baseline."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from product_source_baseline_policy import (  # type: ignore  # noqa: E402
    BASELINE_PATH,
    BaselineMode,
    ProductSourcePolicyError,
    build_canonical_product_snapshot,
    load_baseline_document,
)

MACHINE_FAILURE_SCHEMA = "machine-failure-envelope@1"


class BaselineVerificationError(RuntimeError):
    pass


def _git_revision(workspace: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    value = completed.stdout.strip().lower()
    if completed.returncode != 0 or len(value) != 40:
        raise BaselineVerificationError(
            (completed.stderr or completed.stdout or "git HEAD lookup failed").strip()
        )
    return value


def _drift_paths(
    expected: dict[str, dict[str, str]],
    current: dict[str, dict[str, str]],
) -> list[str]:
    return sorted(
        path
        for path in set(expected) | set(current)
        if expected.get(path) != current.get(path)
    )


def verify(
    workspace: Path,
    *,
    mode: BaselineMode = BaselineMode.ACCEPTED_REF,
) -> dict[str, Any]:
    """Verify registry syntax, source witness, and the exact current commit tree."""

    workspace = workspace.resolve()
    try:
        document = load_baseline_document(workspace)
        source_sha = document.product_source_ref.removeprefix("git-commit-sha1:")
        source_snapshot = build_canonical_product_snapshot(
            workspace,
            source_sha,
            document.protected_roots,
        )
        if source_snapshot != document.payload:
            raise BaselineVerificationError("baseline_source_witness_mismatch")

        current_sha = _git_revision(workspace)
        current_snapshot = build_canonical_product_snapshot(
            workspace,
            current_sha,
            document.protected_roots,
        )
    except (OSError, subprocess.SubprocessError, ProductSourcePolicyError) as exc:
        raise BaselineVerificationError(str(exc)) from exc

    drift = _drift_paths(document.entries, current_snapshot["entries"])
    errors: list[str] = []
    if mode in {BaselineMode.ACCEPTED_REF, BaselineMode.PERMIT_BOUND} and drift:
        errors.append("protected_baseline_drift")

    result: dict[str, Any] = {
        "status": "PASS" if not errors else "FAIL",
        "baseline_path": BASELINE_PATH,
        "schema_version": document.payload["schema_version"],
        "snapshot_format": document.payload["snapshot_format"],
        "product_source_ref": document.product_source_ref,
        "current_commit_sha": current_sha,
        "recorded_entry_count": len(document.entries),
        "current_entry_count": len(current_snapshot["entries"]),
        "accepted_protected_snapshot_digest": document.protected_snapshot_digest,
        "source_rebuilt_protected_snapshot_digest": source_snapshot[
            "protected_snapshot_digest"
        ],
        "current_protected_snapshot_digest": current_snapshot[
            "protected_snapshot_digest"
        ],
        "source_snapshot_match": source_snapshot == document.payload,
        "drift_paths": drift,
        "errors": errors,
        "snapshot_source": "git_object_tree",
        "production_closed": False,
    }
    if errors:
        result["machine_failure"] = {
            "schema": MACHINE_FAILURE_SCHEMA,
            "status": "FAIL",
            "failure_class": "PROTECTED_PRODUCT_SOURCE_DRIFT",
            "errors": errors,
            "production_closed": False,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = verify(Path(args.workspace))
    except (OSError, subprocess.SubprocessError, BaselineVerificationError) as exc:
        result = {
            "status": "FAIL",
            "baseline_path": BASELINE_PATH,
            "errors": [str(exc)],
            "production_closed": False,
            "machine_failure": {
                "schema": MACHINE_FAILURE_SCHEMA,
                "status": "FAIL",
                "failure_class": "BASELINE_VERIFICATION_BLOCKED",
                "errors": [str(exc)],
                "production_closed": False,
            },
        }
        exit_code = 2
    else:
        exit_code = 0 if result["status"] == "PASS" else 1
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
