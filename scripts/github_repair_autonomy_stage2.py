#!/usr/bin/env python3
"""Verify an exact M3 autonomy handoff before Stage-2 repair can start.

Stage-1 failure evidence remains read-only classification evidence. Product repair
authority comes only from the separately persisted local-first TaskRun carried by
the trusted M3 handoff. Both ledgers must bind the exact same source run/attempt/
head/failure before the protected Stage-2 repair environment may be entered.
"""
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
    validate_dispatch_plan,
    validate_network_request,
)


STAGE1_SCHEMA = "github-failure-ingest@1"
TRUSTED_AUTHORIZE_WORKFLOW = ".github/workflows/engineering-autonomy-authorize.yml"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AutonomyDispatchError(f"JSON object required: {path}")
    return value


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_result_digest(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    if payload.get("schema") != HANDOFF_RESULT_SCHEMA:
        raise AutonomyDispatchError("invalid engineering autonomy handoff result schema")
    expected = _text(payload.pop("result_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise AutonomyDispatchError("engineering autonomy handoff result digest is missing")
    if _digest(payload) != expected:
        raise AutonomyDispatchError("engineering autonomy handoff result digest mismatch")
    payload["result_sha256"] = expected
    return payload


def verify_stage2_autonomy_handoff(
    *,
    failure: Mapping[str, Any],
    stage1_task: Mapping[str, Any],
    handoff_result: Mapping[str, Any],
    repository: str,
    source_run_id: int,
    source_run_attempt: int,
    handoff_run_id: int,
    handoff_run_attempt: int,
    handoff_head_sha: str,
    stage2_control_sha: str,
) -> dict[str, Any]:
    if failure.get("schema") != STAGE1_SCHEMA or failure.get("status") != "INGESTED":
        raise AutonomyDispatchError("invalid Stage-1 failure-case contract")
    if failure.get("repair_allowed") is not True:
        raise AutonomyDispatchError("Stage-1 evidence did not classify this failure as repair-eligible")
    if failure.get("same_repository") is not True:
        raise AutonomyDispatchError("Stage-1 evidence belongs to a different repository")
    if _text(failure.get("repository")) != _text(repository):
        raise AutonomyDispatchError("Stage-1 repository binding mismatch")
    if _text(failure.get("classification")) != "code_or_contract":
        raise AutonomyDispatchError("autonomy Stage-2 requires a code_or_contract Stage-1 classification")
    if not failure.get("candidate_paths"):
        raise AutonomyDispatchError("Stage-1 evidence has no bounded repair candidate paths")
    if _text(failure.get("head_branch")).startswith("governed-repair/"):
        raise AutonomyDispatchError("recursive Stage-2 repair of a governed-repair branch is forbidden")

    source_id = int(source_run_id)
    source_attempt = int(source_run_attempt)
    if _text(failure.get("workflow_run_id")) != str(source_id):
        raise AutonomyDispatchError("Stage-1 source run differs from autonomy request")
    if _text(failure.get("workflow_run_attempt")) != str(source_attempt):
        raise AutonomyDispatchError("Stage-1 source attempt differs from autonomy request")
    failure_head = _text(failure.get("head_sha")).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", failure_head):
        raise AutonomyDispatchError("Stage-1 head sha is malformed")
    failure_signature = _text(failure.get("failure_signature"))
    if not failure_signature:
        raise AutonomyDispatchError("Stage-1 failure signature is missing")

    authorized_control_sha = _text(handoff_head_sha).lower()
    observed_stage2_control_sha = _text(stage2_control_sha).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", authorized_control_sha):
        raise AutonomyDispatchError("owner-authorized control SHA is malformed")
    if not re.fullmatch(r"[0-9a-f]{40}", observed_stage2_control_sha):
        raise AutonomyDispatchError("Stage-2 trusted control SHA is malformed")
    if observed_stage2_control_sha != authorized_control_sha:
        raise AutonomyDispatchError(
            "Stage-2 trusted control SHA differs from the owner-authorized control SHA"
        )

    binding = stage1_task.get("binding") if isinstance(stage1_task.get("binding"), Mapping) else {}
    expected_stage1 = {
        "repository": _text(failure.get("repository")),
        "workflow_run_id": str(source_id),
        "workflow_run_attempt": str(source_attempt),
        "head_sha": failure_head,
        "failure_signature": failure_signature,
    }
    for key, value in expected_stage1.items():
        if _text(binding.get(key)) != value:
            raise AutonomyDispatchError(f"Stage-1 TaskRun binding mismatch: {key}")

    result = _validate_result_digest(handoff_result)
    task = result.get("task") if isinstance(result.get("task"), Mapping) else None
    grant = result.get("grant") if isinstance(result.get("grant"), Mapping) else None
    outcome = result.get("reconcile_outcome") if isinstance(result.get("reconcile_outcome"), Mapping) else None
    authorization = result.get("authorization") if isinstance(result.get("authorization"), Mapping) else None
    plan = result.get("plan") if isinstance(result.get("plan"), Mapping) else None
    request = result.get("network_request") if isinstance(result.get("network_request"), Mapping) else None
    if not all((task, grant, outcome, authorization, plan, request)):
        raise AutonomyDispatchError("autonomy handoff result is missing a required bound component")

    compact_bundle = {
        "schema": "engineering-autonomy-handoff-bundle@1",
        "task": dict(task),
        "grant": dict(grant),
        "reconcile_outcome": dict(outcome),
        "failure_signature": failure_signature,
        "source_pr_number": int(result.get("source_pr_number") or 0),
    }
    bundle = validate_handoff_bundle(compact_bundle)
    if int(bundle["source_run_id"]) != source_id:
        raise AutonomyDispatchError("local-first handoff source run differs from Stage-1 evidence")
    if int(bundle["source_run_attempt"]) != source_attempt:
        raise AutonomyDispatchError("local-first handoff source attempt differs from Stage-1 evidence")
    if _text(bundle["source_head_sha"]) != failure_head:
        raise AutonomyDispatchError("local-first handoff source head differs from Stage-1 evidence")
    if _text(result.get("source_head_sha")) != failure_head:
        raise AutonomyDispatchError("handoff result source head differs from Stage-1 evidence")

    trusted_ref = f"{TRUSTED_AUTHORIZE_WORKFLOW}@{authorized_control_sha}"
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
    if validated_plan.get("kind") != "REQUEST_STAGE2_REPAIR":
        raise AutonomyDispatchError("Stage-2 autonomy handoff must carry REQUEST_STAGE2_REPAIR")
    if validated_request.get("kind") != "DISPATCH_STAGE2":
        raise AutonomyDispatchError("Stage-2 autonomy handoff network request is not DISPATCH_STAGE2")
    if _text(authorization.get("failure_signature")) != failure_signature:
        raise AutonomyDispatchError("owner authorization failure signature differs from Stage-1 evidence")
    if int(validated_request.get("handoff_run_id") or 0) != int(handoff_run_id):
        raise AutonomyDispatchError("Stage-2 request handoff run id mismatch")
    if int(validated_request.get("handoff_run_attempt") or 0) != int(handoff_run_attempt):
        raise AutonomyDispatchError("Stage-2 request handoff run attempt mismatch")
    inputs = validated_request.get("inputs") if isinstance(validated_request.get("inputs"), Mapping) else {}
    exact_inputs = {
        "source_run_id": str(source_id),
        "source_run_attempt": str(source_attempt),
        "autonomy_handoff_run_id": str(handoff_run_id),
        "autonomy_handoff_run_attempt": str(handoff_run_attempt),
        "autonomy_authorization_id": _text(authorization.get("authorization_id")),
        "autonomy_authorization_sha256": _text(authorization.get("authorization_sha256")),
        "autonomy_grant_id": _text(grant.get("grant_id")),
        "autonomy_grant_sha256": _text(grant.get("grant_sha256")),
        "autonomy_plan_sha256": _text(validated_plan.get("plan_sha256")),
        "repair_round": "1",
    }
    if {str(key): str(value) for key, value in inputs.items()} != exact_inputs:
        raise AutonomyDispatchError("Stage-2 autonomy dispatch inputs differ from the bound network request")
    if any(
        result.get(field) is not False
        for field in (
            "write_authority_effect",
            "test_authority_effect",
            "merge_allowed",
            "deploy_allowed",
            "production_closed",
        )
    ):
        raise AutonomyDispatchError("autonomy handoff result crossed an authority boundary")
    return {
        "repair_allowed": True,
        "head_sha": failure_head,
        "source_run_id": source_id,
        "source_run_attempt": source_attempt,
        "failure_signature": failure_signature,
        "source_pr_number": int(bundle["source_pr_number"]),
        "plan_sha256": _text(validated_plan.get("plan_sha256")),
        "authorization_sha256": _text(authorization.get("authorization_sha256")),
        "grant_sha256": _text(grant.get("grant_sha256")),
        "handoff_run_id": int(handoff_run_id),
        "handoff_run_attempt": int(handoff_run_attempt),
        "control_sha": observed_stage2_control_sha,
        "input_kind": "autonomy_stage1",
        "repair_round": 1,
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--stage1-task-run", required=True)
    parser.add_argument("--handoff-result", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--source-run-attempt", required=True, type=int)
    parser.add_argument("--handoff-run-id", required=True, type=int)
    parser.add_argument("--handoff-run-attempt", required=True, type=int)
    parser.add_argument("--handoff-head-sha", required=True)
    parser.add_argument("--stage2-control-sha", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    result = verify_stage2_autonomy_handoff(
        failure=_load(Path(args.failure_case)),
        stage1_task=_load(Path(args.stage1_task_run)),
        handoff_result=_load(Path(args.handoff_result)),
        repository=args.repository,
        source_run_id=args.source_run_id,
        source_run_attempt=args.source_run_attempt,
        handoff_run_id=args.handoff_run_id,
        handoff_run_attempt=args.handoff_run_attempt,
        handoff_head_sha=args.handoff_head_sha,
        stage2_control_sha=args.stage2_control_sha,
    )
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            for key, value in result.items():
                if isinstance(value, bool):
                    text = "true" if value else "false"
                else:
                    text = str(value)
                handle.write(f"{key}={text}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
