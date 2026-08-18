#!/usr/bin/env python3
"""Bind a successful Stage-2 patch to immutable metadata required by Stage 3.

The handoff preserves the Stage-1 failure authority and additionally requires the
read-only RCA, exact write-grant digests, immutable write scope, and G0 scope
proof already recorded by Stage 2. It never broadens or creates repair authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_autonomy_continuation import (  # type: ignore  # noqa: E402
    AutonomyContinuationError,
    build_autonomy_continuation,
    validate_autonomy_continuation,
)

SOURCE_AUTHORITY_SCHEMA = "github-stage2-source-failure-authority@2"
MAX_FAILURE_SIGNATURE_BYTES = 512


class HandoffError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HandoffError(f"JSON object required: {path}")
    return payload


def _sanitize_branch(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("-./")
    return cleaned[:180]


def _normalize_path(raw: object) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in Path(value).parts:
        raise HandoffError(f"invalid source authority path: {raw!r}")
    return value


def _failure_signature(value: object) -> str:
    result = str(value or "").strip()
    if (
        not result
        or "\n" in result
        or "\r" in result
        or len(result.encode("utf-8")) > MAX_FAILURE_SIGNATURE_BYTES
    ):
        raise HandoffError("Stage-1 source authority failure signature is invalid")
    return result


def _source_failure_authority(failure: dict[str, Any]) -> dict[str, Any]:
    candidate_paths = failure.get("candidate_paths")
    if not isinstance(candidate_paths, list) or not candidate_paths:
        raise HandoffError("Stage-1 source authority is missing candidate_paths")
    normalized_paths: list[str] = []
    for raw in candidate_paths:
        path = _normalize_path(raw)
        if path not in normalized_paths:
            normalized_paths.append(path)

    authority = {
        "authority_schema": SOURCE_AUTHORITY_SCHEMA,
        "schema": str(failure.get("schema") or ""),
        "status": str(failure.get("status") or ""),
        "repository": str(failure.get("repository") or ""),
        "workflow_name": str(failure.get("workflow_name") or ""),
        "workflow_run_id": str(failure.get("workflow_run_id") or ""),
        "workflow_run_attempt": str(failure.get("workflow_run_attempt") or ""),
        "head_sha": str(failure.get("head_sha") or ""),
        "head_branch": str(failure.get("head_branch") or ""),
        "source_pr_number": int(failure.get("source_pr_number") or 0),
        "failure_signature": _failure_signature(failure.get("failure_signature")),
        "classification": str(failure.get("classification") or ""),
        "repair_allowed": failure.get("repair_allowed") is True,
        "same_repository": failure.get("same_repository") is True,
        "candidate_paths": normalized_paths,
        "repair_branch": _sanitize_branch(str(failure.get("repair_branch") or "")),
        "repair_base_branch": _sanitize_branch(
            str(failure.get("repair_base_branch") or "")
        ),
    }
    if authority["schema"] != "github-failure-ingest@1" or authority["status"] != "INGESTED":
        raise HandoffError("invalid Stage-1 source authority contract")
    if not authority["repository"] or not authority["workflow_run_id"]:
        raise HandoffError("Stage-1 source authority binding is incomplete")
    if not re.fullmatch(r"[0-9a-f]{40}", authority["head_sha"]):
        raise HandoffError("Stage-1 source authority head SHA is invalid")
    if authority["classification"] != "code_or_contract":
        raise HandoffError("Stage-1 source authority classification is not repairable")
    if not authority["repair_allowed"] or not authority["same_repository"]:
        raise HandoffError("Stage-1 source authority did not authorize same-repository repair")
    if not authority["repair_branch"].startswith("governed-repair/"):
        raise HandoffError("Stage-1 source authority repair branch is invalid")
    if (
        not authority["repair_base_branch"]
        or authority["repair_base_branch"].startswith("governed-repair/")
    ):
        raise HandoffError("Stage-1 source authority repair base is invalid")
    return authority


def _authority_digest(authority: dict[str, Any]) -> str:
    canonical = json.dumps(
        authority,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_repair_authority(result: dict[str, Any]) -> None:
    if result.get("governed_repair_state") != "INDEPENDENT_REVIEW":
        raise HandoffError("Stage-2 candidate is not awaiting independent review")
    if result.get("production_closed") is not False:
        raise HandoffError("Stage-2 candidate illegally asserted production closure")
    for field in (
        "rca_sha256",
        "write_grant_sha256",
        "violated_invariant",
        "authority_owner",
        "required_permanent_guard",
    ):
        if not str(result.get(field) or "").strip():
            raise HandoffError(f"Stage-2 repair authority is missing {field}")
    guard_ids = result.get("required_guard_ids")
    if (
        not isinstance(guard_ids, list)
        or not guard_ids
        or any(not isinstance(item, str) or not item.strip() for item in guard_ids)
        or len(set(guard_ids)) != len(guard_ids)
    ):
        raise HandoffError("Stage-2 permanent machine guard binding is missing or invalid")
    scope = result.get("write_scope")
    changed = result.get("changed_paths")
    if not isinstance(scope, list) or not scope:
        raise HandoffError("Stage-2 write_scope is missing")
    if not isinstance(changed, list) or not changed:
        raise HandoffError("Stage-2 changed_paths is missing")
    normalized_scope = [_normalize_path(item) for item in scope]
    normalized_changed = [_normalize_path(item) for item in changed]
    if len(set(normalized_scope)) != len(normalized_scope):
        raise HandoffError("Stage-2 write_scope contains duplicate paths")
    if len(set(normalized_changed)) != len(normalized_changed):
        raise HandoffError("Stage-2 changed_paths contains duplicate paths")
    escaped = [path for path in normalized_changed if path not in normalized_scope]
    if escaped:
        raise HandoffError(f"Stage-2 changes escape exact write grant: {escaped}")
    gates = result.get("gates")
    if not isinstance(gates, dict):
        raise HandoffError("Stage-2 G0 scope authority proof is missing")
    g0 = gates.get("G0_SCOPE_AUTHORITY")
    if not isinstance(g0, dict) or g0.get("status") != "PASS":
        raise HandoffError("Stage-2 G0 scope authority did not pass")


def bind_handoff(
    *,
    failure_path: Path,
    result_path: Path,
    patch_path: Path,
    autonomy_continuation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failure = _load(failure_path)
    result = _load(result_path)
    if failure.get("schema") != "github-failure-ingest@1":
        raise HandoffError("invalid Stage-1 failure schema")
    if result.get("schema") != "github-governed-repair-stage2@1":
        raise HandoffError("invalid Stage-2 result schema")
    if result.get("status") != "REPAIR_CANDIDATE_READY":
        raise HandoffError("only a successful Stage-2 candidate can be bound")
    for key in ("workflow_run_id", "head_sha", "failure_signature"):
        if str(result.get(key)) != str(failure.get(key)):
            raise HandoffError(f"Stage-1/Stage-2 binding mismatch: {key}")
    _validate_repair_authority(result)

    if not patch_path.is_file() or patch_path.is_symlink():
        raise HandoffError("repair patch must be a regular file")
    data = patch_path.read_bytes()
    if not data or len(data) > 2_000_000 or b"\x00" in data:
        raise HandoffError("repair patch is empty, oversized, or binary")

    repair_branch = _sanitize_branch(str(failure.get("repair_branch") or ""))
    repair_base = _sanitize_branch(str(failure.get("repair_base_branch") or ""))
    if not repair_branch.startswith("governed-repair/"):
        raise HandoffError("repair branch is outside governed-repair namespace")
    if (
        not repair_base
        or repair_base.startswith("governed-repair/")
        or repair_base == repair_branch
    ):
        raise HandoffError("invalid repair base branch")

    validated_continuation: dict[str, Any] | None = None
    if autonomy_continuation is not None:
        try:
            validated_continuation = validate_autonomy_continuation(
                autonomy_continuation,
                source_run_id=failure.get("workflow_run_id"),
                source_run_attempt=failure.get("workflow_run_attempt"),
                source_head_sha=failure.get("head_sha"),
                failure_signature=failure.get("failure_signature"),
            )
        except AutonomyContinuationError as exc:
            raise HandoffError(str(exc)) from exc
        try:
            repair_round = int(result.get("repair_round") or 0)
        except (TypeError, ValueError) as exc:
            raise HandoffError("Stage-2 repair round is invalid") from exc
        if repair_round < 1 or repair_round > int(validated_continuation["max_repair_rounds"]):
            raise HandoffError("Stage-2 candidate exceeds the autonomy repair budget")

    source_authority = _source_failure_authority(failure)
    bound = dict(result)
    bound.update(
        {
            "repository": str(failure.get("repository") or ""),
            "head_branch": str(failure.get("head_branch") or ""),
            "source_pr_number": int(failure.get("source_pr_number") or 0),
            "repair_branch": repair_branch,
            "repair_base_branch": repair_base,
            "patch_sha256": hashlib.sha256(data).hexdigest(),
            "source_failure_authority": source_authority,
            "source_failure_authority_sha256": _authority_digest(source_authority),
            "stage3_handoff_bound": True,
            "full_validation_passed": False,
            "draft_pr_published": False,
            "governance_closed": False,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "production_closed": False,
        }
    )
    if validated_continuation is not None:
        bound["autonomy_continuation"] = validated_continuation
    if not bound["repository"]:
        raise HandoffError("repository binding is missing")
    result_path.write_text(
        json.dumps(bound, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bound


def _continuation_from_args(args: argparse.Namespace, failure: dict[str, Any]) -> dict[str, Any] | None:
    values = {
        "grant_id": args.autonomy_grant_id,
        "grant_sha256": args.autonomy_grant_sha256,
        "authorization_id": args.autonomy_authorization_id,
        "authorization_sha256": args.autonomy_authorization_sha256,
        "continuation_sha256": args.autonomy_continuation_sha256,
        "max_repair_rounds": args.max_repair_rounds,
        "max_validation_retries": args.max_validation_retries,
    }
    present = {key: str(value or "").strip() for key, value in values.items() if str(value or "").strip()}
    if not present:
        return None
    if len(present) != len(values):
        raise HandoffError("partial autonomy continuation arguments are forbidden")
    try:
        continuation = build_autonomy_continuation(
            grant_id=values["grant_id"],
            grant_sha256=values["grant_sha256"],
            authorization_id=values["authorization_id"],
            authorization_sha256=values["authorization_sha256"],
            source_run_id=failure.get("workflow_run_id"),
            source_run_attempt=failure.get("workflow_run_attempt"),
            source_head_sha=failure.get("head_sha"),
            failure_signature=failure.get("failure_signature"),
            max_repair_rounds=values["max_repair_rounds"],
            max_validation_retries=values["max_validation_retries"],
        )
    except AutonomyContinuationError as exc:
        raise HandoffError(str(exc)) from exc
    if continuation["continuation_sha256"] != values["continuation_sha256"]:
        raise HandoffError("reconstructed autonomy continuation digest mismatch")
    return continuation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--autonomy-grant-id", default="")
    parser.add_argument("--autonomy-grant-sha256", default="")
    parser.add_argument("--autonomy-authorization-id", default="")
    parser.add_argument("--autonomy-authorization-sha256", default="")
    parser.add_argument("--autonomy-continuation-sha256", default="")
    parser.add_argument("--max-repair-rounds", default="")
    parser.add_argument("--max-validation-retries", default="")
    args = parser.parse_args()
    try:
        failure_path = Path(args.failure_case)
        continuation = _continuation_from_args(args, _load(failure_path))
        bind_handoff(
            failure_path=failure_path,
            result_path=Path(args.result),
            patch_path=Path(args.patch),
            autonomy_continuation=continuation,
        )
    except (OSError, json.JSONDecodeError, HandoffError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "production_closed": False,
                }
            ),
            file=__import__("sys").stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
