#!/usr/bin/env python3
from __future__ import annotations

"""Accept the protected product-source baseline only after governance closes.

The baseline is version-acceptance metadata, not semantic truth. This controller
requires an immutable governance receipt with G0-G5 PASS, verifies that the only
baseline drift is exactly the already validated RCA-authorized source patch,
updates only the baseline registry, commits that registry as a child of the
published source commit, and leaves G6 pending until exact-head CI succeeds.
"""

import argparse
from datetime import datetime, timezone
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

from task_run import TaskRunStore  # type: ignore  # noqa: E402

GOVERNANCE_SCHEMA = "governed-repair-governance@1"
BASELINE_SCHEMA = "governed-baseline-acceptance@1"
BASELINE_PATH = "skill-system/registry/product-source-baseline.json"
IGNORED_PARTS = {".venv", "node_modules", "__pycache__", ".pytest_cache"}


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


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _current_protected_files(workspace: Path, baseline: dict[str, Any]) -> dict[str, str]:
    roots = baseline.get("protected_roots")
    if not isinstance(roots, list) or not roots:
        raise BaselineAcceptanceError("baseline protected_roots are missing")
    current: dict[str, str] = {}
    for raw in roots:
        name = str(raw or "").strip().replace("\\", "/")
        root = workspace / name
        if not root.is_dir():
            raise BaselineAcceptanceError(f"protected root is missing: {name}")
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            relative = path.relative_to(workspace).as_posix()
            current[relative] = _hash_file(path)
    return current


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

    baseline_path = workspace / BASELINE_PATH
    baseline = _load(baseline_path)
    files = baseline.get("files")
    if not isinstance(files, dict):
        raise BaselineAcceptanceError("baseline files map is missing")
    recorded = {str(key): str(value) for key, value in files.items()}
    current = _current_protected_files(workspace, baseline)
    observed_drift = {
        path
        for path in set(recorded) | set(current)
        if recorded.get(path) != current.get(path)
    }
    approved = {
        str(path or "").strip().replace("\\", "/")
        for path in governance.get("approved_baseline_paths") or []
        if str(path or "").strip()
    }
    if not approved:
        raise BaselineAcceptanceError("governance approved no baseline paths")
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

    baseline["files"] = dict(sorted(current.items()))
    baseline["file_count"] = len(current)
    baseline["generated_from"] = f"git:{source_sha}"
    baseline["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
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
    if _git(workspace, "rev-parse", "HEAD^") != source_sha:
        raise BaselineAcceptanceError("baseline commit parent drifted from governed source SHA")
    if _git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BaselineAcceptanceError("baseline workspace is dirty after commit")

    baseline_gates = dict(gates)
    baseline_gates["G6_GOVERNANCE_EXACT_HEAD"] = {
        "status": "BASELINE_ACCEPTED_EXACT_HEAD_PENDING",
        "evidence": [
            f"governance-sha256:{governance['governance_sha256']}",
            f"baseline-parent:{source_sha}",
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
        "baseline_commit_sha": baseline_commit,
        "validated_tree_sha": governance.get("validated_tree_sha"),
        "rca_sha256": governance.get("rca_sha256"),
        "write_grant_sha256": governance.get("write_grant_sha256"),
        "governance_sha256": governance.get("governance_sha256"),
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
