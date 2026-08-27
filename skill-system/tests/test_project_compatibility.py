from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from product_source_baseline_policy import build_canonical_product_snapshot  # type: ignore
from project_compatibility import evaluate  # type: ignore


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ProjectCompatibilityCanonicalSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write(self.root / "services/app.py", "VALUE = 1\n")
        for path in (
            "scripts/quality_loop.py",
            "scripts/repair_loop.py",
            "architecture-skill/scripts/verify_skill_package.py",
        ):
            _write(self.root / path, "# entrypoint\n")
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.name", "Test")
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "product source")
        source_sha = _git(self.root, "rev-parse", "HEAD")
        baseline = self.root / "skill-system/registry/product-source-baseline.json"
        _write(baseline, build_canonical_product_snapshot(self.root, source_sha, ("contracts", "services", "web")))
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "v3 baseline")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_checkout_uses_v3_historical_registry(self) -> None:
        result = evaluate(self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["baseline_authority"], "historical-registry-baseline")
        self.assertEqual(result["baseline_mode"], "accepted_ref")

    def test_uncommitted_product_bytes_are_not_git_object_authority(self) -> None:
        _write(self.root / "services/app.py", "VALUE = 2\n")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["drift_paths"], [])

    def test_committed_product_drift_fails_accepted_ref(self) -> None:
        _write(self.root / "services/app.py", "VALUE = 2\n")
        _git(self.root, "add", "services/app.py")
        _git(self.root, "commit", "-qm", "product drift")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("services/app.py", result["drift_paths"])

    def test_pull_request_product_drift_is_reported_without_promotion(self) -> None:
        _write(self.root / "services/app.py", "VALUE = 2\n")
        _git(self.root, "add", "services/app.py")
        _git(self.root, "commit", "-qm", "candidate drift")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}, clear=False):
            result = evaluate(self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["baseline_mode"], "pr_candidate")
        self.assertIn("services/app.py", result["drift_paths"])

    def test_registry_v2_is_unavailable(self) -> None:
        _write(self.root / "skill-system/registry/product-source-baseline.json", {"schema_version": 2})
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["baseline_authority"], "unavailable")


if __name__ == "__main__":
    unittest.main()
