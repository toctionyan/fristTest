#!/usr/bin/env python3
from __future__ import annotations

"""One-shot migration: bind every automatic repair to an existing machine guard.

The migration is temporary authority only and is deleted by its workflow after
successful focused validation.  It deliberately fails closed on unexpected
source shapes.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_if_needed(path: str, old: str, new: str, *, marker: str, label: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    if marker in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def authority() -> None:
    path = "scripts/github_repair_authority.py"
    replace_if_needed(
        path,
        """def _require_binding(binding: Mapping[str, Any]) -> None:\n    missing = [field for field in _BINDING_FIELDS if not str(binding.get(field) or \"\").strip()]\n    if missing:\n        raise RepairAuthorityError(f\"failure binding is missing: {missing}\")\n\n\n""",
        """def _require_binding(binding: Mapping[str, Any]) -> None:\n    missing = [field for field in _BINDING_FIELDS if not str(binding.get(field) or \"\").strip()]\n    if missing:\n        raise RepairAuthorityError(f\"failure binding is missing: {missing}\")\n\n\ndef required_guard_ids(failure_case: Mapping[str, Any]) -> tuple[str, ...]:\n    \"\"\"Return the immutable machine guards that originally caught this failure.\"\"\"\n\n    rows = failure_case.get(\"failed_gates\")\n    if not isinstance(rows, list):\n        return ()\n    result: list[str] = []\n    for row in rows:\n        if not isinstance(row, dict):\n            continue\n        gate_id = str(row.get(\"gate_id\") or \"\").strip()\n        if not gate_id or \"\\n\" in gate_id or \"\\r\" in gate_id:\n            continue\n        if gate_id not in result:\n            result.append(gate_id)\n    return tuple(result)\n\n\n""",
        marker="def required_guard_ids(",
        label="insert required guard compiler",
    )

    replace_if_needed(
        path,
        """    binding = failure_binding(failure_case)\n    grant: dict[str, Any] = {\n""",
        """    guard_ids = required_guard_ids(failure_case)\n    if not guard_ids:\n        raise RepairAuthorityError(\n            \"write authority requires at least one existing machine guard from failed_gates\"\n        )\n    binding = failure_binding(failure_case)\n    grant: dict[str, Any] = {\n""",
        marker="write authority requires at least one existing machine guard",
        label="require machine guard before write",
    )
    replace_if_needed(
        path,
        """        \"allowed_paths\": list(recommended),\n        \"write_scope_mode\": \"exact_allowlist\",\n""",
        """        \"allowed_paths\": list(recommended),\n        \"required_guard_ids\": list(guard_ids),\n        \"write_scope_mode\": \"exact_allowlist\",\n""",
        marker='"required_guard_ids": list(guard_ids)',
        label="persist required guard ids",
    )
    replace_if_needed(
        path,
        """    granted_paths = normalize_paths(grant.get(\"allowed_paths\") or [])\n    if granted_paths != recommended:\n""",
        """    expected_guard_ids = required_guard_ids(failure_case)\n    if not expected_guard_ids:\n        raise RepairAuthorityError(\n            \"write authority requires at least one existing machine guard from failed_gates\"\n        )\n    actual_guard_ids = tuple(str(item or \"\").strip() for item in grant.get(\"required_guard_ids\") or [])\n    if actual_guard_ids != expected_guard_ids:\n        raise RepairAuthorityError(\n            \"write-grant permanent guard binding mismatch: \"\n            f\"expected={list(expected_guard_ids)} actual={list(actual_guard_ids)}\"\n        )\n\n    granted_paths = normalize_paths(grant.get(\"allowed_paths\") or [])\n    if granted_paths != recommended:\n""",
        marker="write-grant permanent guard binding mismatch",
        label="validate permanent guard binding",
    )


def orchestrator() -> None:
    path = "scripts/github_repair_orchestrator.py"
    replace_if_needed(
        path,
        """        \"allowed_paths\": list(allowed_paths),\n    }\n""",
        """        \"allowed_paths\": list(allowed_paths),\n        \"required_guard_ids\": list(grant.get(\"required_guard_ids\") or []),\n    }\n""",
        marker='"required_guard_ids": list(grant.get("required_guard_ids") or [])',
        label="authority evidence guard ids",
    )
    replace_if_needed(
        path,
        """                \"write_scope\": list(allowed_paths),\n                \"patch\": str(patch_path),\n""",
        """                \"write_scope\": list(allowed_paths),\n                \"required_guard_ids\": list(grant.get(\"required_guard_ids\") or []),\n                \"patch\": str(patch_path),\n""",
        marker='"required_guard_ids": list(grant.get("required_guard_ids") or []),\n                "patch"',
        label="stage2 result guard ids",
    )


def handoff() -> None:
    path = "scripts/github_stage2_handoff.py"
    replace_if_needed(
        path,
        """    scope = result.get(\"write_scope\")\n    changed = result.get(\"changed_paths\")\n""",
        """    guard_ids = result.get(\"required_guard_ids\")\n    if (\n        not isinstance(guard_ids, list)\n        or not guard_ids\n        or any(not isinstance(item, str) or not item.strip() for item in guard_ids)\n        or len(set(guard_ids)) != len(guard_ids)\n    ):\n        raise HandoffError(\"Stage-2 permanent machine guard binding is missing or invalid\")\n    scope = result.get(\"write_scope\")\n    changed = result.get(\"changed_paths\")\n""",
        marker="Stage-2 permanent machine guard binding is missing or invalid",
        label="stage2 handoff guard ids",
    )


def stage3() -> None:
    path = "scripts/github_repair_stage3.py"
    replace_if_needed(
        path,
        """    for field in (\n        \"repository\",\n""",
        """    guard_ids = result.get(\"required_guard_ids\")\n    if (\n        not isinstance(guard_ids, list)\n        or not guard_ids\n        or any(not isinstance(item, str) or not item.strip() for item in guard_ids)\n        or len(set(guard_ids)) != len(guard_ids)\n    ):\n        raise Stage3Error(\"Stage-2 result lacks immutable permanent guard IDs\")\n    for field in (\n        \"repository\",\n""",
        marker="Stage-2 result lacks immutable permanent guard IDs",
        label="stage3 result guard preflight",
    )
    replace_if_needed(
        path,
        """    result_scope = tuple(_normalize_path(str(item)) for item in result.get(\"write_scope\") or [])\n    if result_scope != granted:\n""",
        """    result_guard_ids = tuple(str(item or \"\").strip() for item in result.get(\"required_guard_ids\") or [])\n    grant_guard_ids = tuple(str(item or \"\").strip() for item in grant.get(\"required_guard_ids\") or [])\n    if result_guard_ids != grant_guard_ids or not result_guard_ids:\n        raise Stage3Error(\"Stage-2 permanent guard binding does not equal the write grant\")\n    result_scope = tuple(_normalize_path(str(item)) for item in result.get(\"write_scope\") or [])\n    if result_scope != granted:\n""",
        marker="Stage-2 permanent guard binding does not equal the write grant",
        label="stage3 authority bundle guard equality",
    )
    replace_if_needed(
        path,
        """        \"write_scope\": list(granted),\n        \"patch_sha256\": digest,\n""",
        """        \"write_scope\": list(granted),\n        \"required_guard_ids\": list(result.get(\"required_guard_ids\") or []),\n        \"patch_sha256\": digest,\n""",
        marker='"required_guard_ids": list(result.get("required_guard_ids") or [])',
        label="stage3 handoff output guard ids",
    )
    replace_if_needed(
        path,
        """        \"write_grant_sha256\": plan.get(\"write_grant_sha256\"),\n        \"governed_repair_state\": \"INDEPENDENT_REVIEW\",\n""",
        """        \"write_grant_sha256\": plan.get(\"write_grant_sha256\"),\n        \"required_guard_ids\": plan.get(\"required_guard_ids\"),\n        \"governed_repair_state\": \"INDEPENDENT_REVIEW\",\n""",
        marker='"required_guard_ids": plan.get("required_guard_ids"),\n        "governed_repair_state": "INDEPENDENT_REVIEW"',
        label="targeted evidence guard ids",
    )

    replace_if_needed(
        path,
        """def record_validation(\n""",
        """def _require_permanent_guard_reverified(\n    summary: dict[str, Any],\n    guard_ids: Iterable[str],\n) -> dict[str, str]:\n    \"\"\"Require every original machine guard to be mandatory and PASS in Quick.\"\"\"\n\n    required = {str(item) for item in summary.get(\"required_gate_ids\") or []}\n    statuses = {\n        str(row.get(\"id\")): str(row.get(\"status\"))\n        for row in summary.get(\"results\") or []\n        if isinstance(row, dict)\n    }\n    guards = tuple(str(item or \"\").strip() for item in guard_ids)\n    if not guards or any(not item for item in guards) or len(set(guards)) != len(guards):\n        raise Stage3Error(\"permanent_guard_not_reverified: invalid required_guard_ids\")\n    missing_required = [item for item in guards if item not in required]\n    failed = [item for item in guards if statuses.get(item) != \"PASS\"]\n    if missing_required or failed:\n        raise Stage3Error(\n            \"permanent_guard_not_reverified: \"\n            f\"not_required={missing_required} not_pass={failed}\"\n        )\n    return {item: statuses[item] for item in guards}\n\n\ndef record_validation(\n""",
        marker="def _require_permanent_guard_reverified(",
        label="insert stage3 permanent guard verifier",
    )
    replace_if_needed(
        path,
        """    summary = validate_quick_evidence(quick_summary_path)\n    workspace = workspace.resolve()\n""",
        """    summary = validate_quick_evidence(quick_summary_path)\n    guard_proof = _require_permanent_guard_reverified(\n        summary,\n        plan.get(\"required_guard_ids\") or [],\n    )\n    workspace = workspace.resolve()\n""",
        marker="guard_proof = _require_permanent_guard_reverified(",
        label="invoke permanent guard verifier",
    )
    replace_if_needed(
        path,
        """                \"evidence\": [str(targeted_result_path), f\"candidate-sha:{candidate_sha}\"],\n""",
        """                \"evidence\": [\n                    str(targeted_result_path),\n                    str(quick_summary_path),\n                    f\"candidate-sha:{candidate_sha}\",\n                    *[f\"permanent-guard:{gate}:PASS\" for gate in guard_proof],\n                ],\n""",
        marker="permanent-guard:{gate}:PASS",
        label="bind permanent guard evidence into G2",
    )
    replace_if_needed(
        path,
        """        \"write_grant_sha256\": plan.get(\"write_grant_sha256\"),\n        \"violated_invariant\": plan.get(\"violated_invariant\"),\n""",
        """        \"write_grant_sha256\": plan.get(\"write_grant_sha256\"),\n        \"required_guard_ids\": plan.get(\"required_guard_ids\"),\n        \"permanent_guard_reverification\": guard_proof,\n        \"violated_invariant\": plan.get(\"violated_invariant\"),\n""",
        marker='"permanent_guard_reverification": guard_proof',
        label="persist permanent guard proof",
    )


def stage3_publish() -> None:
    path = "scripts/github_repair_stage3_publish.py"
    replace_if_needed(
        path,
        """        \"write_grant_sha256\",\n        \"violated_invariant\",\n""",
        """        \"write_grant_sha256\",\n        \"required_guard_ids\",\n        \"violated_invariant\",\n""",
        marker='"required_guard_ids",\n        "violated_invariant"',
        label="publication metadata guard binding",
    )
    replace_if_needed(
        path,
        """        \"write_grant_sha256\": str(plan.get(\"write_grant_sha256\")),\n        \"violated_invariant\": str(plan.get(\"violated_invariant\")),\n""",
        """        \"write_grant_sha256\": str(plan.get(\"write_grant_sha256\")),\n        \"required_guard_ids\": list(plan.get(\"required_guard_ids\") or []),\n        \"violated_invariant\": str(plan.get(\"violated_invariant\")),\n""",
        marker='"required_guard_ids": list(plan.get("required_guard_ids") or [])',
        label="publication output guard ids",
    )


def publication_receipt() -> None:
    path = "scripts/github_repair_stage3_record_publication.py"
    replace_if_needed(
        path,
        """        \"write_grant_sha256\",\n        \"violated_invariant\",\n""",
        """        \"write_grant_sha256\",\n        \"required_guard_ids\",\n        \"violated_invariant\",\n""",
        marker='"required_guard_ids",\n        "violated_invariant"',
        label="Draft publication guard binding",
    )


def governance_chain() -> None:
    replace_if_needed(
        "scripts/github_repair_governance.py",
        """        \"write_grant_sha256\": publication.get(\"write_grant_sha256\"),\n        \"violated_invariant\": publication.get(\"violated_invariant\"),\n""",
        """        \"write_grant_sha256\": publication.get(\"write_grant_sha256\"),\n        \"required_guard_ids\": list(publication.get(\"required_guard_ids\") or []),\n        \"violated_invariant\": publication.get(\"violated_invariant\"),\n""",
        marker='"required_guard_ids": list(publication.get("required_guard_ids") or [])',
        label="governance guard ids",
    )
    replace_if_needed(
        "scripts/github_repair_baseline_acceptance.py",
        """        \"write_grant_sha256\": governance.get(\"write_grant_sha256\"),\n        \"governance_sha256\": governance.get(\"governance_sha256\"),\n""",
        """        \"write_grant_sha256\": governance.get(\"write_grant_sha256\"),\n        \"required_guard_ids\": list(governance.get(\"required_guard_ids\") or []),\n        \"governance_sha256\": governance.get(\"governance_sha256\"),\n""",
        marker='"required_guard_ids": list(governance.get("required_guard_ids") or [])',
        label="baseline receipt guard ids",
    )
    replace_if_needed(
        "scripts/github_repair_exact_head.py",
        """        \"write_grant_sha256\": baseline.get(\"write_grant_sha256\"),\n        \"governance_sha256\": baseline.get(\"governance_sha256\"),\n""",
        """        \"write_grant_sha256\": baseline.get(\"write_grant_sha256\"),\n        \"required_guard_ids\": list(baseline.get(\"required_guard_ids\") or []),\n        \"governance_sha256\": baseline.get(\"governance_sha256\"),\n""",
        marker='"required_guard_ids": list(baseline.get("required_guard_ids") or [])',
        label="exact head receipt guard ids",
    )


def tests() -> None:
    replace_if_needed(
        "skill-system/tests/test_github_repair_authority.py",
        """            \"candidate_paths\": [self.path],\n        }\n""",
        """            \"candidate_paths\": [self.path],\n            \"failed_gates\": [{\"gate_id\": \"semantic-contract\", \"status\": \"FAIL\"}],\n        }\n""",
        marker='"failed_gates": [{"gate_id": "semantic-contract"',
        label="authority fixture guard",
    )
    replace_if_needed(
        "skill-system/tests/test_github_repair_authority.py",
        """    def test_repeated_failure_revokes_write(self) -> None:\n""",
        """    def test_missing_machine_guard_denies_write_authority(self) -> None:\n        failure = dict(self.failure)\n        failure[\"failed_gates\"] = []\n        rca = dict(self.rca)\n        rca[\"failure_case_sha256\"] = failure_case_fingerprint(failure)\n        rca[\"binding\"] = failure_binding(failure)\n        rca[\"rca_sha256\"] = rca_fingerprint(rca)\n        with self.assertRaises(RepairAuthorityError):\n            compile_write_grant(\n                failure_case=failure,\n                rca=rca,\n                candidate_paths=failure[\"candidate_paths\"],\n            )\n\n    def test_tampered_permanent_guard_binding_is_rejected(self) -> None:\n        grant = compile_write_grant(\n            failure_case=self.failure,\n            rca=self.rca,\n            candidate_paths=self.failure[\"candidate_paths\"],\n        )\n        grant[\"required_guard_ids\"] = [\"different-gate\"]\n        from github_repair_authority import write_grant_fingerprint\n        grant[\"write_grant_sha256\"] = write_grant_fingerprint(grant)\n        with self.assertRaises(RepairAuthorityError):\n            validate_write_grant(\n                grant,\n                failure_case=self.failure,\n                rca=self.rca,\n                candidate_paths=self.failure[\"candidate_paths\"],\n            )\n\n    def test_repeated_failure_revokes_write(self) -> None:\n""",
        marker="test_missing_machine_guard_denies_write_authority",
        label="authority permanent guard tests",
    )
    replace_if_needed(
        "skill-system/tests/test_github_repair_stage3_authority.py",
        """            \"candidate_paths\": [self.path],\n        }\n""",
        """            \"candidate_paths\": [self.path],\n            \"failed_gates\": [{\"gate_id\": \"semantic-contract\", \"status\": \"FAIL\"}],\n        }\n""",
        marker='"failed_gates": [{"gate_id": "semantic-contract"',
        label="stage3 fixture guard",
    )
    replace_if_needed(
        "skill-system/tests/test_github_repair_stage3_authority.py",
        """            \"write_scope\": [self.path],\n            \"changed_paths\": [self.path],\n""",
        """            \"write_scope\": [self.path],\n            \"required_guard_ids\": [\"semantic-contract\"],\n            \"changed_paths\": [self.path],\n""",
        marker='"required_guard_ids": ["semantic-contract"]',
        label="stage3 result guard",
    )
    replace_if_needed(
        "skill-system/tests/test_github_repair_stage3_authority.py",
        """    def test_legacy_stage3_complete_path_is_fail_closed(self) -> None:\n""",
        """    def test_permanent_guard_must_be_mandatory_and_pass(self) -> None:\n        summary = {\n            \"required_gate_ids\": [\"semantic-contract\"],\n            \"results\": [{\"id\": \"semantic-contract\", \"status\": \"PASS\"}],\n        }\n        self.assertEqual(\n            stage3._require_permanent_guard_reverified(summary, [\"semantic-contract\"]),\n            {\"semantic-contract\": \"PASS\"},\n        )\n        with self.assertRaises(stage3.Stage3Error):\n            stage3._require_permanent_guard_reverified(\n                {\n                    \"required_gate_ids\": [\"other\"],\n                    \"results\": [{\"id\": \"semantic-contract\", \"status\": \"PASS\"}],\n                },\n                [\"semantic-contract\"],\n            )\n        with self.assertRaises(stage3.Stage3Error):\n            stage3._require_permanent_guard_reverified(\n                {\n                    \"required_gate_ids\": [\"semantic-contract\"],\n                    \"results\": [{\"id\": \"semantic-contract\", \"status\": \"FAIL\"}],\n                },\n                [\"semantic-contract\"],\n            )\n\n    def test_legacy_stage3_complete_path_is_fail_closed(self) -> None:\n""",
        marker="test_permanent_guard_must_be_mandatory_and_pass",
        label="stage3 permanent guard reverify tests",
    )


def architecture_gate() -> None:
    path = "scripts/verify_governed_repair_architecture.py"
    replace_if_needed(
        path,
        """        \"ARCHITECTURE_REPLAN_AND_NEW_RCA\",\n""",
        """        \"ARCHITECTURE_REPLAN_AND_NEW_RCA\",\n        \"required_guard_ids\",\n""",
        marker='"required_guard_ids",\n',
        label="architecture gate guard marker",
    )
    replace_if_needed(
        path,
        """    for state in REQUIRED_STATES:\n""",
        """    if \"permanent_guard_not_reverified\" not in aggregate:\n        errors.append(\"permanent_guard_reverification_missing\")\n    for state in REQUIRED_STATES:\n""",
        marker="permanent_guard_reverification_missing",
        label="architecture gate permanent guard check",
    )


def main() -> int:
    authority()
    orchestrator()
    handoff()
    stage3()
    stage3_publish()
    publication_receipt()
    governance_chain()
    tests()
    architecture_gate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
