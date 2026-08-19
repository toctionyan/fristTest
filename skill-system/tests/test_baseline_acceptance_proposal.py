from __future__ import annotations

import hashlib
import json
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BaselineAcceptanceProposalTests(unittest.TestCase):
    def _workspace(self, root: Path) -> None:
        baseline = root / "skill-system/registry/product-source-baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        first = root / "services/agent-service/src/a.py"
        second = root / "contracts/b.json"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"old-a\n")
        second.write_bytes(b"old-b\n")
        payload = {
            "schema_version": 2,
            "generated_from": "git:" + ("a" * 40),
            "protected_roots": ["services", "contracts"],
            "file_count": 2,
            "files": {
                "services/agent-service/src/a.py": _sha(b"old-a\n"),
                "contracts/b.json": _sha(b"old-b\n"),
            },
        }
        baseline.write_text(json.dumps(payload), encoding="utf-8")

    def test_exact_drift_is_reported_without_baseline_write_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._workspace(root)
            (root / "services/agent-service/src/a.py").write_bytes(b"new-a\n")
            added = root / "services/agent-service/src/new.py"
            added.write_bytes(b"new\n")
            (root / "contracts/b.json").unlink()
            proposal = build_baseline_acceptance_proposal(
                root,
                candidate_sha="b" * 40,
            )

        self.assertEqual(proposal["status"], "DECISION_REQUIRED")
        self.assertEqual(proposal["drift"]["added"], ["services/agent-service/src/new.py"])
        self.assertEqual(proposal["drift"]["modified"], ["services/agent-service/src/a.py"])
        self.assertEqual(proposal["drift"]["deleted"], ["contracts/b.json"])
        self.assertEqual(proposal["drift"]["total_count"], 3)
        self.assertTrue(proposal["human_required"])
        for field in (
            "baseline_write_allowed",
            "source_write_allowed",
            "test_write_allowed",
            "oracle_write_allowed",
            "scope_expansion_allowed",
            "merge_allowed",
            "deploy_allowed",
            "authority_effect",
            "production_closed",
        ):
            self.assertFalse(proposal[field], field)
        rendered = render_baseline_acceptance_proposal(proposal)
        self.assertIn("Human decision required: true", rendered)
        self.assertIn("Baseline write allowed by this proposal: false", rendered)

    def test_no_drift_requires_no_human_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._workspace(root)
            proposal = build_baseline_acceptance_proposal(
                root,
                candidate_sha="b" * 40,
            )

        self.assertEqual(proposal["status"], "NO_DRIFT")
        self.assertFalse(proposal["decision_required"])
        self.assertFalse(proposal["human_required"])
        self.assertEqual(proposal["drift"]["total_count"], 0)


if __name__ == "__main__":
    unittest.main()
