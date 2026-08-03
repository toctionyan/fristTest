"""Capability gate and runtime-owned execution permits.

The model may propose a tool and arguments, but it never self-certifies that a
nearby capability is acceptable.  This module checks the declared tool
contract and JSON-schema shape, produces an auditable MatchProof, and issues a
short-lived ExecutionPermit.  Tool dispatchers must reject calls without a
valid permit.

This module intentionally knows nothing about ecommerce nouns.  Domain schemas
and capability contracts are supplied by the registered skill overlay.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any
from agent_core.ledger import execution_scope_for_state
from uuid import uuid4

from agent_core.kernel.capability import ToolCapabilityContract
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.runtime.semantic_capability_verifier import verify_candidate_semantics
from agent_core.runtime.capability_effects import (
    completion_effects_for_contract,
    goal_effect_match_proof,
    support_effects_for_contract,
)
from agent_core.kernel.semantic_contract import semantic_goals
from agent_core.context.visible_result_refs import validate_runtime_result_ref, visible_result_refs_from_ledger


CAPABILITY_REGISTRY_VERSION = "customer-agent-capability-gate@3.7"


@dataclass(frozen=True)
class PermitDecision:
    permitted: bool
    match_proof: dict[str, Any]
    execution_permit: dict[str, Any] | None
    rejection: dict[str, Any] | None = None
    normalized_arguments: dict[str, Any] | None = None


def _function_schema(tool_name: str, capability_registry: CapabilityRegistry) -> dict[str, Any] | None:
    """Read a schema only from the injected generic registry.

    Generic Gate must not import a concrete Overlay; Composition Root has
    already checked schema/contract/dispatcher closure during registration.
    """
    return capability_registry.function_schema(tool_name)


def _validate_value(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Small deterministic JSON-schema subset used by function-call tools.

    The model provider validates schema on many paths, but the runtime checks
    the same decisive subset again so direct API calls, stale tool definitions
    and malformed checkpoints cannot bypass the exact-capability boundary.
    """
    errors: list[str] = []
    if "const" in schema and value != schema.get("const"):
        return [f"{path}: const_mismatch"]
    if "enum" in schema and value not in list(schema.get("enum") or []):
        errors.append(f"{path}: enum_mismatch")
        return errors
    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list):
        matches = [
            candidate
            for candidate in alternatives
            if isinstance(candidate, dict) and not _validate_value(value, candidate, path)
        ]
        if len(matches) != 1:
            return [f"{path}: one_of_{'ambiguous' if len(matches) > 1 else 'mismatch'}"]
    typ = str(schema.get("type") or "")
    if typ == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected_object"]
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = [str(item) for item in schema.get("required") or []]
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: undeclared")
        for key, nested in properties.items():
            if key in value and isinstance(nested, dict):
                errors.extend(_validate_value(value[key], nested, f"{path}.{key}"))
    elif typ == "array":
        if not isinstance(value, list):
            return [f"{path}: expected_array"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_value(item, item_schema, f"{path}[{index}]"))
        if isinstance(schema.get("minItems"), int) and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: min_items")
        if isinstance(schema.get("maxItems"), int) and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: max_items")
        if schema.get("uniqueItems") is True and len({json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value}) != len(value):
            errors.append(f"{path}: unique_items")
    elif typ == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected_string")
        else:
            if isinstance(schema.get("minLength"), int) and len(value) < int(schema["minLength"]):
                errors.append(f"{path}: min_length")
            if isinstance(schema.get("maxLength"), int) and len(value) > int(schema["maxLength"]):
                errors.append(f"{path}: max_length")
    elif typ == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{path}: expected_integer")
        else:
            if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
                errors.append(f"{path}: minimum")
            if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
                errors.append(f"{path}: maximum")
    return errors


def validate_tool_arguments(tool_name: str, args: dict[str, Any], *, capability_registry: CapabilityRegistry) -> list[str]:
    schema = _function_schema(tool_name, capability_registry)
    if schema is None:
        return ["tool_schema_not_registered"]
    return _validate_value(dict(args or {}), schema)


def _argument_leaf_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    rows: dict[str, Any] = {}
    if not isinstance(value, dict):
        return rows
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            rows.update(_argument_leaf_paths(item, path))
        elif key != "constraint_bindings":
            rows[path] = item
    return rows


