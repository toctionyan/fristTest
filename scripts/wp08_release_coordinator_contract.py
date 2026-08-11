#!/usr/bin/env python3
"""Static contract for the durable WP-08 release-run coordinator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

CONTRACT = "wp08-release-coordinator-static-contract@1"
BOOTSTRAP_CONTRACT = "wp08-release-bootstrap@1"
EXPECTED_MAX_ATTEMPTS = 8
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class CoordinatorContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoordinatorContractError("coordinator_json_invalid", f"invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise CoordinatorContractError("coordinator_json_invalid", f"JSON object required: {path}")
    return payload


def _required(path: Path, *, code: str) -> str:
    if not path.is_file():
        raise CoordinatorContractError(code, f"required coordinator asset is missing: {path}")
    return path.read_text(encoding="utf-8")


def validate_static(workspace_root: Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    workflow_path = root / ".github" / "workflows" / "wp08-release-coordinator.yml"
    coordinator_path = root / "scripts" / "wp08_release_coordinator.py"
    state_path = root / "scripts" / "wp08_release_state.py"
    github_path = root / "scripts" / "wp08_release_github.py"
    lock_path = root / "deployment" / "ci" / "release-toolchain-lock.json"

    workflow = _required(workflow_path, code="coordinator_workflow_missing")
    coordinator = _required(coordinator_path, code="coordinator_orchestrator_missing")
    state_source = _required(state_path, code="coordinator_state_owner_missing")
    github_source = _required(github_path, code="coordinator_github_adapter_missing")
    lock = _load(lock_path)

    required_workflow_fragments = (
        "name: wp08-release-coordinator",
        "workflow_dispatch:",
        "operation:",
        "default: authorize",
        "- reconcile",
        "cron: '2-59/5 * * * *'",
        "pull_request:",
        "workflow_run:",
        "push:",
        "wp08-full-stack-certification",
        "quality",
        "types:",
        "- closed",
        "- completed",
        "governance/release-runs/wp08-bootstrap-*.json",
        "contents: read",
        "actions: write",
        "issues: write",
        "pull-requests: read",
        "group: wp08-release-coordinator",
        "cancel-in-progress: false",
        "ref: main",
        "persist-credentials: false",
        "python-version: '3.12.13'",
        "scripts/wp08_release_coordinator.py --mode authorize",
        "scripts/wp08_release_coordinator.py --mode bootstrap",
        "scripts/wp08_release_coordinator.py --mode pull-request",
        "scripts/wp08_release_coordinator.py --mode workflow-run",
        "scripts/wp08_release_recovery.py --mode reconcile",
        "WP08_COORDINATOR_OPERATION",
        "GITHUB_TOKEN: ${{ github.token }}",
    )
    missing = [fragment for fragment in required_workflow_fragments if fragment not in workflow]
    if missing:
        raise CoordinatorContractError(
            "coordinator_workflow_contract_missing",
            "missing coordinator workflow fragments: " + ", ".join(missing),
        )

    forbidden_workflow_fragments = (
        "environment: production-certification",
        "secrets.",
        "contents: write",
        "packages: write",
        "deployments: write",
    )
    forbidden = [fragment for fragment in forbidden_workflow_fragments if fragment in workflow]
    if forbidden:
        raise CoordinatorContractError(
            "coordinator_secret_boundary_invalid",
            "coordinator must remain outside protected production secret/write boundaries: " + ", ".join(forbidden),
        )

    actions = lock.get("github_actions") if isinstance(lock.get("github_actions"), dict) else {}
    for name in ("actions/checkout", "actions/setup-python"):
        row = actions.get(name) if isinstance(actions.get(name), dict) else {}
        sha = str(row.get("sha") or "")
        if not sha or f"{name}@{sha}" not in workflow:
            raise CoordinatorContractError(
                "coordinator_action_not_locked",
                f"{name} is not pinned to the release toolchain authority",
            )

    required_state_fragments = (
        'CONTRACT = "wp08-release-run@1"',
        'BOOTSTRAP_CONTRACT = "wp08-release-bootstrap@1"',
        "DEFAULT_MAX_ATTEMPTS = 8",
        'STATUS_CERTIFYING = "CERTIFYING"',
        'STATUS_WAITING_REPAIR_CI = "WAITING_FOR_REPAIR_CI"',
        'STATUS_FAILED_NEEDS_CLASSIFICATION = "FAILED_NEEDS_CLASSIFICATION"',
        'STATUS_ATTEMPT_BUDGET_EXHAUSTED = "ATTEMPT_BUDGET_EXHAUSTED"',
        'STATUS_WP08_PASS = "WP08_PASS"',
        'RETRYABLE_WORKFLOW_CONCLUSIONS = {"cancelled", "timed_out", "stale"}',
        "WP08-Release-Run-ID",
        "WP08-Parent-Run-ID",
        "WP-08 coordinator cannot claim production_closed",
    )
    missing_state = [fragment for fragment in required_state_fragments if fragment not in state_source]
    if missing_state:
        raise CoordinatorContractError(
            "coordinator_state_contract_missing",
            "missing release state controls: " + ", ".join(missing_state),
        )

    required_adapter_fragments = (
        'WP08_WORKFLOW_FILE = "wp08-certification.yml"',
        'QUALITY_WORKFLOW_FILE = "quality.yml"',
        'actions/workflows/{WP08_WORKFLOW_FILE}/dispatches',
        'dispatch_payload: dict[str, Any] = {"ref": MAIN_BRANCH}',
        "payload=dispatch_payload",
        "multiple active WP-08 release runs are forbidden",
        "workflow dispatch succeeded but the WP-08 run ID could not be resolved",
        '"X-GitHub-Api-Version": "2026-03-10"',
    )
    missing_adapter = [fragment for fragment in required_adapter_fragments if fragment not in github_source]
    if missing_adapter:
        raise CoordinatorContractError(
            "coordinator_github_adapter_contract_missing",
            "missing GitHub adapter controls: " + ", ".join(missing_adapter),
        )

    required_orchestrator_fragments = (
        "Semantic ``failure`` never retries blindly.",
        "another WP-08 release run is already active",
        "completed WP-08 run does not match the current release candidate",
        "initial_authorization_main_quality_passed",
        "repair_main_quality_already_passed",
        "bounded_retry_after_",
        "STATUS_FAILED_NEEDS_CLASSIFICATION",
        "STATUS_WAITING_REPAIR_CI",
        "workflow run source must be event or reconcile",
        "wp08_reconciled_completed_failure",
    )
    missing_orchestrator = [
        fragment for fragment in required_orchestrator_fragments if fragment not in coordinator
    ]
    if missing_orchestrator:
        raise CoordinatorContractError(
            "coordinator_orchestrator_contract_missing",
            "missing orchestrator controls: " + ", ".join(missing_orchestrator),
        )

    combined = "\n".join((coordinator, state_source, github_source))
    forbidden_runtime_ownership = (
        "PRODUCTION_MODEL_API_KEY",
        "PRODUCTION_EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "OPENAI_API_KEY",
        "EMBEDDING_API_KEY",
    )
    owned = [fragment for fragment in forbidden_runtime_ownership if fragment in combined]
    if owned:
        raise CoordinatorContractError(
            "coordinator_runtime_configuration_boundary_invalid",
            "coordinator modules cannot own production model/runtime secrets: " + ", ".join(owned),
        )

    bootstrap_paths = sorted((root / "governance" / "release-runs").glob("wp08-bootstrap-*.json"))
    bootstraps: list[dict[str, Any]] = []
    for path in bootstrap_paths:
        payload = _load(path)
        if payload.get("contract") != BOOTSTRAP_CONTRACT:
            raise CoordinatorContractError("coordinator_bootstrap_contract_invalid", f"invalid bootstrap: {path}")
        if payload.get("production_closed") is not False:
            raise CoordinatorContractError("coordinator_bootstrap_claim_invalid", f"bootstrap claims production closure: {path}")
        if int(payload.get("max_attempts") or 0) != EXPECTED_MAX_ATTEMPTS:
            raise CoordinatorContractError("coordinator_bootstrap_budget_invalid", f"bootstrap attempt budget drift: {path}")
        if not str(payload.get("authorized_initial_wp08_run_id") or "").isdigit():
            raise CoordinatorContractError("coordinator_bootstrap_run_invalid", f"bootstrap run ID invalid: {path}")
        sha = str(payload.get("authorized_initial_sha") or "").casefold()
        if not _SHA40_RE.fullmatch(sha):
            raise CoordinatorContractError("coordinator_bootstrap_sha_invalid", f"bootstrap SHA invalid: {path}")
        bootstraps.append({
            "path": path.relative_to(root).as_posix(),
            "release_run_id": str(payload.get("release_run_id") or ""),
            "authorized_initial_wp08_run_id": int(payload["authorized_initial_wp08_run_id"]),
            "authorized_initial_sha": sha,
        })

    return {
        "contract": CONTRACT,
        "status": "PASS",
        "workflow": workflow_path.relative_to(root).as_posix(),
        "orchestrator": coordinator_path.relative_to(root).as_posix(),
        "state_owner": state_path.relative_to(root).as_posix(),
        "github_adapter": github_path.relative_to(root).as_posix(),
        "single_human_authorization": True,
        "automatic_repair_continuation": True,
        "main_quality_required_before_continuation": True,
        "bounded_retry": True,
        "max_attempts": EXPECTED_MAX_ATTEMPTS,
        "semantic_failure_auto_retry": False,
        "manual_reconcile": True,
        "reconciliation_authority": "coordinator-event-replay",
        "production_secret_boundary": "not-accessible-to-coordinator",
        "production_closed": False,
        "bootstraps": bootstraps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = validate_static(Path(args.workspace_root))
    except CoordinatorContractError as exc:
        result = {
            "contract": CONTRACT,
            "status": "FAIL",
            "reason": exc.code,
            "error": str(exc),
            "production_closed": False,
        }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
