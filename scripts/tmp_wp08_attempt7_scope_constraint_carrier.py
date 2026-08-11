#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return text.replace(old, new, 1)


def patch_protocol(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/protocol.py"
    text = read(path)
    anchor = '''\n\n_GOAL_LIFECYCLE_ENUM = ["OPEN", "ACTIVE", "BLOCKED", "PAUSED", "COMPLETED", "CANCELLED", "SUPERSEDED"]\n'''
    schema = '''\n\nTARGET_CANDIDATE_SCHEMA: dict[str, Any] = {\n    "type": "object",\n    "description": (\n        "开放的目标候选，不是业务事实。若当前 Goal 含有明确缩小目标/结果人口的筛选、状态、阈值或比较谓词，"\n        "必须把最小的当前原文字面证据写入 scope_constraints[].evidence_span。这里只冻结用户表达过的范围证据，"\n        "不得在此猜测归一化业务值；普通目标集合筛选也不得为了结构化而伪装成 Goal.condition。"\n    ),\n    "properties": {\n        "scope_constraints": {\n            "type": "array",\n            "maxItems": 8,\n            "items": {\n                "type": "object",\n                "properties": {\n                    "evidence_span": {\n                        "type": "string",\n                        "description": "缩小本 Goal 目标/结果范围的最小当前用户原话连续片段。",\n                    },\n                },\n                "required": ["evidence_span"],\n                "additionalProperties": False,\n            },\n        },\n    },\n    "additionalProperties": True,\n}\n\n_GOAL_LIFECYCLE_ENUM = ["OPEN", "ACTIVE", "BLOCKED", "PAUSED", "COMPLETED", "CANCELLED", "SUPERSEDED"]\n'''
    text = replace_once(text, anchor, schema, "target candidate schema")
    generic = '"target_candidate": {"type": "object", "additionalProperties": True},'
    count = text.count(generic)
    if count != 2:
        raise SystemExit(f"target candidate schema use count={count}")
    text = text.replace(generic, '"target_candidate": deepcopy(TARGET_CANDIDATE_SCHEMA),')
    write(path, text)


def patch_goal_planning(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    text = read(path)

    old_projection = '''            "requested_effect": deepcopy(goal.get("requested_effect"))\n            if isinstance(goal.get("requested_effect"), dict)\n            else None,\n            "condition": deepcopy(goal.get("condition"))\n'''
    new_projection = '''            "requested_effect": deepcopy(goal.get("requested_effect"))\n            if isinstance(goal.get("requested_effect"), dict)\n            else None,\n            "target_candidate": deepcopy(goal.get("target_candidate"))\n            if isinstance(goal.get("target_candidate"), dict)\n            else None,\n            "condition": deepcopy(goal.get("condition"))\n'''
    text = replace_once(text, old_projection, new_projection, "blind target candidate projection")

    old_blind = '''            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer's actual business effect instead of "\n            "coercing an unsupported/open effect into a nearby registered effect; and (3) whether every explicit user-stated "\n            "filter, status predicate, threshold, condition or other result-scope constraint inside a Goal evidence_span is "\n            "actually represented in that Goal's structured condition rather than existing only in description/evidence prose. "\n            "Do not invent a missing target member, slot/form value, current business fact or execution-time cardinality as a "\n            "semantic condition; those remain downstream Runtime concerns. If requested_effect is semantically substituted, or "\n            "an explicit predicate is missing from structured condition, verdict must be incomplete and missing_spans must copy "\n            "the smallest literal USER_TEXT span that proves the mismatch. Do not propose a replacement identity, normalized "\n            "predicate value, tool or capability. A result dependency exists only when the later user-visible outcome itself "\n'''
    new_blind = '''            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer's actual business effect instead of "\n            "coercing an unsupported/open effect into a nearby registered effect; and (3) whether every explicit user-stated "\n            "filter, status predicate, threshold or comparison that narrows the Goal target/result population is preserved as "\n            "literal evidence in DECLARED_GOAL.target_candidate.scope_constraints. A scope constraint stores only the smallest "\n            "literal USER_TEXT evidence_span; do not translate it into a normalized business value, tool field or capability. "\n            "Mere object/topic/member naming is target identity, not automatically a scope constraint. Goal.condition is a "\n            "separate condition/dependency algebra and ordinary target-population filtering must not be forced into it. Do not "\n            "invent a missing target member, slot/form value, current business fact or execution-time cardinality. If "\n            "requested_effect is semantically substituted, or an explicit narrowing predicate is absent from scope_constraints, "\n            "verdict must be incomplete and missing_spans must copy the smallest literal USER_TEXT span that proves the mismatch. "\n            "Do not propose a replacement identity, normalized predicate value, tool or capability. A result dependency exists "\n            "only when the later user-visible outcome itself "\n'''
    text = replace_once(text, old_blind, new_blind, "blind scope fidelity instruction")

    old_rule = '''            "an explicit user-stated result filter/status/predicate must be structurally preserved in Goal.condition; repeating the words only in description, raw_description or evidence_span is not enough",\n            "target-member selection, unprovided form values and current business facts are downstream Runtime concerns and are not missing semantic conditions",\n'''
    new_rule = '''            "an explicit user-stated predicate that narrows the target/result population must be preserved as a literal target_candidate.scope_constraints evidence span; prose alone is not enough and no normalized business value is required here",\n            "ordinary target selection/scope filtering is not a Goal.condition; Goal.condition remains reserved for the separate frozen conditional/dependency algebra",\n            "target-member selection, unprovided form values and current business facts are downstream Runtime concerns and are not missing scope constraints",\n'''
    text = replace_once(text, old_rule, new_rule, "blind scope fidelity rules")

    text = replace_once(
        text,
        'For requested_effect or structured-condition mismatch, do not alter the ',
        'For requested_effect or target-scope-constraint mismatch, do not alter the ',
        "blind effective scope wording",
    )
    text = replace_once(
        text,
        'a reason_code that identifies requested-effect fidelity or structured-condition coverage.',
        'a reason_code that identifies requested-effect fidelity or target-scope-constraint coverage.',
        "blind effective reason wording",
    )
    text = replace_once(
        text,
        '# dependency graph plus requested-effect/condition fidelity.',
        '# dependency graph plus requested-effect/target-scope fidelity.',
        "blind exact comment",
    )
    text = replace_once(
        text,
        '# requested_effect and condition so the verifier can detect semantic\n                # substitution or a predicate that exists only in prose.',
        '# requested_effect and target_candidate so the verifier can detect semantic\n                # substitution or a target-scope predicate that exists only in prose.',
        "blind trigger comment",
    )
    old_format = '''                    f"{verdict.reason_code}. Re-audit requested_effect fidelity, structured condition coverage, and every unordered "\n                    "Goal pair from USER_TEXT only. A nearby registered effect is not a faithful replacement for an unsupported/open "\n                    "business effect. An explicit filter/status/predicate must be present in Goal.condition, not merely repeated in "\n                    "description/evidence prose. Do not treat target-member selection, missing form values or current business facts as "\n                    "semantic conditions. If a semantic-field mismatch exists, return verdict=incomplete and copy its smallest literal "\n'''
    new_format = '''                    f"{verdict.reason_code}. Re-audit requested_effect fidelity, target scope-constraint coverage, and every unordered "\n                    "Goal pair from USER_TEXT only. A nearby registered effect is not a faithful replacement for an unsupported/open "\n                    "business effect. An explicit filter/status/threshold/comparison that narrows the target population must have its "\n                    "smallest literal phrase in target_candidate.scope_constraints; do not translate it into Goal.condition or a "\n                    "normalized business value. Do not treat target-member selection, missing form values or current business facts as "\n                    "scope constraints. If a semantic-field mismatch exists, return verdict=incomplete and copy its smallest literal "\n'''
    text = replace_once(text, old_format, new_format, "blind format scope repair")

    clean_anchor = '''def _clean_text(value: Any, *, limit: int = 500) -> str:\n    return str(value or "").strip()[:limit]\n\n\ndef _known_tool_names(capability_registry: CapabilityRegistry) -> set[str]:\n'''
    clean_replacement = '''def _clean_text(value: Any, *, limit: int = 500) -> str:\n    return str(value or "").strip()[:limit]\n\n\ndef _normalize_target_candidate_scope_constraints(\n    raw: Any,\n    *,\n    user_text: str,\n    goal_evidence_span: str,\n    goal_id: str,\n) -> tuple[dict[str, Any] | None, list[str]]:\n    """Validate only literal scope evidence; never interpret or normalize its meaning."""\n    if raw in (None, "", [], {}):\n        return None, []\n    if not isinstance(raw, dict):\n        return None, [f"target_candidate_object_required:{goal_id}"]\n    candidate = deepcopy(raw)\n    values = candidate.get("scope_constraints")\n    if values is None:\n        return candidate, []\n    if not isinstance(values, list):\n        return candidate, [f"scope_constraints_array_required:{goal_id}"]\n    errors: list[str] = []\n    normalized: list[dict[str, str]] = []\n    seen: set[str] = set()\n    for index, item in enumerate(values[:8]):\n        if not isinstance(item, dict) or set(item) - {"evidence_span"}:\n            errors.append(f"scope_constraint_invalid:{goal_id}:{index}")\n            continue\n        span = _clean_text(item.get("evidence_span"), limit=240)\n        if (\n            not span\n            or span not in user_text\n            or not goal_evidence_span\n            or span not in goal_evidence_span\n        ):\n            errors.append(f"scope_constraint_evidence_not_in_goal:{goal_id}:{index}")\n            continue\n        if span not in seen:\n            seen.add(span)\n            normalized.append({"evidence_span": span})\n    if len(values) > 8:\n        errors.append(f"scope_constraint_limit_exceeded:{goal_id}")\n    candidate["scope_constraints"] = normalized\n    return candidate, errors\n\n\ndef _known_tool_names(capability_registry: CapabilityRegistry) -> set[str]:\n'''
    text = replace_once(text, clean_anchor, clean_replacement, "scope candidate validator")

    old_copy = '''        for key in ("target_candidate", "input_candidates", "execution_commitment"):\n            value = raw.get(key)\n            if value not in (None, "", [], {}):\n                row[key] = deepcopy(value)\n'''
    new_copy = '''        target_candidate, target_errors = _normalize_target_candidate_scope_constraints(\n            raw.get("target_candidate"),\n            user_text=user_text,\n            goal_evidence_span=evidence_span,\n            goal_id=goal_id,\n        )\n        errors.extend(target_errors)\n        if target_candidate is not None:\n            row["target_candidate"] = target_candidate\n        for key in ("input_candidates", "execution_commitment"):\n            value = raw.get(key)\n            if value not in (None, "", [], {}):\n                row[key] = deepcopy(value)\n'''
    text = replace_once(text, old_copy, new_copy, "target candidate normalization")

    repair_rule = '''            "requested_effect_rule": "preserve the user's open business effect; do not coerce it into a nearby registered capability",\n'''
    repair_rule_new = '''            "requested_effect_rule": "preserve the user's open business effect; do not coerce it into a nearby registered capability",\n            "scope_constraint_rule": "explicit target/result-population predicates use target_candidate.scope_constraints literal evidence; ordinary scope filters are not Goal.condition",\n'''
    text = replace_once(text, repair_rule, repair_rule_new, "declaration repair scope guidance")
    write(path, text)


def patch_semantic_capability(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py"
    text = read(path)
    old = '''            "requested_effect": dict(row.get("requested_effect") or {})\n            if isinstance(row.get("requested_effect"), dict)\n            else None,\n            "goal_type_compatibility": str(\n'''
    new = '''            "requested_effect": dict(row.get("requested_effect") or {})\n            if isinstance(row.get("requested_effect"), dict)\n            else None,\n            "target_candidate": deepcopy(row.get("target_candidate"))\n            if isinstance(row.get("target_candidate"), dict)\n            else None,\n            "goal_type_compatibility": str(\n'''
    text = replace_once(text, old, new, "semantic verifier target candidate projection")
    instruction = '''                "Treat a broader call that drops a decisive user condition as unsupported: a condition is exact only "\n                "when it is bound to a declared formal parameter with a matching value. "\n'''
    instruction_new = '''                "Treat a broader call that drops a decisive user scope constraint or condition as unsupported. "\n                "DECLARED_WORKFLOW_STEP target_candidate.scope_constraints are frozen literal scope evidence; each must be "\n                "preserved by the candidate target/query arguments or by a Runtime-proven current-turn narrowed target. "\n                "A Goal.condition is separate conditional/dependency semantics and is exact only when bound as required. "\n'''
    text = replace_once(text, instruction, instruction_new, "semantic candidate scope instruction")
    rule = '''            "exact only when the candidate's declared effect and formal arguments preserve every decisive condition in the user request; an unfiltered/broader query is not exact when the user requested a condition",\n'''
    rule_new = '''            "exact only when the candidate's declared effect and formal arguments preserve every frozen target scope constraint and decisive condition; an unfiltered/broader query is not exact when the user narrowed the population",\n'''
    text = replace_once(text, rule, rule_new, "semantic candidate scope rule")
    write(path, text)


def patch_capability_gate(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/runtime/capability_gate.py"
    text = read(path)
    old_row = '''            "kind": str(raw.get("kind") or "condition"),\n            "source_span": span,\n            "parameter_path": path,\n'''
    new_row = '''            "kind": str(raw.get("kind") or "condition"),\n            "provenance": "candidate_constraint_binding",\n            "source_span": span,\n            "parameter_path": path,\n'''
    text = replace_once(text, old_row, new_row, "constraint binding provenance")

    target_anchor = '''    target = args.get("target") if isinstance(args.get("target"), dict) else {}\n    if str(target.get("mode") or "") == "set_operation" and str(target.get("operator") or "") == "sort":\n'''
    target_new = '''    target = args.get("target") if isinstance(args.get("target"), dict) else {}\n    # A typed target field may carry its own literal evidence sibling\n    # (for example <field> + <field>_span). Project that pair as a Runtime\n    # target-evidence binding without interpreting the field or value.\n    for span_key, span_value in target.items():\n        if not str(span_key).endswith("_span"):\n            continue\n        field = str(span_key)[:-5]\n        if not field or field not in target or target.get(field) in (None, ""):\n            continue\n        span = str(span_value or "").strip()\n        covered = bool(span and span in user_text)\n        if not covered:\n            errors.append(f"target_parameter_evidence_not_current_turn:target.{field}")\n        if not any(\n            str(row.get("parameter_path") or "") == f"target.{field}"\n            and str(row.get("source_span") or "") == span\n            for row in rows\n        ):\n            rows.append({\n                "kind": "scope",\n                "provenance": "runtime_target_evidence",\n                "source_span": span,\n                "parameter_path": f"target.{field}",\n                "normalized_value": target.get(field),\n                "actual_value": target.get(field),\n                "status": "covered" if covered else "uncovered",\n            })\n    if str(target.get("mode") or "") == "set_operation" and str(target.get("operator") or "") == "sort":\n'''
    text = replace_once(text, target_anchor, target_new, "typed target evidence projection")
    sort_row = '''                "kind": "condition",\n                "source_span": span,\n                "parameter_path": f"target.{field}",\n'''
    sort_new = '''                "kind": "condition",\n                "provenance": "runtime_target_evidence",\n                "source_span": span,\n                "parameter_path": f"target.{field}",\n'''
    text = replace_once(text, sort_row, sort_new, "sort evidence provenance")
    pipeline_row = '''                    "kind": "condition",\n                    "source_span": span,\n                    "parameter_path": path,\n'''
    pipeline_new = '''                    "kind": "condition",\n                    "provenance": "runtime_target_evidence",\n                    "source_span": span,\n                    "parameter_path": path,\n'''
    text = replace_once(text, pipeline_row, pipeline_new, "pipeline evidence provenance")

    visible_marker = '''\n\ndef _visible_reference_proof(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:\n'''
    helper = r'''

def _literal_scope_overlap(left: str, right: str) -> bool:
    left = str(left or "").strip()
    right = str(right or "").strip()
    return bool(left and right and (left in right or right in left))


def _source_operation_scope_spans(value: Any) -> set[str]:
    """Read literal evidence already stored on a verified operation; never interpret it."""
    spans: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).endswith("_span") and isinstance(item, str) and item.strip():
                spans.add(item.strip())
            elif key == "source_span" and isinstance(item, str) and item.strip():
                spans.add(item.strip())
            spans.update(_source_operation_scope_spans(item))
    elif isinstance(value, list):
        for item in value:
            spans.update(_source_operation_scope_spans(item))
    return spans


def _formal_goal_scope_coverage_proof(
    state: dict[str, Any],
    *,
    goal_ids: set[str],
    parameterization: dict[str, Any],
    visible_reference: dict[str, Any],
) -> dict[str, Any]:
    """Bind frozen literal scope predicates to real candidate narrowing evidence.

    Lifecycle/Alignment decides only which current-user phrases are explicit
    population-narrowing constraints. Runtime never interprets those phrases or
    normalizes their business values. It requires each frozen literal span to
    overlap evidence attached to a real query/target parameter, or to a verified
    current-turn observation that already carries the same narrowing evidence.
    """
    formal_by_id = {
        str(goal.get("goal_id") or ""): goal
        for goal in semantic_goals(state)
        if str(goal.get("goal_id") or "")
    }
    requirements: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for goal_id in sorted(goal_ids):
        goal = formal_by_id.get(goal_id) or {}
        target_candidate = (
            goal.get("target_candidate")
            if isinstance(goal.get("target_candidate"), dict)
            else {}
        )
        values = target_candidate.get("scope_constraints")
        if not isinstance(values, list):
            continue
        for index, raw in enumerate(values):
            span = str((raw or {}).get("evidence_span") or "").strip() if isinstance(raw, dict) else ""
            if not span or (goal_id, span) in seen:
                continue
            seen.add((goal_id, span))
            requirements.append({"goal_id": goal_id, "scope_index": str(index), "evidence_span": span})

    structural_leaves = {
        "mode", "operator", "left_handle", "right_handle", "source_handle",
        "reference_kind", "group_size", "expected_shape", "action", "capability",
    }
    candidate_rows: list[dict[str, Any]] = []
    for raw in list(parameterization.get("bindings") or []):
        if not isinstance(raw, dict) or str(raw.get("status") or "") != "covered":
            continue
        path = str(raw.get("parameter_path") or "").strip()
        span = str(raw.get("source_span") or "").strip()
        provenance = str(raw.get("provenance") or "")
        leaf = path.rsplit(".", 1)[-1]
        if provenance == "runtime_target_evidence":
            eligible = True
        else:
            eligible = (
                provenance == "candidate_constraint_binding"
                and (path.startswith("query.") or path.startswith("target."))
                and leaf not in structural_leaves
                and not leaf.endswith("_span")
            )
        if eligible and span:
            candidate_rows.append({"source_span": span, "parameter_path": path, "provenance": provenance})

    user_text = str(state.get("current_user_input") or "")
    lineage_spans: set[str] = set()
    for check in list(visible_reference.get("checks") or []):
        if not isinstance(check, dict):
            continue
        ref = check.get("validated_ref") if isinstance(check.get("validated_ref"), dict) else {}
        source = ref.get("source_operation") if isinstance(ref, dict) else None
        for span in _source_operation_scope_spans(source):
            if span and span in user_text:
                lineage_spans.add(span)

    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    for requirement in requirements:
        formal_span = requirement["evidence_span"]
        parameter_matches = [
            row for row in candidate_rows
            if _literal_scope_overlap(formal_span, str(row.get("source_span") or ""))
        ]
        lineage_matches = sorted(
            span for span in lineage_spans if _literal_scope_overlap(formal_span, span)
        )
        covered = bool(parameter_matches or lineage_matches)
        if not covered:
            errors.append(
                f"formal_goal_scope_constraint_unbound:{requirement['goal_id']}:{requirement['scope_index']}"
            )
        checks.append({
            **requirement,
            "status": "covered" if covered else "uncovered",
            "parameter_matches": parameter_matches,
            "verified_lineage_spans": lineage_matches,
        })
    return {
        "version": "formal-goal-scope-coverage@1",
        "required": bool(requirements),
        "goal_ids": sorted(goal_ids),
        "requirements": requirements,
        "checks": checks,
        "complete": not errors,
        "errors": errors,
        "language_interpretation_used": False,
        "value_normalization_used": False,
    }


def _visible_reference_proof(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
'''
    text = replace_once(text, visible_marker, helper, "formal goal scope proof")

    call_anchor = '''    formal_condition_coverage = _formal_goal_condition_coverage_proof(\n        state, goal_ids=goal_ids, parameterization=parameterization\n    )\n    semantic_reference_binding = _semantic_reference_binding_proof(\n'''
    call_new = '''    formal_condition_coverage = _formal_goal_condition_coverage_proof(\n        state, goal_ids=goal_ids, parameterization=parameterization\n    )\n    formal_scope_coverage = _formal_goal_scope_coverage_proof(\n        state, goal_ids=goal_ids, parameterization=parameterization, visible_reference=visible_reference\n    )\n    semantic_reference_binding = _semantic_reference_binding_proof(\n'''
    text = replace_once(text, call_anchor, call_new, "formal scope proof call")
    text = replace_once(
        text,
        '''        and formal_condition_coverage.get("complete")\n        and visible_reference.get("complete")\n''',
        '''        and formal_condition_coverage.get("complete")\n        and formal_scope_coverage.get("complete")\n        and visible_reference.get("complete")\n''',
        "formal scope semantic prerequisite",
    )
    text = replace_once(
        text,
        '''        "formal_goal_condition_coverage": formal_condition_coverage,\n        "visible_result_reference": visible_reference,\n''',
        '''        "formal_goal_condition_coverage": formal_condition_coverage,\n        "formal_goal_scope_coverage": formal_scope_coverage,\n        "visible_result_reference": visible_reference,\n''',
        "formal scope match proof",
    )
    text = replace_once(
        text,
        '''*list(formal_condition_coverage.get("errors") or []), *list(visible_reference.get("errors") or []),''',
        '''*list(formal_condition_coverage.get("errors") or []), *list(formal_scope_coverage.get("errors") or []), *list(visible_reference.get("errors") or []),''',
        "formal scope constraint errors",
    )
    write(path, text)


def patch_attempt6_tests(root: Path) -> None:
    path = root / "skill-system/tests/test_wp08_attempt6_semantic_fidelity_repair.py"
    text = read(path)
    text = text.replace(
        'def _goal(goal_id: str, span: str, effect: dict, *, condition=None) -> dict:',
        'def _goal(goal_id: str, span: str, effect: dict, *, target_candidate=None, condition=None) -> dict:',
        1,
    )
    text = text.replace(
        '    if condition is not None:\n        row["condition"] = condition\n',
        '    if target_candidate is not None:\n        row["target_candidate"] = target_candidate\n    if condition is not None:\n        row["condition"] = condition\n',
        1,
    )
    text = text.replace(
        'def test_single_goal_exact_is_reaudited_for_omitted_structured_filter() -> None:',
        'def test_single_goal_exact_is_reaudited_for_omitted_scope_constraint() -> None:',
        1,
    )
    text = text.replace('"reason_code": "explicit_condition_not_structured",', '"reason_code": "explicit_scope_constraint_not_structured",', 1)
    text = text.replace('assert verdict.reason_code == "explicit_condition_not_structured"', 'assert verdict.reason_code == "explicit_scope_constraint_not_structured"', 1)
    text = text.replace('assert "structured condition" in audit_text', 'assert "scope_constraints" in audit_text', 1)
    text = text.replace(
        'def test_single_goal_with_structured_condition_can_pass_same_reaudit() -> None:',
        'def test_single_goal_with_structured_scope_constraint_can_pass_same_reaudit() -> None:',
        1,
    )
    old_condition_setup = '''    condition = {\n        "op": "eq",\n        "left": {"source": "input", "path": "delivery_status"},\n        "right": {"source": "literal", "value": "运输中"},\n    }\n    goal = _goal(\n        "g1",\n        text,\n        {"domain": "order", "operation": "query_logistics", "object_type": "order"},\n        condition=condition,\n    )\n'''
    new_scope_setup = '''    goal = _goal(\n        "g1",\n        text,\n        {"domain": "order", "operation": "query_logistics", "object_type": "order"},\n        target_candidate={"scope_constraints": [{"evidence_span": "在路上"}]},\n    )\n'''
    text = replace_once(text, old_condition_setup, new_scope_setup, "attempt6 positive scope test")
    old_projection_setup = '''    condition = {\n        "op": "eq",\n        "left": {"source": "input", "path": "delivery_status"},\n        "right": {"source": "literal", "value": "运输中"},\n    }\n    projected = _dependency_blind_goal_projection([\n        {\n            "goal_id": "g1",\n            "evidence_span": "在路上",\n            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},\n            "condition": condition,\n            "expected_result_cardinality": "collection",\n            "required": True,\n            "depends_on": ["g0"],\n        }\n    ])[0]\n    assert "depends_on" not in projected\n    assert projected["condition"] == condition\n'''
    new_projection_setup = '''    target_candidate = {"scope_constraints": [{"evidence_span": "在路上"}]}\n    projected = _dependency_blind_goal_projection([\n        {\n            "goal_id": "g1",\n            "evidence_span": "在路上",\n            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},\n            "target_candidate": target_candidate,\n            "expected_result_cardinality": "collection",\n            "required": True,\n            "depends_on": ["g0"],\n        }\n    ])[0]\n    assert "depends_on" not in projected\n    assert projected["target_candidate"] == target_candidate\n'''
    text = replace_once(text, old_projection_setup, new_projection_setup, "attempt6 blind scope projection test")
    text = text.replace('assert "structured condition" in policy', 'assert "scope_constraints" in policy', 1)
    text = text.replace(
        '    assert "def _formal_goal_condition_coverage_proof" in source\n',
        '    assert "def _formal_goal_condition_coverage_proof" in source\n    assert "def _formal_goal_scope_coverage_proof" in source\n',
        1,
    )
    write(path, text.rstrip() + "\n")


def add_attempt7_tests(root: Path) -> None:
    path = root / "skill-system/tests/test_wp08_attempt7_scope_constraint_repair.py"
    if path.exists():
        raise SystemExit("Attempt 7 scope-constraint test file already exists")
    path.write_text(r'''from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services" / "agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for value in (AGENT_ROOT, AGENT_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _state(text: str, span: str) -> dict:
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract

    contract = freeze_semantic_contract(
        turn=3,
        user_text=text,
        summary=text,
        goals=[{
            "goal_id": "g1",
            "description": text,
            "evidence_span": text,
            "requested_effect": {
                "domain": "order",
                "operation": "query_logistics",
                "object_type": "order",
            },
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": [],
            "target_candidate": {
                "scope_constraints": [{"evidence_span": span}],
            },
        }],
        alignment_proof={"verdict": "exact", "source": "test"},
    )
    return {"current_user_input": text, "frozen_semantic_contract": contract}


def test_scope_constraint_candidate_accepts_only_literal_goal_local_evidence() -> None:
    from agent_core.lifecycle.goal_planning import _normalize_target_candidate_scope_constraints

    candidate, errors = _normalize_target_candidate_scope_constraints(
        {"scope_constraints": [{"evidence_span": "待发货"}]},
        user_text="把待发货的订单取消，已签收的看看能不能退款",
        goal_evidence_span="把待发货的订单取消",
        goal_id="g1",
    )
    assert errors == []
    assert candidate == {"scope_constraints": [{"evidence_span": "待发货"}]}

    _, cross_goal_errors = _normalize_target_candidate_scope_constraints(
        {"scope_constraints": [{"evidence_span": "已签收"}]},
        user_text="把待发货的订单取消，已签收的看看能不能退款",
        goal_evidence_span="把待发货的订单取消",
        goal_id="g1",
    )
    assert cross_goal_errors == ["scope_constraint_evidence_not_in_goal:g1:0"]


def test_formal_scope_constraint_requires_real_parameter_binding() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("哪些还在路上？", "在路上")
    missing = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": []},
        visible_reference={"checks": []},
    )
    assert missing["complete"] is False
    assert missing["errors"] == ["formal_goal_scope_constraint_unbound:g1:0"]

    bound = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": [{
            "status": "covered",
            "source_span": "在路上",
            "parameter_path": "query.delivery_status",
            "provenance": "candidate_constraint_binding",
        }]},
        visible_reference={"checks": []},
    )
    assert bound["complete"] is True
    assert bound["checks"][0]["parameter_matches"][0]["parameter_path"] == "query.delivery_status"


def test_structural_target_mode_cannot_fake_scope_binding() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("哪些还在路上？", "在路上")
    proof = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": [{
            "status": "covered",
            "source_span": "在路上",
            "parameter_path": "target.mode",
            "provenance": "candidate_constraint_binding",
        }]},
        visible_reference={"checks": []},
    )
    assert proof["complete"] is False


def test_runtime_target_evidence_can_bind_scope_without_domain_keyword_rules() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("哪些还在路上？", "还在路上")
    proof = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": [{
            "status": "covered",
            "source_span": "在路上",
            "parameter_path": "target.status",
            "provenance": "runtime_target_evidence",
        }]},
        visible_reference={"checks": []},
    )
    assert proof["complete"] is True


def test_current_turn_verified_lineage_can_carry_scope_into_later_action() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("把待发货的订单取消", "待发货")
    proof = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": []},
        visible_reference={
            "checks": [{
                "validated_ref": {
                    "reference_kind": "current_turn_verified_observation",
                    "source_operation": {
                        "target": {
                            "mode": "all_orders",
                            "status": "待处理值",
                            "status_span": "待发货",
                        }
                    },
                }
            }]
        },
    )
    assert proof["complete"] is True
    assert proof["checks"][0]["verified_lineage_spans"] == ["待发货"]


def test_core_scope_bridge_is_domain_neutral_and_keeps_condition_separate() -> None:
    goal_source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    gate_source = (AGENT_SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")
    semantic_source = (AGENT_SRC / "agent_core/runtime/semantic_capability_verifier.py").read_text(encoding="utf-8")
    start = goal_source.index("blind_dependency_instruction =")
    end = goal_source.index("prompt = {", start)
    policy = goal_source[start:end]
    assert "target_candidate.scope_constraints" in policy
    assert "Goal.condition is a separate" in policy
    assert "requested_effect" in policy
    assert "def _formal_goal_scope_coverage_proof" in gate_source
    assert "language_interpretation_used" in gate_source
    assert "target_candidate" in semantic_source
    for forbidden in ("待发货", "已签收", "在路上", "运输中", "快递员"):
        assert forbidden not in policy
        assert forbidden not in gate_source
''', encoding="utf-8")


def patch(root: Path) -> None:
    patch_protocol(root)
    patch_goal_planning(root)
    patch_semantic_capability(root)
    patch_capability_gate(root)
    patch_attempt6_tests(root)
    add_attempt7_tests(root)


def baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    data = json.loads(read(path))
    protected = [
        "services/agent-service/src/agent_core/lifecycle/protocol.py",
        "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
        "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py",
        "services/agent-service/src/agent_core/runtime/capability_gate.py",
    ]
    for rel in protected:
        data["files"][rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["generated_from"] = f"git:{product_sha}"
    write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("patch")
    p.add_argument("--workspace", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--workspace", required=True)
    b.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        baseline(root, args.product_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