def normalize_tool_arguments(args: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair only unambiguous structural aliases before schema validation.

    The normalizer never invents a value or interprets business language.  It
    can repair a constraint binding such as ``delivery_status`` when exactly
    one supplied formal argument ends with that leaf
    (``query.delivery_status``). Ambiguous aliases remain unchanged and fail
    the existing parameterization proof.
    """
    normalized = deepcopy(dict(args or {}))
    target = normalized.get("target")
    transformations: list[dict[str, Any]] = []
    if (
        isinstance(target, dict)
        and str(target.get("mode") or "") == "set_operation"
        and str(target.get("operator") or "") == "filter"
        and not str(target.get("left_handle") or "").strip()
        and str(target.get("status") or "").strip()
        and str(target.get("status_span") or "").strip()
    ):
        # ``filter`` is a derived-set operation only when it has a verified
        # left ResultRef.  With no left operand, the same supplied status
        # predicate unambiguously means a root-population query.  Re-express
        # that structure as all_orders without inventing a value or selecting
        # an entity.  CapabilityGate still verifies the literal status span
        # and exact semantics before dispatch.
        normalized["target"] = {
            "mode": "all_orders",
            "status": target["status"],
            "status_span": target["status_span"],
        }
        transformations.append({
            "kind": "anchorless_root_filter",
            "from": "target.set_operation/filter",
            "to": "target.all_orders+status",
            "reason": "filter_has_no_left_result_ref_and_reuses_only_supplied_values",
        })
    leaves = _argument_leaf_paths(normalized)
    bindings = normalized.get("constraint_bindings")
    if isinstance(bindings, list):
        next_bindings: list[Any] = []
        for raw in bindings:
            if not isinstance(raw, dict):
                next_bindings.append(raw)
                continue
            binding = dict(raw)
            original = str(binding.get("parameter_path") or "").strip()
            if original and _path_value(normalized, original) in (None, ""):
                leaf = original.rsplit(".", 1)[-1]
                matches = [
                    path for path, value in leaves.items()
                    if path.rsplit(".", 1)[-1] == leaf and value not in (None, "")
                ]
                if len(matches) == 1:
                    binding["parameter_path"] = matches[0]
                    transformations.append({
                        "kind": "constraint_parameter_path",
                        "from": original,
                        "to": matches[0],
                        "reason": "unique_supplied_argument_leaf",
                    })
            next_bindings.append(binding)
        normalized["constraint_bindings"] = next_bindings
    return normalized, {
        "version": "argument-normalization@1",
        "changed": bool(transformations),
        "transformations": transformations,
        "value_invention_allowed": False,
    }


def _requested_effect(contract: ToolCapabilityContract, args: dict[str, Any]) -> str:
    """Record the model's declared effect without reinterpreting user text."""
    qualifier = str(args.get("action") or args.get("capability") or "").strip()
    return f"{contract.key}:{qualifier}" if qualifier else contract.key


def _target_cardinality_hint(args: dict[str, Any]) -> str:
    """Retain the candidate's target shape for WorkflowPlan classification.

    This is orchestration metadata only: it neither resolves a target nor
    grants a capability.  The later MatchProof/ExecutionPermit remains the
    sole authority for a concrete resource.  Persisting the hint matters
    because a later terminal model call replaces raw ``tool_calls`` while the
    WorkflowPlan still needs to distinguish a batch attempt from an ordinal
    selection out of a visible collection.
    """
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    mode = str(target.get("mode") or "")
    expected_shape = str(args.get("expected_shape") or "")
    if mode == "artifact":
        return "single"
    if mode == "set_operation":
        operator = str(target.get("operator") or "")
        if operator == "ordinal":
            return "single"
        try:
            limit = int(target.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        if operator == "take" and limit == 1:
            return "single"
        return "collection"
    if mode == "collection" or expected_shape == "collection":
        return "collection"
    return "unknown"


def _scope(state: dict[str, Any]) -> dict[str, str]:
    return execution_scope_for_state(state)


def _path_value(value: Any, path: str) -> Any:
    current = value
    for segment in str(path or "").split("."):
        if not segment or not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _canonical_digest(value: dict[str, Any]) -> str:
    return sha256(json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _parameterization_proof(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Verify declared decisive constraints bind to actual formal arguments.

    The generic gate does not interpret business phrases.  A domain schema may
    expose finite typed parameters and the planner records only conditions that
    affect execution/result scope.  Every declared binding must point to an
    actual argument and prove its source span belongs to the current turn.
    """
    bindings = args.get("constraint_bindings")
    if not isinstance(bindings, list):
        bindings = []
    user_text = str(state.get("current_user_input") or "")
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(bindings):
        if not isinstance(raw, dict):
            errors.append(f"constraint_bindings[{index}]: invalid")
            continue
        path = str(raw.get("parameter_path") or "").strip()
        span = str(raw.get("source_span") or "").strip()
        actual = _path_value(args, path)
        expected = raw.get("normalized_value", actual)
        status = "covered"
        if not path or actual is None or actual == "":
            status = "uncovered"
            errors.append(f"constraint_binding_missing_parameter:{path or index}")
        elif not span or span not in user_text:
            status = "uncovered"
            errors.append(f"constraint_binding_evidence_not_current_turn:{path}")
        elif "normalized_value" in raw and expected != actual:
            status = "uncovered"
            errors.append(f"constraint_binding_value_mismatch:{path}")
        rows.append({
            "kind": str(raw.get("kind") or "condition"),
            "source_span": span,
            "parameter_path": path,
            "normalized_value": expected,
            "actual_value": actual,
            "status": status,
        })
    # Certain domain formal query objects carry a paired evidence field.  This
    # is schema-owned, not a keyword map: if a non-span leaf is set, it must
    # have a matching declared constraint binding.
    query = args.get("query") if isinstance(args.get("query"), dict) else {}
    for field, value in query.items():
        if field.endswith("_span") or value in (None, ""):
            continue
        path = f"query.{field}"
        if not any(row.get("parameter_path") == path and row.get("status") == "covered" for row in rows):
            errors.append(f"parameterized_query_missing_constraint_binding:{path}")
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    if str(target.get("mode") or "") == "set_operation" and str(target.get("operator") or "") == "sort":
        span = str(target.get("sort_span") or "").strip()
        status = "covered" if span and span in user_text else "uncovered"
        if status != "covered":
            errors.append("sort_parameter_evidence_not_current_turn")
        for field in ("sort_field", "sort_direction"):
            rows.append({
                "kind": "condition",
                "source_span": span,
                "parameter_path": f"target.{field}",
                "normalized_value": target.get(field),
                "actual_value": target.get(field),
                "status": status,
            })
    return {
        "version": "constraint-coverage-proof@1",
        "bindings": rows,
        "parameterization_complete": not errors,
        "errors": errors,
    }


def _visible_reference_proof(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Validate only explicit references carried by the formal target schema.

    Entity matching and all-orders are fresh business queries rather than
    historical references, so they do not enter this check.  For collection,
    artifact and set expressions, every supplied handle must be either a
    customer-visible historical ResultRef or a permit-backed observation
    produced earlier in the current turn, with matching scope and shape.
    """
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    mode = str(target.get("mode") or "")
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    fields: list[tuple[str, str | None]] = []
    if mode == "artifact":
        fields.append(("left_handle", "one"))
    elif mode == "collection":
        fields.append(("left_handle", "collection"))
    elif mode == "set_operation":
        fields.append(("left_handle", "collection"))
        if str(target.get("operator") or "") in {"difference", "union", "intersection"}:
            # Set algebra accepts either a collection or one verified artifact
            # on the right; a single artifact is a one-element set.
            fields.append(("right_handle", None))
    for field, expected_shape in fields:
        value = str(target.get(field) or "").strip()
        ref, error = validate_runtime_result_ref(state=state, result_ref=value, expected_shape=expected_shape) if value else (None, "runtime_result_ref_missing")
        checks.append({
            "parameter_path": f"target.{field}",
            "result_ref": value or None,
            "expected_shape": expected_shape,
            "valid": error is None,
            "reason_code": error,
            "validated_ref": ref,
        })
        if error:
            errors.append(f"visible_result_ref:{field}:{error}")
    visible_refs = visible_result_refs_from_ledger(
        state.get("artifact_ledger") or [],
        state=state,
        limit=12,
    )
    latest_handles = {
        str(ref.get("result_ref") or "")
        for ref in visible_refs
        if bool(ref.get("is_latest_visible_turn")) and str(ref.get("result_ref") or "")
    }
    latest_member_handles = {
        str(member)
        for ref in visible_refs
        if bool(ref.get("is_latest_visible_turn"))
        for member in list(ref.get("member_handles") or [])
        if str(member)
    }
    binding = args.get("context_binding") if isinstance(args.get("context_binding"), dict) else {}
    binding_kind = str(binding.get("reference_kind") or "")
    binding_span = str(binding.get("source_span") or "").strip()
    try:
        binding_group_size = int(binding.get("group_size") or 0)
    except (TypeError, ValueError):
        binding_group_size = 0
    user_text = str(state.get("current_user_input") or "")
    selected_latest_lineage = {
        str(lineage_handle)
        for ref in visible_refs
        if str(ref.get("result_ref") or "") in latest_handles
        for lineage_handle in list(ref.get("lineage_result_refs") or [])
        if str(lineage_handle)
    }
    older_visible_handles: list[str] = []
    structurally_returned_handles: list[str] = []
    visible_rank_by_handle = {
        str(ref.get("result_ref") or ""): int(ref.get("discourse_recency_rank") or 0)
        for ref in visible_refs
        if str(ref.get("result_ref") or "") and int(ref.get("discourse_recency_rank") or 0) > 0
    }
    selected_visible_handles = {
        str(check.get("result_ref") or "")
        for check in checks
        if isinstance(check, dict)
        and isinstance(check.get("validated_ref"), dict)
        and str((check.get("validated_ref") or {}).get("reference_kind") or "") != "current_turn_verified_observation"
        and str(check.get("result_ref") or "") in visible_rank_by_handle
    }
    selected_ranks = {
        visible_rank_by_handle[handle]
        for handle in selected_visible_handles
        if handle in visible_rank_by_handle
    }
    max_selected_rank = max(selected_ranks, default=0)
    required_prefix_handles = {
        handle
        for handle, rank in visible_rank_by_handle.items()
        if max_selected_rank and rank <= max_selected_rank
    }
    group_source_span = (
        binding_span
        if binding_kind == "explicit_group_reference"
        else str(args.get("reference_span") or "").strip()
    )
    explicit_group_binding = bool(
        binding_kind in {"", "explicit_group_reference"}
        and group_source_span
        and group_source_span in user_text
        and mode == "set_operation"
        and str(target.get("operator") or "") in {"union", "intersection", "difference"}
        and len(selected_visible_handles) >= 2
        and bool(selected_visible_handles.intersection(latest_handles))
        and selected_visible_handles == required_prefix_handles
        and (
            binding_kind != "explicit_group_reference"
            or binding_group_size == len(selected_visible_handles)
        )
    )
    if binding_kind == "explicit_group_reference":
        if binding_group_size < 2:
            errors.append("explicit_group_reference_group_size_required")
        elif binding_group_size != len(selected_visible_handles):
            errors.append("explicit_group_reference_group_size_mismatch")
        if not explicit_group_binding:
            errors.append("explicit_group_reference_must_select_recent_contiguous_visible_group")

    selected_latest_handles = selected_visible_handles.intersection(latest_handles)
    if (
        len(latest_handles) > 1
        and mode in {"collection", "artifact"}
        and len(selected_latest_handles) == 1
        and binding_kind != "explicit_return"
    ):
        # Several independent results crossed the release boundary in the same
        # latest turn.  A bare singular continuation cannot choose one of them.
        # The model must either bind a literal label to one exact result/member
        # or explicitly compose the recent group with a set operation.
        errors.append("latest_visible_scope_ambiguous_requires_explicit_return_or_group")

    if binding_kind == "explicit_return" and selected_visible_handles:
        if len(selected_visible_handles) != 1:
            errors.append("explicit_return_must_select_exactly_one_visible_result")
        selected_handle = next(iter(selected_visible_handles), "")
        selected_ref = next(
            (
                ref for ref in visible_refs
                if str(ref.get("result_ref") or "") == selected_handle
            ),
            {},
        )
        labels = [
            str(value) for value in list(selected_ref.get("member_labels") or [])
            if str(value)
        ]
        own_label = str(selected_ref.get("label") or "").strip()
        if own_label:
            labels.append(own_label)
        binding_key = _literal_key(binding_span)
        label_keys = [_literal_key(label) for label in labels]
        if not binding_span or binding_span not in user_text:
            errors.append("explicit_return_binding_evidence_not_current_turn")
        if not binding_key or not any(
            binding_key in label_key or label_key in binding_key
            for label_key in label_keys
            if label_key
        ):
            errors.append("explicit_return_binding_not_literal_member_label")
    for check in checks:
        ref = check.get("validated_ref") if isinstance(check.get("validated_ref"), dict) else {}
        handle = str(check.get("result_ref") or "")
        if not handle or not ref or str(ref.get("reference_kind") or "") == "current_turn_verified_observation":
            continue
        if handle in latest_handles or handle in latest_member_handles:
            continue
        # When the same typed set expression also selects a latest visible
        # derived result, consuming its recorded source collection is a
        # lineage operation, not an implicit jump to an unrelated old topic.
        if handle in selected_latest_lineage:
            continue
        operation = ref.get("source_operation") if isinstance(ref.get("source_operation"), dict) else {}
        if any(_literal_operation_in_text(value, user_text) for value in operation.values()):
            # The user explicitly repeated a typed condition that produced
            # this older visible collection (for example 已签收/签收). This is
            # a deterministic structural return, not a model-inferred topic.
            structurally_returned_handles.append(handle)
            continue
        older_visible_handles.append(handle)
        if explicit_group_binding:
            continue
        if binding_kind != "explicit_return":
            errors.append("older_visible_result_requires_explicit_return_binding")
        if not binding_span or binding_span not in user_text:
            errors.append("explicit_return_binding_evidence_not_current_turn")
        reference_labels = [str(value) for value in list(ref.get("member_labels") or []) if str(value)]
        own_label = str(ref.get("label") or "").strip()
        if own_label:
            reference_labels.append(own_label)
        binding_key = _literal_key(binding_span)
        label_keys = [_literal_key(label) for label in reference_labels]
        if not binding_key or not any(
            binding_key in label_key or label_key in binding_key
            for label_key in label_keys
            if label_key
        ):
            errors.append("explicit_return_binding_not_literal_member_label")
    return {
        "version": "runtime-result-ref-proof@4",
        "checks": checks,
        "discourse_binding": {
            "latest_visible_result_refs": sorted(latest_handles),
            "latest_visible_member_refs": sorted(latest_member_handles),
            "selected_latest_lineage_result_refs": sorted(selected_latest_lineage),
            "selected_structural_return_result_refs": structurally_returned_handles,
            "selected_older_visible_result_refs": older_visible_handles,
            "reference_kind": binding_kind or None,
            "source_span": binding_span or None,
            "group_size": binding_group_size or None,
            "latest_visible_result_count": len(latest_handles),
            "latest_visible_scope_ambiguous": len(latest_handles) > 1,
            "group_source_span": group_source_span if explicit_group_binding else None,
            "explicit_group_binding_complete": explicit_group_binding,
        },
        "complete": not errors,
        "errors": errors,
    }


def _literal_key(value: Any) -> str:
    """Normalize only typography; never translate or infer a business alias."""
    return "".join(re.findall(r"[0-9A-Za-z\u3400-\u9fff]+", str(value or "").casefold()))


def _literal_operation_in_text(operation_value: Any, user_text: Any) -> bool:
    """Match a typed operation value without synonyms or domain aliases."""
    operation = _literal_key(operation_value)
    current = _literal_key(user_text)
    if len(operation) < 2 or not current:
        return False
    if operation in current or current in operation:
        return True
    # Natural Chinese references commonly omit a one-character aspect/state
    # prefix (已签收 -> 签收). Accept only a large contiguous literal fragment;
    # no translation, stemming or business vocabulary is introduced.
    minimum = max(2, (len(operation) * 2 + 2) // 3)
    return any(
        operation[start : start + minimum] in current
        for start in range(0, len(operation) - minimum + 1)
    )


def _member_label_aliases(label: str) -> set[str]:
    """Return structural aliases already present in one rendered label.

    A presentation label commonly has a primary label followed by bracketed
    identifying text.  Splitting those existing pieces is deterministic
    projection handling, not domain interpretation.  Long numeric identifiers
    are retained because users often copy only that visible identifier.
    """
    raw = str(label or "").strip()
    aliases = {_literal_key(raw)}
    primary = re.split(r"[（(【\[]", raw, maxsplit=1)[0]
    aliases.add(_literal_key(primary))
    for bracketed in re.findall(r"[（(【\[]([^）)】\]]+)[）)】\]]", raw):
        aliases.add(_literal_key(bracketed))
    aliases.update(re.findall(r"\d{3,}", _literal_key(raw)))
    return {alias for alias in aliases if len(alias) >= 2}


def _explicit_member_scope_proof(
    state: dict[str, Any],
    args: dict[str, Any],
    visible_reference: dict[str, Any],
) -> dict[str, Any]:
    """Reject any target that contradicts one uniquely named visible member.

    The model remains responsible for selecting an opaque target.  Runtime
    only checks a contradiction between that proposed target and literal,
    customer-visible labels: a collection with multiple members cannot stand
    in for exactly one named member.  It does not choose the replacement.
    """
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    target_mode = str(target.get("mode") or "")
    if target_mode == "entity_match":
        return {
            "version": "explicit-member-scope-proof@2",
            "complete": True,
            "applies": False,
            "errors": [],
        }

    binding = args.get("context_binding") if isinstance(args.get("context_binding"), dict) else {}
    literal_spans = list(dict.fromkeys(
        str(value).strip()
        for value in (
            target.get("attribute_span"), args.get("reference_span"),
            binding.get("source_span"),
        )
        if str(value or "").strip()
    ))

    # Index every member that has actually crossed the customer-visible
    # boundary.  Repeated appearances of the same opaque member are folded;
    # Runtime never infers a business alias that was not rendered.
    visible_members: dict[str, set[str]] = {}
    visible_labels: dict[str, str] = {}
    for ref in visible_result_refs_from_ledger(
        state.get("artifact_ledger") or [], state=state, limit=12,
    ):
        members = [str(value) for value in list(ref.get("member_handles") or []) if str(value)]
        labels = [str(value) for value in list(ref.get("member_labels") or []) if str(value)]
        for index, handle in enumerate(members):
            label = labels[index] if index < len(labels) else ""
            if not label:
                continue
            visible_members.setdefault(handle, set()).update(_member_label_aliases(label))
            visible_labels.setdefault(handle, label)

    named_handles: set[str] = set()
    matches: list[dict[str, Any]] = []
    for span in literal_spans:
        key = _literal_key(span)
        if len(key) < 2:
            continue
        literal_aliases = {key}
        # Chinese short labels often add a neutral nominal suffix (杯子/桌子)
        # that is absent from the rendered product label.  Removing only that
        # final typography-level suffix mirrors the module entity resolver; it
        # does not translate or introduce a business synonym.
        if len(key) >= 2 and key.endswith(("子", "儿")):
            literal_aliases.add(key[:-1])
        handles = [
            handle for handle, aliases in visible_members.items()
            if any(
                alias in literal or literal in alias
                for alias in aliases
                for literal in literal_aliases
                if literal
            )
        ]
        named_handles.update(handles)
        if handles:
            matches.append({
                "source_span": span,
                "matched_member_handles": handles,
                "matched_member_labels": [visible_labels.get(handle, handle) for handle in handles],
            })

    if len(named_handles) != 1:
        return {
            "version": "explicit-member-scope-proof@2",
            "complete": True,
            "applies": bool(matches),
            "literal_matches": matches,
            "errors": [],
        }

    named_handle = next(iter(named_handles))
    right_ref = next((
        check.get("validated_ref")
        for check in list(visible_reference.get("checks") or [])
        if isinstance(check, dict)
        and str(check.get("parameter_path") or "") == "target.right_handle"
        and isinstance(check.get("validated_ref"), dict)
    ), None)
    right_members = {
        str(value) for value in list((right_ref or {}).get("member_handles") or []) if str(value)
    }
    # ``difference(parent, named_member)`` is a typed exclusion, not an
    # attempt to use the broad parent as the uniquely named output target.
    if (
        target_mode == "set_operation"
        and str(target.get("operator") or "") == "difference"
        and right_members == {named_handle}
    ):
        return {
            "version": "explicit-member-scope-proof@2",
            "complete": True,
            "applies": False,
            "literal_matches": matches,
            "named_member_handle": named_handle,
            "typed_exclusion": True,
            "errors": [],
        }
    selected_handles = {
        str(member)
        for check in list(visible_reference.get("checks") or [])
        if isinstance(check, dict)
        for ref in [check.get("validated_ref") if isinstance(check.get("validated_ref"), dict) else {}]
        for member in list(ref.get("member_handles") or [])
        if str(member)
    }
    selected_ref = next((
        check.get("validated_ref")
        for check in list(visible_reference.get("checks") or [])
        if isinstance(check, dict) and isinstance(check.get("validated_ref"), dict)
        and named_handle in {str(value) for value in list(check["validated_ref"].get("member_handles") or [])}
    ), None)
    selected_ref_members = [
        str(value) for value in list((selected_ref or {}).get("member_handles") or []) if str(value)
    ]
    matched_indexes = [
        index for index, handle in enumerate(selected_ref_members) if handle == named_handle
    ]
    # A fresh all-orders candidate has no selected opaque handle and is still
    # a contradiction when the current literal uniquely names a visible
    # member.  entity_match was returned above because its business resolver
    # independently validates the literal and cardinality.
    mismatch = target_mode == "all_orders" or not selected_handles or selected_handles != {named_handle}
    error = (
        "explicit_unique_member_target_mismatch"
        if selected_handles and named_handle not in selected_handles
        else "explicit_unique_member_requires_single_member_target"
    )
    return {
        "version": "explicit-member-scope-proof@2",
        "complete": not mismatch,
        "applies": mismatch,
        "target_mode": target_mode,
        "collection_ref": str((selected_ref or {}).get("result_ref") or "") or None,
        "collection_member_count": len(selected_ref_members),
        "literal_matches": matches,
        "matched_member_indexes": matched_indexes,
        "named_member_handle": named_handle,
        "named_member_label": visible_labels.get(named_handle, named_handle),
        "selected_member_handles": sorted(selected_handles),
        "errors": [error] if mismatch else [],
    }


def _derived_collection_scope_proof(
    args: dict[str, Any],
    visible_reference: dict[str, Any],
) -> dict[str, Any]:
    """Reject re-ranking a singleton produced from a larger parent set.

    A visible singleton is normally a perfectly valid continuation scope.  It
    is not, however, a valid comparison population when the current call asks
    to sort it again and the ledger proves that singleton came from a prior
    sort/take/ordinal operation.  The model must explicitly consume a recorded
    lineage parent; Runtime still does not choose which parent.
    """
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    if str(target.get("mode") or "") != "set_operation" or str(target.get("operator") or "") != "sort":
        return {
            "version": "derived-collection-scope-proof@1",
            "complete": True,
            "applies": False,
            "errors": [],
        }
    left_handle = str(target.get("left_handle") or "")
    selected_ref = next((
        check.get("validated_ref")
        for check in list(visible_reference.get("checks") or [])
        if isinstance(check, dict)
        and str(check.get("result_ref") or "") == left_handle
        and isinstance(check.get("validated_ref"), dict)
    ), None)
    if not isinstance(selected_ref, dict):
        return {
            "version": "derived-collection-scope-proof@1",
            "complete": True,
            "applies": False,
            "errors": [],
        }
    members = [str(value) for value in list(selected_ref.get("member_handles") or []) if str(value)]
    lineage = [str(value) for value in list(selected_ref.get("lineage_result_refs") or []) if str(value)]
    source = dict(selected_ref.get("source_operation") or {})
    derived_selection = str(source.get("operator") or "") in {"sort", "take", "ordinal"}
    invalid = len(members) == 1 and bool(lineage) and derived_selection
    return {
        "version": "derived-collection-scope-proof@1",
        "complete": not invalid,
        "applies": invalid,
        "selected_result_ref": left_handle,
        "selected_member_count": len(members),
        "source_operation": source,
        "lineage_result_refs": lineage,
        "requested_operation": {
            key: target.get(key)
            for key in ("operator", "sort_field", "sort_direction", "sort_span")
            if target.get(key) not in (None, "")
        },
        "errors": ["derived_singleton_rerank_requires_lineage_parent"] if invalid else [],
    }


def issue_execution_permit(
    *,
    state: dict[str, Any],
    tool_name: str,
    args: dict[str, Any],
    effect_id: str,
    capability_registry: CapabilityRegistry,
) -> PermitDecision:
    contract = capability_registry.contract_for_tool(tool_name)
    normalized_args, normalization = normalize_tool_arguments(args)
    arg_errors = validate_tool_arguments(tool_name, normalized_args, capability_registry=capability_registry) if contract is not None else ["tool_not_registered"]
    parameterization = _parameterization_proof(state, normalized_args) if contract is not None and not arg_errors else {
        "version": "constraint-coverage-proof@1", "bindings": [], "parameterization_complete": False,
        "errors": ["tool_schema_invalid"] if arg_errors else ["tool_not_registered"],
    }
    visible_reference = _visible_reference_proof(state, normalized_args) if contract is not None and not arg_errors else {
        "version": "runtime-result-ref-proof@4", "checks": [], "complete": False,
        "errors": ["tool_schema_invalid"] if arg_errors else ["tool_not_registered"],
    }
    member_scope = (
        _explicit_member_scope_proof(state, normalized_args, visible_reference)
        if contract is not None and not arg_errors and visible_reference.get("complete")
        else {
            "version": "explicit-member-scope-proof@2", "complete": False, "applies": False,
            "errors": ["visible_result_reference_invalid"],
        }
    )
    derived_scope = (
        _derived_collection_scope_proof(normalized_args, visible_reference)
        if contract is not None and not arg_errors and visible_reference.get("complete")
        else {
            "version": "derived-collection-scope-proof@1", "complete": False, "applies": False,
            "errors": ["visible_result_reference_invalid"],
        }
    )
    semantic = (
        verify_candidate_semantics(state=state, tool_name=tool_name, args=normalized_args, contract=contract, effect_id=effect_id)
        if contract is not None and not arg_errors and parameterization.get("parameterization_complete") and visible_reference.get("complete") and member_scope.get("complete") and derived_scope.get("complete") and contract.execution_kind != "unsupported"
        else None
    )
    semantic_exact = bool(semantic is None or semantic.exact)
    semantic_errors = [] if semantic_exact else [f"semantic_{semantic.verdict if semantic else 'not_evaluated'}"]
    surface = state.get("capability_surface") if isinstance(state.get("capability_surface"), dict) else None
    effect = next((
        row for row in list((state.get("current_turn_plan") or {}).get("effects") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "") == str(effect_id or "")
    ), {})
    goal_ids = {str(value) for value in list(effect.get("goal_ids") or []) if str(value)}
    surface_rows = [
        row for row in list((surface or {}).get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") in goal_ids
    ]
    surface_required = surface is not None and bool(goal_ids)
    surface_allowed = not surface_required or bool(surface_rows) and all(
        tool_name in {str(value) for value in list(row.get("candidate_tools") or [])}
        for row in surface_rows
    )
    surface_proof = {
        "required": surface_required,
        "allowed": surface_allowed,
        "goal_ids": sorted(goal_ids),
        "candidate_tools": sorted({
            str(value)
            for row in surface_rows
            for value in list(row.get("candidate_tools") or [])
            if str(value)
        }),
    }
    formal_goals = semantic_goals(state)
    formal_effect_required = bool(
        formal_goals
        and goal_ids
        and all(
            str((row.get("requested_effect") or {}).get("domain") or "").strip().lower() != "legacy"
            for row in formal_goals
            if str(row.get("goal_id") or "") in goal_ids
        )
    )
    formal_effect_proof = goal_effect_match_proof(
        state=state,
        tool_name=tool_name,
        goal_ids=sorted(goal_ids),
        registry=capability_registry,
    )
    formal_effect_allowed = (
        not formal_effect_required or bool(formal_effect_proof.get("allowed"))
    )
    proof: dict[str, Any] = {
        "match_proof_version": "match-proof@4.0",
        "registry_version": CAPABILITY_REGISTRY_VERSION,
        "effect_id": effect_id,
        "candidate_tool": tool_name,
        "candidate_capability": contract.key if contract else None,
        "requested_effect": _requested_effect(contract, normalized_args) if contract else None,
        "execution_kind": contract.execution_kind if contract else None,
        "schema_valid": not arg_errors,
        "argument_normalization": normalization,
        "capability_surface": surface_proof,
        "goal_effect_identity": formal_effect_proof,
        "goal_effect_identity_required": formal_effect_required,
        "semantic_verdict": semantic.as_dict() if semantic is not None else {"verdict": "not_required", "reason_code": "unsupported_capability_report"},
        "parameterization": parameterization,
        "parameterization_complete": bool(parameterization.get("parameterization_complete")),
        "visible_result_reference": visible_reference,
        "explicit_member_scope": member_scope,
        "derived_collection_scope": derived_scope,
        "constraint_errors": [*list(arg_errors), *list(parameterization.get("errors") or []), *list(visible_reference.get("errors") or []), *list(member_scope.get("errors") or []), *list(derived_scope.get("errors") or []), *semantic_errors, *([] if surface_allowed else ["capability_not_in_current_goal_surface"]), *([] if formal_effect_allowed else ["capability_goal_effect_identity_mismatch"])],
        "exact_match": bool(contract is not None and not arg_errors and parameterization.get("parameterization_complete") and visible_reference.get("complete") and member_scope.get("complete") and derived_scope.get("complete") and semantic_exact and surface_allowed and formal_effect_allowed),
        "rejected_candidates": [],
        "scope": _scope(state),
    }
    if contract is None or arg_errors or not parameterization.get("parameterization_complete") or not visible_reference.get("complete") or not member_scope.get("complete") or not derived_scope.get("complete") or not semantic_exact or not surface_allowed or not formal_effect_allowed:
        return PermitDecision(
            permitted=False,
            match_proof=proof,
            execution_permit=None,
            rejection={
                "code": (
                    "CAPABILITY_NOT_AVAILABLE_IN_GOAL_SURFACE"
                    if not surface_allowed
                    else "CAPABILITY_GOAL_EFFECT_MISMATCH"
                    if not formal_effect_allowed
                    else "DERIVED_SINGLETON_REQUIRES_PARENT_SCOPE"
                    if contract is not None and not arg_errors and visible_reference.get("complete") and member_scope.get("complete") and not derived_scope.get("complete")
                    else "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET"
                    if contract is not None and not arg_errors and visible_reference.get("complete") and not member_scope.get("complete")
                    else "VISIBLE_RESULT_REF_INVALID"
                    if contract is not None and not arg_errors and not visible_reference.get("complete")
                    else "CAPABILITY_PARAMETERIZATION_INCOMPLETE"
                    if contract is not None and not arg_errors and not parameterization.get("parameterization_complete")
                    else "CAPABILITY_SEMANTIC_CLARIFICATION_REQUIRED"
                    if semantic is not None and semantic.verdict == "clarify"
                    else "CAPABILITY_UNAVAILABLE"
                    if semantic is not None and semantic.verdict == "unsupported"
                    else "CAPABILITY_EXACT_MATCH_REQUIRED"
                ),
                "message": (
                    "当前能力不属于本轮目标发现得到的有限候选集合，系统不会绕过能力面调用相近工具。"
                    if not surface_allowed
                    else "当前工具不能精确完成或支持其绑定 Goal 的 requested_effect；系统不会用相似能力替代。"
                    if not formal_effect_allowed
                    else "当前候选把已由排序或截取得到的单项再次作为比较全集；请使用该结果记录的父集合引用完成本轮比较。"
                    if contract is not None and not arg_errors and visible_reference.get("complete") and member_scope.get("complete") and not derived_scope.get("complete")
                    else "用户证据明确点名了可见集合中的唯一成员，系统拒绝用整个集合代替该成员；请改用该成员的精确可验证引用。"
                    if contract is not None and not arg_errors and visible_reference.get("complete") and not member_scope.get("complete")
                    else "当前引用的结果不存在、未向用户展示、已失效或与所需对象形态不符；系统不会改选其他结果。"
                    if contract is not None and not arg_errors and not visible_reference.get("complete")
                    else "当前请求中的决定性条件没有被完整绑定到正式参数，系统不会用更宽泛查询代替。"
                    if contract is not None and not arg_errors and not parameterization.get("parameterization_complete")
                    else "当前请求需要先澄清，系统不会自行改用相近能力。"
                    if semantic is not None and semantic.verdict == "clarify"
                    else "当前系统没有与该请求精确匹配的能力，未改用相近工具。"
                    if semantic is not None and semantic.verdict == "unsupported"
                    else "当前请求没有通过已注册能力的精确合同校验，系统不会改用相近工具。"
                ),
            },
            normalized_arguments=normalized_args,
        )
    permit = {
        "permit_version": "execution-permit@3.7",
        "permit_id": f"permit:{uuid4().hex}",
        "registry_version": CAPABILITY_REGISTRY_VERSION,
        "effect_id": effect_id,
        "capability_id": contract.key,
        "tool_name": tool_name,
        "execution_kind": contract.execution_kind,
        "scope": _scope(state),
        "turn": int(state.get("turn_index") or 0),
        "expires_after_turn": int(state.get("turn_index") or 0),
        "arguments_digest": _canonical_digest(normalized_args),
        "match_proof_hash": sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    return PermitDecision(
        permitted=True,
        match_proof=proof,
        execution_permit=permit,
        normalized_arguments=normalized_args,
    )


def permit_allows_dispatch(
    *,
    state: dict[str, Any],
    permit: dict[str, Any] | None,
    tool_name: str,
    effect_id: str,
    args: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(permit, dict):
        return False
    if str(permit.get("tool_name") or "") != tool_name:
        return False
    if str(permit.get("effect_id") or "") != effect_id:
        return False
    if int(permit.get("turn") or -1) != int(state.get("turn_index") or 0):
        return False
    if dict(permit.get("scope") or {}) != _scope(state):
        return False
    if args is not None and str(permit.get("arguments_digest") or "") != _canonical_digest(dict(args or {})):
        return False
    return bool(permit.get("permit_id") and permit.get("capability_id"))


def build_effects(*, plan_id: str, calls: list[dict[str, Any]], capability_registry: CapabilityRegistry, existing_effects: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return a single authoritative TurnPlan effect list and decorated calls.

    Each planned external call has one effect record.  Calls in the same model
    response are ordered conservatively: an action draft depends on preceding
    observations in that response; the dependency is audit data, not an
    automatic approval to execute a write.
    """
    effects = [deepcopy(row) for row in existing_effects or [] if isinstance(row, dict)]
    decorated: list[dict[str, Any]] = []
    # Only calls emitted together in the *current model response* gain the
    # conservative positional dependency below.  Existing effects belong to
    # earlier Loop attempts.  Making a corrected candidate depend on its
    # rejected predecessor creates an unrecoverable cycle: validation asks the
    # model to repair, then Workflow blocks the repair because the invalid
    # candidate did not succeed.  Cross-goal dependencies remain represented
    # by the declared goal graph; opaque ResultRef validation independently
    # proves same-turn observation lineage.
    current_response_effect_ids: list[str] = []
    for index, raw_call in enumerate(calls):
        call = deepcopy(raw_call)
        name = str(call.get("name") or "")
        args = dict(call.get("args") or {})
        goal_ids = list(dict.fromkeys(
            str(value).strip()
            for value in list(args.pop("goal_ids", []) or [])
            if str(value).strip()
        ))
        call["args"] = args
        call["_goal_ids"] = goal_ids
        # Terminal/internal calls are protocol controls, not business effects.
        if name in {"respond_to_user", "ask_user_clarification", "declare_turn_goals", "update_task_board", "inspect_audit_event"}:
            decorated.append(call)
            continue
        effect_id = f"{plan_id}:effect:{len(effects) + 1}"
        call["_effect_id"] = effect_id
        contract = capability_registry.contract_for_tool(name)
        dependency_ids = list(current_response_effect_ids[-1:]) if contract and contract.execution_kind == "action_draft" else []
        effect = {
            "effect_id": effect_id,
            "tool_name": name,
            "requested_effect": _requested_effect(contract, dict(call.get("args") or {})) if contract else None,
            "candidate_capability": contract.key if contract else None,
            "execution_kind": contract.execution_kind if contract else "unknown",
            "goal_completion_types": list(contract.goal_completion_types) if contract else [],
            "completion_effect_identities": list(
                completion_effects_for_contract(contract) if contract else ()
            ),
            "support_effect_identities": list(
                support_effects_for_contract(contract) if contract else ()
            ),
            "target_cardinality_hint": _target_cardinality_hint(dict(call.get("args") or {})),
            "goal_ids": goal_ids,
            "depends_on": dependency_ids,
            "status": "candidate",
        }
        effects.append(effect)
        current_response_effect_ids.append(effect_id)
        decorated.append(call)
    return effects, decorated


def record_effect_decision(plan: dict[str, Any], *, effect_id: str, decision: PermitDecision) -> dict[str, Any]:
    next_plan = deepcopy(plan)
    effects = list(next_plan.get("effects") or [])
    changed = False
    for row in effects:
        if str(row.get("effect_id") or "") == effect_id:
            row["match_proof"] = deepcopy(decision.match_proof)
            row["execution_permit"] = deepcopy(decision.execution_permit) if decision.execution_permit else None
            row["status"] = "permitted" if decision.permitted else "rejected"
            if decision.rejection:
                row["rejection"] = deepcopy(decision.rejection)
            changed = True
            break
    if changed:
        next_plan["effects"] = effects
    return next_plan
