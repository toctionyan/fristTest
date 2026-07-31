from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from trusted_judge import MANIFEST_REL, verify_candidate, verify_root  # type: ignore


class TrustedJudgeTest(unittest.TestCase):
    def test_candidate_trust_root_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            judge = base / "judge"
            candidate = base / "candidate"
            rel = Path("scripts/quality_loop.py")
            for root in (judge, candidate):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("trusted\n", encoding="utf-8")
            digest = hashlib.sha256(b"trusted\n").hexdigest()
            manifest = judge / MANIFEST_REL
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps({"schema_version": 1, "files": {rel.as_posix(): digest}}),
                encoding="utf-8",
            )
            self.assertEqual(verify_root(judge), [])
            self.assertEqual(verify_candidate(candidate, judge), [])
            (candidate / rel).write_text("changed\n", encoding="utf-8")
            self.assertEqual(
                verify_candidate(candidate, judge),
                ["candidate_trust_root_changed:scripts/quality_loop.py"],
            )


if __name__ == "__main__":
    unittest.main()
