from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from capability_registry import CapabilityBinding
from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from workflow_graph_contract import WorkflowStepSpec


class ProviderAdapterError(RuntimeError):
    """Raised when a concrete provider adapter cannot execute its bound capability safely."""


ProfileRunner = Callable[..., Mapping[str, Any]]
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


def _dedupe(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _text(value)
        if ref and ref not in seen:
            seen.add(ref)
            result.append(ref)
    return tuple(result)


def _default_profile_runner(profile: str, *, state_file: Path) -> Mapping[str, Any]:
    from profile_runner import run

    return run(
        profile,
        state_file=state_file,
        resume=False,
        stream_output=False,
    )


class LocalProcessProviderAdapter:
    """Concrete adapter for deterministic local test/Quality profiles.

    Workflow definitions remain target-independent. The active target supplies a
    profile selection under `target_ref.execution_profiles`, while composition
    supplies the allow-list for each capability. No arbitrary shell command is
    accepted from Workflow state.
    """

    provider_id = "local.process"
    provider_type = "executor"
    _SUPPORTED = frozenset({"test.run", "quality.evaluate"})

    def __init__(
        self,
        *,
        workspace: Path,
        allowed_profiles: Mapping[str, Iterable[str]],
        runner: ProfileRunner | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.runner = runner or _default_profile_runner
        self.allowed_profiles = {
            _text(capability): frozenset(_text(profile) for profile in profiles if _text(profile))
            for capability, profiles in allowed_profiles.items()
        }
        for capability, profiles in self.allowed_profiles.items():
            if capability not in self._SUPPORTED:
                raise ProviderAdapterError(f"local.process policy contains unsupported capability: {capability}")
            if not profiles:
                raise ProviderAdapterError(f"local.process policy requires profiles for {capability}")

    def _select_profile(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> str:
        target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
        raw_profiles = target.get("execution_profiles") if isinstance(target, Mapping) else None
        profiles = raw_profiles if isinstance(raw_profiles, Mapping) else {}
        profile = _text(profiles.get(step.step_id)) or _text(profiles.get(binding.capability_id))
        if not profile:
            raise ProviderAdapterError(
                f"target_ref.execution_profiles must select a profile for step={step.step_id!r} "
                f"or capability={binding.capability_id!r}"
            )
        allowed = self.allowed_profiles.get(binding.capability_id, frozenset())
        if profile not in allowed:
            raise ProviderAdapterError(
                f"profile {profile!r} is not allowed for capability {binding.capability_id!r}"
            )
        return profile

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        if binding.provider_id != self.provider_id or binding.provider_type != self.provider_type:
            raise ProviderAdapterError("local.process adapter received another provider binding")
        if binding.capability_id not in self._SUPPORTED:
            raise ProviderAdapterError(
                f"local.process adapter does not implement {binding.capability_id!r}"
            )
        if binding.external_wait or binding.mutates:
            raise ProviderAdapterError("local test/Quality profiles cannot be external-wait or mutating bindings")

        profile = self._select_profile(binding=binding, step=step, state=state)
        task = _safe(state.get("task_id"), fallback="task")
        step_id = _safe(step.step_id, fallback="step")
        attempt = _attempt(state, step)
        root = self.workspace / ".quality" / "workflow-provider-runs" / task
        state_file = root / f"{step_id}-{attempt}.profile.json"
        summary_file = root / f"{step_id}-{attempt}.provider.json"

        try:
            result = dict(self.runner(profile, state_file=state_file))
        except Exception as exc:
            summary = {
                "schema": "workflow-provider-result@1",
                "provider_id": self.provider_id,
                "capability_id": binding.capability_id,
                "step_id": step.step_id,
                "attempt": attempt,
                "profile": profile,
                "status": "BLOCKED",
                "error": f"{type(exc).__name__}: {exc}",
                "authority_effect": False,
                "completion_authority_changed": False,
                "quality_authority_changed": False,
            }
            _write_json(summary_file, summary)
            return StepDispatchResult(
                outcome="blocked",
                evidence_refs=(f"file:{summary_file.relative_to(self.workspace).as_posix()}",),
                payload=summary,
            )

        status = _text(result.get("status")).upper()
        if status == "PASS":
            outcome = "green"
        elif _text(result.get("error")):
            outcome = "blocked"
        else:
            outcome = "red"
        summary = {
            "schema": "workflow-provider-result@1",
            "provider_id": self.provider_id,
            "capability_id": binding.capability_id,
            "step_id": step.step_id,
            "attempt": attempt,
            "profile": profile,
            "status": status or "UNKNOWN",
            "outcome": outcome,
            "authority_effect": False,
            "completion_authority_changed": False,
            "quality_authority_changed": False,
        }
        _write_json(summary_file, summary)
        refs: list[str] = [f"file:{summary_file.relative_to(self.workspace).as_posix()}"]
        if state_file.is_file():
            refs.append(f"file:{state_file.relative_to(self.workspace).as_posix()}")
        return StepDispatchResult(
            outcome=outcome,
            evidence_refs=_dedupe(refs),
            payload={**summary, "profile_result_status": status or "UNKNOWN"},
        )


class EventDrivenCIProviderAdapter:
    """Provider-specific CI wait adapter with no polling loop.

    It creates one durable wait contract on the first invocation and interprets one
    externally supplied completion event on resume. The scheduler/integration layer
    is responsible for producing that event; this adapter never sleeps or polls.
    """

    provider_type = "integration"

    def __init__(self, *, workspace: Path, provider_id: str) -> None:
        self.workspace = workspace.resolve()
        self.provider_id = _text(provider_id)
        if not self.provider_id:
            raise ProviderAdapterError("CI adapter requires provider_id")

    def _handle(self, state: WorkflowRuntimeState) -> dict[str, Any]:
        target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
        raw_handles = target.get("external_handles") if isinstance(target, Mapping) else None
        handles = raw_handles if isinstance(raw_handles, Mapping) else {}
        raw = handles.get("ci.run.wait")
        if not isinstance(raw, Mapping):
            raise ProviderAdapterError("target_ref.external_handles['ci.run.wait'] is required")
        correlation = _text(raw.get("correlation_ref"))
        resume_event = _text(raw.get("resume_event")) or "ci.completed"
        if not correlation:
            raise ProviderAdapterError("CI wait handle requires correlation_ref")
        return {
            "provider": self.provider_id,
            "correlation_ref": correlation,
            "resume_event": resume_event,
        }

    def _summary_path(self, state: WorkflowRuntimeState, step: WorkflowStepSpec) -> Path:
        task = _safe(state.get("task_id"), fallback="task")
        step_id = _safe(step.step_id, fallback="step")
        attempt = _attempt(state, step)
        return (
            self.workspace
            / ".quality"
            / "workflow-provider-runs"
            / task
            / f"{step_id}-{attempt}.ci.json"
        )

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        if binding.provider_id != self.provider_id or binding.provider_type != self.provider_type:
            raise ProviderAdapterError("CI adapter received another provider binding")
        if binding.capability_id != "ci.run.wait" or not binding.external_wait or binding.mutates:
            raise ProviderAdapterError("event-driven CI adapter only implements non-mutating ci.run.wait")

        handle = self._handle(state)
        summary_path = self._summary_path(state, step)
        event = state.get("external_event") if isinstance(state.get("external_event"), Mapping) else None
        if event is None:
            summary = {
                "schema": "workflow-ci-wait@1",
                "status": "WAITING_EXTERNAL",
                **handle,
                "authority_effect": False,
                "completion_authority_changed": False,
            }
            _write_json(summary_path, summary)
            return StepDispatchResult(
                outcome="pending",
                evidence_refs=(f"file:{summary_path.relative_to(self.workspace).as_posix()}",),
                payload=summary,
                external_wait=handle,
            )

        event_provider = _text(event.get("provider") or event.get("provider_id"))
        correlation = _text(event.get("correlation_ref"))
        event_name = _text(event.get("event") or event.get("event_name"))
        if event_provider != self.provider_id:
            raise ProviderAdapterError(
                f"CI resume provider mismatch: expected={self.provider_id!r} actual={event_provider!r}"
            )
        if correlation != handle["correlation_ref"]:
            raise ProviderAdapterError("CI resume correlation_ref does not match durable wait handle")
        if event_name != handle["resume_event"]:
            raise ProviderAdapterError("CI resume event does not match durable wait handle")
        raw_refs = event.get("evidence_refs")
        event_refs = tuple(_text(ref) for ref in raw_refs) if isinstance(raw_refs, list) else ()
        event_refs = tuple(ref for ref in event_refs if ref)
        if not event_refs:
            raise ProviderAdapterError("CI resume event requires durable evidence_refs")

        conclusion = _text(event.get("conclusion")).upper()
        if conclusion in {"SUCCESS", "PASS", "PASSED", "GREEN"}:
            outcome = "green"
        elif conclusion in {"FAILURE", "FAIL", "FAILED", "RED", "CANCELLED", "TIMED_OUT"}:
            outcome = "red"
        else:
            outcome = "blocked"
        summary = {
            "schema": "workflow-ci-event-result@1",
            "provider": self.provider_id,
            "correlation_ref": correlation,
            "event": event_name,
            "conclusion": conclusion or "UNKNOWN",
            "outcome": outcome,
            "authority_effect": False,
            "completion_authority_changed": False,
        }
        _write_json(summary_path, summary)
        summary_ref = f"file:{summary_path.relative_to(self.workspace).as_posix()}"
        return StepDispatchResult(
            outcome=outcome,
            evidence_refs=_dedupe((*event_refs, summary_ref)),
            payload=summary,
        )


__all__ = [
    "EventDrivenCIProviderAdapter",
    "LocalProcessProviderAdapter",
    "ProviderAdapterError",
]
