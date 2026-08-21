from __future__ import annotations

"""Normalize an observed GitHub post-merge workflow run into a Harness resume event.

The PR comment is transport/locator data only. It may identify the candidate
``workflow_run_id`` emitted by the repository-owned post-merge workflow, but it
never authorizes progress. The bridge accepts progress only after the caller has
fetched that exact GitHub Actions run and this module verifies its repository,
workflow identity, run id, and request correlation.

This module performs no polling, no dispatch, no completion decision, and no
Quality/merge/release authority. Missing child evidence remains a pending
``WAITING_FOR_EXPECTED_CHILD`` condition in the provider adapter.
"""

import re
from typing import Any, Iterable, Mapping


class GithubPostMergeDiscoveryError(RuntimeError):
    """Raised when GitHub child-run discovery evidence is ambiguous or invalid."""


PROVIDER_ID = "github.governed_validation"
WORKFLOW_NAME = "governed-ci-post-merge-validation"
DISCOVERY_EVENT = "post_merge.validation.child_discovered"

_SHA = re.compile(r"^[0-9a-f]{40}$")
_START_COMMENT = re.compile(
    r"^Post-merge validation started for exact merge `([0-9a-f]{40})` "
    r"in workflow run `([1-9][0-9]*)`\."
)
_KNOWN_RUN_STATUSES = frozenset(
    {
        "requested",
        "queued",
        "pending",
        "waiting",
        "in_progress",
        "completed",
    }
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise GithubPostMergeDiscoveryError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GithubPostMergeDiscoveryError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise GithubPostMergeDiscoveryError(f"{field} must be a positive integer")
    return parsed


def _merge_sha(value: object) -> str:
    text = _text(value)
    if not _SHA.fullmatch(text):
        raise GithubPostMergeDiscoveryError("merge_sha must be an exact lowercase 40-character SHA")
    return text


def _repository(value: object) -> str:
    text = _text(value)
    if not text or text.count("/") != 1 or any(part.strip() != part or not part for part in text.split("/")):
        raise GithubPostMergeDiscoveryError("repository must be owner/name")
    return text


def _canonical_correlation(*, source_pr_number: int, merge_sha: str) -> str:
    return f"post-merge:{source_pr_number}:{merge_sha}"


def select_expected_child_run_id(
    *,
    comments: Iterable[Mapping[str, Any]],
    merge_sha: str,
) -> int | None:
    """Return the unique child run id announced for ``merge_sha``.

    The comment is deliberately only a locator. Zero matches means discovery has
    not happened yet; multiple different run ids are ambiguous and fail closed.
    """

    expected_sha = _merge_sha(merge_sha)
    run_ids: set[int] = set()
    for comment in comments:
        body = _text(comment.get("body"))
        match = _START_COMMENT.match(body)
        if match is None or match.group(1) != expected_sha:
            continue
        run_ids.add(_positive_int(match.group(2), field="comment workflow_run_id"))

    if not run_ids:
        return None
    if len(run_ids) != 1:
        raise GithubPostMergeDiscoveryError(
            "multiple post-merge child workflow runs were announced for the same merge SHA"
        )
    return next(iter(run_ids))


def build_child_discovered_event(
    *,
    run: Mapping[str, Any],
    expected_run_id: int,
    repository: str,
    source_pr_number: int,
    merge_sha: str,
    correlation_ref: str,
    evidence_refs: Iterable[object] = (),
) -> dict[str, Any]:
    """Validate one fetched GitHub run and emit the exact Harness discovery event."""

    repo = _repository(repository)
    source_pr = _positive_int(source_pr_number, field="source_pr_number")
    exact_merge = _merge_sha(merge_sha)
    expected_id = _positive_int(expected_run_id, field="expected_run_id")
    expected_correlation = _canonical_correlation(
        source_pr_number=source_pr,
        merge_sha=exact_merge,
    )
    if _text(correlation_ref) != expected_correlation:
        raise GithubPostMergeDiscoveryError(
            "correlation_ref does not match the exact source PR and merge SHA"
        )

    actual_run_id = _positive_int(run.get("id"), field="workflow run id")
    if actual_run_id != expected_id:
        raise GithubPostMergeDiscoveryError(
            "fetched workflow run id does not match the announced expected child run id"
        )
    run_attempt = _positive_int(run.get("run_attempt"), field="workflow run attempt")
    if _text(run.get("name")) != WORKFLOW_NAME:
        raise GithubPostMergeDiscoveryError("fetched workflow run name is not the governed post-merge validator")

    run_repository = run.get("repository")
    if not isinstance(run_repository, Mapping) or _text(run_repository.get("full_name")) != repo:
        raise GithubPostMergeDiscoveryError("fetched workflow run repository does not match the requested repository")

    status = _text(run.get("status")).lower()
    if status not in _KNOWN_RUN_STATUSES:
        raise GithubPostMergeDiscoveryError(f"workflow run status is unknown: {status or 'missing'}")
    html_url = _text(run.get("html_url"))
    if not html_url:
        raise GithubPostMergeDiscoveryError("fetched workflow run requires html_url")

    refs: list[str] = []
    seen: set[str] = set()
    for value in (
        *evidence_refs,
        f"github-workflow-run:{repo}:{actual_run_id}:attempt:{run_attempt}",
        html_url,
    ):
        ref = _text(value)
        if ref and ref not in seen:
            refs.append(ref)
            seen.add(ref)

    return {
        "provider": PROVIDER_ID,
        "correlation_ref": expected_correlation,
        "event": DISCOVERY_EVENT,
        "workflow_run_id": actual_run_id,
        "workflow_run_attempt": run_attempt,
        "source_pr_number": source_pr,
        "merge_sha": exact_merge,
        "child_status": status.upper(),
        "child_conclusion": _text(run.get("conclusion")).upper() or None,
        # This conclusion is about successful *discovery*, not child completion.
        "conclusion": "success",
        "evidence_refs": refs,
        "authority_effect": False,
        "completion_authority_changed": False,
        "quality_authority_changed": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


__all__ = [
    "DISCOVERY_EVENT",
    "GithubPostMergeDiscoveryError",
    "PROVIDER_ID",
    "WORKFLOW_NAME",
    "build_child_discovered_event",
    "select_expected_child_run_id",
]
