#!/usr/bin/env python3
from __future__ import annotations

"""Non-Judge permanent guard for the Release 56 dependency-basis invariant.

This module deliberately lives under ``skill-system/tests`` rather than a trusted
Judge path.  It validates product semantics but has no authority to redefine the
Quality controller, policy, Skill profiles, or deterministic dependency reducer.
The mutation proof copies the real product/projection surface to isolated temporary
roots and requires the same guard to turn RED for three independent drift classes.
"""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.goal_graph.dependency_basis_contract import (  # noqa: E402
    CONTRACT_ID,
    FINAL_DEPENDENCY_AUTHORITY,
    contract_fingerprint,
    mutation_detection_matrix,
    validate_contract,
    verify_projection_manifest,
)

INVARIANT_ID = "DEP-BASIS-CONTRACT-001"
CONTRACT_GUARD_ID = "dependency-basis-contract"
MUTATION_GUARD_ID = "dependency-basis-contract-mutation-proof"
HELPER = "skill-system/tests/dependency_basis_guard.py"
CONTRACT = "services/agent-service/src/agent_core/goal_graph/dependency_basis_contract.py"
GOAL_PLANNING = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
MANIFEST = "contracts/generated/dependency-basis-projections.json"
SURFACE = (HELPER, CONTRACT, GOAL_PLANNING, MANIFEST)
MAX_OUTPUT = 40_000

_STALE_PHRASES = (
    "must be disjoint from the dependent Goal requested_outputs evidence spans",
    "if no disjoint relation-only basis exists",
)


class GuardError(RuntimeError):
    pass


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise GuardError(f"function not found: {name}")


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
    raise GuardError(f"class method not found: {class_name}.{method_name}")


def _failure_kind(errors: list[str]) -> str | None:
    if not errors:
        return None
    if any(error.startswith("mutation_not_detected:") for error in errors):
        return "dependency_contract_mutation_gap"
    projection_prefixes = (
        "stale_projection_text:",
        "candidate_blind_projection_not_generated",
        "format_repair_projection_not_generated",
        "structural_projection_not_delegated",
        "duplicated_structural_semantics",
        "projection_manifest_",
    )
    if any(error.startswith(projection_prefixes) for error in errors):
        return "dependency_contract_projection_drift"
    return "dependency_contract_drift"


