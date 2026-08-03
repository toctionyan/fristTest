from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .common import _clean_text, _now, _sha256_text
from .constants import *
from .convergence import _failure_classification
from .environment import (
    _environment_problem, _python_ast_parse, _run_shell, _runtime_environment_block_evidence,
    _structured_stdout_payload,
)

def _run_step(workspace: Path, evidence_dir: Path, mode: str, step: dict[str, Any]) -> dict[str, Any]:
    started_at = _now()
    missing = _environment_problem(workspace, step)
    if missing:
        raw = {
            "exit_code": 78,
            "stdout": "",
            "stderr": "missing or unavailable required environment: " + ", ".join(missing),
            "metadata": {"missing_environment": missing},
        }
        status = BLOCKED
    else:
        try:
            kind = str(step.get("kind", "shell"))
            if kind == "python_ast_parse":
                raw = _python_ast_parse(workspace)
            elif kind == "shell":
                raw = _run_shell(workspace, evidence_dir, mode, step)
            else:
                raw = {"exit_code": 2, "stdout": "", "stderr": f"unknown step kind: {kind}", "metadata": {}}
        except Exception as exc:  # fail closed while preserving repair evidence
            raw = {"exit_code": 2, "stdout": "", "stderr": repr(exc), "metadata": {"exception": exc.__class__.__name__}}
        blocked_codes = {int(code) for code in step.get("blocked_exit_codes") or []}
        runtime_block = _runtime_environment_block_evidence(raw)
        if runtime_block is not None:
            raw.setdefault("metadata", {})["runtime_environment_block"] = runtime_block
        status = BLOCKED if (
            int(raw["exit_code"]) in blocked_codes or runtime_block is not None
        ) else (PASS if int(raw["exit_code"]) == 0 else FAIL)
    structured_payload = _structured_stdout_payload(raw)
    if structured_payload is not None:
        raw.setdefault("metadata", {})["structured_assessment"] = structured_payload
    ended_at = _now()
    return {
        "id": step["id"],
        "name": step.get("name", step["id"]),
        "status": status,
        "owner": step.get("owner", "unassigned"),
        "category": step.get("category", "verification"),
        "blocking_level": step.get("blocking_level", "required"),
        "repair_playbook": step.get("repair_playbook", "apply the smallest fix within the declared target scope"),
        "depends_on": list(step.get("depends_on") or []),
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": raw["exit_code"],
        "duration_ms": raw.get("duration_ms"),
        "stdout": _clean_text(raw.get("stdout") or ""),
        "stderr": _clean_text(raw.get("stderr") or ""),
        "metadata": raw.get("metadata") or {},
    }

