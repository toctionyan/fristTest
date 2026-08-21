from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Protocol

from capability_registry import CapabilityBinding
from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from workflow_graph_contract import WorkflowStepSpec
from workflow_step_values import resolve_request_from_steps


class ProviderAdapter(Protocol):
    provider_id: str
    provider_type: str

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        ...


class RequestDataflowProviderAdapter:
    """Resolve explicit target request templates before delegating to one provider.

    This adapter does not invent topology or authority. It only materializes values
    explicitly bound by `target_ref.publication_requests[*].from_steps` from prior
    durable step payloads, then delegates to the existing provider adapter.
    """

    def __init__(self, delegate: ProviderAdapter) -> None:
        self.delegate = delegate
        self.provider_id = str(delegate.provider_id)
        self.provider_type = str(delegate.provider_type)

    def invoke(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        target = state.get("target_ref") if isinstance(state.get("target_ref"), Mapping) else {}
        raw_requests = target.get("publication_requests") if isinstance(target, Mapping) else None
        requests = raw_requests if isinstance(raw_requests, Mapping) else {}
        request_key: str | None = None
        raw_request: object = requests.get(step.step_id)
        if isinstance(raw_request, Mapping):
            request_key = step.step_id
        else:
            raw_request = requests.get(binding.capability_id)
            if isinstance(raw_request, Mapping):
                request_key = binding.capability_id
        if request_key is None or not isinstance(raw_request, Mapping) or "from_steps" not in raw_request:
            return self.delegate.invoke(binding=binding, step=step, state=state)

        resolved_request = resolve_request_from_steps(state, raw_request)
        delegated_state: WorkflowRuntimeState = deepcopy(dict(state))  # type: ignore[assignment]
        delegated_target: dict[str, Any] = deepcopy(dict(target))
        delegated_requests = deepcopy(dict(requests))
        delegated_requests[request_key] = resolved_request
        delegated_target["publication_requests"] = delegated_requests
        delegated_state["target_ref"] = delegated_target
        return self.delegate.invoke(binding=binding, step=step, state=delegated_state)


__all__ = ["RequestDataflowProviderAdapter"]
