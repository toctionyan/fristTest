from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore, stable_task_id

import github_repair_authority as AUTHORITY  # noqa: E402
import github_repair_stage3 as STAGE3  # noqa: E402
import github_repair_stage3_record_publication as RECORD  # noqa: E402
import github_stage2_handoff as HANDOFF  # noqa: E402


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> dict:
    workspace = tmp_path / "candidate"
    source = workspace / "services" / "agent-service" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "baseline")
    head_sha = _git(workspace, "rev-parse", "HEAD")

    source.write_text("value = 1\n", encoding="utf-8")
    patch_text = _git(workspace, "diff", "--no-ext-diff", "--binary") + "\n"
    patch_path = tmp_path / "repair.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    _git(workspace, "reset", "--hard", "HEAD")

    candidate_path = "services/agent-service/app.py"
    failure = {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "owner/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": head_sha,
        "head_branch": "feature/source",
        "source_pr_number": 7,
        "failure_signature": "f" * 64,
        "classification": "code_or_contract",
        "repair_allowed": True,
        "same_repository": True,
        "candidate_paths": [candidate_path],
        "failed_gates": [
            {
                "gate_id": "original-semantic-guard",
                "status": "FAIL",
                "category": "test",
                "owner": "tests",
            }
        ],
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/source",
        "production_closed": False,
    }
    failure_path = tmp_path / "failure-case.json"
    _write(failure_path, failure)

    rca = {
        "schema": AUTHORITY.RCA_SCHEMA,
        "state": "RCA_READ_ONLY",
        "binding": AUTHORITY.failure_binding(failure),
        "failure_case_sha256": AUTHORITY.failure_case_fingerprint(failure),
        "candidate_paths": [candidate_path],
        "repair_round": 1,
        "read_only": True,
        "workspace_mutated": False,
        "workspace_fingerprint_before": "a" * 64,
        "workspace_fingerprint_after": "a" * 64,
        "failure_class": "semantic_contract_drift",
        "violated_invariant": "INV-GOVERNED-REPAIR-TEST",
        "authority_owner": "deterministic-reducer",
        "drifted_projection": "secondary semantic projection",
        "root_cause": "secondary projection drifted from deterministic authority",
        "existing_gate_gap": "same-class drift was not permanently rebound",
        "required_permanent_guard": "original-semantic-guard must remain required and PASS",
        "repair_plan": ["change only the authorized product source"],
        "write_scope_recommendation": {
            "decision": "GRANT",
            "paths": [candidate_path],
        },
        "production_closed": False,
    }
    rca["rca_sha256"] = AUTHORITY.rca_fingerprint(rca)
    rca_path = tmp_path / "rca.json"
    _write(rca_path, rca)

    grant = AUTHORITY.compile_write_grant(
        failure_case=failure,
        rca=rca,
        candidate_paths=[candidate_path],
    )
    grant_path = tmp_path / "write-grant.json"
    _write(grant_path, grant)

    stage2 = {
        "schema": "github-governed-repair-stage2@1",
        "status": "REPAIR_CANDIDATE_READY",
        "workflow_run_id": "123",
        "head_sha": head_sha,
        "failure_signature": "f" * 64,
        "changed_paths": [candidate_path],
        "write_scope": [candidate_path],
        "required_guard_ids": list(grant["required_guard_ids"]),
        "rca_sha256": AUTHORITY.rca_fingerprint(rca),
        "write_grant_sha256": AUTHORITY.write_grant_fingerprint(grant),
        "violated_invariant": rca["violated_invariant"],
        "authority_owner": rca["authority_owner"],
        "required_permanent_guard": rca["required_permanent_guard"],
        "deterministic_file_verification_passed": True,
        "governed_repair_state": "INDEPENDENT_REVIEW",
        "gates": grant["gates"],
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    stage2_path = tmp_path / "repair-result.json"
    _write(stage2_path, stage2)
    HANDOFF.bind_handoff(
        failure_path=failure_path,
        result_path=stage2_path,
        patch_path=patch_path,
    )

    binding = {
        "repository": failure["repository"],
        "workflow_name": failure["workflow_name"],
        "workflow_run_id": failure["workflow_run_id"],
        "workflow_run_attempt": failure["workflow_run_attempt"],
        "head_sha": failure["head_sha"],
        "failure_signature": failure["failure_signature"],
    }
    task_path = tmp_path / "task-run.json"
    task = TaskRunStore.open_or_create(
        task_path,
        task_id=stable_task_id("github-repair", binding),
        task_kind="github-governed-repair",
        binding=binding,
        required_conditions=(
            "failure_ingested",
            "classification_complete",
            "source_changed",
            "validation_passed",
            "draft_pr_published",
            "governance_closed",
            "baseline_accepted",
            "exact_head_certified",
            "ready_for_review",
        ),
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE3_VALIDATION_REQUIRED",
        workspace_fingerprint=None,
        evidence_refs=[str(stage2_path), str(patch_path)],
    )
    return {
        "workspace": workspace,
        "source": source,
        "failure": failure,
        "failure_path": failure_path,
        "rca": rca,
        "rca_path": rca_path,
        "grant": grant,
        "grant_path": grant_path,
        "stage2_path": stage2_path,
        "patch_path": patch_path,
        "task_path": task_path,
    }


def _prepare(tmp_path: Path) -> dict:
    fx = _fixture(tmp_path)
    plan_path = tmp_path / "stage3-plan.json"
    plan = STAGE3.prepare_candidate(
        workspace=fx["workspace"],
        result_path=fx["stage2_path"],
        task_run_path=fx["task_path"],
        patch_path=fx["patch_path"],
        failure_case_path=fx["failure_path"],
        rca_path=fx["rca_path"],
        write_grant_path=fx["grant_path"],
        plan_path=plan_path,
    )
    fx["plan"] = plan
    fx["plan_path"] = plan_path
    return fx


def _quick_summary(*, include_anti_drift: bool = True, original_guard_pass: bool = True) -> dict:
    required = ["original-semantic-guard"]
    if include_anti_drift:
        required.append(STAGE3.ANTI_DRIFT_REQUIRED_GATE_ID)
    results = [
        {
            "id": "original-semantic-guard",
            "status": "PASS" if original_guard_pass else "FAIL",
        }
    ]
    if include_anti_drift:
        results.append({"id": STAGE3.ANTI_DRIFT_REQUIRED_GATE_ID, "status": "PASS"})
    return {
        "mode": "quick",
        "run_kind": "verification",
        "decision": "PASS" if original_guard_pass else "FAIL",
        "loop_status": "CI_VERIFIED" if original_guard_pass else "FAILED",
        "completion_eligible": original_guard_pass,
        "required_gate_ids": required,
        "results": results,
        "workspace_snapshot_fingerprint": "s" * 64,
    }


class GovernedRepairStage3Tests(unittest.TestCase):
    def test_stage2_handoff_binds_exact_rca_write_grant_and_machine_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fx = _fixture(Path(temp))
            result = json.loads(fx["stage2_path"].read_text(encoding="utf-8"))
            self.assertTrue(result["stage3_handoff_bound"])
            self.assertEqual(
                result["patch_sha256"],
                hashlib.sha256(fx["patch_path"].read_bytes()).hexdigest(),
            )
            self.assertEqual(result["required_guard_ids"], ["original-semantic-guard"])
            self.assertEqual(result["rca_sha256"], AUTHORITY.rca_fingerprint(fx["rca"]))
            self.assertEqual(
                result["write_grant_sha256"],
                AUTHORITY.write_grant_fingerprint(fx["grant"]),
            )

    def test_stage3_prepare_replays_exact_patch_under_immutable_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fx = _prepare(Path(temp))
            plan = fx["plan"]
            self.assertEqual(fx["source"].read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual(plan["status"], "CANDIDATE_PREPARED")
            self.assertEqual(plan["write_scope"], ["services/agent-service/app.py"])
            self.assertEqual(plan["required_guard_ids"], ["original-semantic-guard"])
            self.assertEqual(plan["governed_repair_state"], "INDEPENDENT_REVIEW")
            self.assertEqual(_git(fx["workspace"], "status", "--porcelain"), "")

    def test_stage3_rejects_tampered_patch_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fx = _fixture(Path(temp))
            fx["patch_path"].write_text(
                fx["patch_path"].read_text(encoding="utf-8") + "# tampered\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(STAGE3.Stage3Error, "digest mismatch"):
                STAGE3.inspect_handoff(
                    result_path=fx["stage2_path"],
                    task_run_path=fx["task_path"],
                    patch_path=fx["patch_path"],
                    failure_case_path=fx["failure_path"],
                    rca_path=fx["rca_path"],
                    write_grant_path=fx["grant_path"],
                )

    def test_stage3_requires_original_machine_guard_and_executable_anti_drift_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fx = _prepare(root)
            targeted_path = root / "targeted-result.json"
            _write(
                targeted_path,
                {
                    "schema": STAGE3.STAGE3_SCHEMA,
                    "status": "TARGETED_VALIDATION_PASSED",
                    "candidate_sha": fx["plan"]["candidate_sha"],
                    "rca_sha256": fx["plan"]["rca_sha256"],
                    "write_grant_sha256": fx["plan"]["write_grant_sha256"],
                    "required_guard_ids": fx["plan"]["required_guard_ids"],
                    "results": [],
                    "production_closed": False,
                },
            )

            missing_anti_drift = root / "quick-missing-anti-drift.json"
            _write(missing_anti_drift, _quick_summary(include_anti_drift=False))
            with self.assertRaisesRegex(
                STAGE3.Stage3Error, "anti_drift_proof_not_reverified"
            ):
                STAGE3.record_validation(
                    workspace=fx["workspace"],
                    plan_path=fx["plan_path"],
                    targeted_result_path=targeted_path,
                    quick_summary_path=missing_anti_drift,
                    task_run_path=fx["task_path"],
                    output_path=root / "invalid-validation.json",
                )

            quick_path = root / "quick.json"
            _write(quick_path, _quick_summary())
            result = STAGE3.record_validation(
                workspace=fx["workspace"],
                plan_path=fx["plan_path"],
                targeted_result_path=targeted_path,
                quick_summary_path=quick_path,
                task_run_path=fx["task_path"],
                output_path=root / "validation.json",
            )
            self.assertEqual(result["status"], "VALIDATED_FOR_DRAFT_PR")
            self.assertEqual(result["anti_drift_proof"]["status"], "PASS")
            self.assertEqual(
                result["anti_drift_proof"]["governed_repair_state"],
                "ANTI_DRIFT_PROOF",
            )
            self.assertEqual(result["gates"]["G3_MUTATION"]["status"], "PASS")
            self.assertEqual(
                result["gates"]["G3_MUTATION"]["governed_repair_state"],
                "ANTI_DRIFT_PROOF",
            )
            self.assertEqual(result["governed_repair_state"], "PR_CERTIFICATION")

    def test_legacy_stage3_completion_path_is_permanently_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(STAGE3.Stage3Error, "deprecated completion path"):
                STAGE3.complete_publication(
                    workspace=root,
                    validation_result_path=root / "validation.json",
                    task_run_path=root / "task.json",
                    pr_url="https://github.com/owner/repo/pull/99",
                    output_path=root / "out.json",
                )

    def test_draft_publication_hands_off_to_governance_without_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fx = _prepare(root)
            targeted_path = root / "targeted-result.json"
            _write(
                targeted_path,
                {
                    "schema": STAGE3.STAGE3_SCHEMA,
                    "status": "TARGETED_VALIDATION_PASSED",
                    "candidate_sha": fx["plan"]["candidate_sha"],
                    "rca_sha256": fx["plan"]["rca_sha256"],
                    "write_grant_sha256": fx["plan"]["write_grant_sha256"],
                    "required_guard_ids": fx["plan"]["required_guard_ids"],
                    "results": [],
                    "production_closed": False,
                },
            )
            quick_path = root / "quick.json"
            _write(quick_path, _quick_summary())
            validation_path = root / "validation.json"
            validation = STAGE3.record_validation(
                workspace=fx["workspace"],
                plan_path=fx["plan_path"],
                targeted_result_path=targeted_path,
                quick_summary_path=quick_path,
                task_run_path=fx["task_path"],
                output_path=validation_path,
            )
            publication_path = root / "publication.json"
            publication = {
                "schema": RECORD.PUBLICATION_SCHEMA,
                "status": "PUBLICATION_COMMIT_PREPARED",
                "source_run_id": validation["source_run_id"],
                "source_head_sha": validation["head_sha"],
                "validated_candidate_sha": validation["candidate_sha"],
                "validated_tree_sha": "t" * 40,
                "published_candidate_sha": "p" * 40,
                "repair_branch": validation["repair_branch"],
                "repair_base_branch": validation["repair_base_branch"],
                "changed_paths": validation["changed_paths"],
                "write_scope": validation["write_scope"],
                "rca_sha256": validation["rca_sha256"],
                "write_grant_sha256": validation["write_grant_sha256"],
                "required_guard_ids": validation["required_guard_ids"],
                "violated_invariant": validation["violated_invariant"],
                "authority_owner": validation["authority_owner"],
                "required_permanent_guard": validation["required_permanent_guard"],
                "governance_closed": False,
                "baseline_accepted": False,
                "exact_head_certified": False,
                "ready_for_review": False,
                "production_closed": False,
            }
            _write(publication_path, publication)
            receipt = RECORD.record_publication(
                validation_path=validation_path,
                publication_path=publication_path,
                task_run_path=fx["task_path"],
                pr_url="https://github.com/owner/repo/pull/99",
                output_path=root / "publication-receipt.json",
            )
            task = json.loads(fx["task_path"].read_text(encoding="utf-8"))
            self.assertEqual(
                receipt["status"],
                "DRAFT_REPAIR_PR_PUBLISHED_AWAITING_GOVERNANCE",
            )
            self.assertEqual(receipt["governed_repair_state"], "GOVERNANCE_REQUIRED")
            self.assertFalse(receipt["merge_allowed"])
            self.assertFalse(receipt["deploy_allowed"])
            self.assertFalse(receipt["production_closed"])
            self.assertEqual(task["status"], "WAITING_EXTERNAL_RESULT")
            self.assertEqual(task["phase"], "STAGE4_GOVERNANCE_REQUIRED")


if __name__ == "__main__":
    unittest.main()
