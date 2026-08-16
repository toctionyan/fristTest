#!/usr/bin/env python3
from __future__ import annotations

"""One-shot bounded migration for PR #1348.

This script is temporary migration authority only.  It restores the protected
Release-55 oracle from main, rewires the current runtime to the canonical
structural contract, and fails closed on any unexpected source shape.  The
migration workflow deletes this script before committing the permanent result.
"""

import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
GOAL_PLANNING = (
    ROOT
    / "services"
    / "agent-service"
    / "src"
    / "agent_core"
    / "lifecycle"
    / "goal_planning.py"
)
PROTECTED_RELEASE55_TEST = (
    "services/agent-service/tests/runtime/"
    "test_release55_dependency_output_role_overlap.py"
)


def _replace_exact(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def _restore_protected_oracle() -> None:
    original = subprocess.run(
        ["git", "show", f"origin/main:{PROTECTED_RELEASE55_TEST}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    (ROOT / PROTECTED_RELEASE55_TEST).write_bytes(original)


def _migrate_goal_planning() -> None:
    text = GOAL_PLANNING.read_text(encoding="utf-8")
    ast.parse(text)

    import_block = """from agent_core.goal_graph.dependency_basis_contract import (
    ALLOWED_DEPENDENCY_BASIS_KINDS,
    dependency_basis_conflicts_with_requested_outputs,
    render_candidate_blind_dependency_rule,
    render_dependency_format_repair_rule,
)

"""
    anchor = 'GOAL_PLAN_VERSION = "turn-goal-plan@1.1"'
    if "from agent_core.goal_graph.dependency_basis_contract import (" not in text:
        if text.count(anchor) != 1:
            raise SystemExit("goal-planning import anchor is not unique")
        text = text.replace(anchor, import_block + anchor, 1)

    old_kinds = """_ALLOWED_ALIGNMENT_DEPENDENCY_BASIS_KINDS = {
    "result_reference",
    "result_condition",
    "result_value_input",
}"""
    new_kinds = "_ALLOWED_ALIGNMENT_DEPENDENCY_BASIS_KINDS = set(ALLOWED_DEPENDENCY_BASIS_KINDS)"
    if old_kinds in text:
        text = _replace_exact(text, old_kinds, new_kinds, label="basis-kind projection")
    elif new_kinds not in text:
        raise SystemExit("dependency basis-kind projection missing")

    start_marker = "def _dependency_basis_overlaps_requested_output("
    end_marker = "\n\ndef _model_alignment_dependency_proof("
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("dependency structural projection boundary missing")
    replacement = """def _dependency_basis_overlaps_requested_output(
    goal: dict[str, Any],
    basis_span: str,
) -> bool:
    \"\"\"Project the canonical structural dependency-basis contract.\"\"\"

    return dependency_basis_conflicts_with_requested_outputs(
        basis_span,
        _requested_output_evidence_spans(goal),
    )
"""
    text = text[:start] + replacement + text[end:]

    old_primary = """            \"dependency basis evidence must identify only the result-reference, result-condition or result-value-input relation itself; \"
            \"it must be disjoint from the dependent Goal requested_outputs evidence spans. A basis inside requested-output evidence, or a broader phrase that wraps requested-output evidence with control/action wording, proves the requested outcome rather than a result dependency and must be rejected; use a relation-only literal basis when one exists, otherwise the pair is independent\","""
    new_primary = "            render_candidate_blind_dependency_rule(),"
    text = _replace_exact(
        text,
        old_primary,
        new_primary,
        label="candidate-blind semantic projection",
    )

    old_repair = """                    \"The basis must not be requested-output evidence and must not wrap a requested-output evidence span with action/control wording; \"
                    \"if no disjoint relation-only basis exists, return relation=independent. Return the complete dependency_decisions array and the strict JSON fields only.\""""
    new_repair = """                    + render_dependency_format_repair_rule()
                    + \" Return the complete dependency_decisions array and the strict JSON fields only.\""""
    text = _replace_exact(
        text,
        old_repair,
        new_repair,
        label="format-repair semantic projection",
    )

    stale = (
        "must be disjoint from the dependent Goal requested_outputs evidence spans",
        "if no disjoint relation-only basis exists",
    )
    for phrase in stale:
        if phrase in text:
            raise SystemExit(f"stale dependency projection survived migration: {phrase}")

    if text.count("render_candidate_blind_dependency_rule()") != 1:
        raise SystemExit("candidate-blind renderer call is not unique")
    if text.count("render_dependency_format_repair_rule()") != 1:
        raise SystemExit("format-repair renderer call is not unique")
    if text.count("dependency_basis_conflicts_with_requested_outputs(") != 1:
        raise SystemExit("structural contract delegation is not unique")

    ast.parse(text)
    GOAL_PLANNING.write_text(text, encoding="utf-8")


def main() -> int:
    _restore_protected_oracle()
    _migrate_goal_planning()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
