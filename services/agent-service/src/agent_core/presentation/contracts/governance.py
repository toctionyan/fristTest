"""Core-neutral governance for versioned customer presentation contracts.

The generic runtime never decides domain field names.  Domain overlays declare
what a customer must be able to see, while this module verifies that one
canonical projection reaches an approved renderer without field guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ProjectionContractViolation:
    contract_id: str
    version: int | None
    producer: str
    consumer: str
    projection_boundary: str
    missing_required_semantics: tuple[str, ...]
    degradation_level: str
    trace_id: str | None = None
    code: str = "projection_contract_violation"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "contract_id": self.contract_id,
            "version": self.version,
            "producer": self.producer,
            "consumer": self.consumer,
            "projection_boundary": self.projection_boundary,
            "missing_required_semantics": list(self.missing_required_semantics),
            "degradation_level": self.degradation_level,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class ContractValidationResult:
    valid: bool
    violations: tuple[ProjectionContractViolation, ...] = ()


class PresentationContractRegistry:
    """Registry of domain manifests.

    ``validate`` remains deliberately reusable for lightweight callers.  The
    stricter rule that every *formal structured result* must declare a contract
    belongs to :class:`StructuredResultReleaseGate`, not to text-only callers.
    """

    def __init__(self, manifests: Iterable[dict[str, Any]] | None = None) -> None:
        self._manifests: dict[str, dict[str, Any]] = {}
        for manifest in manifests or ():
            self.register(manifest)

    def register(self, manifest: dict[str, Any]) -> None:
        contract_id = str(manifest.get("contract_id") or "").strip()
        if not contract_id:
            raise ValueError("presentation contract manifest requires contract_id")
        if contract_id in self._manifests:
            existing = self._manifests[contract_id]
            if existing != manifest:
                raise ValueError(f"conflicting presentation contract manifest: {contract_id}")
            return
        self._manifests[contract_id] = dict(manifest)

    def manifest(self, contract_id: str) -> dict[str, Any] | None:
        value = self._manifests.get(str(contract_id or ""))
        return dict(value) if isinstance(value, dict) else None

    def has(self, contract_id: str) -> bool:
        return str(contract_id or "") in self._manifests

    def manifests(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(value) for value in self._manifests.values())

    def validate(
        self,
        block: dict[str, Any],
        *,
        consumer: str,
        trace_id: str | None = None,
        require_contract: bool = False,
    ) -> ContractValidationResult:
        contract_id = str(block.get("contract_id") or "").strip()
        if not contract_id:
            if not require_contract:
                return ContractValidationResult(valid=True)
            return ContractValidationResult(
                valid=False,
                violations=(
                    _violation(
                        contract_id="unknown_contract",
                        block=block,
                        consumer=consumer,
                        trace_id=trace_id,
                        semantics=("registered_presentation_contract",),
                    ),
                ),
            )
        manifest = self._manifests.get(contract_id)
        if manifest is None:
            return ContractValidationResult(
                valid=False,
                violations=(
                    _violation(
                        contract_id=contract_id,
                        block=block,
                        consumer=consumer,
                        trace_id=trace_id,
                        semantics=("registered_presentation_contract",),
                    ),
                ),
            )
        return validate_block_against_manifest(block, manifest, consumer=consumer, trace_id=trace_id)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _path_value(value: Any, path: str) -> Any:
    current = value
    for segment in str(path).split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _field_map(payload: dict[str, Any], key: str) -> dict[str, str]:
    values = payload.get(key) if isinstance(payload.get(key), dict) else {}
    return {str(field): str(semantic) for field, semantic in values.items() if str(field) and str(semantic)}


def _degradation_semantics(block: dict[str, Any]) -> set[str]:
    degradation = block.get("degradation") if isinstance(block.get("degradation"), dict) else {}
    values = degradation.get("missing_optional_semantics")
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value)}


def _violation(
    *,
    contract_id: str,
    block: dict[str, Any],
    consumer: str,
    trace_id: str | None,
    semantics: tuple[str, ...] | list[str],
    version: int | None = None,
) -> ProjectionContractViolation:
    return ProjectionContractViolation(
        contract_id=contract_id or str(block.get("contract_id") or "unknown_contract"),
        version=version if version is not None else _as_int(block.get("contract_version")),
        producer=str(block.get("producer") or "unknown_producer"),
        consumer=consumer,
        projection_boundary=str(block.get("projection_boundary") or "unknown_boundary"),
        missing_required_semantics=tuple(sorted(set(str(value) for value in semantics if str(value)))),
        degradation_level="controlled_error",
        trace_id=trace_id,
    )


def _validate_collection(
    *,
    block: dict[str, Any],
    required_fields: dict[str, str],
    optional_fields: dict[str, str],
    collection_key: str,
    shape_semantic: str,
    missing_required: list[str],
    missing_optional: set[str],
) -> None:
    if not required_fields and not optional_fields:
        return
    values = block.get(collection_key)
    if not isinstance(values, list):
        missing_required.append(shape_semantic)
        return
    for item in values:
        if not isinstance(item, dict):
            missing_required.append(f"{shape_semantic}_item")
            continue
        for field, semantic in required_fields.items():
            if _missing(_path_value(item, field)):
                missing_required.append(semantic)
        for field, semantic in optional_fields.items():
            if _missing(_path_value(item, field)):
                missing_optional.add(semantic)


def validate_block_against_manifest(
    block: dict[str, Any],
    manifest: dict[str, Any],
    *,
    consumer: str,
    trace_id: str | None = None,
) -> ContractValidationResult:
    """Validate domain-declared block semantics without hard-coding domain fields."""

    contract_id = str(manifest.get("contract_id") or "")
    version = _as_int(manifest.get("version"))
    payload = manifest.get("payload") if isinstance(manifest.get("payload"), dict) else {}
    problems: list[str] = []

    if str(block.get("contract_id") or "") != contract_id:
        problems.append("contract_identity")
    if _as_int(block.get("contract_version")) != version:
        problems.append("contract_version")
    if str(block.get("contract_owner") or "") != str(manifest.get("contract_owner") or ""):
        problems.append("contract_owner")
    if str(block.get("projection_boundary") or "") != str(manifest.get("projection_boundary") or ""):
        problems.append("projection_boundary")
    expected_block_type = str(payload.get("block_type") or "")
    if expected_block_type and str(block.get("type") or "") != expected_block_type:
        problems.append("block_type")

    missing_required: list[str] = list(problems)
    missing_optional: set[str] = set()

    for field, semantic in _field_map(payload, "block_required_fields").items():
        if _missing(_path_value(block, field)):
            missing_required.append(semantic)
    for field, semantic in _field_map(payload, "block_optional_fields").items():
        if _missing(_path_value(block, field)):
            missing_optional.add(semantic)

    _validate_collection(
        block=block,
        required_fields=_field_map(payload, "item_required_fields"),
        optional_fields=_field_map(payload, "item_optional_fields"),
        collection_key="items",
        shape_semantic="result_items",
        missing_required=missing_required,
        missing_optional=missing_optional,
    )
    _validate_collection(
        block=block,
        required_fields=_field_map(payload, "action_required_fields"),
        optional_fields=_field_map(payload, "action_optional_fields"),
        collection_key="actions",
        shape_semantic="result_actions",
        missing_required=missing_required,
        missing_optional=missing_optional,
    )

    if missing_optional and not missing_optional.issubset(_degradation_semantics(block)):
        missing_required.append("declared_optional_degradation")

    if missing_required:
        return ContractValidationResult(
            valid=False,
            violations=(
                _violation(
                    contract_id=contract_id,
                    block=block,
                    consumer=consumer,
                    trace_id=trace_id,
                    semantics=missing_required,
                    version=version,
                ),
            ),
        )
    return ContractValidationResult(valid=True)


def controlled_violation_block(violation: ProjectionContractViolation) -> dict[str, Any]:
    """Customer-safe failure block; never leak a partial structured result."""
    return {
        "type": "projection_contract_violation",
        "role": "primary",
        "priority": 1000,
        "tone": "warning",
        "title": "结果暂时无法完整展示",
        "content": "系统已获取到相关结果，但缺少必要的展示信息；为避免误导，未展示不完整内容。请刷新后重试或到对应业务中心查看。",
        "contract_violation": violation.as_dict(),
    }