def _validate_policy(policy: dict[str, Any]) -> list[dict[str, Any]]:
    steps = policy.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("quality-loop policy must contain a non-empty steps array")
    ids = [str(step.get("id") or "") for step in steps if isinstance(step, dict)]
    if len(ids) != len(steps) or not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("quality-loop step ids must be non-empty and unique")
    by_id = {str(step["id"]): step for step in steps}
    for step in steps:
        for field in ("owner", "category", "blocking_level", "repair_playbook", "rerun_contract"):
            if not str(step.get(field) or "").strip():
                raise ValueError(f"step {step['id']} must declare {field}")
        if str(step.get("blocking_level")) not in BLOCKING_LEVELS:
            raise ValueError(f"step {step['id']} has unknown blocking_level")
        if str(step.get("rerun_contract")) != RERUN_CONTRACT:
            raise ValueError(f"step {step['id']} must use rerun_contract={RERUN_CONTRACT}")
        modes = {str(mode) for mode in step.get("modes") or []}
        if not modes or not modes.issubset(set(MODES)):
            raise ValueError(f"step {step['id']} has invalid modes")
        unknown = set(step.get("depends_on") or []) - set(by_id)
        if unknown:
            raise ValueError(f"step {step['id']} depends on unknown steps: {sorted(unknown)}")
        for dependency in step.get("depends_on") or []:
            if not modes.issubset(set(by_id[str(dependency)].get("modes") or [])):
                raise ValueError(f"step {step['id']} depends on {dependency}, which is absent in one of its modes")
    # Kahn's algorithm detects cycles while retaining policy order for ties.
    remaining = {step_id: set(by_id[step_id].get("depends_on") or []) for step_id in by_id}
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [step_id for step_id in by_id if step_id in remaining and not remaining[step_id]]
        if not ready:
            raise ValueError("quality-loop policy dependency graph contains a cycle")
        for step_id in ready:
            ordered.append(by_id[step_id])
            remaining.pop(step_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return ordered

def _steps_for_mode(ordered_steps: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    return [step for step in ordered_steps if mode in set(step.get("modes") or [])]

def _validate_claim_gate_contracts(
    target: dict[str, Any], ordered_steps: list[dict[str, Any]]
) -> None:
    by_id = {str(step["id"]): step for step in ordered_steps}
    for claim in target["claims"]:
        claim_id = str(claim["id"])
        required_mode = str(claim["required_mode"])
        unknown = [gate for gate in claim["required_gates"] if gate not in by_id]
        if unknown:
            raise ValueError(
                f"claim {claim_id} references unknown required_gates: {unknown}"
            )
        unavailable = [
            gate
            for gate in claim["required_gates"]
            if required_mode not in set(by_id[gate].get("modes") or [])
        ]
        if unavailable:
            raise ValueError(
                f"claim {claim_id} requires mode {required_mode}, but gates are absent in that mode: {unavailable}"
            )
        gate_log_refs = {
            str(ref).removeprefix("gate-log:")
            for ref in claim["evidence_refs"]
            if str(ref).startswith("gate-log:")
        }
        unbound_logs = sorted(gate_log_refs.difference(claim["required_gates"]))
        if unbound_logs:
            raise ValueError(
                f"claim {claim_id} gate-log refs must name required_gates: {unbound_logs}"
            )
        categories = {
            str(by_id[gate].get("category") or "verification")
            for gate in claim["required_gates"]
        }
        evidence_kind = str(claim["evidence_kind"])
        if evidence_kind == "counterexample":
            if MODE_RANK[required_mode] < MODE_RANK["quick"]:
                raise ValueError(f"counterexample claim {claim_id} must require quick or higher mode")
            if not categories.intersection(
                {"counterexample-regression", "unit-contract", "frontend-test", "integration", "preproduction"}
            ):
                raise ValueError(
                    f"counterexample claim {claim_id} has no test or adversarial evidence gate"
                )
        elif evidence_kind == "integration":
            if MODE_RANK[required_mode] < MODE_RANK["integration"]:
                raise ValueError(f"integration claim {claim_id} must require integration or release mode")
            if not categories.intersection({"integration", "preproduction"}):
                raise ValueError(f"integration claim {claim_id} has no integration evidence gate")
        elif evidence_kind == "release-provenance":
            if required_mode != "release":
                raise ValueError(f"release-provenance claim {claim_id} must require release mode")
            if "release" not in categories:
                raise ValueError(f"release-provenance claim {claim_id} has no release artifact evidence gate")

def _junit_case_index(evidence_dir: Path) -> list[dict[str, Any]]:
    """Collect executed pytest cases from this run's JUnit artifacts only."""
    cases: list[dict[str, Any]] = []
    junit_dir = evidence_dir / "junit"
    if not junit_dir.is_dir():
        return cases
    for xml_path in sorted(junit_dir.rglob("*.xml")):
        try:
            root = ET.parse(xml_path).getroot()
        except (ET.ParseError, OSError):
            continue
        for testcase in root.iter("testcase"):
            classname = str(testcase.attrib.get("classname") or "")
            name = str(testcase.attrib.get("name") or "")
            failed = any(
                child.tag.rsplit("}", 1)[-1] in {"failure", "error", "skipped"}
                for child in list(testcase)
            )
            cases.append(
                {
                    "classname": classname,
                    "name": name,
                    "passed": not failed,
                    "file": xml_path.relative_to(evidence_dir).as_posix(),
                }
            )
    return cases

def _test_module_from_ref(path_text: str) -> str:
    parts = list(Path(path_text).parts)
    try:
        index = parts.index("tests")
    except ValueError:
        return ""
    module_parts = parts[index:]
    if module_parts and module_parts[-1].endswith(".py"):
        module_parts[-1] = Path(module_parts[-1]).stem
    return ".".join(module_parts)

def _claim_evidence_ref_statuses(
    claim: dict[str, Any],
    *,
    result_statuses: dict[str, str],
    snapshot_files: set[str],
    junit_cases: list[dict[str, Any]],
) -> dict[str, str]:
    """Prove that claim references were produced by this exact run.

    A source selector is not enough merely because it exists in the repository:
    it must appear as a passing testcase in this run's JUnit output. A gate-log
    reference is verified only when that exact required Gate passed. Bare source
    references are accepted only when they are bound into the current source
    snapshot.
    """
    statuses: dict[str, str] = {}
    for raw_ref in claim["evidence_refs"]:
        ref = str(raw_ref)
        if ref.startswith("gate-log:"):
            gate_id = ref.removeprefix("gate-log:")
            gate_status = result_statuses.get(gate_id, "NOT_EXECUTED")
            statuses[ref] = "VERIFIED" if gate_status == PASS else gate_status
            continue
        path_text, separator, selector = ref.partition("::")
        path_text = path_text.strip()
        selector = selector.strip()
        if path_text not in snapshot_files:
            statuses[ref] = "SOURCE_NOT_BOUND"
            continue
        if not separator:
            statuses[ref] = "VERIFIED"
            continue
        expected_module = _test_module_from_ref(path_text)
        matched = [
            case
            for case in junit_cases
            if case["name"].split("[", 1)[0] == selector
            and (
                not expected_module
                or case["classname"] == expected_module
                or case["classname"].endswith(expected_module)
            )
        ]
        if any(bool(case["passed"]) for case in matched):
            statuses[ref] = "VERIFIED"
        elif matched:
            statuses[ref] = "FAILED"
        else:
            statuses[ref] = "NOT_EXECUTED"
    return statuses

def _gate_is_environment_blocked(
    gate_id: str,
    result_by_id: dict[str, dict[str, Any]],
    *,
    seen: set[str] | None = None,
) -> bool:
    """Return true only when a gate is blocked transitively by environment.

    A downstream gate skipped because an environment prerequisite was missing is
    not a code failure.  Conversely, a mixed dependency set containing any real
    FAIL must remain a failure.
    """
    visited = set(seen or ())
    if gate_id in visited:
        return False
    visited.add(gate_id)
    result = result_by_id.get(gate_id)
    if result is None:
        return False
    status = str(result.get("status") or "NOT_EXECUTED")
    if status == BLOCKED:
        return True
    if status != UPSTREAM_SKIPPED:
        return False
    failed_dependencies = [
        str(item)
        for item in ((result.get("metadata") or {}).get("failed_dependencies") or [])
    ]
    return bool(failed_dependencies) and all(
        _gate_is_environment_blocked(dependency, result_by_id, seen=visited)
        for dependency in failed_dependencies
    )

def _claim_results(
    target: dict[str, Any],
    result_by_id: dict[str, dict[str, Any]],
    *,
    mode: str,
    evidence_dir: Path,
    snapshot_files: set[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    junit_cases = _junit_case_index(evidence_dir)
    result_statuses = {
        str(gate_id): str(result.get("status") or "NOT_EXECUTED")
        for gate_id, result in result_by_id.items()
    }
    for claim in target["claims"]:
        gate_statuses = {
            gate: result_statuses.get(gate, "NOT_EXECUTED")
            for gate in claim["required_gates"]
        }
        environment_blocked_gates = [
            gate
            for gate in claim["required_gates"]
            if _gate_is_environment_blocked(str(gate), result_by_id)
        ]
        evidence_ref_statuses = _claim_evidence_ref_statuses(
            claim,
            result_statuses=result_statuses,
            snapshot_files=snapshot_files,
            junit_cases=junit_cases,
        )
        gate_states = set(gate_statuses.values())
        proof_states = set(evidence_ref_statuses.values())
        if MODE_RANK[mode] < MODE_RANK[str(claim["required_mode"])]:
            status = "INSUFFICIENT_MODE"
        elif FAIL in gate_states or "FAILED" in proof_states:
            status = "FAILED"
        elif environment_blocked_gates:
            status = "BLOCKED_BY_ENVIRONMENT"
        elif UPSTREAM_SKIPPED in gate_states:
            status = "FAILED"
        elif gate_states == {PASS} and proof_states == {"VERIFIED"}:
            status = "VERIFIED"
        else:
            status = "NOT_EXECUTED"
        results.append(
            {
                "id": claim["id"],
                "statement": claim["statement"],
                "risk": claim["risk"],
                "required_mode": claim["required_mode"],
                "evidence_kind": claim["evidence_kind"],
                "required_gates": list(claim["required_gates"]),
                "evidence_refs": list(claim["evidence_refs"]),
                "owner": claim["owner"],
                "closure_requirement": claim["closure_requirement"],
                "gate_statuses": gate_statuses,
                "environment_blocked_gates": environment_blocked_gates,
                "evidence_ref_statuses": evidence_ref_statuses,
                "status": status,
            }
        )
    return results

def _downstream_steps(steps: list[dict[str, Any]], root: str) -> list[dict[str, Any]]:
    by_id = {str(step["id"]): step for step in steps}
    known = set(by_id)
    if root not in known:
        raise ValueError(f"unknown --rerun-from gate: {root}")
    # First identify only the root's affected descendants.  Then close that
    # complete set over prerequisites.  Interleaving these two expansions can
    # either omit a downstream Gate's sibling prerequisite or pull unrelated
    # branches into the selection.
    descendants = {root}
    changed = True
    while changed:
        changed = False
        for step in steps:
            step_id = str(step["id"])
            dependencies = {str(dep) for dep in step.get("depends_on") or []}
            if step_id not in descendants and dependencies.intersection(descendants):
                descendants.add(step_id)
                changed = True
    selected = set(descendants)
    pending = list(descendants)
    while pending:
        current = pending.pop()
        for dependency in by_id[current].get("depends_on") or []:
            dependency = str(dependency)
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [step for step in steps if step["id"] in selected]

def _gate_contract_fingerprints(steps: list[dict[str, Any]]) -> dict[str, str]:
    """Bind evidence to each exact executable Gate contract, not only its id."""
    return {
        str(step["id"]): _sha256_text(
            json.dumps(step, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        for step in steps
    }

def _write_step_evidence(evidence_dir: Path, result: dict[str, Any]) -> None:
    steps_dir = evidence_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    (steps_dir / f"{result['id']}.stdout.txt").write_text(result.get("stdout") or "", encoding="utf-8")
    (steps_dir / f"{result['id']}.stderr.txt").write_text(result.get("stderr") or "", encoding="utf-8")

