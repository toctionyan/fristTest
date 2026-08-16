#!/usr/bin/env python3
from __future__ import annotations

"""Fail-closed verifier for the canonical dependency-basis contract."""

import argparse
import ast
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.goal_graph.dependency_basis_contract import (  # noqa: E402
    CONTRACT_ID,
    FINAL_DEPENDENCY_AUTHORITY,
    contract_fingerprint,
    mutation_detection_matrix,
    projection_manifest,
    validate_contract,
    verify_projection_manifest,
)

INVARIANT_ID = "DEP-BASIS-CONTRACT-001"
GUARD_ID = "dependency-basis-contract"
MANIFEST = ROOT / "contracts" / "generated" / "dependency-basis-projections.json"
GOAL_PLANNING = (
    ROOT
    / "services"
    / "agent-service"
    / "src"
    / "agent_core"
    / "lifecycle"
    / "goal_planning.py"
)

_STALE_PHRASES = (
    "must be disjoint from the dependent Goal requested_outputs evidence spans",
    "if no disjoint relation-only basis exists",
)


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise ValueError(f"function not found: {name}")


def _class_method_source(source: str, class_name: str, method_name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                segment = ast.get_source_segment(source, child)
                if segment:
                    return segment
    raise ValueError(f"class method not found: {class_name}.{method_name}")


def _failure_kind(errors: list[str]) -> str | None:
    if not errors:
        return None
    if any(error.startswith("mutation_not_detected:") for error in errors):
        return "dependency_contract_mutation_gap"
    projection_prefixes = (
        "stale_projection_text:",
        "missing_projection_renderer:",
        "candidate_blind_projection_not_generated",
        "format_repair_projection_not_generated",
        "structural_projection_not_delegated",
        "duplicated_structural_semantics",
        "projection_manifest_",
    )
    if any(error.startswith(projection_prefixes) for error in errors):
        return "dependency_contract_projection_drift"
    return "dependency_contract_drift"


def verify() -> dict[str, Any]:
    errors = list(validate_contract())
    source = GOAL_PLANNING.read_text(encoding="utf-8")
    ast.parse(source)

    for phrase in _STALE_PHRASES:
        if phrase in source:
            errors.append(f"stale_projection_text:{phrase}")

    verifier_source = _class_method_source(
        source,
        "ModelGoalAlignmentVerifier",
        "verify",
    )
    candidate_call = "render_candidate_blind_dependency_rule()"
    repair_call = "render_dependency_format_repair_rule()"
    if candidate_call not in verifier_source:
        errors.append("candidate_blind_projection_not_generated")
    if repair_call not in verifier_source:
        errors.append("format_repair_projection_not_generated")

    for renderer in (
        "render_candidate_blind_dependency_rule",
        "render_dependency_format_repair_rule",
    ):
        if renderer not in source:
            errors.append(f"missing_projection_renderer:{renderer}")

    overlap_source = _function_source(
        source,
        "_dependency_basis_overlaps_requested_output",
    )
    if "dependency_basis_conflicts_with_requested_outputs" not in overlap_source:
        errors.append("structural_projection_not_delegated")
    if "basis == output_span" in overlap_source or "output_span in basis" in overlap_source:
        errors.append("duplicated_structural_semantics")

    manifest_payload: dict[str, Any] | None = None
    if not MANIFEST.is_file():
        errors.append("projection_manifest_missing")
    else:
        loaded = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            errors.append("projection_manifest_not_object")
        else:
            manifest_payload = loaded
            if not verify_projection_manifest(loaded):
                errors.append("projection_manifest_drift")

    mutations = mutation_detection_matrix()
    for mutation, detected in mutations.items():
        if detected is not True:
            errors.append(f"mutation_not_detected:{mutation}")

    errors = list(dict.fromkeys(errors))
    return {
        "schema": "dependency-basis-contract-verification@2",
        "status": "PASS" if not errors else "FAIL",
        "failure_kind": _failure_kind(errors),
        "invariant_id": INVARIANT_ID,
        "guard_id": GUARD_ID,
        "contract_id": CONTRACT_ID,
        "contract_sha256": contract_fingerprint(),
        "final_dependency_authority": FINAL_DEPENDENCY_AUTHORITY,
        "authority_effect": False,
        "projection_manifest": MANIFEST.relative_to(ROOT).as_posix(),
        "manifest_loaded": manifest_payload is not None,
        "mutation_detection": mutations,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()

    if args.write_manifest:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(
            json.dumps(
                projection_manifest(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    try:
        result = verify()
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
        result = {
            "schema": "dependency-basis-contract-verification@2",
            "status": "FAIL",
            "failure_kind": "dependency_contract_verifier_failure",
            "invariant_id": INVARIANT_ID,
            "guard_id": GUARD_ID,
            "errors": [str(exc)],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
