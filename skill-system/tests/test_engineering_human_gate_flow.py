from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for path in (CONTROL, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from engineering_merge_consumption import classify_consumption, consumption_context  # noqa: E402
from engineering_merge_grant import create_merge_grant, evaluate_merge_gate  # noqa: E402
from github_repair_exact_head_state import classify_exact_head_ci  # noqa: E402

REPO = "toctionyan/fristTest"
BASE = "a" * 40
HEAD = "b" * 40
PR = 2100
PR_URL = f"https://github.com/{REPO}/pull/{PR}"


def task() -> dict:
    return {
        "task_id": "human-gate-flow",
        "binding": {
            "base_sha": BASE,
            "branch": "repair/human-gate-flow",
            "allowed_paths": ["services/agent-service/app/runtime.py"],
            "target_fingerprint": "human-gate-flow-v1",
        },
    }


def ci(conclusion: str) -> dict:
    def row(name: str, run_id: int) -> dict:
        return {
            "run_id": str(run_id),
            "status": "completed",
            "conclusion": conclusion,
            "head_sha": HEAD,
            "event": "pull_request",
            "pr_number": PR,
        }

    return {
        "schema": "governed-repair-exact-head-ci@1",
        "head_sha": HEAD,
        "pr_url": PR_URL,
        "pr_number": PR,
        "pr_is_draft": True,
        "pr_head_sha": HEAD,
        "workflows": {
            "quality": row("quality", 101),
            "skill-self-validation": row("skill-self-validation", 102),
        },
    }


def pr() -> dict:
    return {
        "number": PR,
        "state": "open",
        "draft": True,
        "merged": False,
        "merged_at": None,
        "mergeable": True,
        "head": {"sha": HEAD, "ref": "governed-repair/human-gate-flow", "repo": {"full_name": REPO}},
        "base": {"sha": BASE, "ref": "main"},
    }


def lineage() -> dict:
    return {
        "schema": "governed-baseline-acceptance@1",
        "draft_pr_url": PR_URL,
        "repair_branch": "governed-repair/human-gate-flow",
        "governance_closed": True,
        "baseline_accepted": True,
    }


def exact_result() -> dict:
    return {
        "schema": "governed-repair-exact-head@1",
        "status": "READY_FOR_REVIEW",
        "draft_pr_url": PR_URL,
        "baseline_commit_sha": HEAD,
        "ready_for_review": True,
        "governance_closed": True,
        "baseline_accepted": True,
        "exact_head_certified": True,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def run(name: str, event: str, conclusion: str = "success", number: int = 10) -> dict:
    row = {
        "id": number,
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


class EngineeringHumanGateFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = task()
        self.grant = create_merge_grant(
            task=self.task,
            repository=REPO,
            source_pr_number=1900,
            issued_by="toctionyan",
            owner_authorization_ref="engineering-autonomy-authorize:500/1",
        )

    def test_action_required_is_a_real_human_gate_and_never_merge_authority(self) -> None:
        state = classify_exact_head_ci(ci("action_required"), exact_sha=HEAD, draft_pr_url=PR_URL)
        self.assertEqual(state["status"], "EXACT_HEAD_CI_AWAITING_APPROVAL")
        self.assertTrue(state["resume_required"])
        self.assertFalse(state["finalize_allowed"])
        self.assertFalse(state["merge_allowed"])
        self.assertFalse(state["deploy_allowed"])

    def test_after_real_approval_same_task_can_continue_without_new_merge_authority(self) -> None:
        state = classify_exact_head_ci(ci("success"), exact_sha=HEAD, draft_pr_url=PR_URL)
        self.assertEqual(state["status"], "EXACT_HEAD_CI_PASSED")
        decision = evaluate_merge_gate(
            self.grant,
            task=self.task,
            pr=pr(),
            lineage_result=lineage(),
            exact_head_result=exact_result(),
            exact_head_ci_state=state,
            workflow_runs=[
                run("quality", "pull_request", number=101),
                run("skill-self-validation", "pull_request", number=102),
                run("quality", "push", number=103),
                run("skill-self-validation", "push", number=104),
            ],
            changed_paths=["services/agent-service/app/runtime.py", "skill-system/registry/product-source-baseline.json"],
            reviews=[],
            unresolved_review_threads=0,
            human_gate_reasons=[],
        )
        self.assertEqual(decision["status"], "READY")
        self.assertTrue(decision["merge_allowed"])
        self.assertFalse(decision["deploy_allowed"])

    def test_same_head_push_red_still_stops_landing_after_pr_approval(self) -> None:
        state = classify_exact_head_ci(ci("success"), exact_sha=HEAD, draft_pr_url=PR_URL)
        decision = evaluate_merge_gate(
            self.grant,
            task=self.task,
            pr=pr(),
            lineage_result=lineage(),
            exact_head_result=exact_result(),
            exact_head_ci_state=state,
            workflow_runs=[
                run("quality", "pull_request", number=101),
                run("skill-self-validation", "pull_request", number=102),
                run("quality", "push", conclusion="failure", number=105),
                run("skill-self-validation", "push", number=104),
            ],
            changed_paths=["services/agent-service/app/runtime.py"],
            reviews=[],
            unresolved_review_threads=0,
            human_gate_reasons=[],
        )
        self.assertEqual(decision["status"], "BLOCKED")
        self.assertIn("current_head_push_not_green:quality", decision["blockers"])

    def test_consumed_grant_cannot_be_replayed(self) -> None:
        context = consumption_context(self.grant)
        consumed = classify_consumption(
            self.grant,
            combined_status={
                "statuses": [{
                    "id": 1,
                    "context": context,
                    "state": "success",
                    "created_at": "2026-08-19T00:00:00Z",
                    "updated_at": "2026-08-19T00:00:00Z",
                }]
            },
        )
        self.assertEqual(consumed["status"], "CONSUMED")
        self.assertFalse(consumed["reservation_allowed"])
        self.assertFalse(consumed["merge_allowed"])


if __name__ == "__main__":
    unittest.main()
