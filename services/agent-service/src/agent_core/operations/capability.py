from __future__ import annotations

"""Canonical execution contracts for registered business operations.

Tool capability answers *which entry point the model may invoke*.  Operation
capability answers *whether a concrete business action can be executed for a
resolved target set*.  Keeping the two contracts separate prevents prompt/tool
metadata from becoming a second action state machine.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Literal


TargetCardinality = Literal["exactly_one", "fan_out_many", "business_batch"]
InputBinding = Literal["per_item_required", "shared_allowed", "shared_required"]
AuthorityScope = Literal["per_item", "group"]
ExecutionMode = Literal["single", "fan_out", "business_batch"]
ResultShape = Literal["single", "per_item"]


@dataclass(frozen=True)
class OperationCapability:
    """Frozen execution semantics attached to exactly one ActionPlugin."""

    capability_id: str
    version: str
    target_resource_types: tuple[str, ...]
    target_cardinality: TargetCardinality
    min_targets: int
    max_targets: int | None
    input_binding: InputBinding
    authority_scope: AuthorityScope
    execution_mode: ExecutionMode
    result_shape: ResultShape
    supports_lifecycle_query: bool

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.version.strip():
            raise ValueError("operation capability requires id and version")
        if not self.target_resource_types:
            raise ValueError("operation capability requires at least one target resource type")
        if self.min_targets < 1:
            raise ValueError("min_targets must be >= 1")
        if self.max_targets is not None and self.max_targets < self.min_targets:
            raise ValueError("max_targets must be >= min_targets")
        if self.target_cardinality == "exactly_one":
            if self.min_targets != 1 or self.max_targets != 1:
                raise ValueError("exactly_one capability must require exactly one target")
            if self.execution_mode != "single" or self.result_shape != "single":
                raise ValueError("exactly_one capability must use single execution/result shape")

    def snapshot(self, *, input_schema: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
        """Return immutable Draft-time evidence of the execution contract."""
        schema = [dict(row) for row in input_schema if isinstance(row, dict)]
        input_schema_digest = sha256(
            json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        payload = {
            **asdict(self),
            "target_resource_types": list(self.target_resource_types),
            "input_schema_version": "action_plugin.input_schema@1",
            "input_schema_digest": input_schema_digest,
        }
        payload["digest"] = sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return payload


def single_target_operation_capability(*, action_id: str, target_type: str) -> OperationCapability:
    """Generic baseline for a module-declared one-target write action."""
    return OperationCapability(
        capability_id=f"operation.{action_id}",
        version="1",
        target_resource_types=(str(target_type),),
        target_cardinality="exactly_one",
        min_targets=1,
        max_targets=1,
        input_binding="per_item_required",
        authority_scope="per_item",
        execution_mode="single",
        result_shape="single",
        supports_lifecycle_query=True,
    )


def capability_digest(snapshot: dict[str, Any] | None) -> str:
    source = dict(snapshot or {})
    digest = str(source.get("digest") or "").strip()
    if digest:
        return digest
    return sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
