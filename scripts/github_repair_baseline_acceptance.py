#!/usr/bin/env python3
from __future__ import annotations

"""Accept the protected product-source baseline only after governance closes.

The baseline is version-acceptance metadata, not semantic truth. This controller
requires an immutable governance receipt with G0-G5 PASS, verifies that the only
baseline drift is exactly the already validated RCA-authorized source patch,
updates only the baseline registry from the governed Git source tree, and leaves
G6 pending until exact-head CI succeeds.
"""

import argparse
import hashlib
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
    ProductSourcePolicyError,
    build_canonical_product_snapshot,
    load_baseline_document,
)
from task_run import TaskRunStore  # type: ignore  # noqa: E402

GOVERNANCE_SCHEMA = "governed-repair-governance@1"
BASELINE_SCHEMA = "governed-baseline-acceptance@1"


class BaselineAcceptanceError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BaselineAcceptanceError(f"JSON object required: {path}")
    return payload


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _without(payload: dict[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(field, None)
    return result


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if completed.returncode:
        raise BaselineAcceptanceError(
            (completed.stderr or completed.stdout or "git failed").strip()
        )
    return completed.stdout.strip()


def _validate_governance(receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schema") != GOVERNANCE_SCHEMA:
        raise BaselineAcceptanceError("unsupported governance receipt")
    if receipt.get("status") != "GOVERNANCE_CLOSED":
        raise BaselineAcceptanceError("governance is not closed")
    if receipt.get("governed_repair_state") != "GOVERNANCE_CLOSED":
        raise BaselineAcceptanceError("governance state drift")
    if receipt.get("governance_closed") is not True:
        raise BaselineAcceptanceError("governance_closed is not true")
    for field in ("baseline_accepted", "exact_head_certified", "ready_for_review"):
        if receipt.get(field) is not False:
            raise BaselineAcceptanceError(f"governance prematurely asserted {field}")
    if receipt.get("merge_allowed") is not False or receipt.get("deploy_allowed") is not False:
        raise BaselineAcceptanceError("governance illegally enabled merge/deploy")
    if receipt.get("production_closed") is not False:
        raise BaselineAcceptanceError("governance illegally closed production")
    expected_digest = _fingerprint(_without(receipt, "governance_sha256"))
    if str(receipt.get("governance_sha256") or "") != expected_digest:
        raise BaselineAcceptanceError("governance receipt fingerprint mismatch")

    gates = receipt.get("gates")
    if not isinstance(gates, dict):
        raise BaselineAcceptanceError("governance gates are missing")
    for gate in (
        "G0_SCOPE_AUTHORITY",
        "G1_CONTRACT_PROJECTION",
        "G2_SEMANTIC_INVARIANT",
        "G3_MUTATION",
        "G4_FINAL_AUTHORITY",
        "G5_INTEGRATION_CERTIFICATION",
    ):
        row = gates.get(gate)
        if not isinstance(row, dict) or row.get("status") != "PASS":
            raise BaselineAcceptanceError(f"baseline acceptance blocked by {gate}")
    g6 = gates.get("G6_GOVERNANCE_EXACT_HEAD")
    if not isinstance(g6, dict) or g6.get("status") != "GOVERNANCE_CLOSED_BASELINE_PENDING":
        raise BaselineAcceptanceError("G6 is not awaiting baseline acceptance")
    return gates


def accept_baseline(
    *,
    workspace: Path,
    governance_path: Path,
    task_run_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    governance = _load(governance_path)
    gates = _validate_governance(governance)
    source_sha = str(governance.get("published_source_sha") or "")
    if len(source_sha) != 40:
        raise BaselineAcceptanceError("published source SHA is invalid")
    if _git(workspace, "rev-parse", "HEAD") != source_sha:
        raise BaselineAcceptanceError("baseline workspace is not the governed source SHA")
    if _git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BaselineAcceptanceError("baseline workspace must start clean")

    try:
        document = load_baseline_document(workspace)
        candidate_snapshot = build_canonical_product_snapshot(
            workspace,
            source_sha,
            document.protected_roots,
        )
    except ProductSourcePolicyError as exc:
        raise BaselineAcceptanceError(str(exc)) from exc

    baseline_path = workspace / BASELINE_PATH
    baseline = dict(candidate_snapshot)
    current = dict(candidate_snapshot["entries"])
    observed_drift = {
        path
        for path in set(document.entries) | set(current)
        if document.entries.get(path) != current.get(path)
    }
    approved_source = {
        str(path or "").strip().replace("\\", "/")
        for path in governance.get("approved_source_paths") or []
        if str(path or "").strip()
    }
    if not approved_source:
        raise BaselineAcceptanceError("governance approved no source paths")
    protected_roots = set(document.protected_roots)
    approved = {
        path
        for path in approved_source
        if any(path.startswith(root + "/") for root in protected_roots)
    }
    if observed_drift != approved:
        raise BaselineAcceptanceError(
            "baseline drift is not exactly the governed source delta: "
            + json.dumps(
                {
                    "observed": sorted(observed_drift),
                    "approved": sorted(approved),
                    "unexpected": sorted(observed_drift - approved),
                    "missing": sorted(approved - observed_drift),
                },
                sort_keys=True,
            )
        )

    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    changed = _git(workspace, "diff", "--name-only", "HEAD", "--")
    if changed.splitlines() != [BASELINE_PATH]:
        raise BaselineAcceptanceError(
            f"baseline acceptance changed unauthorized paths: {changed.splitlines()}"
        )
    _git(workspace, "diff", "--check")
    _git(workspace, "config", "user.name", "customer-agent-governance[bot]")
    _git(
        workspace,
        "config",
        "user.email",
        "customer-agent-governance[bot]@users.noreply.github.com",
    )
    _git(workspace, "add", BASELINE_PATH)
    _git(
        workspace,
        "commit",
        "-m",
        f"Accept protected baseline after governed repair {governance.get('source_run_id')}",
    )
    baseline_commit = _git(workspace, "rev-parse", "HEAD")
    if _git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BaselineAcceptanceError("baseline workspace is dirty after commit")

    baseline_gates = dict(gates)
    baseline_gates["G6_GOVERNANCE_EXACT_HEAD"] = {
        "status": "BASELINE_ACCEPTED_EXACT_HEAD_PENDING",
        "evidence": [
            f"governance-sha256:{governance['governance_sha256']}",
            f"baseline-source-ref:{source_sha}",
            f"baseline-snapshot-digest:{candidate_snapshot['protected_snapshot_digest']}",
            f"baseline-commit:{baseline_commit}",
            *[f"baseline-path:{path}" for path in sorted(approved)],
        ],
    }
    receipt: dict[str, Any] = {
        "schema": BASELINE_SCHEMA,
        "status": "BASELINE_ACCEPTED",
        "governed_repair_state": "BASELINE_ACCEPTED",
        "repository": governance.get("repository"),
        "source_run_id": governance.get("source_run_id"),
        "draft_pr_url": governance.get("draft_pr_url"),
        "repair_branch": governance.get("repair_branch"),
        "repair_base_branch": governance.get("repair_base_branch"),
        "published_source_sha": source_sha,
        "product_source_ref": candidate_snapshot["product_source_ref"],
        "protected_snapshot_digest": candidate_snapshot["protected_snapshot_digest"],
        "baseline_commit_sha": baseline_commit,
        "validated_tree_sha": governance.get("validated_tree_sha"),
        "rca_sha256": governance.get("rca_sha256"),
        "write_grant_sha256": governance.get("write_grant_sha256"),
        "required_guard_ids": list(governance.get("required_guard_ids") or []),
        "governance_sha256": governance.get("governance_sha256"),
        "approved_source_paths": sorted(approved_source),
        "approved_baseline_paths": sorted(approved),
        "baseline_path": BASELINE_PATH,
        "gates": baseline_gates,
        "governance_closed": True,
        "baseline_accepted": True,
        "exact_head_certified": False,
        "ready_for_review": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    receipt["baseline_acceptance_sha256"] = _fingerprint(receipt)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("phase") != "STAGE5_BASELINE_ACCEPTANCE_REQUIRED":
        raise BaselineAcceptanceError("TaskRun is not awaiting baseline acceptance")
    task.mark_condition(
        "baseline_accepted",
        evidence_refs=[
            str(output_path),
            f"baseline-commit:{baseline_commit}",
            f"baseline-acceptance-sha256:{receipt['baseline_acceptance_sha256']}",
        ],
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE6_EXACT_HEAD_CERTIFICATION_REQUIRED",
        workspace_fingerprint=baseline_commit,
        evidence_refs=[str(output_path), f"baseline-commit:{baseline_commit}"],
        metadata={
            "governed_repair_state": "BASELINE_ACCEPTED",
            "baseline_commit_sha": baseline_commit,
            "gates": baseline_gates,
            "governance_closed": True,
            "baseline_accepted": True,
            "exact_head_certified": False,
            "ready_for_review": False,
            "production_closed": False,
        },
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--governance-receipt", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        accept_baseline(
            workspace=Path(args.workspace),
            governance_path=Path(args.governance_receipt),
            task_run_path=Path(args.task_run),
            output_path=Path(args.output),
        )
    except (
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        BaselineAcceptanceError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "baseline_accepted": False,
                    "merge_allowed": False,
                    "deploy_allowed": False,
                    "production_closed": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
