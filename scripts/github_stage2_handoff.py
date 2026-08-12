#!/usr/bin/env python3
"""Bind a successful Stage-2 patch to immutable metadata required by Stage 3."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SOURCE_AUTHORITY_SCHEMA = "github-stage2-source-failure-authority@1"


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


def _source_failure_authority(failure: dict[str, Any]) -> dict[str, Any]:
    candidate_paths = failure.get("candidate_paths")
    if not isinstance(candidate_paths, list) or not candidate_paths:
        raise HandoffError("Stage-1 source authority is missing candidate_paths")
    normalized_paths: list[str] = []
    for raw in candidate_paths:
        path = str(raw or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise HandoffError(f"invalid Stage-1 source authority path: {raw!r}")
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
        "failure_signature": str(failure.get("failure_signature") or ""),
        "classification": str(failure.get("classification") or ""),
        "repair_allowed": failure.get("repair_allowed") is True,
        "same_repository": failure.get("same_repository") is True,
        "candidate_paths": normalized_paths,
        "repair_branch": _sanitize_branch(str(failure.get("repair_branch") or "")),
        "repair_base_branch": _sanitize_branch(str(failure.get("repair_base_branch") or "")),
    }
    if authority["schema"] != "github-failure-ingest@1" or authority["status"] != "INGESTED":
        raise HandoffError("invalid Stage-1 source authority contract")
    if not authority["repository"] or not authority["workflow_run_id"]:
        raise HandoffError("Stage-1 source authority binding is incomplete")
    if not re.fullmatch(r"[0-9a-f]{40}", authority["head_sha"]):
        raise HandoffError("Stage-1 source authority head SHA is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", authority["failure_signature"]):
        raise HandoffError("Stage-1 source authority failure signature is invalid")
    if authority["classification"] != "code_or_contract":
        raise HandoffError("Stage-1 source authority classification is not repairable")
    if not authority["repair_allowed"] or not authority["same_repository"]:
        raise HandoffError("Stage-1 source authority did not authorize same-repository repair")
    if not authority["repair_branch"].startswith("governed-repair/"):
        raise HandoffError("Stage-1 source authority repair branch is invalid")
    if not authority["repair_base_branch"] or authority["repair_base_branch"].startswith("governed-repair/"):
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


def bind_handoff(*, failure_path: Path, result_path: Path, patch_path: Path) -> dict[str, Any]:
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
    if not patch_path.is_file() or patch_path.is_symlink():
        raise HandoffError("repair patch must be a regular file")
    data = patch_path.read_bytes()
    if not data or len(data) > 2_000_000 or b"\x00" in data:
        raise HandoffError("repair patch is empty, oversized, or binary")
    repair_branch = _sanitize_branch(str(failure.get("repair_branch") or ""))
    repair_base = _sanitize_branch(str(failure.get("repair_base_branch") or ""))
    if not repair_branch.startswith("governed-repair/"):
        raise HandoffError("repair branch is outside governed-repair namespace")
    if not repair_base or repair_base.startswith("governed-repair/") or repair_base == repair_branch:
        raise HandoffError("invalid repair base branch")
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
            "production_closed": False,
        }
    )
    if not bound["repository"]:
        raise HandoffError("repository binding is missing")
    result_path.write_text(
        json.dumps(bound, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bound


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--patch", required=True)
    args = parser.parse_args()
    try:
        bind_handoff(
            failure_path=Path(args.failure_case),
            result_path=Path(args.result),
            patch_path=Path(args.patch),
        )
    except (OSError, json.JSONDecodeError, HandoffError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())