from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Protocol

from capability_registry import CapabilityBinding
from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from workflow_graph_contract import WorkflowStepSpec


class PublicationProviderError(RuntimeError):
    """Raised when a publication capability cannot be executed safely."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe(value: object, *, fallback: str) -> str:
    text = _SAFE.sub("-", _text(value)).strip("-.")
    return text or fallback


def _attempt(state: Mapping[str, Any], step: WorkflowStepSpec) -> int:
    attempts = state.get("step_attempts") if isinstance(state.get("step_attempts"), Mapping) else {}
    return int(attempts.get(step.step_id) or 0) + 1


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path.as_posix()


def _evidence(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _text(value)
        if ref and ref not in seen:
            result.append(ref)
            seen.add(ref)
    return tuple(result)


def _sha(value: object, *, field: str) -> str:
    text = _text(value)
    if not _SHA.fullmatch(text):
        raise PublicationProviderError(f"{field} must be an exact 40-character lowercase commit SHA")
    return text


def _stable(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or any(char.isspace() for char in text):
        raise PublicationProviderError(f"{field} must be a non-empty stable identifier")
    return text


def _repo_path(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or text.startswith("/"):
        raise PublicationProviderError(f"{field} must be a relative repository path")
    path = PurePosixPath(text)
    if ".." in path.parts or "." in path.parts:
        raise PublicationProviderError(f"{field} cannot contain traversal segments")
    return path.as_posix()


def _request(
    state: WorkflowRuntimeState,
    *,
    step: WorkflowStepSpec,
    capability_id: str,
) -> dict[str, Any]:
    target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
    raw_requests = target.get("publication_requests") if isinstance(target, Mapping) else None
    requests = raw_requests if isinstance(raw_requests, Mapping) else {}
    raw = requests.get(step.step_id)
    if not isinstance(raw, Mapping):
        raw = requests.get(capability_id)
    if not isinstance(raw, Mapping):
        raise PublicationProviderError(
            f"target_ref.publication_requests requires request for step={step.step_id!r} "
            f"or capability={capability_id!r}"
        )
    request = dict(raw)
    declared = _text(request.get("capability_id"))
    if declared and declared != capability_id:
        raise PublicationProviderError(
            f"publication request capability mismatch: expected={capability_id!r} actual={declared!r}"
        )
    return request


def _summary_path(workspace: Path, state: WorkflowRuntimeState, step: WorkflowStepSpec) -> Path:
    task = _safe(state.get("task_id"), fallback="task")
    step_id = _safe(step.step_id, fallback="step")
    attempt = _attempt(state, step)
    return workspace / ".quality" / "workflow-provider-runs" / task / f"{step_id}-{attempt}.publication.json"


def _blocked(
    *,
    workspace: Path,
    state: WorkflowRuntimeState,
    step: WorkflowStepSpec,
    provider_id: str,
    capability_id: str,
    error: Exception | str,
) -> StepDispatchResult:
    path = _summary_path(workspace, state, step)
    message = str(error)
    payload = {
        "schema": "workflow-publication-provider-result@1",
        "provider_id": provider_id,
        "capability_id": capability_id,
        "step_id": step.step_id,
        "attempt": _attempt(state, step),
        "status": "BLOCKED",
        "outcome": "blocked",
        "error": message,
        "authority_effect": False,
        "completion_authority_changed": False,
        "quality_authority_changed": False,
        "production_closed": False,
    }
    _write_json(path, payload)
    return StepDispatchResult(
        outcome="blocked",
        evidence_refs=(f"file:{path.relative_to(workspace).as_posix()}",),
        payload=payload,
    )


@dataclass(frozen=True)
class PublicationHostResult:
    receipt: Mapping[str, Any]
    evidence_refs: tuple[str, ...]


class LocalGitPublicationHost(Protocol):
    def create_commit(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PublicationHostResult:
        ...


class CodeReviewPublicationHost(Protocol):
    def create_pull_request(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PublicationHostResult:
        ...

    def merge_pull_request(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PublicationHostResult:
        ...


class MergeAuthorityGuard(Protocol):
    """Independent merge authority, stronger than the generic write guard."""

    def assert_merge_allowed(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
        request: Mapping[str, Any],
    ) -> None:
        ...


class LocalGitProviderAdapter:
    provider_id = "local.git"
    provider_type = "executor"
    _SUPPORTED = frozenset({"vcs.commit.create"})

    def __init__(self, *, workspace: Path, host: LocalGitPublicationHost) -> None:
        self.workspace = workspace.resolve()
        self.host = host

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        path = _summary_path(self.workspace, state, step)
        try:
            if binding.provider_id != self.provider_id or binding.provider_type != self.provider_type:
                raise PublicationProviderError("local.git adapter received another provider binding")
            if binding.capability_id not in self._SUPPORTED or not binding.mutates or binding.external_wait:
                raise PublicationProviderError("local.git adapter only implements mutating vcs.commit.create")
            request = _request(state, step=step, capability_id=binding.capability_id)
            parent_sha = _sha(request.get("expected_parent_sha"), field="expected_parent_sha")
            message = _text(request.get("message"))
            if not message:
                raise PublicationProviderError("vcs.commit.create requires a non-empty message")
            raw_paths = request.get("changed_paths")
            if not isinstance(raw_paths, list) or not raw_paths:
                raise PublicationProviderError("vcs.commit.create requires non-empty changed_paths")
            changed_paths = tuple(_repo_path(value, field="changed_path") for value in raw_paths)
            if len(set(changed_paths)) != len(changed_paths):
                raise PublicationProviderError("vcs.commit.create changed_paths must be unique")

            normalized = {
                **request,
                "capability_id": binding.capability_id,
                "expected_parent_sha": parent_sha,
                "message": message,
                "changed_paths": list(changed_paths),
            }
            host_result = self.host.create_commit(request=normalized, step=step, state=state)
            refs = _evidence(host_result.evidence_refs)
            if not refs:
                raise PublicationProviderError("commit host result requires durable evidence_refs")
            receipt = dict(host_result.receipt)
            commit_sha = _sha(receipt.get("commit_sha"), field="commit receipt commit_sha")
            observed_parent = _sha(receipt.get("parent_sha"), field="commit receipt parent_sha")
            if observed_parent != parent_sha:
                raise PublicationProviderError("commit receipt parent_sha does not match expected_parent_sha")
            observed_paths_raw = receipt.get("changed_paths")
            if not isinstance(observed_paths_raw, list):
                raise PublicationProviderError("commit receipt requires changed_paths")
            observed_paths = tuple(_repo_path(value, field="receipt changed_path") for value in observed_paths_raw)
            if observed_paths != changed_paths:
                raise PublicationProviderError("commit receipt changed_paths do not match requested exact scope")

            payload = {
                "schema": "workflow-publication-provider-result@1",
                "provider_id": self.provider_id,
                "capability_id": binding.capability_id,
                "step_id": step.step_id,
                "attempt": _attempt(state, step),
                "status": "PASS",
                "outcome": "green",
                "commit_sha": commit_sha,
                "parent_sha": observed_parent,
                "changed_paths": list(observed_paths),
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
                "production_closed": False,
            }
            _write_json(path, payload)
            summary_ref = f"file:{path.relative_to(self.workspace).as_posix()}"
            return StepDispatchResult(
                outcome="green",
                evidence_refs=_evidence((*refs, summary_ref)),
                payload=payload,
            )
        except Exception as exc:
            return _blocked(
                workspace=self.workspace,
                state=state,
                step=step,
                provider_id=self.provider_id,
                capability_id=binding.capability_id,
                error=exc,
            )


class CodeReviewProviderAdapter:
    provider_type = "integration"
    _SUPPORTED = frozenset({"code_review.pull_request.create", "code_review.pull_request.merge"})
    _MERGE_METHODS = frozenset({"merge", "squash", "rebase"})

    def __init__(
        self,
        *,
        workspace: Path,
        provider_id: str,
        host: CodeReviewPublicationHost,
        merge_authority_guard: MergeAuthorityGuard | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.provider_id = _stable(provider_id, field="provider_id")
        self.host = host
        self.merge_authority_guard = merge_authority_guard

    def _create_pr(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
        request: Mapping[str, Any],
    ) -> PublicationHostResult:
        base_branch = _stable(request.get("base_branch"), field="base_branch")
        head_branch = _stable(request.get("head_branch"), field="head_branch")
        head_sha = _sha(request.get("head_sha"), field="head_sha")
        title = _text(request.get("title"))
        body = _text(request.get("body"))
        draft = request.get("draft")
        if not title:
            raise PublicationProviderError("pull request create requires title")
        if not isinstance(draft, bool):
            raise PublicationProviderError("pull request create requires boolean draft")
        normalized = {
            **request,
            "capability_id": binding.capability_id,
            "base_branch": base_branch,
            "head_branch": head_branch,
            "head_sha": head_sha,
            "title": title,
            "body": body,
            "draft": draft,
        }
        result = self.host.create_pull_request(request=normalized, step=step, state=state)
        receipt = dict(result.receipt)
        pr_number = receipt.get("pull_request_number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise PublicationProviderError("pull request receipt requires positive pull_request_number")
        if _stable(receipt.get("base_branch"), field="receipt base_branch") != base_branch:
            raise PublicationProviderError("pull request receipt base_branch mismatch")
        if _stable(receipt.get("head_branch"), field="receipt head_branch") != head_branch:
            raise PublicationProviderError("pull request receipt head_branch mismatch")
        if _sha(receipt.get("head_sha"), field="receipt head_sha") != head_sha:
            raise PublicationProviderError("pull request receipt head_sha mismatch")
        if receipt.get("draft") is not draft:
            raise PublicationProviderError("pull request receipt draft state mismatch")
        if not _text(receipt.get("pull_request_url")):
            raise PublicationProviderError("pull request receipt requires pull_request_url")
        return result

    def _merge_pr(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
        request: Mapping[str, Any],
    ) -> PublicationHostResult:
        pr_number = request.get("pull_request_number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise PublicationProviderError("pull request merge requires positive pull_request_number")
        head_sha = _sha(request.get("head_sha"), field="head_sha")
        merge_method = _text(request.get("merge_method"))
        if merge_method not in self._MERGE_METHODS:
            raise PublicationProviderError(f"unsupported merge_method: {merge_method!r}")
        merge_grant_ref = _text(request.get("merge_grant_ref"))
        if not merge_grant_ref:
            raise PublicationProviderError("pull request merge requires merge_grant_ref")
        normalized = {
            **request,
            "capability_id": binding.capability_id,
            "pull_request_number": pr_number,
            "head_sha": head_sha,
            "merge_method": merge_method,
            "merge_grant_ref": merge_grant_ref,
        }
        if self.merge_authority_guard is None:
            raise PublicationProviderError(
                "code_review.pull_request.merge requires independent MergeAuthorityGuard"
            )
        self.merge_authority_guard.assert_merge_allowed(
            binding=binding,
            step=step,
            state=state,
            request=normalized,
        )
        result = self.host.merge_pull_request(request=normalized, step=step, state=state)
        receipt = dict(result.receipt)
        if receipt.get("pull_request_number") != pr_number:
            raise PublicationProviderError("merge receipt pull_request_number mismatch")
        if _sha(receipt.get("head_sha"), field="merge receipt head_sha") != head_sha:
            raise PublicationProviderError("merge receipt head_sha mismatch")
        _sha(receipt.get("merge_commit_sha"), field="merge receipt merge_commit_sha")
        if _text(receipt.get("merge_grant_ref")) != merge_grant_ref:
            raise PublicationProviderError("merge receipt merge_grant_ref mismatch")
        return result

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        path = _summary_path(self.workspace, state, step)
        try:
            if binding.provider_id != self.provider_id or binding.provider_type != self.provider_type:
                raise PublicationProviderError("code-review adapter received another provider binding")
            if binding.capability_id not in self._SUPPORTED or not binding.mutates or binding.external_wait:
                raise PublicationProviderError("code-review adapter received unsupported capability contract")
            request = _request(state, step=step, capability_id=binding.capability_id)
            if binding.capability_id == "code_review.pull_request.create":
                result = self._create_pr(binding=binding, step=step, state=state, request=request)
            else:
                result = self._merge_pr(binding=binding, step=step, state=state, request=request)

            refs = _evidence(result.evidence_refs)
            if not refs:
                raise PublicationProviderError("code-review host result requires durable evidence_refs")
            receipt = dict(result.receipt)
            payload = {
                "schema": "workflow-publication-provider-result@1",
                "provider_id": self.provider_id,
                "capability_id": binding.capability_id,
                "step_id": step.step_id,
                "attempt": _attempt(state, step),
                "status": "PASS",
                "outcome": "green",
                "receipt": receipt,
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
                "production_closed": False,
            }
            _write_json(path, payload)
            summary_ref = f"file:{path.relative_to(self.workspace).as_posix()}"
            return StepDispatchResult(
                outcome="green",
                evidence_refs=_evidence((*refs, summary_ref)),
                payload=payload,
            )
        except Exception as exc:
            return _blocked(
                workspace=self.workspace,
                state=state,
                step=step,
                provider_id=self.provider_id,
                capability_id=binding.capability_id,
                error=exc,
            )


__all__ = [
    "CodeReviewProviderAdapter",
    "CodeReviewPublicationHost",
    "LocalGitProviderAdapter",
    "LocalGitPublicationHost",
    "MergeAuthorityGuard",
    "PublicationHostResult",
    "PublicationProviderError",
]