def verify_contract(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors = list(validate_contract())
    goal_planning = root / GOAL_PLANNING
    manifest = root / MANIFEST
    source = goal_planning.read_text(encoding="utf-8")
    ast.parse(source)

    for phrase in _STALE_PHRASES:
        if phrase in source:
            errors.append(f"stale_projection_text:{phrase}")

    verifier_source = _class_method_source(source, "ModelGoalAlignmentVerifier", "verify")
    if "render_candidate_blind_dependency_rule()" not in verifier_source:
        errors.append("candidate_blind_projection_not_generated")
    if "render_dependency_format_repair_rule()" not in verifier_source:
        errors.append("format_repair_projection_not_generated")

    overlap_source = _function_source(source, "_dependency_basis_overlaps_requested_output")
    if "dependency_basis_conflicts_with_requested_outputs" not in overlap_source:
        errors.append("structural_projection_not_delegated")
    if "basis == output_span" in overlap_source or "output_span in basis" in overlap_source:
        errors.append("duplicated_structural_semantics")

    manifest_loaded = False
    if not manifest.is_file():
        errors.append("projection_manifest_missing")
    else:
        loaded = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            errors.append("projection_manifest_not_object")
        else:
            manifest_loaded = True
            if not verify_projection_manifest(loaded):
                errors.append("projection_manifest_drift")

    mutations = mutation_detection_matrix()
    for mutation, detected in mutations.items():
        if detected is not True:
            errors.append(f"mutation_not_detected:{mutation}")

    errors = list(dict.fromkeys(errors))
    return {
        "schema": "dependency-basis-contract-verification@3",
        "status": "PASS" if not errors else "FAIL",
        "failure_kind": _failure_kind(errors),
        "invariant_id": INVARIANT_ID,
        "guard_id": CONTRACT_GUARD_ID,
        "contract_id": CONTRACT_ID,
        "contract_sha256": contract_fingerprint(),
        "final_dependency_authority": FINAL_DEPENDENCY_AUTHORITY,
        "authority_effect": False,
        "manifest_loaded": manifest_loaded,
        "errors": errors,
    }


def _surface_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(SURFACE):
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_surface(source_root: Path, destination: Path) -> None:
    for relative in SURFACE:
        source = source_root / relative
        if not source.is_file():
            raise GuardError(f"required semantic surface missing: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _run_contract(root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", str(root / HELPER), "--check", "contract"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        env=env,
    )
    raw = (completed.stdout or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        diagnostic = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-MAX_OUTPUT:]
        raise GuardError(f"dependency guard did not return JSON: {diagnostic}") from exc
    if not isinstance(payload, dict):
        raise GuardError("dependency guard JSON must be an object")
    payload["process_exit_code"] = completed.returncode
    return payload


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise GuardError(f"mutation target must occur exactly once in {path.name}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _mutate_runtime_projection(root: Path) -> None:
    _replace_once(
        root / GOAL_PLANNING,
        "render_candidate_blind_dependency_rule()",
        '"DRIFTED HANDWRITTEN DEPENDENCY RULE"',
    )


def _mutate_generated_manifest(root: Path) -> None:
    path = root / MANIFEST
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["projections"]["candidate_blind_dependency_rule"] += " DRIFT"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _mutate_canonical_rule_without_projection(root: Path) -> None:
    _replace_once(
        root / CONTRACT,
        '"strict_nested_requested_output": "allowed"',
        '"strict_nested_requested_output": "forbidden"',
    )


def _kill_mutation(
    source_root: Path,
    name: str,
    mutation: Callable[[Path], None],
    *,
    expected_error: str,
    expected_failure_kind: str = "dependency_contract_projection_drift",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"dependency-basis-{name}-") as temp:
        isolated = Path(temp)
        _copy_surface(source_root, isolated)
        baseline = _run_contract(isolated)
        if baseline.get("status") != "PASS" or baseline.get("process_exit_code") != 0:
            return {"name": name, "killed": False, "reason": "isolated_baseline_not_green", "baseline": baseline}
        mutation(isolated)
        observed = _run_contract(isolated)
        errors = [str(item) for item in observed.get("errors") or []]
        killed = (
            observed.get("status") == "FAIL"
            and observed.get("process_exit_code") != 0
            and observed.get("failure_kind") == expected_failure_kind
            and expected_error in errors
        )
        return {
            "name": name,
            "killed": killed,
            "expected_failure_kind": expected_failure_kind,
            "expected_error": expected_error,
            "observed_failure_kind": observed.get("failure_kind"),
            "observed_errors": errors,
        }


def verify_mutation_proof(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    before = _surface_fingerprint(root)
    live = verify_contract(root)
    if live.get("status") != "PASS":
        return {
            "schema": "dependency-basis-mutation-proof@2",
            "status": "FAIL",
            "reason": "live_contract_not_green",
            "live": live,
            "workspace_unchanged": _surface_fingerprint(root) == before,
            "production_closed": False,
        }
    proofs = [
        _kill_mutation(
            root,
            "runtime_projection_copy_drift",
            _mutate_runtime_projection,
            expected_error="candidate_blind_projection_not_generated",
        ),
        _kill_mutation(
            root,
            "generated_manifest_drift",
            _mutate_generated_manifest,
            expected_error="projection_manifest_drift",
        ),
        _kill_mutation(
            root,
            "canonical_rule_changed_without_projection",
            _mutate_canonical_rule_without_projection,
            expected_error="mutation_not_detected:nested_rule_flipped",
            expected_failure_kind="dependency_contract_mutation_gap",
        ),
    ]
    workspace_unchanged = _surface_fingerprint(root) == before
    all_killed = all(row.get("killed") is True for row in proofs)
    return {
        "schema": "dependency-basis-mutation-proof@2",
        "status": "PASS" if all_killed and workspace_unchanged else "FAIL",
        "invariant_id": INVARIANT_ID,
        "guard_id": MUTATION_GUARD_ID,
        "mutations": proofs,
        "all_mutations_killed": all_killed,
        "workspace_unchanged": workspace_unchanged,
        "authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("contract", "mutation"), required=True)
    args = parser.parse_args()
    try:
        result = verify_contract() if args.check == "contract" else verify_mutation_proof()
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError, GuardError, subprocess.SubprocessError) as exc:
        result = {
            "schema": "dependency-basis-guard-error@1",
            "status": "FAIL",
            "guard_id": CONTRACT_GUARD_ID if args.check == "contract" else MUTATION_GUARD_ID,
            "errors": [str(exc)],
            "authority_effect": False,
            "production_closed": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
