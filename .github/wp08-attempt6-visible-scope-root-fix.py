#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:160]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_exact_count(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"expected {expected} replacements in {path}: found {count} for {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def replace_region(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


visible = ROOT / "services/agent-service/src/agent_core/context/visible_result_refs.py"
replace_once(
    visible,
    "\n\ndef _origin(entry: dict[str, Any]) -> dict[str, Any] | None:\n",
    r'''

def visible_result_scope_key(ref: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Return the stable semantic target scope of one visible-result alias.

    Result handles are provenance identities, not necessarily distinct business
    scopes.  A singleton artifact and a one-member collection can both be
    released in the same turn while denoting the exact same ordered member
    population.  Runtime ambiguity checks must compare this scope, not raw
    handle count.  Ordered membership is retained so two differently ordered
    visible collections remain distinguishable for ordinal discourse.
    """
    members = tuple(
        str(value)
        for value in list(ref.get("canonical_order") or ref.get("member_handles") or [])
        if str(value)
    )
    if members:
        return ("members", members)
    result_ref = str(ref.get("result_ref") or "").strip()
    return ("result", (result_ref,)) if result_ref else ("empty", ())


def _origin(entry: dict[str, Any]) -> dict[str, Any] | None:
''',
)

reference = ROOT / "services/agent-service/src/agent_core/context/reference_resolution.py"
replace_once(
    reference,
    "from typing import Any, Iterable\n",
    "from typing import Any, Iterable\n\nfrom agent_core.context.visible_result_refs import visible_result_scope_key\n",
)
new_resolution = r'''    equivalent_groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for row in cardinality_filtered:
        equivalent_groups.setdefault(visible_result_scope_key(row), []).append(row)
    distinct_candidate_groups = list(equivalent_groups.values())

    status = "NOT_FOUND"
    resolved_result_ref: str | None = None
    resolved_members: list[str] = []
    resolved_position: int | None = None
    if candidates and not type_filtered:
        status = "TYPE_CONFLICT"
    elif type_filtered and not cardinality_filtered:
        status = "CARDINALITY_CONFLICT"
    elif len(distinct_candidate_groups) == 1:
        equivalent = distinct_candidate_groups[0]
        preferred_shape = (
            "one" if expected == "single"
            else "collection" if expected == "collection"
            else ""
        )
        selected = next(
            (row for row in equivalent if str(row.get("shape") or "") == preferred_shape),
            equivalent[0],
        )
        members = [
            str(value)
            for value in list(
                selected.get("canonical_order")
                or selected.get("member_handles")
                or []
            )
            if str(value)
        ]
        if reference_type == "ordinal_visible_member":
            position = int(expression.get("position") or 0)
            if position < 1 or position > len(members):
                status = "CARDINALITY_CONFLICT"
            else:
                status = "UNIQUE"
                resolved_result_ref = str(selected.get("result_ref") or "")
                resolved_members = [members[position - 1]]
                resolved_position = position
        elif reference_type == "explicit_visible_member":
            member = str(expression.get("member_handle") or "")
            status = "UNIQUE"
            resolved_result_ref = str(selected.get("result_ref") or "")
            resolved_members = [member]
        else:
            status = "UNIQUE"
            resolved_result_ref = str(selected.get("result_ref") or "")
            resolved_members = members
    elif len(distinct_candidate_groups) > 1:
        status = "AMBIGUOUS"

'''
replace_region(
    reference,
    '    status = "NOT_FOUND"\n',
    '    payload: dict[str, Any] = {\n',
    new_resolution,
)
replace_once(
    reference,
    '        "auto_substitution_used": False,\n        "selection_policy": "typed_relation_then_runtime_validation_no_fallback",\n',
    '        "auto_substitution_used": False,\n        "equivalent_candidate_scope_count": len(distinct_candidate_groups),\n        "equivalent_aliases_collapsed": sum(max(0, len(group) - 1) for group in distinct_candidate_groups),\n        "selection_policy": "typed_relation_then_semantic_scope_equivalence_then_runtime_validation_no_fallback",\n',
)

capability = ROOT / "services/agent-service/src/agent_core/runtime/capability_gate.py"
replace_once(
    capability,
    "from agent_core.context.visible_result_refs import validate_runtime_result_ref, visible_result_refs_from_ledger\n",
    "from agent_core.context.visible_result_refs import (\n    validate_runtime_result_ref,\n    visible_result_refs_from_ledger,\n    visible_result_scope_key,\n)\n",
)
new_latest = r'''    latest_refs = [
        ref for ref in visible_refs
        if bool(ref.get("is_latest_visible_turn")) and str(ref.get("result_ref") or "")
    ]
    latest_handles = {
        str(ref.get("result_ref") or "")
        for ref in latest_refs
    }
    latest_scopes = {
        visible_result_scope_key(ref)
        for ref in latest_refs
        if visible_result_scope_key(ref) != ("empty", ())
    }
    latest_member_handles = {
        str(member)
        for ref in latest_refs
        for member in list(ref.get("member_handles") or [])
        if str(member)
    }
'''
replace_region(
    capability,
    "    latest_handles = {\n",
    '    binding = args.get("context_binding") if isinstance(args.get("context_binding"), dict) else {}\n',
    new_latest,
)
replace_exact_count(
    capability,
    "len(latest_handles) > 1",
    "len(latest_scopes) > 1",
    2,
)
replace_once(
    capability,
    '            "latest_visible_result_count": len(latest_handles),\n            "latest_visible_scope_ambiguous": len(latest_scopes) > 1,\n',
    '            "latest_visible_result_count": len(latest_handles),\n            "latest_visible_scope_count": len(latest_scopes),\n            "latest_visible_equivalent_alias_count": max(0, len(latest_handles) - len(latest_scopes)),\n            "latest_visible_scope_ambiguous": len(latest_scopes) > 1,\n',
)

print("attempt6 visible-scope root fix applied")
