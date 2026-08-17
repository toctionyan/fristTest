from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_existing_candidate_adoption.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("github_existing_candidate_adoption", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load existing-candidate adoption controller")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


class ExistingCandidateAdoptionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote = root / "origin.git"
        self.workspace = root / "candidate"
        self.evidence = root / "evidence"
        self.evidence.mkdir()
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(self.workspace)], check=True, capture_output=True)
        _git(self.workspace, "config", "user.email", "adoption-test@example.invalid")
        _git(self.workspace, "config", "user.name", "Adoption Test")
        _git(self.workspace, "remote", "add", "origin", str(self.remote))

        source = self.workspace / "services" / "example.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        _git(self.workspace, "add", "services/example.py")
        _git(self.workspace, "commit", "-m", "base")
        _git(self.workspace, "push", "-u", "origin", "main")
        self.base_sha = _git(self.workspace, "rev-parse", "HEAD")

        _git(self.workspace, "checkout", "-b", "repair/example")
        source.write_text("VALUE = 2\n", encoding="utf-8")
        _git(self.workspace, "add", "services/example.py")
        _git(self.workspace, "commit", "-m", "candidate")
        self.head_sha = _git(self.workspace, "rev-parse", "HEAD")
        self.blob_sha = _git(self.workspace, "rev-parse", "HEAD:services/example.py")

        self.profile_path = self.evidence / "profile.json"
        self.pr_path = self.evidence / "pr.json"
        self.plan_path = self.evidence / "plan.json"
        self.authority_path = self.evidence / "authority.json"
        self.task_path = self.evidence / "task-run.json"
        self.profile_validation_path = self.evidence / "profile-validation.json"
        self.quick_path = self.evidence / "quick.json"
        self.validation_path = self.evidence / "validation.json"
        self.publication_path = self.evidence / "publication.json"

        profile = {
            "schema": "governed-existing-candidate-adoption-profile@1",
            "profile_id": "unit-test-profile",
            "source_pr_number": 99,
            "base_branch": "main",
            "authority_effect": False,
            "existing_candidate_only": True,
            "allowed_changed_files": {"services/example.py": self.blob_sha},
            "forbidden_changed_prefixes": [".github/", "governance/", "deployment/"],
            "forbidden_changed_exact": ["skill-system/registry/product-source-baseline.json"],
            "required_guard_ids": ["python-test-suites"],
            "gate_guard_ids": {
                "G1_CONTRACT_PROJECTION": ["unit-contract"],
                "G2_SEMANTIC_INVARIANT": ["unit-semantic"],
                "G3_MUTATION": ["unit-mutation", "python-test-suites"],
            },
            "verification_commands": [
                {
                    "id": "unit-contract",
                    "cwd": ".",
                    "argv": ["{python}", "-B", "-c", "print('unit-contract-pass')"],
                },
                {
                    "id": "unit-semantic",
                    "cwd": ".",
                    "argv": ["{python}", "-B", "-c", "print('unit-semantic-pass')"],
                },
                {
                    "id": "unit-mutation",
                    "cwd": ".",
                    "argv": ["{python}", "-B", "-c", "print('unit-mutation-pass')"],
                },
                {
                    "id": "python-test-suites",
                    "cwd": ".",
                    "argv": ["{python}", "-B", "-c", "print('guard-pass')"],
                },
            ],
            "violated_invariant": "UNIT-INVARIANT",
            "authority_owner": "deterministic-unit-authority",
            "required_permanent_guard": "unit permanent guard",
            "production_closed": False,
        }
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        pr = {
            "number": 99,
            "isDraft": True,
            "state": "OPEN",
            "headRefName": "repair/example",
            "headRefOid": self.head_sha,
            "baseRefName": "main",
            "url": "https://github.com/example/repo/pull/99",
        }
        self.pr_path.write_text(json.dumps(pr), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_full_adoption_certification_never_writes_candidate_source(self) -> None:
        before_tree = _git(self.workspace, "rev-parse", "HEAD^{tree}")
        plan = self.module.inspect(
            workspace=self.workspace,
            profile_path=self.profile_path,
            pr_json_path=self.pr_path,
            output_path=self.plan_path,
            authority_path=self.authority_path,
            task_run_path=self.task_path,
            repository="example/repo",
        )
        self.assertFalse(plan["write_authority_effect"])
        self.assertFalse(plan["source_writes_allowed"])
        self.assertEqual(plan["changed_paths"], ["services/example.py"])

        profile_validation = self.module.run_profile(
            workspace=self.workspace,
            profile_path=self.profile_path,
            plan_path=self.plan_path,
            output_path=self.profile_validation_path,
        )
        self.assertEqual(profile_validation["status"], "PROFILE_VALIDATION_PASSED")

        quick = {
            "mode": "quick",
            "run_kind": "verification",
            "decision": "PASS",
            "loop_status": "CI_VERIFIED",
            "completion_eligible": True,
            "required_gate_ids": ["python-test-suites"],
            "workspace_snapshot_fingerprint": "snapshot-test",
            "results": [{"id": "python-test-suites", "status": "PASS"}],
        }
        self.quick_path.write_text(json.dumps(quick), encoding="utf-8")
        publication = self.module.finalize(
            workspace=self.workspace,
            profile_path=self.profile_path,
            plan_path=self.plan_path,
            authority_path=self.authority_path,
            profile_validation_path=self.profile_validation_path,
            quick_summary_path=self.quick_path,
            task_run_path=self.task_path,
            validation_output_path=self.validation_path,
            publication_output_path=self.publication_path,
            workflow_run_id="12345",
        )

        self.assertEqual(publication["status"], "DRAFT_REPAIR_PR_PUBLISHED_AWAITING_GOVERNANCE")
        self.assertEqual(publication["candidate_origin"], "existing_pr_adoption")
        self.assertFalse(publication["write_authority_effect"])
        self.assertFalse(publication["merge_allowed"])
        self.assertFalse(publication["deploy_allowed"])
        self.assertFalse(publication["production_closed"])
        self.assertEqual(publication["gates"]["G6_GOVERNANCE_EXACT_HEAD"]["status"], "PENDING")
        self.assertEqual(
            publication["gates"]["G2_SEMANTIC_INVARIANT"]["evidence"],
            ["guard:unit-semantic", "invariant:UNIT-INVARIANT"],
        )
        self.assertEqual(_git(self.workspace, "rev-parse", "HEAD^{tree}"), before_tree)
        self.assertEqual(_git(self.workspace, "status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_blob_identity_drift_is_rejected(self) -> None:
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        profile["allowed_changed_files"]["services/example.py"] = "f" * 40
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(self.module.AdoptionError, "candidate blob identity drift"):
            self.module.inspect(
                workspace=self.workspace,
                profile_path=self.profile_path,
                pr_json_path=self.pr_path,
                output_path=self.plan_path,
                authority_path=self.authority_path,
                task_run_path=self.task_path,
                repository="example/repo",
            )

    def test_extra_candidate_path_is_rejected(self) -> None:
        extra = self.workspace / "services" / "extra.py"
        extra.write_text("EXTRA = True\n", encoding="utf-8")
        _git(self.workspace, "add", "services/extra.py")
        _git(self.workspace, "commit", "-m", "unexpected extra path")
        pr = json.loads(self.pr_path.read_text(encoding="utf-8"))
        pr["headRefOid"] = _git(self.workspace, "rev-parse", "HEAD")
        self.pr_path.write_text(json.dumps(pr), encoding="utf-8")
        with self.assertRaisesRegex(self.module.AdoptionError, "path set does not equal trusted adoption profile"):
            self.module.inspect(
                workspace=self.workspace,
                profile_path=self.profile_path,
                pr_json_path=self.pr_path,
                output_path=self.plan_path,
                authority_path=self.authority_path,
                task_run_path=self.task_path,
                repository="example/repo",
            )


if __name__ == "__main__":
    unittest.main()
