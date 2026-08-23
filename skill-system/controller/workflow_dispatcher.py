from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from capability_registry import CapabilityBinding
from langgraph_workflow_runtime import StepDispatchResult, WorkflowRuntimeState
from skill_invocation import (
    build_receipt,
    canonical_skill_identity,
    canonical_skill_path,
    validate_receipt,
    write_receipt,
)
from workflow_graph_contract import WorkflowStepSpec


class WorkflowDispatchError(RuntimeError):
    """Raised when a workflow step cannot be dispatched through registered adapters."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _evidence(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _text(value)
        if ref and ref not in seen:
            result.append(ref)
            seen.add(ref)
    return tuple(result)


def _invocation_id(state: Mapping[str, Any], step: WorkflowStepSpec) -> str:
    attempts = state.get("step_attempts") if isinstance(state.get("step_attempts"), Mapping) else {}
    attempt = int(attempts.get(step.step_id) or 0) + 1
    seed = "|".join(
        (
            _text(state.get("workflow_id")),
            _text(state.get("task_id")),
            step.step_id,
            str(attempt),
        )
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"wf-{digest}"


@dataclass(frozen=True)
class SkillHostResult:
    """Result returned by the real host that executed one canonical Skill.

    The host is responsible for actually loading/executing the Skill. This adapter
    only validates that execution and writes the existing canonical invocation
    receipt; it never fabricates a Skill PASS from graph position alone.
    """

    outcome: str
    output_schema: str
    output_content: str | bytes
    output_evidence_ref: str
    evidence_refs: tuple[str, ...] = ()
    payload: Mapping[str, Any] | None = None
    problem_ledger_ref: str | None = None


class SkillHostAdapter(Protocol):
    def execute(
        self,
        *,
        skill_name: str,
        request_class: str,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> SkillHostResult:
        ...


class CapabilityProviderAdapter(Protocol):
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


class HumanGateAdapter(Protocol):
    def invoke(
        self,
        *,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        ...


class WriteAuthorityGuard(Protocol):
    def assert_allowed(
        self,
        *,
        binding: CapabilityBinding,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> None:
        ...


class CanonicalSkillInvocationAdapter:
    """Bridge a real host Skill execution into the existing Skill Invocation ledger."""

    def __init__(
        self,
        *,
        workspace: Path,
        request_class: str,
        host: SkillHostAdapter,
        canonical_skill_paths: Mapping[str, str | Path] | None = None,
        skill_capability_bindings: Mapping[str, Iterable[CapabilityBinding]] | None = None,
        write_authority_guard: Any | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.request_class = _text(request_class).upper()
        self.host = host
        self.canonical_skill_paths = (
            {str(key): Path(value) for key, value in canonical_skill_paths.items()}
            if canonical_skill_paths is not None
            else None
        )
        self.skill_capability_bindings = {
            str(key): tuple(values)
            for key, values in (skill_capability_bindings or {}).items()
        }
        self.write_authority_guard = write_authority_guard
        if not self.request_class:
            raise WorkflowDispatchError("Skill adapter requires request_class")

    def invoke(
        self,
        *,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
    ) -> StepDispatchResult:
        skill_name = _text(step.use)
        if step.step_type != "skill" or not skill_name:
            raise WorkflowDispatchError("canonical Skill adapter requires a skill step with use")

        canonical_skill_identity(
            self.workspace,
            skill_name,
            canonical_skill_paths=self.canonical_skill_paths,
        )
        for binding in self.skill_capability_bindings.get(skill_name, ()):
            if binding.mutates:
                if self.write_authority_guard is None:
                    raise WorkflowDispatchError(
                        f"mutating Skill requires existing write authority: {skill_name}"
                    )
                self.write_authority_guard.assert_allowed(
                    binding=binding,
                    step=step,
                    state=state,
                )

        host_result = self.host.execute(
            skill_name=skill_name,
            request_class=self.request_class,
            step=step,
            state=state,
        )
        output_ref = _text(host_result.output_evidence_ref)
        if not output_ref:
            raise WorkflowDispatchError("Skill host result requires output_evidence_ref")

        receipt = build_receipt(
            self.workspace,
            invocation_id=_invocation_id(state, step),
            request_class=self.request_class,
            required_skill=skill_name,
            selected_skill=skill_name,
            entrypoint=(
                self.canonical_skill_paths[skill_name].as_posix()
                if self.canonical_skill_paths is not None
                else canonical_skill_path(skill_name).as_posix()
            ),
            output_schema=_text(host_result.output_schema),
            output_content=host_result.output_content,
            output_evidence_ref=output_ref,
            change_id=_text(state.get("change_id")) or None,
            task_id=_text(state.get("task_id")) or None,
            response_bound=False,
            canonical_skill_paths=self.canonical_skill_paths,
        )
        path = write_receipt(
            self.workspace,
            receipt,
            canonical_skill_paths=self.canonical_skill_paths,
        )
        validate_receipt(
            self.workspace,
            receipt,
            expected_request_class=self.request_class,
            expected_skill=skill_name,
            expected_change_id=_text(state.get("change_id")) or None,
            expected_task_id=_text(state.get("task_id")) or None,
            canonical_skill_paths=self.canonical_skill_paths,
        )
        receipt_ref = f"file:{path.relative_to(self.workspace).as_posix()}"
        refs = _evidence((*host_result.evidence_refs, output_ref, receipt_ref))
        if not refs:
            raise WorkflowDispatchError("Skill execution produced no durable evidence")
        return StepDispatchResult(
            outcome=_text(host_result.outcome),
            evidence_refs=refs,
            payload=dict(host_result.payload or {}),
            problem_ledger_ref=_text(host_result.problem_ledger_ref) or None,
        )


class ProviderAdapterRegistry:
    """Runtime-only provider adapter table.

    Registry presence does not make a provider available. Capability activation
    still selects the provider; this table only supplies the executable adapter for
    a provider that was already bound by CapabilityResolver.
    """

    def __init__(self, adapters: Iterable[CapabilityProviderAdapter] = ()) -> None:
        self._adapters: dict[str, CapabilityProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: CapabilityProviderAdapter) -> None:
        provider_id = _text(getattr(adapter, "provider_id", ""))
        provider_type = _text(getattr(adapter, "provider_type", ""))
        if not provider_id or provider_type not in {"executor", "integration"}:
            raise WorkflowDispatchError("provider adapter requires stable provider_id and provider_type")
        if provider_id in self._adapters:
            raise WorkflowDispatchError(f"duplicate provider adapter: {provider_id}")
        self._adapters[provider_id] = adapter

    def require(self, binding: CapabilityBinding) -> CapabilityProviderAdapter:
        adapter = self._adapters.get(binding.provider_id)
        if adapter is None:
            raise WorkflowDispatchError(
                f"activated provider has no runtime adapter: {binding.provider_id}"
            )
        adapter_type = _text(getattr(adapter, "provider_type", ""))
        if adapter_type != binding.provider_type:
            raise WorkflowDispatchError(
                f"provider adapter type mismatch for {binding.provider_id}: "
                f"binding={binding.provider_type} adapter={adapter_type}"
            )
        return adapter


class WorkflowAdapterDispatcher:
    """Dispatch validated LangGraph steps without acquiring any authority.

    - Skill steps go through the canonical Skill Invocation adapter.
    - Executor/Gate/External Wait steps go through the provider selected by the
      provider-neutral CapabilityResolver.
    - Mutating capabilities require the existing write-authority guard.
    - Human gates are delegated to an injected adapter.
    """

    def __init__(
        self,
        *,
        skill_adapter: CanonicalSkillInvocationAdapter | None = None,
        provider_adapters: ProviderAdapterRegistry | None = None,
        write_authority_guard: WriteAuthorityGuard | None = None,
        human_gate_adapter: HumanGateAdapter | None = None,
    ) -> None:
        self.skill_adapter = skill_adapter
        self.provider_adapters = provider_adapters or ProviderAdapterRegistry()
        self.write_authority_guard = write_authority_guard
        self.human_gate_adapter = human_gate_adapter

    def run(
        self,
        *,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
        capability_binding: CapabilityBinding | None,
    ) -> StepDispatchResult:
        if step.step_type == "skill":
            if capability_binding is not None:
                raise WorkflowDispatchError("Skill steps cannot receive a capability binding")
            if self.skill_adapter is None:
                raise WorkflowDispatchError("Skill step has no canonical Skill execution adapter")
            return self.skill_adapter.invoke(step=step, state=state)

        if step.step_type in {"executor", "gate", "external_wait"}:
            if capability_binding is None:
                raise WorkflowDispatchError(
                    f"{step.step_type} step requires an activated capability binding"
                )
            if _text(step.use) != capability_binding.capability_id:
                raise WorkflowDispatchError(
                    f"step/binding capability mismatch: step={step.use!r} "
                    f"binding={capability_binding.capability_id!r}"
                )
            if step.step_type == "external_wait" and not capability_binding.external_wait:
                raise WorkflowDispatchError(
                    f"external_wait step requires an external-wait capability: {step.use}"
                )
            if step.step_type != "external_wait" and capability_binding.external_wait:
                raise WorkflowDispatchError(
                    f"external-wait capability must use external_wait step type: {step.use}"
                )
            if capability_binding.mutates:
                if self.write_authority_guard is None:
                    raise WorkflowDispatchError(
                        f"mutating capability requires existing write authority: {step.use}"
                    )
                self.write_authority_guard.assert_allowed(
                    binding=capability_binding,
                    step=step,
                    state=state,
                )
            adapter = self.provider_adapters.require(capability_binding)
            return adapter.invoke(binding=capability_binding, step=step, state=state)

        if step.step_type == "human_gate":
            if capability_binding is not None:
                raise WorkflowDispatchError("human_gate cannot receive a capability binding")
            if self.human_gate_adapter is None:
                raise WorkflowDispatchError("human_gate step has no Human Gate adapter")
            return self.human_gate_adapter.invoke(step=step, state=state)

        raise WorkflowDispatchError(f"unsupported workflow step type: {step.step_type!r}")


__all__ = [
    "CanonicalSkillInvocationAdapter",
    "CapabilityProviderAdapter",
    "HumanGateAdapter",
    "ProviderAdapterRegistry",
    "SkillHostAdapter",
    "SkillHostResult",
    "WorkflowAdapterDispatcher",
    "WorkflowDispatchError",
    "WriteAuthorityGuard",
]
