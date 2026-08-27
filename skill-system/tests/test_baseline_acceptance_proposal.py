from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from baseline_acceptance_proposal import (  # noqa: E402
    build_baseline_acceptance_proposal,
    render_baseline_acceptance_proposal,
)
from product_source_baseline_policy import build_canonical_product_snapshot  # noqa: E402


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class BaselineAcceptanceProposalTests(unittest.TestCase):
    def _workspace(self, root: Path) -> tuple[str, str]:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        _git(root, "config", "user.name", "Proposal Test")
        _git(root, "config", "user.email", "proposal@example.com")
        first = root / "services/agent-service/src/a.py"
        second = root / "contracts/b.json"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"old-a\n")
        second.write_bytes(b"old-b\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "accepted source")
        accepted_sha = _git(root, "rev-parse", "HEAD")
        baseline = root / "skill-system/registry/product-source-baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps(
                build_canonical_product_snapshot(root, accepted_sha, ("contracts", "services")),
                indent=2,
            ),
            encoding="utf-8",
        )
        first.write_bytes(b"new-a\n")
        first.parent.joinpath("new.py").write_bytes(b"new\n")
        second.unlink()
        _git(root, "add", "services", "contracts")
        _git(root, "commit", "-qm", "candidate source")
        return accepted_sha, _git(root, "rev-parse", "HEAD")

    def test_exact_drift_uses_v3_identity_without_write_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accepted_sha, candidate_sha = self._workspace(root)
            proposal = build_baseline_acceptance_proposal(root, candidate_sha=candidate_sha)

        self.assertEqual(proposal["status"], "DECISION_REQUIRED")
        self.assertEqual(proposal["drift"]["added"], ["services/agent-service/src/new.py"])
        self.assertEqual(proposal["drift"]["modified"], ["services/agent-service/src/a.py"])
        self.assertEqual(proposal["drift"]["deleted"], ["contracts/b.json"])
        self.assertEqual(proposal["accepted_product_source_ref"], f"git-commit-sha1:{accepted_sha}")
        self.assertTrue(proposal["accepted_protected_snapshot_digest"].startswith("sha256:"))
        self.assertTrue(proposal["candidate_protected_snapshot_digest"].startswith("sha256:"))
        self.assertNotIn("accepted_generated_from", proposal)
        self.assertFalse(proposal["baseline_write_allowed"])
        rendered = render_baseline_acceptance_proposal(proposal)
        self.assertIn("Human decision required: true", rendered)
        self.assertIn("Baseline write allowed by this proposal: false", rendered)

    def test_no_drift_requires_no_human_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accepted_sha, _ = self._workspace(root)
            proposal = build_baseline_acceptance_proposal(root, candidate_sha=accepted_sha)
        self.assertEqual(proposal["status"], "NO_DRIFT")
        self.assertFalse(proposal["decision_required"])
        self.assertFalse(proposal["human_required"])
        self.assertEqual(proposal["drift"]["total_count"], 0)


if __name__ == "__main__":
    unittest.main()
