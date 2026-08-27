from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from product_source_baseline_policy import (
    ProductSourcePolicyError,
    build_canonical_product_snapshot,
    load_baseline_document,
)


BASELINE_ACCEPTANCE_PROPOSAL_SCHEMA = "product-source-baseline-acceptance-proposal@1"


class BaselineAcceptanceProposalError(RuntimeError):
    """Raised when a read-only baseline acceptance proposal cannot be proven."""


def _current_git_sha(workspace: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        return None
    return value


def build_baseline_acceptance_proposal(
    workspace: Path,
    *,
    candidate_sha: str | None = None,
) -> dict[str, Any]:
    """Describe exact protected-source drift without changing acceptance authority.

    This is deliberately read-only. It tells the user/harness *what* would be
    accepted if the baseline were promoted, but cannot write the baseline, weaken
    an oracle, or treat prior merges as implicit baseline approval.
    """

    root = workspace.resolve()
    try:
        document = load_baseline_document(root)
        accepted_sha = document.product_source_ref.removeprefix("git-commit-sha1:")
        accepted = build_canonical_product_snapshot(
            root,
            accepted_sha,
            document.protected_roots,
        )
        if accepted != document.payload:
            raise ProductSourcePolicyError("baseline_source_witness_mismatch")
    except (OSError, subprocess.SubprocessError, ProductSourcePolicyError) as exc:
        raise BaselineAcceptanceProposalError(str(exc)) from exc

    resolved_sha = str(candidate_sha or "").strip().lower() or _current_git_sha(root)
    if resolved_sha is None:
        raise BaselineAcceptanceProposalError("candidate_sha could not be resolved")
    if len(resolved_sha) != 40 or any(char not in "0123456789abcdef" for char in resolved_sha):
        raise BaselineAcceptanceProposalError("candidate_sha must be an exact 40-hex commit")
    try:
        candidate = build_canonical_product_snapshot(
            root,
            resolved_sha,
            document.protected_roots,
        )
    except (OSError, subprocess.SubprocessError, ProductSourcePolicyError) as exc:
        raise BaselineAcceptanceProposalError(str(exc)) from exc

    expected = document.entries
    current = candidate["entries"]
    added = sorted(path for path in current if path not in expected)
    deleted = sorted(path for path in expected if path not in current)
    modified = sorted(
        path for path in set(current) & set(expected) if current[path] != expected[path]
    )
    unchanged_count = sum(
        1 for path in set(current) & set(expected) if current[path] == expected[path]
    )

    decision_required = bool(added or deleted or modified)
    return {
        "schema": BASELINE_ACCEPTANCE_PROPOSAL_SCHEMA,
        "status": "DECISION_REQUIRED" if decision_required else "NO_DRIFT",
        "candidate_sha": resolved_sha,
        "snapshot_source": "git_object_tree",
        "accepted_product_source_ref": document.product_source_ref,
        "accepted_protected_snapshot_digest": document.protected_snapshot_digest,
        "candidate_protected_snapshot_digest": candidate[
            "protected_snapshot_digest"
        ],
        "protected_roots": list(document.protected_roots),
        "accepted_entry_count": len(expected),
        "candidate_entry_count": len(current),
        "unchanged_entry_count": unchanged_count,
        "drift": {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "added_count": len(added),
            "modified_count": len(modified),
            "deleted_count": len(deleted),
            "total_count": len(added) + len(modified) + len(deleted),
        },
        "decision_required": decision_required,
        "human_required": decision_required,
        "baseline_write_allowed": False,
        "source_write_allowed": False,
        "test_write_allowed": False,
        "oracle_write_allowed": False,
        "scope_expansion_allowed": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "authority_effect": False,
        "production_closed": False,
    }


def render_baseline_acceptance_proposal(proposal: Mapping[str, Any]) -> str:
    drift = proposal.get("drift") if isinstance(proposal.get("drift"), Mapping) else {}
    lines = [
        f"Baseline acceptance: {proposal.get('status')}",
        f"Accepted product source: {proposal.get('accepted_product_source_ref')}",
        f"Accepted snapshot digest: {proposal.get('accepted_protected_snapshot_digest')}",
        f"Candidate snapshot digest: {proposal.get('candidate_protected_snapshot_digest')}",
        f"Candidate SHA: {proposal.get('candidate_sha') or 'unknown'}",
        (
            "Protected entries: "
            f"accepted={proposal.get('accepted_entry_count')} "
            f"candidate={proposal.get('candidate_entry_count')}"
        ),
        (
            "Drift: "
            f"added={drift.get('added_count', 0)} "
            f"modified={drift.get('modified_count', 0)} "
            f"deleted={drift.get('deleted_count', 0)}"
        ),
        f"Human decision required: {str(proposal.get('human_required') is True).lower()}",
        "Baseline write allowed by this proposal: false",
    ]
    for category in ("added", "modified", "deleted"):
        paths = drift.get(category) if isinstance(drift.get(category), list) else []
        if paths:
            lines.append(f"{category.upper()}:")
            lines.extend(f"- {path}" for path in paths)
    return "\n".join(lines)


def dump_proposal(proposal: Mapping[str, Any]) -> str:
    return json.dumps(dict(proposal), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only product-source baseline acceptance proposal."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output")
    args = parser.parse_args()

    proposal = build_baseline_acceptance_proposal(
        Path(args.workspace),
        candidate_sha=args.candidate_sha,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dump_proposal(proposal), encoding="utf-8")
    if args.summary_output:
        summary = Path(args.summary_output)
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(render_baseline_acceptance_proposal(proposal) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
