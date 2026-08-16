#!/usr/bin/env python3
from __future__ import annotations

"""Strictly verify the accepted protected product-source baseline."""

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
    evaluate_binding,
    load_baseline_document,
)

MACHINE_FAILURE_SCHEMA = "machine-failure-envelope@1"


class BaselineVerificationError(RuntimeError):
    pass


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


def verify(workspace: Path, *, require_parent_binding: bool) -> dict[str, Any]:
    workspace = workspace.resolve()
    try:
        document = load_baseline_document(workspace)
        binding = evaluate_binding(
            workspace,
            expected=document.files,
            protected_roots=document.protected_roots,
            mode=BaselineMode.ACCEPTED_REF,
        )
    except ProductSourcePolicyError as exc:
        raise BaselineVerificationError(str(exc)) from exc

    for error in binding.errors:
        if error.startswith("protected_root_missing:"):
            root_name = error.split(":", 1)[1]
            raise BaselineVerificationError(f"protected root is missing: {root_name}")

    errors = [
        error
        for error in binding.errors
        if not error.startswith("protected_root_missing:")
    ]
    generated_from = document.generated_from
    expected_parent: str | None = None
    if require_parent_binding:
        expected_parent = _git(workspace, "rev-parse", "HEAD^")
        if generated_from != f"git:{expected_parent}":
            errors.append("baseline_parent_binding_mismatch")

    result: dict[str, Any] = {
        "status": "PASS" if not errors else "FAIL",
        "baseline_path": BASELINE_PATH,
        "recorded_file_count": len(document.files),
        "current_file_count": len(binding.current),
        "generated_from": generated_from,
        "expected_parent_sha": expected_parent,
        "drift_paths": list(binding.drift_paths),
        "errors": errors,
        "snapshot_source": binding.source.value,
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
            "implicated_paths": list(binding.drift_paths),
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
    except (OSError, subprocess.SubprocessError, BaselineVerificationError) as exc:
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
