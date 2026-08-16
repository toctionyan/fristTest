#!/usr/bin/env python3
from __future__ import annotations

"""Close governed repair review before protected baseline acceptance.

This is a deterministic governance transition, not a repair model. It requires a
Draft publication receipt with G0-G5 PASS, binds the human/environment approval
to the exact published source commit and validated tree, and moves the TaskRun to
BASELINE_ACCEPTANCE_REQUIRED. It has no source edit, merge, deploy, or production
closure authority.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore  # type: ignore  # noqa: E402

PUBLICATION_RECEIPT_SCHEMA = "github-governed-repair-draft-publication@1"
GOVERNANCE_SCHEMA = "governed-repair-governance@1"


class GovernanceError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GovernanceError(f"JSON object required: {path}")
    return payload


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validated_prefix(gates: object) -> dict[str, Any]:
    if not isinstance(gates, dict):
        raise GovernanceError("governed repair gates are missing")
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
            raise GovernanceError(f"governance cannot close before {gate}=PASS")
    g6 = gates.get("G6_GOVERNANCE_EXACT_HEAD")
    if not isinstance(g6, dict) or g6.get("status") != "PENDING":
        raise GovernanceError("G6 must be PENDING on governance entry")
    return gates


def close_governance(
    *,
    publication_receipt_path: Path,
    task_run_path: Path,
    actor: str,
    approval_ref: str,
    output_path: Path,
) -> dict[str, Any]:
    publication = _load(publication_receipt_path)
    if publication.get("schema") != PUBLICATION_RECEIPT_SCHEMA:
        raise GovernanceError("unsupported Draft publication receipt")
    if publication.get("status") != "DRAFT_REPAIR_PR_PUBLISHED_AWAITING_GOVERNANCE":
        raise GovernanceError("Draft publication is not awaiting governance")
    if publication.get("governed_repair_state") != "GOVERNANCE_REQUIRED":
        raise GovernanceError("publication is not in GOVERNANCE_REQUIRED")
    if publication.get("draft_pr_published") is not True:
        raise GovernanceError("governance requires an existing Draft PR")
    if publication.get("production_closed") is not False:
        raise GovernanceError("publication illegally asserted production closure")
    for field in (
        "governance_closed",
        "baseline_accepted",
        "exact_head_certified",
        "ready_for_review",
    ):
        if publication.get(field) is not False:
            raise GovernanceError(f"governance entry illegally asserted {field}")
    gates = _validated_prefix(publication.get("gates"))

    actor = str(actor or "").strip()
    approval_ref = str(approval_ref or "").strip()
    if not actor or not approval_ref:
        raise GovernanceError("explicit governance actor and approval reference are required")

    source_sha = str(publication.get("published_source_sha") or "")
    tree_sha = str(publication.get("validated_tree_sha") or "")
    pr_url = str(publication.get("draft_pr_url") or "")
    if len(source_sha) != 40 or len(tree_sha) != 40:
        raise GovernanceError("published source/tree identity is incomplete")
    if not pr_url.startswith("https://github.com/") or "/pull/" not in pr_url:
        raise GovernanceError("governance requires a bound Draft PR URL")

    approved_source_paths = []
    for raw in publication.get("changed_paths") or []:
        path = str(raw or "").strip().replace("\\", "/")
        if path and path not in approved_source_paths:
            approved_source_paths.append(path)
    if not approved_source_paths:
        raise GovernanceError("governance has no validated source paths")

    closed_gates = dict(gates)
    closed_gates["G6_GOVERNANCE_EXACT_HEAD"] = {
        "status": "GOVERNANCE_CLOSED_BASELINE_PENDING",
        "evidence": [
            f"actor:{actor}",
            f"approval:{approval_ref}",
            f"published-source-sha:{source_sha}",
            f"validated-tree-sha:{tree_sha}",
        ],
    }
    receipt: dict[str, Any] = {
        "schema": GOVERNANCE_SCHEMA,
        "status": "GOVERNANCE_CLOSED",
        "governed_repair_state": "GOVERNANCE_CLOSED",
        "repository": publication.get("repository"),
        "source_run_id": publication.get("source_run_id"),
        "draft_pr_url": pr_url,
        "repair_branch": publication.get("repair_branch"),
        "repair_base_branch": publication.get("repair_base_branch"),
        "published_source_sha": source_sha,
        "validated_tree_sha": tree_sha,
        "rca_sha256": publication.get("rca_sha256"),
        "write_grant_sha256": publication.get("write_grant_sha256"),
        "violated_invariant": publication.get("violated_invariant"),
        "authority_owner": publication.get("authority_owner"),
        "required_permanent_guard": publication.get("required_permanent_guard"),
        "approved_source_paths": approved_source_paths,
        "governance_actor": actor,
        "approval_ref": approval_ref,
        "publication_receipt_sha256": _fingerprint(publication),
        "gates": closed_gates,
        "governance_closed": True,
        "baseline_accepted": False,
        "exact_head_certified": False,
        "ready_for_review": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    receipt["governance_sha256"] = _fingerprint(receipt)
    _write(output_path, receipt)

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("phase") != "STAGE4_GOVERNANCE_REQUIRED":
        raise GovernanceError("TaskRun is not awaiting governance")
    task.mark_condition(
        "governance_closed",
        evidence_refs=[
            str(output_path),
            f"governance-sha256:{receipt['governance_sha256']}",
            f"approval:{approval_ref}",
        ],
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE5_BASELINE_ACCEPTANCE_REQUIRED",
        workspace_fingerprint=tree_sha,
        evidence_refs=[str(output_path), pr_url],
        metadata={
            "governed_repair_state": "GOVERNANCE_CLOSED",
            "governance_sha256": receipt["governance_sha256"],
            "published_source_sha": source_sha,
            "gates": closed_gates,
            "governance_closed": True,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "production_closed": False,
        },
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication-receipt", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        close_governance(
            publication_receipt_path=Path(args.publication_receipt),
            task_run_path=Path(args.task_run),
            actor=args.actor,
            approval_ref=args.approval_ref,
            output_path=Path(args.output),
        )
    except (OSError, json.JSONDecodeError, GovernanceError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "governance_closed": False,
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
