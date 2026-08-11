from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
POLICY = ROOT / "governance" / "long-task-execution-policy.md"


class LongTaskExecutionPolicyTests(unittest.TestCase):
    def test_agents_binds_repository_policy(self) -> None:
        agents = AGENTS.read_text(encoding="utf-8")
        self.assertIn("governance/long-task-execution-policy.md", agents)
        self.assertIn(
            "Time budget follows correctness boundaries; split orchestration, not atomic correctness units.",
            agents,
        )
        self.assertIn("10-15 minutes", agents)
        self.assertIn("18-20 minutes", agents)
        self.assertIn("RUNNING_WAITING_EXTERNAL", agents)
        self.assertIn("Never claim completion without terminal validation evidence.", agents)

    def test_policy_preserves_safe_checkpoint_and_resume_boundaries(self) -> None:
        policy = POLICY.read_text(encoding="utf-8")
        required_fragments = (
            "## Safe Checkpoint",
            "Do not cancel, split, or truncate an Atomic Work Unit merely to satisfy a reporting clock.",
            "10-15 minutes",
            "18-20 minutes",
            "durable resume identity",
            "Failed gates block progression.",
            "Never claim completion without terminal validation evidence.",
            "Do not cross a failed Milestone boundary.",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, policy)

    def test_policy_distinguishes_repo_liveness_from_platform_state(self) -> None:
        policy = POLICY.read_text(encoding="utf-8")
        for state in (
            "RUNNING_ACTIVE",
            "RUNNING_WAITING_EXTERNAL",
            "SUSPECTED_STALL",
            "COMPLETED",
            "FAILED",
            "SAFETY_CHECK_WAIT",
            "MODEL_LIMIT",
        ):
            with self.subTest(state=state):
                self.assertIn(f"`{state}`", policy)
        self.assertIn(
            "The following are platform/UI classifications, not repository execution states:",
            policy,
        )
        self.assertIn(
            "do not infer a repository stall solely from a ChatGPT UI spinner",
            policy,
        )

    def test_policy_does_not_expand_release_authority(self) -> None:
        policy = POLICY.read_text(encoding="utf-8")
        required_fragments = (
            "attempt budgets remain authoritative",
            "product and production closure remain separate authorities",
            "a reporting checkpoint never authorizes another release attempt",
            "recovery/reconciliation must use repository-owned coordinator/recovery paths rather than direct ledger edits",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, policy)


if __name__ == "__main__":
    unittest.main()
