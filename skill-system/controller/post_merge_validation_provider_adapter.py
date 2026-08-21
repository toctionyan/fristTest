from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from capability_registry import CapabilityBinding
from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from workflow_graph_contract import WorkflowStepSpec
from workflow_step_values import resolve_request_from_steps


class PostMergeValidationProviderError(RuntimeError):
    """Raised when governed post-merge validation cannot be requested or consumed safely."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_REQUEST = "publication.post_merge.validation.request"
_WAIT = "publication.post_merge.validation.wait"


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe(value: object, *, fallback: str) -> str:
    text = _SAFE.sub("-", _text(value)).strip("-.")
    return text or fallback


def _attempt(state: Mapping[str, Any], step: WorkflowStepSpec) -> int:
    attempts = state.get("step_attempts") if isinstance(state.get("step_attempts"), Mapping) else {}
    return int(attempts.get(step.step_id) or 0) + 1


def _sha(value: object, *, field: str) -> str:
    text = _text(value)
    if not _SHA.fullmatch(text):
        raise PostMergeValidationProviderError(f"{field} must be an exact lowercase 40-character SHA")
    return text


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PostMergeValidationProviderError(f"{field} must be a positive integer")
    return value


def _refs(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _text(value)
        if ref and ref not in seen:
            result.append(ref)
            seen.add(ref)
    return tuple(result)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _summary_path(workspace: Path, state: WorkflowRuntimeState, step: WorkflowStepSpec) -> Path:
    task = _safe(state.get("task_id"), fallback="task")
    step_id = _safe(step.step_id, fallback="step")
    return workspace / ".quality" / "workflow-provider-runs" / task / f"{step_id}-{_attempt(state, step)}.post-merge.json"


def _request(state: WorkflowRuntimeState, step: WorkflowStepSpec, capability_id: str) -> dict[str, Any]:
    target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
    raw_requests = target.get("publication_requests") if isinstance(target, Mapping) else None
    requests = raw_requests if isinstance(raw_requests, Mapping) else {}
    raw = requests.get(step.step_id)
    if not isinstance(raw, Mapping):
        raw = requests.get(capability_id)
    if not isinstance(raw, Mapping):
        raise PostMergeValidationProviderError(
            f"target_ref.publication_requests requires request for step={step.step_id!r} or capability={capability_id!r}"
        )
    resolved = resolve_request_from_steps(state, raw)
    declared = _text(resolved.get("capability_id"))
    if declared and declared != capability_id:
        raise PostMergeValidationProviderError(
            f"post-merge request capability mismatch: expected={capability_id!r} actual={declared!r}"
        )
    return resolved


@dataclass(frozen=True)
class PostMergeValidationHostResult:
    receipt: Mapping[str, Any]
    evidence_refs: tuple[str, ...]


class PostMergeValidationHost(Protocol):
    """Thin host boundary that requests the repository-owned validation workflow.

    The host must delegate to the existing governed post-merge workflow. It is not
    allowed to reproduce Quality, project-convergence, or completion policy.
    """

    def request_validation(
        self,
        *,
        source_pr_number: int,
        merge_sha: str,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> PostMergeValidationHostResult:
        ...


class GovernedPostMergeValidationProviderAdapter:
    provider_id = "github.governed_validation"
    provider_type = "integration"
    _SUPPORTED = frozenset({_REQUEST, _WAIT})

    def __init__(self, *, workspace: Path, host: PostMergeValidationHost) -> None:
        self.workspace = workspace.resolve()
        self.host = host

    def _blocked(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
        error: Exception | str,
    ) -> StepDispatchResult:
        path = _summary_path(self.workspace, state, step)
        payload = {
            "schema": "workflow-post-merge-validation-provider-result@1",
            "provider_id": self.provider_id,
            "capability_id": binding.capability_id,
            "step_id": step.step_id,
            "attempt": _attempt(state, step),
            "status": "BLOCKED",
            "outcome": "blocked",
            "error": str(error),
            "authority_effect": False,
            "completion_authority_changed": False,
            "quality_authority_changed": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        _write_json(path, payload)
        return StepDispatchResult(
            outcome="blocked",
            evidence_refs=(f"file:{path.relative_to(self.workspace).as_posix()}",),
            payload=payload,
        )

    def _invoke_request(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        request = _request(state, step, binding.capability_id)
        source_pr = _positive_int(request.get("source_pr_number"), field="source_pr_number")
        merge_sha = _sha(request.get("merge_sha"), field="merge_sha")
        result = self.host.request_validation(
            source_pr_number=source_pr,
            merge_sha=merge_sha,
            step=step,
            state=state,
        )
        host_refs = _refs(result.evidence_refs)
        if not host_refs:
            raise PostMergeValidationProviderError("post-merge validation request requires durable evidence_refs")
        receipt = dict(result.receipt)
        if _text(receipt.get("status")) != "POST_MERGE_VALIDATION_REQUESTED":
            raise PostMergeValidationProviderError("post-merge request receipt has unexpected status")
        if _positive_int(receipt.get("source_pr_number"), field="receipt source_pr_number") != source_pr:
            raise PostMergeValidationProviderError("post-merge request receipt source_pr_number mismatch")
        if _sha(receipt.get("merge_sha"), field="receipt merge_sha") != merge_sha:
            raise PostMergeValidationProviderError("post-merge request receipt merge_sha mismatch")
        correlation_ref = _text(receipt.get("correlation_ref"))
        if not correlation_ref:
            raise PostMergeValidationProviderError("post-merge request receipt requires correlation_ref")

        path = _summary_path(self.workspace, state, step)
        payload = {
            "schema": "workflow-post-merge-validation-provider-result@1",
            "provider_id": self.provider_id,
            "capability_id": binding.capability_id,
            "step_id": step.step_id,
            "attempt": _attempt(state, step),
            "status": "POST_MERGE_VALIDATION_REQUESTED",
            "outcome": "green",
            "source_pr_number": source_pr,
            "merge_sha": merge_sha,
            "correlation_ref": correlation_ref,
            "authority_effect": False,
            "completion_authority_changed": False,
            "quality_authority_changed": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        _write_json(path, payload)
        summary_ref = f"file:{path.relative_to(self.workspace).as_posix()}"
        return StepDispatchResult(
            outcome="green",
            evidence_refs=_refs((*host_refs, summary_ref)),
            payload=payload,
        )

    def _invoke_wait(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        request = _request(state, step, binding.capability_id)
        source_pr = _positive_int(request.get("source_pr_number"), field="source_pr_number")
        merge_sha = _sha(request.get("merge_sha"), field="merge_sha")
        correlation_ref = _text(request.get("correlation_ref"))
        resume_event = _text(request.get("resume_event")) or "post_merge.validation.completed"
        if not correlation_ref:
            raise PostMergeValidationProviderError("post-merge wait requires correlation_ref")
        handle = {
            "provider": self.provider_id,
            "correlation_ref": correlation_ref,
            "resume_event": resume_event,
            "source_pr_number": source_pr,
            "merge_sha": merge_sha,
        }
        path = _summary_path(self.workspace, state, step)

        # An external event from an earlier wait may still exist in graph state while
        # this step is reached in the same invocation. Only consume an event when
        # this exact step is the durable resume stage.
        is_resume = _text(state.get("resume_stage")) == step.step_id
        event = state.get("external_event") if is_resume and isinstance(state.get("external_event"), Mapping) else None
        if event is None:
            payload = {
                "schema": "workflow-post-merge-validation-wait@1",
                "status": "WAITING_EXTERNAL",
                **handle,
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
                "merge_allowed": False,
                "deploy_allowed": False,
                "production_closed": False,
            }
            _write_json(path, payload)
            return StepDispatchResult(
                outcome="pending",
                evidence_refs=(f"file:{path.relative_to(self.workspace).as_posix()}",),
                payload=payload,
                external_wait=handle,
            )

        if _text(event.get("provider") or event.get("provider_id")) != self.provider_id:
            raise PostMergeValidationProviderError("post-merge resume provider does not match durable wait handle")
        if _text(event.get("correlation_ref")) != correlation_ref:
            raise PostMergeValidationProviderError("post-merge resume correlation_ref mismatch")
        if _text(event.get("event") or event.get("event_name")) != resume_event:
            raise PostMergeValidationProviderError("post-merge resume event name mismatch")
        event_refs = _refs(event.get("evidence_refs") if isinstance(event.get("evidence_refs"), list) else ())
        if not event_refs:
            raise PostMergeValidationProviderError("post-merge resume event requires durable evidence_refs")

        conclusion = _text(event.get("conclusion")).upper()
        if conclusion not in {"SUCCESS", "PASS", "PASSED", "GREEN"}:
            outcome = "red" if conclusion in {"FAILURE", "FAIL", "FAILED", "RED", "CANCELLED", "TIMED_OUT"} else "blocked"
            payload = {
                "schema": "workflow-post-merge-validation-event-result@1",
                "provider_id": self.provider_id,
                "status": conclusion or "UNKNOWN",
                "outcome": outcome,
                "source_pr_number": source_pr,
                "merge_sha": merge_sha,
                "correlation_ref": correlation_ref,
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
                "merge_allowed": False,
                "deploy_allowed": False,
                "production_closed": False,
            }
            _write_json(path, payload)
            return StepDispatchResult(
                outcome=outcome,
                evidence_refs=_refs((*event_refs, f"file:{path.relative_to(self.workspace).as_posix()}")),
                payload=payload,
            )

        receipt = event.get("receipt")
        if not isinstance(receipt, Mapping):
            raise PostMergeValidationProviderError("successful post-merge resume requires final validation receipt")
        if _text(receipt.get("schema")) != "governed-repair-post-merge-validation@1":
            raise PostMergeValidationProviderError("post-merge final receipt schema mismatch")
        if _text(receipt.get("status")) != "POST_MERGE_VALIDATED":
            raise PostMergeValidationProviderError("post-merge final receipt is not validated")
        if _positive_int(receipt.get("source_pr_number"), field="final source_pr_number") != source_pr:
            raise PostMergeValidationProviderError("post-merge final receipt source_pr_number mismatch")
        if _sha(receipt.get("merge_sha"), field="final merge_sha") != merge_sha:
            raise PostMergeValidationProviderError("post-merge final receipt merge_sha mismatch")
        _positive_int(receipt.get("quality_run_id"), field="quality_run_id")
        _positive_int(receipt.get("convergence_run_id"), field="convergence_run_id")
        final_evidence = receipt.get("evidence")
        if not isinstance(final_evidence, list) or not final_evidence:
            raise PostMergeValidationProviderError("post-merge final receipt requires durable evidence list")
        for field in (
            "authority_effect",
            "merge_allowed",
            "deploy_allowed",
            "production_closed",
        ):
            if receipt.get(field) is not False:
                raise PostMergeValidationProviderError(
                    f"post-merge final receipt must keep {field}=false"
                )

        payload = {
            "schema": "workflow-post-merge-validation-event-result@1",
            "provider_id": self.provider_id,
            "status": "POST_MERGE_VALIDATED",
            "outcome": "green",
            "source_pr_number": source_pr,
            "merge_sha": merge_sha,
            "correlation_ref": correlation_ref,
            "quality_run_id": receipt["quality_run_id"],
            "convergence_run_id": receipt["convergence_run_id"],
            "authority_effect": False,
            "completion_authority_changed": False,
            "quality_authority_changed": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        _write_json(path, payload)
        return StepDispatchResult(
            outcome="green",
            evidence_refs=_refs((*event_refs, *final_evidence, f"file:{path.relative_to(self.workspace).as_posix()}")),
            payload=payload,
        )

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        try:
            if binding.provider_id != self.provider_id or binding.provider_type != self.provider_type:
                raise PostMergeValidationProviderError("post-merge adapter received another provider binding")
            if binding.capability_id not in self._SUPPORTED:
                raise PostMergeValidationProviderError("post-merge adapter received unsupported capability")
            if binding.capability_id == _REQUEST:
                if not binding.mutates or binding.external_wait:
                    raise PostMergeValidationProviderError("post-merge request must be mutating and non-waiting")
                return self._invoke_request(binding=binding, step=step, state=state)
            if binding.mutates or not binding.external_wait:
                raise PostMergeValidationProviderError("post-merge wait must be non-mutating external wait")
            return self._invoke_wait(binding=binding, step=step, state=state)
        except Exception as exc:
            return self._blocked(binding=binding, step=step, state=state, error=exc)


__all__ = [
    "GovernedPostMergeValidationProviderAdapter",
    "PostMergeValidationHost",
    "PostMergeValidationHostResult",
    "PostMergeValidationProviderError",
]
