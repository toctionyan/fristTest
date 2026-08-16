from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_governed_repair_environment_contract as environment_contract  # noqa: E402


class GovernedRepairEnvironmentContractTests(unittest.TestCase):
    def test_g6_supports_strict_multi_user_and_fail_closed_solo_owner_modes(self) -> None:
        result = environment_contract.verify(ROOT)
        self.assertEqual(result.get("status"), "PASS", result)

        multi = result.get("multi_user") or {}
        self.assertTrue(multi.get("requires_required_reviewers"), result)
        self.assertTrue(multi.get("requires_prevent_self_review"), result)
        self.assertTrue(multi.get("independent_human_review"), result)

        solo = result.get("solo_owner") or {}
        self.assertTrue(solo.get("requires_repository_owner"), result)
        self.assertTrue(solo.get("requires_explicit_acknowledgement"), result)
        self.assertFalse(solo.get("independent_human_review"), result)
        self.assertTrue(solo.get("machine_gates_remain_mandatory"), result)
        self.assertTrue(solo.get("pr_remains_draft"), result)

        self.assertTrue(result.get("requires_exact_pr_pull_request_ci"), result)
        self.assertFalse(result.get("push_or_manual_ci_can_satisfy_g6"), result)
        self.assertFalse(result.get("dispatch_token_authority"), result)
        self.assertFalse(result.get("merge_allowed"), result)
        self.assertFalse(result.get("deploy_allowed"), result)
        self.assertFalse(result.get("production_closed"), result)

    def _isolated_contract_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        for relative in (
            environment_contract.MULTI_WORKFLOW_REL,
            environment_contract.SOLO_WORKFLOW_REL,
            environment_contract.EXACT_HEAD_REL,
        ):
            source = ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return temp, root

    def test_mutation_removing_repository_owner_check_is_killed(self) -> None:
        temp, root = self._isolated_contract_root()
        self.addCleanup(temp.cleanup)
        path = root / environment_contract.SOLO_WORKFLOW_REL
        source = path.read_text(encoding="utf-8")
        marker = 'if [[ "${GITHUB_ACTOR}" != "${GITHUB_REPOSITORY_OWNER}" ]]; then'
        self.assertIn(marker, source)
        path.write_text(source.replace(marker, 'if [[ "${GITHUB_ACTOR}" != "" ]]; then', 1), encoding="utf-8")

        result = environment_contract.verify(root)
        self.assertEqual(result.get("status"), "FAIL", result)
        self.assertTrue(
            any(
                "solo_owner_governance_contract_missing" in str(error)
                or "solo_owner_acknowledgement_not_before_governance_close" in str(error)
                for error in result.get("errors") or []
            ),
            result,
        )

    def test_mutation_auto_readying_solo_pr_is_killed(self) -> None:
        temp, root = self._isolated_contract_root()
        self.addCleanup(temp.cleanup)
        path = root / environment_contract.SOLO_WORKFLOW_REL
        source = path.read_text(encoding="utf-8")
        path.write_text(source + '\n# mutation\n# gh pr ready "$PR_URL"\n', encoding="utf-8")

        result = environment_contract.verify(root)
        self.assertEqual(result.get("status"), "FAIL", result)
        self.assertIn("solo_owner_forbidden_authority:gh pr ready", result.get("errors") or [])


if __name__ == "__main__":
    unittest.main()
