from __future__ import annotations

"""Domain-neutral capability contracts.

Concrete contracts are contributed by installed modules and resolved through an
injected ``CapabilityRegistry``.  This module deliberately contains no global
registry facade and no business-domain vocabulary.  Version 2 adds an
immutable planning contract that can be consumed by a later planner without
letting the Kernel infer user intent or hard-code vertical workflows.
"""

from dataclasses import dataclass
from typing import Any


def _clean(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text


def _unique(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    cleaned = tuple(_clean(value, field=field) for value in values)
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"duplicate {field}")
    return cleaned


@dataclass(frozen=True)
class CapabilityTargetArgumentProjection:
    """Module-owned structural projection for a compiled opaque target binding."""

    argument_name: str
    constant_fields: tuple[tuple[str, str], ...] = ()
    binding_fields: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "argument_name", _clean(self.argument_name, field="target projection argument"))
        constants = tuple(
            (
                _clean(name, field="target projection constant field"),
                _clean(value, field="target projection constant value"),
            )
            for name, value in self.constant_fields
        )
        bindings = tuple(
            (
                _clean(name, field="target projection binding field"),
                _clean(source, field="compiled target binding source field"),
            )
            for name, source in self.binding_fields
        )
        projected_fields = [name for name, _ in (*constants, *bindings)]
        if len(set(projected_fields)) != len(projected_fields):
            raise ValueError("duplicate target projection field")
        if not bindings:
            raise ValueError("target projection requires at least one compiled binding field")
        object.__setattr__(self, "constant_fields", constants)
        object.__setattr__(self, "binding_fields", bindings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "argument_name": self.argument_name,
            "constant_fields": [
                {"argument_field": name, "value": value}
                for name, value in self.constant_fields
            ],
            "binding_fields": [
                {"argument_field": name, "binding_field": source}
                for name, source in self.binding_fields
            ],
        }


@dataclass(frozen=True)
class CapabilityTargetContract:
    resource_types: tuple[str, ...]
    cardinality: str
    binding_sources: tuple[str, ...] = ("target_resolver",)
    argument_projection: CapabilityTargetArgumentProjection | None = None
    logical_type_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_types", _unique(self.resource_types, field="target resource type"))
        object.__setattr__(self, "binding_sources", _unique(self.binding_sources, field="target binding source"))
        if self.logical_type_name is not None:
            object.__setattr__(self, "logical_type_name", _clean(self.logical_type_name, field="target logical type"))
        if self.cardinality not in {"none", "exactly_one", "one_or_collection", "collection"}:
            raise ValueError(f"invalid target cardinality: {self.cardinality!r}")
        if self.cardinality != "none" and not self.resource_types:
            raise ValueError("target resource_types are required")

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_types": list(self.resource_types),
            "cardinality": self.cardinality,
            "binding_sources": list(self.binding_sources),
            "logical_type_name": self.logical_type_name,
            "argument_projection": (
                self.argument_projection.as_dict()
                if self.argument_projection is not None
                else None
            ),
        }


@dataclass(frozen=True)
class CapabilityInputContract:
    name: str
    type_name: str
    source_types: tuple[str, ...]
    required: bool = True
    authority: str = "candidate"
    freshness_seconds: int | None = None
    resource_type: str | None = None
    cardinality: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean(self.name, field="required input name"))
        object.__setattr__(self, "type_name", _clean(self.type_name, field="required input type"))
        object.__setattr__(self, "source_types", _unique(self.source_types, field="required input source"))
        object.__setattr__(self, "authority", _clean(self.authority, field="required input authority"))
        if self.resource_type is not None:
            object.__setattr__(self, "resource_type", _clean(self.resource_type, field="required input resource type"))
        if self.cardinality is not None:
            object.__setattr__(self, "cardinality", _clean(self.cardinality, field="required input cardinality"))
        if not self.source_types:
            raise ValueError(f"required input {self.name!r} must declare source_types")
        if self.freshness_seconds is not None and int(self.freshness_seconds) <= 0:
            raise ValueError(f"required input {self.name!r} freshness_seconds must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_name": self.type_name,
            "source_types": list(self.source_types),
            "required": self.required,
            "authority": self.authority,
            "freshness_seconds": self.freshness_seconds,
            "resource_type": self.resource_type,
            "cardinality": self.cardinality,
        }


@dataclass(frozen=True)
class CapabilityOutputContract:
    name: str
    type_name: str
    authority: str = "verified_tool_output"
    completion_proof: bool = False
    freshness_seconds: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean(self.name, field="produced output name"))
        object.__setattr__(self, "type_name", _clean(self.type_name, field="produced output type"))
        object.__setattr__(self, "authority", _clean(self.authority, field="produced output authority"))
        if self.freshness_seconds is not None and int(self.freshness_seconds) <= 0:
            raise ValueError(f"produced output {self.name!r} freshness_seconds must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_name": self.type_name,
            "authority": self.authority,
            "completion_proof": self.completion_proof,
            "freshness_seconds": self.freshness_seconds,
        }


