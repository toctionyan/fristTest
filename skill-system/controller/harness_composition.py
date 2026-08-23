from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness_authoring import (
    HarnessAuthoringError,
    SkillContractDeclaration,
    compile_workflow_declaration,
    parse_skill_contract,
)
from workflow_graph_contract import TERMINAL_TARGETS


COMPOSITION_SCHEMA = "harness-composition@1"
COMPOSED_PLAN_SCHEMA = "composed-workflow-plan@1"
CONTINUE_TARGET = "$CONTINUE"

_ANCHOR_KINDS = frozenset({"before_step", "after_route"})
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


@dataclass(frozen=True)
class CompositionAnchor:
    kind: str
    step: str
    outcome: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"kind": self.kind, "step": self.step}
        if self.outcome is not None:
            payload["outcome"] = self.outcome
        return payload

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.step, self.outcome or "")


@dataclass(frozen=True)
class CompositionBinding:
    binding_id: str
    host_skill: str
    extension_skill: str
    extension_point: str
    anchor: CompositionAnchor
    order: int
    routes: dict[str, str]
    max_attempts: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.binding_id,
            "host_skill": self.host_skill,
            "extension_skill": self.extension_skill,
            "at": self.extension_point,
            "anchor": self.anchor.as_dict(),
            "order": self.order,
            "routes": dict(self.routes),
        }
        if self.max_attempts is not None:
            payload["max_attempts"] = self.max_attempts
        return payload


@dataclass(frozen=True)
class CompositionDeclaration:
    composition_id: str
    version: str
    base_workflow: str
    bindings: tuple[CompositionBinding, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPOSITION_SCHEMA,
            "id": self.composition_id,
            "version": self.version,
            "base_workflow": self.base_workflow,
            "bindings": [binding.as_dict() for binding in self.bindings],
        }


@dataclass(frozen=True)
class ComposedWorkflowPlan:
    composition: CompositionDeclaration
    composition_sha256: str
    base_sha256: str
    skill_contract_sha256: dict[str, str]
    resolved_bindings: tuple[dict[str, Any], ...]
    derived_workflow: dict[str, Any]
    compiled_plan: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": COMPOSED_PLAN_SCHEMA,
            "composition": {
                "id": self.composition.composition_id,
                "version": self.composition.version,
                "source_sha256": self.composition_sha256,
            },
            "base": {
                "workflow_id": self.composition.base_workflow,
                "source_sha256": self.base_sha256,
            },
            "skill_contracts": dict(sorted(self.skill_contract_sha256.items())),
            "resolved_bindings": [dict(binding) for binding in self.resolved_bindings],
            "derived_workflow": self.derived_workflow,
            "compiled": self.compiled_plan,
        }
        payload["provenance_sha256"] = _digest(payload)
        return payload


def _digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessAuthoringError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise HarnessAuthoringError(f"{field} keys must be strings")
    return dict(value)


def _closed(value: Mapping[str, Any], allowed: set[str], *, field: str) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise HarnessAuthoringError(f"{field} contains unsupported keys: {unexpected}")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessAuthoringError(f"{field} must be a non-empty string")
    return value.strip()


