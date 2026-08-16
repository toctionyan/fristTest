#!/usr/bin/env python3
from __future__ import annotations

"""Executable mutation proof for dependency-basis semantic drift prevention.

The proof never edits the repository under test.  It copies the complete
contract/projection surface to isolated temporary roots, proves the live surface
is GREEN, then deliberately corrupts three different layers and requires the
real verifier to turn RED with the expected machine class:

1. runtime prompt projection stops calling the canonical renderer;
2. generated projection manifest drifts from the compiler output;
3. canonical rule source changes without regenerating its projection.

This is deliberately stronger than an in-memory assertion: the exact repository
verifier is executed against actual mutated files.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = "scripts/verify_dependency_basis_contract.py"
CONTRACT = "services/agent-service/src/agent_core/goal_graph/dependency_basis_contract.py"
GOAL_PLANNING = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
MANIFEST = "contracts/generated/dependency-basis-projections.json"
SURFACE = (VERIFIER, CONTRACT, GOAL_PLANNING, MANIFEST)
MAX_OUTPUT = 40_000


class MutationProofError(RuntimeError):
    """The mutation harness could not establish a trustworthy proof."""


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


def _copy_surface(destination: Path) -> None:
    for relative in SURFACE:
        source = ROOT / relative
        if not source.is_file():
            raise MutationProofError(f"required semantic surface missing: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _run_verifier(root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", str(root / VERIFIER)],
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
        diagnostic = (completed.stdout + "\n" + completed.stderr)[-MAX_OUTPUT:]
        raise MutationProofError(
            f"dependency verifier did not return JSON: {diagnostic}"
        ) from exc
    if not isinstance(payload, dict):
        raise MutationProofError("dependency verifier JSON must be an object")
    payload["process_exit_code"] = completed.returncode
    return payload


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise MutationProofError(
            f"mutation target must occur exactly once in {path.name}: {old!r}"
        )
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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _mutate_canonical_rule_without_projection(root: Path) -> None:
    _replace_once(
        root / CONTRACT,
        '"strict_nested_requested_output": "allowed"',
        '"strict_nested_requested_output": "forbidden"',
    )


def _kill_mutation(
    name: str,
    mutation: Callable[[Path], None],
    *,
    expected_error: str,
    expected_failure_kind: str = "dependency_contract_projection_drift",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"dependency-basis-{name}-") as temp:
        isolated = Path(temp)
        _copy_surface(isolated)
        baseline = _run_verifier(isolated)
        if baseline.get("status") != "PASS" or baseline.get("process_exit_code") != 0:
            return {
                "name": name,
                "killed": False,
                "reason": "isolated_baseline_not_green",
                "baseline": baseline,
            }
        mutation(isolated)
        observed = _run_verifier(isolated)
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


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    if root != ROOT.resolve():
        raise MutationProofError("custom repository roots are not supported")

    before = _surface_fingerprint(root)
    live = _run_verifier(root)
    if live.get("status") != "PASS" or live.get("process_exit_code") != 0:
        return {
            "schema": "dependency-basis-mutation-proof@1",
            "status": "FAIL",
            "reason": "live_contract_not_green",
            "live": live,
            "workspace_unchanged": _surface_fingerprint(root) == before,
            "production_closed": False,
        }

    proofs = [
        _kill_mutation(
            "runtime_projection_copy_drift",
            _mutate_runtime_projection,
            expected_error="candidate_blind_projection_not_generated",
        ),
        _kill_mutation(
            "generated_manifest_drift",
            _mutate_generated_manifest,
            expected_error="projection_manifest_drift",
        ),
        _kill_mutation(
            "canonical_rule_changed_without_projection",
            _mutate_canonical_rule_without_projection,
            expected_error="projection_manifest_drift",
        ),
    ]
    workspace_unchanged = _surface_fingerprint(root) == before
    all_killed = all(row.get("killed") is True for row in proofs)
    return {
        "schema": "dependency-basis-mutation-proof@1",
        "status": "PASS" if all_killed and workspace_unchanged else "FAIL",
        "invariant_id": "DEP-BASIS-CONTRACT-001",
        "guard_id": "dependency-basis-contract-mutation-proof",
        "mutations": proofs,
        "all_mutations_killed": all_killed,
        "workspace_unchanged": workspace_unchanged,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def main() -> int:
    try:
        result = verify()
    except (OSError, MutationProofError, subprocess.SubprocessError) as exc:
        result = {
            "schema": "dependency-basis-mutation-proof@1",
            "status": "FAIL",
            "reason": "mutation_harness_failure",
            "errors": [str(exc)],
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
