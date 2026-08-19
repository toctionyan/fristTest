#!/usr/bin/env python3
"""Compile one owner-authorized engineering autonomy handoff into network data.

This script is deliberately network-free. The trusted GitHub workflow validates
its own actor/ref and current PR identity, invokes this compiler, persists the
result, and only then executes the returned bounded network request.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_autonomy_handoff import compile_trusted_handoff  # type: ignore  # noqa: E402


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _github_output(path: Path | None, values: dict[str, object]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--trusted-workflow-ref", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--handoff-run-id", required=True, type=int)
    parser.add_argument("--handoff-run-attempt", required=True, type=int)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    bundle = _load(Path(args.bundle))
    pr = _load(Path(args.pr))
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    result = compile_trusted_handoff(
        bundle,
        repository=args.repository,
        actor=args.actor,
        event_name=args.event_name,
        trusted_workflow_ref=args.trusted_workflow_ref,
        authorization_id=args.authorization_id,
        handoff_run_id=args.handoff_run_id,
        handoff_run_attempt=args.handoff_run_attempt,
        observed_pr_number=int(pr.get("number") or 0),
        observed_pr_head_sha=str(head.get("sha") or ""),
        observed_pr_draft=pr.get("draft") is True,
        observed_pr_state=str(pr.get("state") or ""),
    )
    output_dir = Path(args.output_dir)
    _write(output_dir / "handoff-result.json", result)
    _write(output_dir / "owner-authorization.json", result["authorization"])
    _write(output_dir / "dispatch-plan.json", result["plan"])
    _write(output_dir / "network-request.json", result["network_request"])
    _github_output(
        Path(args.github_output) if args.github_output else None,
        {
            "network_kind": result["network_request"]["kind"],
            "source_pr_number": result["source_pr_number"],
            "source_run_id": result["source_run_id"],
            "source_run_attempt": result["source_run_attempt"],
            "source_head_sha": result["source_head_sha"],
            "authorization_sha256": result["authorization"]["authorization_sha256"],
            "plan_sha256": result["plan"]["plan_sha256"],
            "request_sha256": result["network_request"]["request_sha256"],
        },
    )
    print(
        json.dumps(
            {
                "schema": result["schema"],
                "network_kind": result["network_request"]["kind"],
                "source_run_id": result["source_run_id"],
                "source_head_sha": result["source_head_sha"],
                "production_closed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
