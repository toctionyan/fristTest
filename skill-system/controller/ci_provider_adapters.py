from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from capability_registry import CapabilityBinding
from workflow_graph_contract import WorkflowStepSpec


class CIProviderError(RuntimeError):
    """Raised when CI capability execution cannot be proven safely."""


@dataclass(frozen=True)
class CIObservation:
    status: str
    run_id: str
    provider: str
    evidence_refs: tuple[str, ...]


class CIProviderHost(Protocol):
    def observe(
        self,
        *,
        request: Mapping[str, Any],
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> CIObservation:
        ...


class CIWaitProviderAdapter:
    """Provider-neutral CI observation adapter.

    The adapter never owns workflow completion. A successful CI observation only
    returns evidence; WorkflowRuntime/TaskRun decide the next transition.
    """

    provider_type = "integration"

    def __init__(self, *, provider_id: str, host: CIProviderHost) -> None:
        self.provider_id = provider_id
        self.host = host

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        target = state.get("target_ref")
        if not isinstance(target, Mapping):
            raise CIProviderError("CI wait requires target_ref")

        requests = target.get("ci_requests")
        if not isinstance(requests, Mapping):
            raise CIProviderError("CI wait requires ci_requests")

        request = requests.get(step.step_id) or requests.get(binding.capability_id)
        if not isinstance(request, Mapping):
            raise CIProviderError("CI wait request is missing")

        observation = self.host.observe(
            request=request,
            step=step,
            state=state,
        )

        if observation.status == "WAITING":
            return StepDispatchResult(
                outcome="waiting",
                evidence_refs=observation.evidence_refs,
                external_wait={
                    "provider": observation.provider,
                    "run_id": observation.run_id,
                    "capability": binding.capability_id,
                },
            )

        if observation.status == "SUCCESS":
            return StepDispatchResult(
                outcome="green",
                evidence_refs=observation.evidence_refs,
                payload={"ci_run_id": observation.run_id},
            )

        return StepDispatchResult(
            outcome="red",
            evidence_refs=observation.evidence_refs,
            payload={"ci_run_id": observation.run_id},
        )


__all__ = ["CIObservation", "CIProviderError", "CIProviderHost", "CIWaitProviderAdapter"]
