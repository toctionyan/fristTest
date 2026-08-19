#!/usr/bin/env python3
from __future__ import annotations

"""Verify that a prior owner-issued MergeGrant may replace a redundant solo G6 click.

This verifier does not close governance, accept a baseline, mark Ready, merge, deploy,
or reach production.  It only proves that the same immutable TaskRun was already
explicitly preauthorized by the repository owner through engineering-autonomy-authorize.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_merge_grant import (  # type: ignore  # noqa: E402
    EngineeringMergeGrantError,
    validate_merge_grant_for_task,
)

SCHEMA = "engineering-solo-governance-preauthorization@1"


class SoloGovernancePreauthorizationError(RuntimeError):
    pass


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SoloGovernancePreauthorizationError(f"JSON object required: {path}")
    return payload


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def verify_preauthorization(
    *,
    task: Mapping[str, Any],
    grant: Mapping[str, Any],
    authorize_run: Mapping[str, Any],
    repository: str,
    repository_owner: str,
    expected_run_id: int,
    expected_run_attempt: int,
) -> dict[str, Any]:
    repo = _text(repository)
    owner = _text(repository_owner)
    if not repo or repo.split("/", 1)[0] != owner:
        raise SoloGovernancePreauthorizationError("repository owner binding is invalid")
    try:
        validated = validate_merge_grant_for_task(grant, task=task)
    except EngineeringMergeGrantError as exc:
        raise SoloGovernancePreauthorizationError(str(exc)) from exc
    if _text(validated.get("repository")) != repo:
        raise SoloGovernancePreauthorizationError("merge grant repository mismatch")
    if _text(validated.get("issued_by")) != owner:
        raise SoloGovernancePreauthorizationError("merge grant was not issued by repository owner")
    if validated.get("single_use") is not True:
        raise SoloGovernancePreauthorizationError("merge grant is not single-use")

    try:
        run_id = int(authorize_run.get("id") or 0)
        run_attempt = int(authorize_run.get("run_attempt") or 0)
    except (TypeError, ValueError) as exc:
        raise SoloGovernancePreauthorizationError("authorization run identity is invalid") from exc
    if run_id != int(expected_run_id) or run_attempt != int(expected_run_attempt):
        raise SoloGovernancePreauthorizationError("authorization run identity mismatch")
    if _text(authorize_run.get("name")) != "engineering-autonomy-authorize":
        raise SoloGovernancePreauthorizationError("unexpected authorization workflow")
    if _text(authorize_run.get("event")) != "workflow_dispatch":
        raise SoloGovernancePreauthorizationError("authorization was not owner-dispatched")
    if _text(authorize_run.get("status")) != "completed" or _text(authorize_run.get("conclusion")) != "success":
        raise SoloGovernancePreauthorizationError("authorization workflow is not successful terminal evidence")
    actor = authorize_run.get("actor") if isinstance(authorize_run.get("actor"), Mapping) else {}
    if _text(actor.get("login")) != owner:
        raise SoloGovernancePreauthorizationError("authorization workflow actor is not repository owner")

    expected_ref = f"engineering-autonomy-authorize:{run_id}/{run_attempt}"
    if _text(validated.get("owner_authorization_ref")) != expected_ref:
        raise SoloGovernancePreauthorizationError("merge grant owner authorization reference mismatch")

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "AUTHORIZED",
        "authorization_mode": "task_bound_owner_preauthorization",
        "repository": repo,
        "repository_owner": owner,
        "task_id": validated["task_id"],
        "task_binding_fingerprint": validated["task_binding_fingerprint"],
        "merge_grant_id": validated["grant_id"],
        "merge_grant_sha256": validated["grant_sha256"],
        "authorize_run_id": run_id,
        "authorize_run_attempt": run_attempt,
        "governance_actor": owner,
        "governance_close_allowed": True,
        "independent_human_review": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    result["preauthorization_sha256"] = _digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--grant", required=True)
    parser.add_argument("--authorize-run", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-owner", required=True)
    parser.add_argument("--expected-run-id", required=True, type=int)
    parser.add_argument("--expected-run-attempt", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        result = verify_preauthorization(
            task=_load(args.task),
            grant=_load(args.grant),
            authorize_run=_load(args.authorize_run),
            repository=args.repository,
            repository_owner=args.repository_owner,
            expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
        )
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as handle:
                handle.write("authorized=true\n")
                handle.write(f"governance_actor={result['governance_actor']}\n")
                handle.write(f"preauthorization_sha256={result['preauthorization_sha256']}\n")
        return 0
    except (OSError, json.JSONDecodeError, ValueError, SoloGovernancePreauthorizationError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "governance_close_allowed": False, "merge_allowed": False, "deploy_allowed": False, "production_closed": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