def _stable_id(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not _STABLE_ID.fullmatch(text):
        raise HarnessAuthoringError(f"{field} must be a stable identifier")
    return text


def _bounded_int(value: object, *, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise HarnessAuthoringError(
            f"{field} must be an integer between {minimum} and {maximum}"
        )
    return value


def parse_composition_declaration(raw: Mapping[str, Any]) -> CompositionDeclaration:
    row = _object(raw, field="Composition declaration")
    _closed(row, {"schema", "id", "version", "base_workflow", "bindings"}, field="Composition declaration")
    if row.get("schema") != COMPOSITION_SCHEMA:
        raise HarnessAuthoringError(f"Composition schema must be {COMPOSITION_SCHEMA!r}")
    composition_id = _stable_id(row.get("id"), field="Composition id")
    version = _required_text(row.get("version"), field="Composition version")
    base_workflow = _stable_id(row.get("base_workflow"), field="base_workflow")
    raw_bindings = row.get("bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise HarnessAuthoringError("bindings must be a non-empty array")

    bindings: list[CompositionBinding] = []
    seen_ids: set[str] = set()
    seen_identities: set[tuple[str, str, str, tuple[str, str, str]]] = set()
    for index, raw_binding in enumerate(raw_bindings):
        field = f"bindings[{index}]"
        binding = _object(raw_binding, field=field)
        _closed(
            binding,
            {
                "id", "host_skill", "extension_skill", "at", "anchor",
                "order", "routes", "max_attempts",
            },
            field=field,
        )
        binding_id = _stable_id(binding.get("id"), field=f"{field}.id")
        if binding_id in TERMINAL_TARGETS or binding_id.startswith("__workflow_"):
            raise HarnessAuthoringError(f"{field}.id is reserved by the Workflow runtime")
        if binding_id in seen_ids:
            raise HarnessAuthoringError(f"duplicate binding id: {binding_id}")
        seen_ids.add(binding_id)
        host_skill = _stable_id(binding.get("host_skill"), field=f"{field}.host_skill")
        extension_skill = _stable_id(
            binding.get("extension_skill"), field=f"{field}.extension_skill"
        )
        extension_point = _stable_id(binding.get("at"), field=f"{field}.at")

        raw_anchor = _object(binding.get("anchor"), field=f"{field}.anchor")
        _closed(raw_anchor, {"kind", "step", "outcome"}, field=f"{field}.anchor")
        kind = _stable_id(raw_anchor.get("kind"), field=f"{field}.anchor.kind")
        if kind not in _ANCHOR_KINDS:
            raise HarnessAuthoringError(
                f"{field}.anchor.kind must be one of {sorted(_ANCHOR_KINDS)}"
            )
        step = _stable_id(raw_anchor.get("step"), field=f"{field}.anchor.step")
        raw_outcome = raw_anchor.get("outcome")
        if kind == "after_route":
            outcome = _stable_id(raw_outcome, field=f"{field}.anchor.outcome")
        else:
            if raw_outcome is not None:
                raise HarnessAuthoringError(
                    f"{field}.anchor.outcome is only valid for after_route"
                )
            outcome = None
        anchor = CompositionAnchor(kind=kind, step=step, outcome=outcome)

        order = _bounded_int(
            binding.get("order", 100), field=f"{field}.order", minimum=-10000, maximum=10000
        )
        raw_routes = _object(binding.get("routes"), field=f"{field}.routes")
        if not raw_routes:
            raise HarnessAuthoringError(f"{field}.routes cannot be empty")
        routes: dict[str, str] = {}
        for raw_outcome_name, raw_target in raw_routes.items():
            outcome_name = _stable_id(raw_outcome_name, field=f"{field}.routes outcome")
            target = (
                CONTINUE_TARGET
                if raw_target == CONTINUE_TARGET
                else _stable_id(raw_target, field=f"{field}.routes.{outcome_name}")
            )
            if target != CONTINUE_TARGET and target not in TERMINAL_TARGETS:
                raise HarnessAuthoringError(
                    f"{field}.routes.{outcome_name} must be {CONTINUE_TARGET!r} "
                    f"or a Workflow terminal target"
                )
            routes[outcome_name] = target
        if CONTINUE_TARGET not in routes.values():
            raise HarnessAuthoringError(
                f"{field}.routes requires at least one {CONTINUE_TARGET!r} target"
            )
        raw_max_attempts = binding.get("max_attempts")
        max_attempts = None
        if raw_max_attempts is not None:
            max_attempts = _bounded_int(
                raw_max_attempts,
                field=f"{field}.max_attempts",
                minimum=1,
                maximum=64,
            )
        identity = (host_skill, extension_skill, extension_point, anchor.key)
        if identity in seen_identities:
            raise HarnessAuthoringError(
                "duplicate binding identity for host Skill, extension Skill, hook, and anchor"
            )
        seen_identities.add(identity)
        bindings.append(
            CompositionBinding(
                binding_id=binding_id,
                host_skill=host_skill,
                extension_skill=extension_skill,
                extension_point=extension_point,
                anchor=anchor,
                order=order,
                routes=routes,
                max_attempts=max_attempts,
            )
        )
    return CompositionDeclaration(
        composition_id=composition_id,
        version=version,
        base_workflow=base_workflow,
        bindings=tuple(bindings),
    )


def _contract_index(
    raw_contracts: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, SkillContractDeclaration], dict[str, str]]:
    contracts: dict[str, SkillContractDeclaration] = {}
    digests: dict[str, str] = {}
    for raw in raw_contracts:
        contract = parse_skill_contract(raw)
        if contract.skill in contracts:
            raise HarnessAuthoringError(f"duplicate Skill contract: {contract.skill}")
        contracts[contract.skill] = contract
        digests[contract.skill] = _digest(raw)
    return contracts, digests


def _require_contract(
    contracts: Mapping[str, SkillContractDeclaration], skill: str
) -> SkillContractDeclaration:
    try:
        return contracts[skill]
    except KeyError as exc:
        raise HarnessAuthoringError(f"missing Skill contract: {skill}") from exc


def _validate_artifact_compatibility(
    host: SkillContractDeclaration, extension: SkillContractDeclaration
) -> None:
    host_artifacts = set(host.inputs) | set(host.outputs)
    if extension.extension_type == "context-provider":
        if not set(extension.outputs).intersection(host.inputs):
            raise HarnessAuthoringError(
                f"context-provider Skill {extension.skill!r} must produce at least one "
                f"input accepted by host Skill {host.skill!r}"
            )
        return
    missing_inputs = sorted(set(extension.inputs) - host_artifacts)
    if missing_inputs:
        raise HarnessAuthoringError(
            f"extension Skill {extension.skill!r} requires artifacts not exposed by "
            f"host Skill {host.skill!r}: {missing_inputs}"
        )
    if extension.extension_type == "finding-enricher" and not set(
        extension.inputs
    ).intersection(host.outputs):
        raise HarnessAuthoringError(
            f"finding-enricher Skill {extension.skill!r} must consume a host output artifact"
        )


def _validate_binding(
    binding: CompositionBinding,
    *,
    base: Mapping[str, Any],
    base_skills: set[str],
    graph_steps: Mapping[str, Any],
    contracts: Mapping[str, SkillContractDeclaration],
) -> tuple[SkillContractDeclaration, SkillContractDeclaration]:
    if binding.host_skill not in base_skills:
        raise HarnessAuthoringError(
            f"binding {binding.binding_id!r} host Skill is not declared by the base Workflow: "
            f"{binding.host_skill}"
        )
    host = _require_contract(contracts, binding.host_skill)
    extension = _require_contract(contracts, binding.extension_skill)
    if host.skill == extension.skill:
        raise HarnessAuthoringError(
            f"binding {binding.binding_id!r} cannot use its host Skill as the extension Skill"
        )
    accepted_types = host.extension_points.get(binding.extension_point)
    if accepted_types is None:
        raise HarnessAuthoringError(
            f"host Skill {host.skill!r} does not declare extension point "
            f"{binding.extension_point!r}"
        )
    if extension.extension_type not in accepted_types:
        raise HarnessAuthoringError(
            f"extension Skill {extension.skill!r} type {extension.extension_type!r} is not "
            f"accepted by {host.skill!r}.{binding.extension_point}"
        )
    if binding.binding_id in graph_steps:
        raise HarnessAuthoringError(
            f"binding id collides with existing Workflow step: {binding.binding_id}"
        )
    if binding.anchor.step not in graph_steps:
        raise HarnessAuthoringError(
            f"binding {binding.binding_id!r} anchor references unknown base step: "
            f"{binding.anchor.step}"
        )
    if binding.anchor.kind == "after_route":
        step = _object(graph_steps[binding.anchor.step], field="base graph step")
        routes = _object(step.get("routes"), field="base graph step routes")
        if binding.anchor.outcome not in routes:
            raise HarnessAuthoringError(
                f"binding {binding.binding_id!r} anchor references unknown route outcome: "
                f"{binding.anchor.step}.{binding.anchor.outcome}"
            )
    if extension.mode == "mutating" and (
        base.get("mode") != "WRITE" or base.get("write_governed") is not True
    ):
        raise HarnessAuthoringError(
            f"mutating extension Skill {extension.skill!r} requires an already WRITE and "
            "write_governed base Workflow"
        )
    _validate_artifact_compatibility(host, extension)
    return host, extension


def _extension_step(binding: CompositionBinding, continuation: str) -> dict[str, Any]:
    step: dict[str, Any] = {
        "type": "skill",
        "use": binding.extension_skill,
        "routes": {
            outcome: continuation if target == CONTINUE_TARGET else target
            for outcome, target in binding.routes.items()
        },
    }
    if binding.max_attempts is not None:
        step["max_attempts"] = binding.max_attempts
    return step


def _insert_chain(
    graph: dict[str, Any], bindings: Sequence[CompositionBinding], continuation: str
) -> str:
    steps = _object(graph["steps"], field="derived graph steps")
    next_target = continuation
    for binding in reversed(bindings):
        steps[binding.binding_id] = _extension_step(binding, next_target)
        next_target = binding.binding_id
    graph["steps"] = steps
    return next_target


def _compose_graph(
    base_graph: Mapping[str, Any], bindings: Sequence[CompositionBinding]
) -> dict[str, Any]:
    graph = json.loads(json.dumps(base_graph, ensure_ascii=False))
    steps = _object(graph.get("steps"), field="derived graph steps")
    groups: dict[tuple[str, str, str], list[CompositionBinding]] = {}
    for binding in bindings:
        groups.setdefault(binding.anchor.key, []).append(binding)
    for grouped in groups.values():
        grouped.sort(key=lambda item: (item.order, item.binding_id))

    before_keys = sorted(key for key in groups if key[0] == "before_step")
    for key in before_keys:
        target_step = key[1]
        chain = groups[key]
        first = _insert_chain(graph, chain, target_step)
        steps = _object(graph["steps"], field="derived graph steps")
        inserted = {binding.binding_id for binding in chain}
        if graph.get("start") == target_step:
            graph["start"] = first
        for step_id, raw_step in steps.items():
            if step_id in inserted:
                continue
            step = _object(raw_step, field=f"derived graph step {step_id}")
            routes = _object(step.get("routes"), field=f"derived graph step {step_id} routes")
            step["routes"] = {
                outcome: first if route_target == target_step else route_target
                for outcome, route_target in routes.items()
            }
            steps[step_id] = step
        graph["steps"] = steps

    after_keys = sorted(key for key in groups if key[0] == "after_route")
    for key in after_keys:
        _, step_id, outcome = key
        steps = _object(graph["steps"], field="derived graph steps")
        step = _object(steps[step_id], field=f"derived graph step {step_id}")
        routes = _object(step.get("routes"), field=f"derived graph step {step_id} routes")
        continuation = str(routes[outcome])
        first = _insert_chain(graph, groups[key], continuation)
        steps = _object(graph["steps"], field="derived graph steps")
        step = _object(steps[step_id], field=f"derived graph step {step_id}")
        routes = _object(step.get("routes"), field=f"derived graph step {step_id} routes")
        routes[outcome] = first
        step["routes"] = routes
        steps[step_id] = step
        graph["steps"] = steps
    return graph


def compose_workflow(
    base_workflow: Mapping[str, Any],
    composition_raw: Mapping[str, Any],
    skill_contracts: Sequence[Mapping[str, Any]],
) -> ComposedWorkflowPlan:
    base_compiled = compile_workflow_declaration(base_workflow)
    composition = parse_composition_declaration(composition_raw)
    if composition.base_workflow != base_compiled.spec.workflow_id:
        raise HarnessAuthoringError(
            f"composition base_workflow {composition.base_workflow!r} does not match "
            f"Workflow {base_compiled.spec.workflow_id!r}"
        )
    contracts, contract_digests = _contract_index(skill_contracts)
    graph = _object(base_workflow.get("graph"), field="base Workflow graph")
    graph_steps = _object(graph.get("steps"), field="base Workflow graph steps")
    base_skills = set(base_compiled.spec.skills)

    extension_contracts: dict[str, SkillContractDeclaration] = {}
    resolved: list[dict[str, Any]] = []
    for binding in composition.bindings:
        host, extension = _validate_binding(
            binding,
            base=base_workflow,
            base_skills=base_skills,
            graph_steps=graph_steps,
            contracts=contracts,
        )
        extension_contracts[extension.skill] = extension
        resolved.append(
            {
                **binding.as_dict(),
                "host_version": host.version,
                "extension_version": extension.version,
                "extension_type": extension.extension_type,
            }
        )

    ordered_bindings = tuple(
        sorted(
            composition.bindings,
            key=lambda binding: (*binding.anchor.key, binding.order, binding.binding_id),
        )
    )
    derived = json.loads(json.dumps(base_workflow, ensure_ascii=False))
    derived["id"] = composition.composition_id
    derived["version"] = composition.version
    derived_skills = list(base_compiled.spec.skills)
    for binding in ordered_bindings:
        if binding.extension_skill not in derived_skills:
            derived_skills.append(binding.extension_skill)
    derived["skills"] = derived_skills

    requirements = _object(derived.get("requirements", {}), field="derived requirements")
    capabilities = _object(
        requirements.get("capabilities", {}), field="derived capability requirements"
    )
    required = list(capabilities.get("required", []))
    optional = list(capabilities.get("optional", []))
    for binding in ordered_bindings:
        for capability in extension_contracts[binding.extension_skill].capabilities:
            if capability not in required:
                required.append(capability)
            if capability in optional:
                optional.remove(capability)
    capabilities["required"] = required
    capabilities["optional"] = optional
    requirements["capabilities"] = capabilities
    derived["requirements"] = requirements
    derived["graph"] = _compose_graph(graph, ordered_bindings)

    compiled = compile_workflow_declaration(derived)
    resolved_by_id = {row["id"]: row for row in resolved}
    resolved_order = tuple(resolved_by_id[binding.binding_id] for binding in ordered_bindings)
    used_contracts = {binding.host_skill for binding in ordered_bindings} | {
        binding.extension_skill for binding in ordered_bindings
    }
    return ComposedWorkflowPlan(
        composition=composition,
        composition_sha256=_digest(composition_raw),
        base_sha256=base_compiled.source_sha256,
        skill_contract_sha256={
            skill: contract_digests[skill] for skill in sorted(used_contracts)
        },
        resolved_bindings=resolved_order,
        derived_workflow=derived,
        compiled_plan=compiled.as_dict(),
    )


__all__ = [
    "COMPOSED_PLAN_SCHEMA",
    "COMPOSITION_SCHEMA",
    "CONTINUE_TARGET",
    "ComposedWorkflowPlan",
    "CompositionAnchor",
    "CompositionBinding",
    "CompositionDeclaration",
    "compose_workflow",
    "parse_composition_declaration",
]