@dataclass(frozen=True)
class CapabilityPreconditionContract:
    code: str
    description: str
    verifier_owner: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _clean(self.code, field="precondition code"))
        object.__setattr__(self, "description", _clean(self.description, field="precondition description"))
        object.__setattr__(self, "verifier_owner", _clean(self.verifier_owner, field="precondition verifier owner"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "verifier_owner": self.verifier_owner,
        }


@dataclass(frozen=True)
class CapabilityAuthorizationContract:
    required: bool = False
    mode: str = "none"
    authority: str = "none"

    def __post_init__(self) -> None:
        if self.mode not in {"none", "structured_interaction", "external_authority"}:
            raise ValueError(f"invalid authorization mode: {self.mode!r}")
        object.__setattr__(self, "authority", _clean(self.authority, field="authorization authority"))
        if self.required and self.mode == "none":
            raise ValueError("required authorization cannot use mode='none'")
        if not self.required and self.mode != "none":
            raise ValueError("optional authorization must use mode='none'")

    def as_dict(self) -> dict[str, Any]:
        return {"required": self.required, "mode": self.mode, "authority": self.authority}


@dataclass(frozen=True)
class CapabilityCompletionContract:
    mode: str
    proof_type: str
    proof_source: str = "verified_tool_output"
    output_name: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"tool_output", "transaction_receipt", "external_receipt", "unsupported_report"}:
            raise ValueError(f"invalid completion proof mode: {self.mode!r}")
        object.__setattr__(self, "proof_type", _clean(self.proof_type, field="completion proof type"))
        object.__setattr__(self, "proof_source", _clean(self.proof_source, field="completion proof source"))
        if self.mode == "tool_output" and not str(self.output_name or "").strip():
            raise ValueError("tool_output completion proof requires output_name")
        if self.mode != "tool_output" and self.output_name is not None:
            raise ValueError(f"{self.mode} completion proof cannot reference a produced output")
        if self.mode == "transaction_receipt" and self.proof_source != "transaction_authority":
            raise ValueError("transaction_receipt completion proof must come from transaction_authority")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "proof_type": self.proof_type,
            "proof_source": self.proof_source,
            "output_name": self.output_name,
        }


@dataclass(frozen=True)
class CapabilityIdempotencyContract:
    required: bool = False
    scope_fields: tuple[str, ...] = ()
    authority: str = "system"

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_fields", _unique(self.scope_fields, field="idempotency scope field"))
        object.__setattr__(self, "authority", _clean(self.authority, field="idempotency authority"))
        if self.required and not self.scope_fields:
            raise ValueError("required idempotency must declare scope_fields")

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "scope_fields": list(self.scope_fields),
            "authority": self.authority,
        }


@dataclass(frozen=True)
class CapabilityResourceConflictContract:
    mode: str = "none"
    key_fields: tuple[str, ...] = ()
    authority: str = "system"

    def __post_init__(self) -> None:
        if self.mode not in {"none", "serialize_by_key", "exclusive"}:
            raise ValueError(f"invalid resource conflict mode: {self.mode!r}")
        object.__setattr__(self, "key_fields", _unique(self.key_fields, field="resource conflict key"))
        object.__setattr__(self, "authority", _clean(self.authority, field="resource conflict authority"))
        if self.mode != "none" and not self.key_fields:
            raise ValueError("resource conflict mode requires key_fields")
        if self.mode == "none" and self.key_fields:
            raise ValueError("resource conflict key_fields require a non-none mode")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "key_fields": list(self.key_fields),
            "authority": self.authority,
        }


