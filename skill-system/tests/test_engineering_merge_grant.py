from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import ABSOLUTE_FORBIDDEN_ACTIONS  # noqa: E402
from engineering_merge_grant import (  # noqa: E402
    EngineeringMergeGrantError,
    compile_merge_network_request,
    create_merge_grant,
    evaluate_merge_gate,
    validate_merge_grant_for_task,
)

REPO = "toctionyan/fristTest"
BASE = "a" * 40
HEAD = "b" * 40
PR = 2001


def task() -> dict:
    return {
        "task_id": "issue167-autonomous-landing",
        "binding": {
            "base_sha": BASE,
            "branch": "repair/source-candidate",
            "allowed_paths": ["services/agent-service/app/runtime.py"],
            "target_fingerprint": "issue167-target-v1",
        },
    }


def pr(*, draft: bool = True, head: str = HEAD, base: str = BASE) -> dict:
    return {
        "number": PR,
        "state": "open",
        "draft": draft,
        "merged": False,
        "merged_at": None,
        "mergeable": True,
        "head": {"sha": head, "ref": "governed-repair/issue167", "repo": {"full_name": REPO}},
        "base": {"sha": base, "ref": "main"},
    }


def lineage() -> dict:
    return {
        "schema": "governed-baseline-acceptance@1",
        "draft_pr_url": f"https://github.com/{REPO}/pull/{PR}",
        "repair_branch": "governed-repair/issue167",
        "governance_closed": True,
        "baseline_accepted": True,
    }


def exact_result() -> dict:
    return {
        "schema": "governed-repair-exact-head@1",
        "status": "READY_FOR_REVIEW",
        "draft_pr_url": f"https://github.com/{REPO}/pull/{PR}",
        "baseline_commit_sha": HEAD,
        "ready_for_review": True,
        "governance_closed": True,
        "baseline_accepted": True,
        "exact_head_certified": True,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def exact_state() -> dict:
    return {
        "schema": "governed-repair-exact-head-ci-state@1",
        "status": "EXACT_HEAD_CI_PASSED",
        "head_sha": HEAD,
        "pr_number": PR,
        "finalize_allowed": True,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def run(name: str, event: str, *, conclusion: str = "success", number: int = 10, run_id: int = 100) -> dict:
    row = {
        "id": run_id,
        "name": name,
        "event": event,
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": conclusion,
        "run_number": number,
        "run_attempt": 1,
    }
    if event == "pull_request":
        row["pull_requests"] = [{"number": PR}]
    return row


def green_runs() -> list[dict]:
    return [
        run("quality", "pull_request", run_id=101),
        run("skill-self-validation", "pull_request", run_id=102),
        run("quality", "push", run_id=103),
        run("skill-self-validation", "push", run_id=104),
    ]


class EngineeringMergeGrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = task()
        self.grant = create_merge_grant(
            task=self.task,
            repository=REPO,
            source_pr_number=1580,
            issued_by="toctionyan",
            owner_authorization_ref="github-actions:123/1:bounded-auto-merge",
        )

    def gate(self, **overrides):
        values = {
            "grant": self.grant,
            "task": self.task,
            "pr": pr(),
            "lineage_result": lineage(),
            "exact_head_result": exact_result(),
            "exact_head_ci_state": exact_state(),
            "workflow_runs": green_runs(),
            "changed_paths": ["services/agent-service/app/runtime.py", "skill-system/registry/product-source-baseline.json"],
            "reviews": [],
            "unresolved_review_threads": 0,
            "human_gate_reasons": [],
        }
        values.update(overrides)
        return evaluate_merge_gate(**values)

    def test_autonomy_grant_still_forbids_merge(self) -> None:
        self.assertIn("merge", ABSOLUTE_FORBIDDEN_ACTIONS)

    def test_merge_grant_never_mints_write_test_deploy_or_production_authority(self) -> None:
        self.assertEqual(self.grant["authority_effect"], "conditional_final_merge_only")
        self.assertFalse(self.grant["write_authority_effect"])
        self.assertFalse(self.grant["test_authority_effect"])
        self.assertFalse(self.grant["acceptance_mutation_allowed"])
        self.assertFalse(self.grant["scope_expansion_allowed"])
        self.assertFalse(self.grant["deploy_allowed"])
        self.assertFalse(self.grant["production_closed"])

    def test_other_task_cannot_reuse_merge_grant(self) -> None:
        other = copy.deepcopy(self.task)
        other["task_id"] = "other-task"
        with self.assertRaises(EngineeringMergeGrantError):
            validate_merge_grant_for_task(self.grant, task=other)

    def test_same_task_governed_descendant_can_be_ready(self) -> None:
        result = self.gate()
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["mark_ready_allowed"])
        self.assertTrue(result["merge_allowed"])
        self.assertEqual(result["expected_head_sha"], HEAD)
        self.assertFalse(result["deploy_allowed"])

    def test_scope_expansion_blocks_merge(self) -> None:
        result = self.gate(changed_paths=["services/agent-service/app/runtime.py", "deployment/prod.yaml"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("scope_violation:deployment/prod.yaml", result["blockers"])

    def test_current_head_push_red_blocks_even_when_pr_ci_green(self) -> None:
        rows = green_runs()
        rows.append(run("quality", "push", conclusion="failure", number=11, run_id=200))
        result = self.gate(workflow_runs=rows)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("current_head_push_not_green:quality", result["blockers"])

    def test_stale_push_red_is_ignored_after_newer_same_head_push_green(self) -> None:
        rows = green_runs()
        rows.append(run("quality", "push", conclusion="failure", number=9, run_id=90))
        result = self.gate(workflow_runs=rows)
        self.assertEqual(result["status"], "READY")

    def test_active_request_changes_and_unresolved_thread_block(self) -> None:
        reviews = [{"id": 1, "state": "CHANGES_REQUESTED", "submitted_at": "2026-08-19T00:00:00Z", "user": {"login": "reviewer"}}]
        result = self.gate(reviews=reviews, unresolved_review_threads=1)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("active_request_changes:reviewer", result["blockers"])
        self.assertIn("unresolved_review_threads:1", result["blockers"])

    def test_human_gate_is_not_bypassed(self) -> None:
        result = self.gate(human_gate_reasons=["independent_environment_review"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("human_gate:independent_environment_review", result["blockers"])

    def test_g6_must_be_closed_and_exact_head_certified(self) -> None:
        broken = exact_result(); broken["exact_head_certified"] = False
        result = self.gate(exact_head_result=broken)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("g6_exact_head_not_certified", result["blockers"])

    def test_exact_head_cas_request_uses_merge_commit_only(self) -> None:
        decision = self.gate()
        current = pr(draft=False)
        request = compile_merge_network_request(self.grant, decision, current_pr=current)
        self.assertEqual(request["body"], {"sha": HEAD, "merge_method": "merge"})
        self.assertEqual(request["authority_effect"], "merge_only")
        self.assertFalse(request["deploy_allowed"])
        stale = pr(draft=False, head="c" * 40)
        with self.assertRaisesRegex(EngineeringMergeGrantError, "drift"):
            compile_merge_network_request(self.grant, decision, current_pr=stale)

    def test_grant_tampering_fails_closed(self) -> None:
        forged = copy.deepcopy(self.grant)
        forged["scope_expansion_allowed"] = True
        with self.assertRaises(EngineeringMergeGrantError):
            validate_merge_grant_for_task(forged, task=self.task)


if __name__ == "__main__":
    unittest.main()
