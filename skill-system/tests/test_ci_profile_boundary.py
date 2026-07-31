from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CIProfileBoundaryTests(unittest.TestCase):
    def test_project_quality_ci_runs_skill_self_checks_without_skill_only_compatibility(self) -> None:
        workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
        self.assertIn("skill-self-validation:", workflow)
        self.assertIn("needs: skill-self-validation", workflow)
        self.assertNotIn("profile_runner.py skill-control-plane", workflow)
        for profile in (
            "skill-static",
            "skill-unit",
            "skill-host-integration",
            "skill-security",
        ):
            self.assertIn(f"profile_runner.py {profile}", workflow)

    def test_skill_only_release_retains_product_compatibility_guard(self) -> None:
        control_plane = json.loads(
            (ROOT / "skill-system/profiles/skill-control-plane.json").read_text(encoding="utf-8")
        )
        skill_release = json.loads(
            (ROOT / "skill-system/profiles/skill-release.json").read_text(encoding="utf-8")
        )
        self.assertIn("project-compatibility-smoke", control_plane["includes"])
        self.assertIn("skill-control-plane", skill_release["includes"])


if __name__ == "__main__":
    unittest.main()