@dataclass(frozen=True)
class CapabilityPlanningContract:
    target: CapabilityTargetContract
    requires: tuple[CapabilityInputContract, ...]
    produces: tuple[CapabilityOutputContract, ...]
    preconditions: tuple[CapabilityPreconditionContract, ...]
    completion: CapabilityCompletionContract
    authorization: CapabilityAuthorizationContract = CapabilityAuthorizationContract()
    idempotency: CapabilityIdempotencyContract = CapabilityIdempotencyContract()
    resource_conflict: CapabilityResourceConflictContract = CapabilityResourceConflictContract()

    def validate(self, *, tool_name: str, execution_kind: str) -> None:
        input_names = [item.name for item in self.requires]
        if len(set(input_names)) != len(input_names):
            raise ValueError(f"capability {tool_name} has duplicate required input")
        output_names = [item.name for item in self.produces]
        if len(set(output_names)) != len(output_names):
            raise ValueError(f"capability {tool_name} has duplicate produced output")
        precondition_codes = [item.code for item in self.preconditions]
        if len(set(precondition_codes)) != len(precondition_codes):
            raise ValueError(f"capability {tool_name} has duplicate precondition")
        if not self.requires:
            raise ValueError(f"capability {tool_name} planning contract requires at least one input")
        if not self.produces:
            raise ValueError(f"capability {tool_name} planning contract requires at least one output")
        if not self.preconditions:
            raise ValueError(f"capability {tool_name} planning contract requires preconditions")
        if self.completion.mode == "tool_output":
            output = next((item for item in self.produces if item.name == self.completion.output_name), None)
            if output is None:
                raise ValueError(f"capability {tool_name} completion output is not declared")
            if not output.completion_proof:
                raise ValueError(f"capability {tool_name} tool_output completion must mark output as completion_proof")
            if output.type_name != self.completion.proof_type:
                raise ValueError(f"capability {tool_name} completion proof type does not match its declared output")
        if self.completion.mode == "transaction_receipt":
            if any(item.completion_proof for item in self.produces):
                raise ValueError(f"capability {tool_name} draft output cannot be final completion proof")
            if execution_kind != "action_draft":
                raise ValueError(f"capability {tool_name} transaction_receipt completion requires action_draft execution_kind")
            if not self.authorization.required:
                raise ValueError(f"capability {tool_name} transaction completion requires authorization")
            if not self.idempotency.required:
                raise ValueError(f"capability {tool_name} transaction completion requires idempotency")
            if self.resource_conflict.mode == "none":
                raise ValueError(f"capability {tool_name} transaction completion requires resource conflict policy")

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.as_dict(),
            "requires": [item.as_dict() for item in self.requires],
            "produces": [item.as_dict() for item in self.produces],
            "preconditions": [item.as_dict() for item in self.preconditions],
            "authorization": self.authorization.as_dict(),
            "completion": self.completion.as_dict(),
            "idempotency": self.idempotency.as_dict(),
            "resource_conflict": self.resource_conflict.as_dict(),
        }


@dataclass(frozen=True)
class ToolCapabilityContract:
    key: str
    tool_name: str
    category: str
    writes_business_data: bool
    evidence_sources: tuple[str, ...]
    planner_rule: str
    unavailable_response: str
    execution_kind: str = "grounding_read"
    goal_completion_types: tuple[str, ...] = ()
    goal_support_types: tuple[str, ...] = ()
    completion_effects: tuple[str, ...] = ()
    support_effects: tuple[str, ...] = ()
    # v2 effect identities are an immutable module-owned declaration.  Legacy
    # completion_effects remain readable for migration and diagnostics, but
    # they are never sufficient for a v2 compatibility proof.
    semantic_effects_v2: tuple[str, ...] = ()
    semantic_support_effects_v2: tuple[str, ...] = ()
    discovery_examples: tuple[str, ...] = ()
    exclusion_examples: tuple[str, ...] = ()
    contract_version: str = "1"
    planning_contract: CapabilityPlanningContract | None = None

    def __post_init__(self) -> None:
        if self.contract_version not in {"1", "2"}:
            raise ValueError(f"unsupported capability contract version: {self.contract_version!r}")
        if self.contract_version == "2":
            if self.planning_contract is None:
                raise ValueError(f"capability {self.tool_name} contract v2 requires a planning contract")
            self.planning_contract.validate(tool_name=self.tool_name, execution_kind=self.execution_kind)
            declared = tuple(str(value or "").strip() for value in self.semantic_effects_v2 if str(value or "").strip())
            if len(set(declared)) != len(declared):
                raise ValueError(f"capability {self.tool_name} has duplicate semantic_effects_v2")
            object.__setattr__(self, "semantic_effects_v2", declared)
            support_declared = tuple(
                str(value or "").strip()
                for value in self.semantic_support_effects_v2
                if str(value or "").strip()
            )
            if len(set(support_declared)) != len(support_declared):
                raise ValueError(f"capability {self.tool_name} has duplicate semantic_support_effects_v2")
            object.__setattr__(self, "semantic_support_effects_v2", support_declared)
        elif self.planning_contract is not None:
            raise ValueError(f"capability {self.tool_name} planning contract requires contract_version='2'")
        else:
            object.__setattr__(
                self,
                "semantic_effects_v2",
                tuple(str(value or "").strip() for value in self.semantic_effects_v2 if str(value or "").strip()),
            )
            object.__setattr__(
                self,
                "semantic_support_effects_v2",
                tuple(str(value or "").strip() for value in self.semantic_support_effects_v2 if str(value or "").strip()),
            )

    def planning_snapshot(self) -> dict[str, Any] | None:
        if self.planning_contract is None:
            return None
        return {
            "contract_version": self.contract_version,
            "capability_key": self.key,
            "tool_name": self.tool_name,
            "execution_kind": self.execution_kind,
            "completion_effects": list(self.completion_effects),
            "support_effects": list(self.support_effects),
            "semantic_effects_v2": list(self.semantic_effects_v2),
            "semantic_support_effects_v2": list(self.semantic_support_effects_v2),
            **self.planning_contract.as_dict(),
        }
