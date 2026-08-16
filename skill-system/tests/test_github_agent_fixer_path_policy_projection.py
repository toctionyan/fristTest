from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_agent_fixer as fixer  # noqa: E402
from governed_repair_path_policy import (  # noqa: E402
    RepairPathPolicyError,
    path_policy_violation,
    policy_payload,
    validate_automatic_repair_paths,
)


class AgentFixerPathPolicyProjectionTests(unittest.TestCase):
    """Keep the executor's legacy local projection exactly aligned to authority policy.

    Write-grant compilation already consumes the canonical policy and therefore
    cannot mint wider authority.  These tests close the opposite failure mode:
    a stale *stricter* executor copy causing needless repair failures/reloops.
    """

    def test_executor_projection_equals_canonical_policy(self) -> None:
        policy = policy_payload()
        self.assertEqual(fixer.MAX_FILES, policy["max_write_paths"])
        self.assertEqual(set(fixer.SUPPORTED_SUFFIXES), set(policy["supported_suffixes"]))
        self.assertEqual(tuple(fixer.AUTOMATIC_SOURCE_ROOTS), tuple(policy["automatic_source_roots"]))
        self.assertEqual(set(fixer.FORBIDDEN_PATH_PARTS), set(policy["forbidden_path_parts"]))
        self.assertEqual(set(fixer.FORBIDDEN_BASENAMES), set(policy["forbidden_basenames"]))
        self.assertEqual(tuple(fixer.PROTECTED_PREFIXES), tuple(policy["protected_prefixes"]))
        self.assertEqual(set(fixer.PROTECTED_EXACT), set(policy["protected_exact"]))

    def test_executor_and_canonical_policy_agree_on_representative_paths(self) -> None:
        samples = {
            "services/agent-service/src/agent_core/example.py": True,
            "services/business-service/app/example.py": True,
            "web/src/example.ts": True,
            "contracts/example.json": True,
            "services/agent-service/tests/test_example.py": False,
            "services/agent-service/src/example.test.ts": False,
            "services/agent-service/.env": False,
            "services/agent-service/pyproject.toml": False,
            ".github/workflows/quality.yml": False,
            "scripts/github_repair_authority.py": False,
        }
        with tempfile.TemporaryDirectory(prefix="fixer-path-policy-") as temp:
            workspace = Path(temp)
            for relative in samples:
                target = workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("pass\n", encoding="utf-8")

            for relative, expected_allowed in samples.items():
                canonical_allowed = path_policy_violation(relative) is None
                self.assertEqual(canonical_allowed, expected_allowed, relative)
                try:
                    canonical = validate_automatic_repair_paths([relative])
                    canonical_runtime_allowed = True
                except RepairPathPolicyError:
                    canonical = ()
                    canonical_runtime_allowed = False
                try:
                    projected = fixer.validate_allowed_paths(workspace, [relative])
                    projected_allowed = True
                except fixer.FixerError:
                    projected = ()
                    projected_allowed = False
                self.assertEqual(projected_allowed, canonical_runtime_allowed, relative)
                if expected_allowed:
                    self.assertEqual(projected, canonical)


if __name__ == "__main__":
    unittest.main()
