#!/usr/bin/env python3
"""Validate and receipt one completed engineering-autonomy-authorize handoff."""
from __future__ import annotations

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

from engineering_autonomy_dispatch import AutonomyDispatchError  # type: ignore  # noqa: E402
from engineering_autonomy_handoff import (  # type: ignore  # noqa: E402
    HANDOFF_RESULT_SCHEMA,
    validate_handoff_bundle,
)
from engineering_autonomy_network import (  # type: ignore  # noqa: E402
    build_dispatch_receipt,
    validate_dispatch_plan,
    validate_dispatch_receipt,
    validate_network_request,
)


TRUSTED_AUTHORIZE_WORKFLOW = ".github/workflows/engineering-autonomy-authorize.yml"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AutonomyDispatchError(f"JSON object required: {path}")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_result(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    if payload.get("schema") != HANDOFF_RESULT_SCHEMA:
        raise AutonomyDispatchError("invalid autonomy handoff result schema")
    expected = _text(payload.pop("result_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or _digest(payload) != expected:
        raise AutonomyDispatchError("autonomy handoff result digest mismatch")
    payload["result_sha256"] = expected
    return payload


def verify_wakeup(
    *,
    handoff_result: Mapping[str, Any],
    pr: Mapping[str, Any],
    repository: str,
    authorize_run_id: int,
    authorize_run_attempt: int,
    authorize_head_sha: str,
) -> dict[str, Any]:
    result = _validate_result(handoff_result)
    task = result.get("task") if isinstance(result.get("task"), Mapping) else None
    grant = result.get("grant") if isinstance(result.get("grant"), Mapping) else None
    outcome = result.get("reconcile_outcome") if isinstance(result.get("reconcile_outcome"), Mapping) else None
    authorization = result.get("authorization") if isinstance(result.get("authorization"), Mapping) else None
    plan = result.get("plan") if isinstance(result.get("plan"), Mapping) else None
    request = result.get("network_request") if isinstance(result.get("network_request"), Mapping) else None
    if not all((task, grant, outcome, authorization, plan, request)):
        raise AutonomyDispatchError("autonomy handoff result is incomplete")

    failure_signature = _text(authorization.get("failure_signature"))
    bundle = validate_handoff_bundle(
        {
            "schema": "engineering-autonomy-handoff-bundle@1",
            "task": dict(task),
            "grant": dict(grant),
            "reconcile_outcome": dict(outcome),
            "failure_signature": failure_signature,
            "source_pr_number": int(result.get("source_pr_number") or 0),
        }
    )
    trusted_ref = f"{TRUSTED_AUTHORIZE_WORKFLOW}@{_text(authorize_head_sha).lower()}"
    validated_plan = validate_dispatch_plan(
        plan,
        task=task,
        grant=grant,
        authorization_evidence=authorization,
        reconcile_outcome=outcome,
        repository=repository,
        trusted_workflow_ref=trusted_ref,
    )
    validated_request = validate_network_request(request, plan=validated_plan)
    if int(validated_request.get("handoff_run_id") or 0) != int(authorize_run_id):
        raise AutonomyDispatchError("network request is bound to a different authorize run")
    if int(validated_request.get("handoff_run_attempt") or 0) != int(authorize_run_attempt):
        raise AutonomyDispatchError("network request is bound to a different authorize run attempt")

    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), Mapping) else {}
    if int(pr.get("number") or 0) != int(bundle["source_pr_number"]):
        raise AutonomyDispatchError("current PR number differs from autonomy handoff")
    if _text(pr.get("state")).lower() != "open" or pr.get("draft") is not True:
        raise AutonomyDispatchError("source PR must remain open and Draft at network wakeup")
    if _text(head.get("sha")).lower() != _text(bundle["source_head_sha"]):
        raise AutonomyDispatchError("source PR head drifted after owner authorization")
    if _text(head_repo.get("full_name")) != _text(repository):
        raise AutonomyDispatchError("source PR head repository differs from authorized repository")

    inputs = validated_request.get("inputs") if isinstance(validated_request.get("inputs"), Mapping) else {}
    result_payload = {
        "network_kind": _text(validated_request.get("kind")),
        "decision_id": _text(validated_plan.get("decision_id")),
        "source_pr_number": int(bundle["source_pr_number"]),
        "source_run_id": int(validated_request["source_run_id"]),
        "source_run_attempt": int(validated_request["source_run_attempt"]),
        "source_head_sha": _text(validated_request.get("source_head_sha")),
        "plan_sha256": _text(validated_plan.get("plan_sha256")),
        "request_sha256": _text(validated_request.get("request_sha256")),
        "stage2_source_run_id": _text(inputs.get("source_run_id")),
        "stage2_source_run_attempt": _text(inputs.get("source_run_attempt")),
        "stage2_autonomy_handoff_run_id": _text(inputs.get("autonomy_handoff_run_id")),
        "stage2_autonomy_handoff_run_attempt": _text(inputs.get("autonomy_handoff_run_attempt")),
        "stage2_autonomy_authorization_id": _text(inputs.get("autonomy_authorization_id")),
        "stage2_autonomy_authorization_sha256": _text(inputs.get("autonomy_authorization_sha256")),
        "stage2_autonomy_grant_id": _text(inputs.get("autonomy_grant_id")),
        "stage2_autonomy_grant_sha256": _text(inputs.get("autonomy_grant_sha256")),
        "stage2_autonomy_plan_sha256": _text(inputs.get("autonomy_plan_sha256")),
        "stage2_repair_round": _text(inputs.get("repair_round")),
        "production_closed": False,
    }
    return result_payload


def _append_github_output(path: Path | None, values: Mapping[str, object]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = "true" if value is True else "false" if value is False else str(value)
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--handoff-result", required=True)
    verify.add_argument("--pr", required=True)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--authorize-run-id", required=True, type=int)
    verify.add_argument("--authorize-run-attempt", required=True, type=int)
    verify.add_argument("--authorize-head-sha", required=True)
    verify.add_argument("--github-output")

    receipt = sub.add_parser("receipt")
    receipt.add_argument("--handoff-result", required=True)
    receipt.add_argument("--status", required=True)
    receipt.add_argument("--network-ref", default="")
    receipt.add_argument("--error-code", default="")
    receipt.add_argument("--output", required=True)

    args = parser.parse_args()
    handoff = _load(Path(args.handoff_result))
    if args.command == "verify":
        result = verify_wakeup(
            handoff_result=handoff,
            pr=_load(Path(args.pr)),
            repository=args.repository,
            authorize_run_id=args.authorize_run_id,
            authorize_run_attempt=args.authorize_run_attempt,
            authorize_head_sha=args.authorize_head_sha,
        )
        _append_github_output(
            Path(args.github_output) if args.github_output else None,
            result,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    validated = _validate_result(handoff)
    plan = validated.get("plan") if isinstance(validated.get("plan"), Mapping) else {}
    request = (
        validated.get("network_request")
        if isinstance(validated.get("network_request"), Mapping)
        else {}
    )
    built = build_dispatch_receipt(
        plan=plan,
        network_request=request,
        status=args.status,
        network_ref=args.network_ref,
        error_code=args.error_code,
    )
    validate_dispatch_receipt(built, plan=plan, network_request=request)
    _write(Path(args.output), built)
    print(json.dumps(built, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
