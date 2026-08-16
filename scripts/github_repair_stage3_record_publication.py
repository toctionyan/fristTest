#!/usr/bin/env python3
from __future__ import annotations

"""Record Draft publication without falsely completing governed repair.

A Draft PR is only the handoff from G0-G5 into governance.  This controller
binds the validated source tree to the published source commit and moves the
TaskRun to STAGE4_GOVERNANCE_REQUIRED.  It cannot refresh the protected baseline,
mark G6 green, merge, deploy, or close production.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore  # type: ignore  # noqa: E402

STAGE3_SCHEMA = "github-governed-repair-stage3@2"
PUBLICATION_SCHEMA = "github-governed-repair-stage3-publication@2"


class PublicationReceiptError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PublicationReceiptError(f"JSON object required: {path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_gates(gates: object) -> dict[str, Any]:
    if not isinstance(gates, dict):
        raise PublicationReceiptError("governed repair gates are missing")
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
            raise PublicationReceiptError(f"pre-governance gate did not pass: {gate}")
    g6 = gates.get("G6_GOVERNANCE_EXACT_HEAD")
    if not isinstance(g6, dict) or g6.get("status") != "PENDING":
        raise PublicationReceiptError("G6 must be PENDING before governance closure")
    return gates


def record_publication(
    *,
    validation_path: Path,
    publication_path: Path,
    task_run_path: Path,
    pr_url: str,
    output_path: Path,
) -> dict[str, Any]:
    validation = _load(validation_path)
    publication = _load(publication_path)
    if validation.get("schema") != STAGE3_SCHEMA:
        raise PublicationReceiptError("unsupported Stage-3 validation schema")
    if validation.get("status") != "VALIDATED_FOR_DRAFT_PR":
        raise PublicationReceiptError("Stage-3 validation is not publishable")
    if publication.get("schema") != PUBLICATION_SCHEMA:
        raise PublicationReceiptError("unsupported Stage-3 publication schema")
    if publication.get("status") != "PUBLICATION_COMMIT_PREPARED":
        raise PublicationReceiptError("publication commit was not prepared")
    if not pr_url.startswith("https://github.com/") or "/pull/" not in pr_url:
        raise PublicationReceiptError("valid Draft PR URL required")

    gates = _validate_gates(validation.get("gates"))
    for field in (
        "rca_sha256",
        "write_grant_sha256",
        "violated_invariant",
        "authority_owner",
        "required_permanent_guard",
    ):
        if str(validation.get(field) or "") != str(publication.get(field) or ""):
            raise PublicationReceiptError(f"validation/publication mismatch: {field}")
    expected = {
        "source_run_id": validation.get("source_run_id"),
        "source_head_sha": validation.get("head_sha"),
        "validated_candidate_sha": validation.get("candidate_sha"),
        "repair_branch": validation.get("repair_branch"),
        "repair_base_branch": validation.get("repair_base_branch"),
        "changed_paths": validation.get("changed_paths"),
        "write_scope": validation.get("write_scope"),
    }
    mismatched = [key for key, value in expected.items() if publication.get(key) != value]
    if mismatched:
        raise PublicationReceiptError(
            f"validation/publication binding mismatch: {mismatched}"
        )
    if publication.get("production_closed") is not False:
        raise PublicationReceiptError("publication illegally asserted production closure")
    for field in (
        "governance_closed",
        "baseline_accepted",
        "exact_head_certified",
        "ready_for_review",
    ):
        if publication.get(field) is not False:
            raise PublicationReceiptError(f"publication illegally asserted {field}")

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("phase") != "STAGE3_DRAFT_PR_REQUIRED":
        raise PublicationReceiptError("TaskRun is not awaiting Draft PR publication")
    if task.payload.get("status") not in {"WAITING_EXTERNAL_RESULT", "VALIDATING"}:
        raise PublicationReceiptError("TaskRun status cannot enter governance handoff")

    published_sha = str(publication.get("published_candidate_sha") or "")
    validated_tree = str(publication.get("validated_tree_sha") or "")
    if len(published_sha) != 40 or len(validated_tree) != 40:
        raise PublicationReceiptError("published source/tree identity is incomplete")

    condition = task.payload.get("conditions", {}).get("draft_pr_published", {})
    if not isinstance(condition, dict) or condition.get("satisfied") is not True:
        task.mark_condition(
            "draft_pr_published",
            evidence_refs=[
                pr_url,
                f"published-source-sha:{published_sha}",
                f"validated-tree-sha:{validated_tree}",
            ],
        )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE4_GOVERNANCE_REQUIRED",
        workspace_fingerprint=str(
            validation.get("quick_workspace_snapshot_fingerprint") or validated_tree
        ),
        evidence_refs=[
            str(validation_path),
            str(publication_path),
            pr_url,
            "G6:PENDING",
        ],
        metadata={
            "governed_repair_state": "GOVERNANCE_REQUIRED",
            "published_source_sha": published_sha,
            "validated_tree_sha": validated_tree,
            "rca_sha256": validation.get("rca_sha256"),
            "write_grant_sha256": validation.get("write_grant_sha256"),
            "gates": gates,
            "governance_closed": False,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "production_closed": False,
        },
    )

    receipt = dict(validation)
    receipt.update(
        {
            "schema": "github-governed-repair-draft-publication@1",
            "status": "DRAFT_REPAIR_PR_PUBLISHED_AWAITING_GOVERNANCE",
            "governed_repair_state": "GOVERNANCE_REQUIRED",
            "published_source_sha": published_sha,
            "validated_tree_sha": validated_tree,
            "draft_pr_published": True,
            "draft_pr_url": pr_url,
            "gates": gates,
            "governance_closed": False,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
    )
    _write(output_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-result", required=True)
    parser.add_argument("--publication-commit", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--pr-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        record_publication(
            validation_path=Path(args.validation_result),
            publication_path=Path(args.publication_commit),
            task_run_path=Path(args.task_run),
            pr_url=args.pr_url,
            output_path=Path(args.output),
        )
    except (OSError, json.JSONDecodeError, PublicationReceiptError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
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
