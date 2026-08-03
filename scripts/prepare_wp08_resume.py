#!/usr/bin/env python3
"""Validate and restore a WP-08 checkpoint downloaded from a prior GitHub run.

The downloaded artifact is untrusted input.  Resume is allowed only when the
artifact belongs to the same repository, commit, source identity, production
workspace identity and known batch contract.  Paths are rebased after copying
so a resumed state never points back to the previous runner's temporary files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_toolchain_contract import (  # noqa: E402
    ReleaseToolchainError,
    validate_runtime_evidence,
)
from run_wp08_certification import (  # noqa: E402
    CONTRACT as STATE_CONTRACT,
    PASS,
    VALID_STATUSES,
    _production_workspace_fingerprint,
    _source_fingerprint,
)

CONTRACT = "wp08-cross-run-resume@1"
BATCH_CONFIG = "deployment/ci/wp08-certification-batches.json"


class ResumeInputError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeInputError("resume_json_invalid", f"invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ResumeInputError("resume_json_invalid", f"JSON object required: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _require_decimal(value: str, *, field: str, minimum: int = 1) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or int(text) < minimum:
        raise ResumeInputError("resume_identity_invalid", f"{field} must be an integer >= {minimum}")
    return text


def _require_sha(value: str, *, field: str) -> str:
    text = str(value or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40,64}", text):
        raise ResumeInputError("resume_identity_invalid", f"{field} must be a 40-64 character hexadecimal SHA")
    return text


def _assert_safe_tree(root: Path) -> None:
    if not root.is_dir():
        raise ResumeInputError("resume_artifact_missing", f"resume artifact directory does not exist: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ResumeInputError("resume_symlink_forbidden", f"resume artifact contains a symlink: {path}")
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ResumeInputError("resume_path_escape", f"resume artifact escapes its root: {path}") from exc


def _known_batches(config_path: Path) -> set[str]:
    payload = _load_json(config_path)
    if payload.get("contract") != "wp08-certification-batches@1":
        raise ResumeInputError("resume_batch_contract_invalid", "current WP-08 batch contract is invalid")
    rows = payload.get("batches")
    if not isinstance(rows, list) or not rows:
        raise ResumeInputError("resume_batch_contract_invalid", "current WP-08 batch set is empty")
    result = {str(row.get("id") or "").strip() for row in rows if isinstance(row, dict)}
    if not result or "" in result:
        raise ResumeInputError("resume_batch_contract_invalid", "current WP-08 batch IDs are invalid")
    return result


def _validate_run_identity(
    toolchain: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_run_attempt: str,
    expected_repository: str,
    expected_commit_sha: str,
) -> dict[str, str]:
    identity = toolchain.get("ci_run_identity")
    if not isinstance(identity, Mapping):
        raise ResumeInputError("resume_toolchain_identity_missing", "previous toolchain evidence has no CI run identity")
    actual = {
        "run_id": str(identity.get("run_id") or "").strip(),
        "run_attempt": str(identity.get("run_attempt") or "").strip(),
        "repository": str(identity.get("repository") or "").strip(),
        "commit_sha": str(identity.get("commit_sha") or "").strip().casefold(),
    }
    expected = {
        "run_id": expected_run_id,
        "run_attempt": expected_run_attempt,
        "repository": expected_repository,
        "commit_sha": expected_commit_sha,
    }
    mismatches = [field for field in expected if actual[field].casefold() != expected[field].casefold()]
    if mismatches:
        raise ResumeInputError(
            "resume_run_identity_mismatch",
            "previous artifact run identity mismatch: " + ", ".join(mismatches),
        )
    return actual


def prepare_resume(
    *,
    workspace: Path,
    artifact_root: Path,
    target_evidence_dir: Path,
    target_state_file: Path,
    expected_run_id: str,
    expected_run_attempt: str,
    expected_repository: str,
    expected_commit_sha: str,
    output: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    artifact_root = artifact_root.resolve()
    target_evidence_dir = target_evidence_dir.resolve()
    target_state_file = target_state_file.resolve()
    output = output.resolve()

    run_id = _require_decimal(expected_run_id, field="expected_run_id")
    run_attempt = _require_decimal(expected_run_attempt, field="expected_run_attempt")
    commit_sha = _require_sha(expected_commit_sha, field="expected_commit_sha")
    repository = str(expected_repository or "").strip()
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise ResumeInputError("resume_identity_invalid", "expected_repository must be owner/name")

    _assert_safe_tree(artifact_root)
    source_state = artifact_root / "wp08-certification-state" / "wp08-state.json"
    source_evidence = artifact_root / "wp08-certification-evidence"
    source_toolchain = artifact_root / "wp08-toolchain.json"
    if not source_state.is_file() or not source_evidence.is_dir() or not source_toolchain.is_file():
        raise ResumeInputError(
            "resume_artifact_incomplete",
            "resume artifact must contain wp08 state, evidence and toolchain identity",
        )

    state = _load_json(source_state)
    try:
        toolchain = validate_runtime_evidence(
            workspace,
            source_toolchain,
            validate_live_runtime=False,
        )
    except ReleaseToolchainError as exc:
        raise ResumeInputError("resume_toolchain_contract_invalid", str(exc)) from exc
    if state.get("contract") != STATE_CONTRACT:
        raise ResumeInputError("resume_state_contract_invalid", "previous state contract is invalid")
    if state.get("production_closed") is not False:
        raise ResumeInputError("resume_production_claim_forbidden", "diagnostic resume artifact cannot close production")
    state_status = str(state.get("status") or "").strip().upper()
    if state_status not in VALID_STATUSES:
        raise ResumeInputError("resume_state_status_invalid", f"previous state status is invalid: {state_status!r}")

    run_identity = _validate_run_identity(
        toolchain,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_repository=repository,
        expected_commit_sha=commit_sha,
    )
    config_path = workspace / BATCH_CONFIG
    current_source_fingerprint = _source_fingerprint(workspace, config_path)
    current_production_fingerprint = _production_workspace_fingerprint(workspace)
    if str(state.get("source_fingerprint_sha256") or "").casefold() != current_source_fingerprint:
        raise ResumeInputError("resume_source_identity_mismatch", "previous state belongs to another source identity")
    if str(state.get("production_workspace_fingerprint_sha256") or "").casefold() != current_production_fingerprint:
        raise ResumeInputError("resume_workspace_identity_mismatch", "previous state belongs to another production workspace")

    known_batches = _known_batches(config_path)
    state_batches = state.get("batches")
    if not isinstance(state_batches, dict) or not state_batches:
        raise ResumeInputError("resume_batch_state_missing", "previous state contains no batch results")
    unknown = sorted(set(str(key) for key in state_batches).difference(known_batches))
    if unknown:
        raise ResumeInputError("resume_unknown_batch", "previous state contains unknown batches: " + ", ".join(unknown))

    if target_evidence_dir.exists():
        shutil.rmtree(target_evidence_dir)
    target_evidence_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_evidence, target_evidence_dir)

    rebased_batches: dict[str, Any] = {}
    passed_batches: list[str] = []
    retry_batches: list[str] = []
    for batch_id, raw in state_batches.items():
        if not isinstance(raw, dict):
            raise ResumeInputError("resume_batch_state_invalid", f"batch state is invalid: {batch_id}")
        status = str(raw.get("status") or "").strip().upper()
        if status not in VALID_STATUSES:
            raise ResumeInputError("resume_batch_state_invalid", f"batch status is invalid: {batch_id}")
        result_path = target_evidence_dir / str(batch_id) / "result.json"
        stdout_path = target_evidence_dir / str(batch_id) / "stdout.log"
        stderr_path = target_evidence_dir / str(batch_id) / "stderr.log"
        if not result_path.is_file() or not stdout_path.is_file() or not stderr_path.is_file():
            raise ResumeInputError("resume_batch_evidence_missing", f"batch evidence is incomplete: {batch_id}")
        result = _load_json(result_path)
        if str(result.get("id") or "") != str(batch_id) or str(result.get("status") or "").upper() != status:
            raise ResumeInputError("resume_batch_evidence_mismatch", f"batch evidence does not match state: {batch_id}")
        rebased = dict(raw)
        rebased["stdout_log"] = str(stdout_path)
        rebased["stderr_log"] = str(stderr_path)
        rebased_batches[str(batch_id)] = rebased
        result["stdout_log"] = str(stdout_path)
        result["stderr_log"] = str(stderr_path)
        _write_json(result_path, result)
        if status == PASS:
            passed_batches.append(str(batch_id))
        else:
            retry_batches.append(str(batch_id))

    restored = dict(state)
    restored["batches"] = rebased_batches
    restored["workspace"] = str(workspace)
    restored["config"] = str(config_path)
    restored["resume_import"] = {
        "contract": CONTRACT,
        "previous_run_id": run_id,
        "previous_run_attempt": run_attempt,
        "previous_repository": repository,
        "previous_commit_sha": commit_sha,
    }
    _write_json(target_state_file, restored)

    provenance = {
        "contract": CONTRACT,
        "status": "PASS",
        "production_closed": False,
        "previous_run_identity": run_identity,
        "source_fingerprint_sha256": current_source_fingerprint,
        "production_workspace_fingerprint_sha256": current_production_fingerprint,
        "restored_state_file": str(target_state_file),
        "restored_evidence_dir": str(target_evidence_dir),
        "passed_batches_skipped_on_resume": sorted(passed_batches),
        "batches_to_retry": sorted(retry_batches),
    }
    _write_json(output, provenance)
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and restore a prior WP-08 workflow artifact.")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--target-evidence-dir", required=True)
    parser.add_argument("--target-state-file", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-attempt", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-commit-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    try:
        result = prepare_resume(
            workspace=Path(args.workspace_root),
            artifact_root=Path(args.artifact_root),
            target_evidence_dir=Path(args.target_evidence_dir),
            target_state_file=Path(args.target_state_file),
            expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
            expected_repository=args.expected_repository,
            expected_commit_sha=args.expected_commit_sha,
            output=output,
        )
        code = 0
    except ResumeInputError as exc:
        result = {
            "contract": CONTRACT,
            "status": "FAIL",
            "code": exc.code,
            "message": str(exc),
            "production_closed": False,
        }
        _write_json(output, result)
        code = 1
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
