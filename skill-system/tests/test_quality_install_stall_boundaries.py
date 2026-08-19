from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
PINNED_PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.61.1-noble@sha256:"
    "5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48"
)


def _job_block(text: str, job_name: str, next_job_name: str) -> str:
    start = text.index(f"  {job_name}:\n")
    end = text.index(f"\n  {next_job_name}:\n", start)
    return text[start:end]


class QualityInstallStallBoundaryTests(unittest.TestCase):
    def test_quick_and_integration_install_paths_are_bounded_and_observable(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        quick = _job_block(text, "quality-quick-execution", "quality-quick-required-status")
        integration = _job_block(text, "quality-integration", "governed-failure-stage1")

        self.assertEqual(text.count(f"image: {PINNED_PLAYWRIGHT_IMAGE}"), 2)

        for block in (quick, integration):
            self.assertIn("    timeout-minutes: 30\n", block)
            self.assertNotIn("- name: Install locked Python environments\n", block)

            self.assertIn(f"      image: {PINNED_PLAYWRIGHT_IMAGE}\n", block)
            self.assertIn("      options: --user 1001\n", block)

            self.assertIn(
                "      - name: Install locked Agent environment\n"
                "        timeout-minutes: 5\n"
                "        working-directory: services/agent-service\n"
                "        run: uv sync --locked --all-groups\n",
                block,
            )
            self.assertIn(
                "      - name: Install locked Business environment\n"
                "        timeout-minutes: 5\n"
                "        working-directory: services/business-service\n"
                "        run: uv sync --locked --all-groups\n",
                block,
            )
            self.assertIn(
                "      - name: Install locked frontend dependencies\n"
                "        timeout-minutes: 5\n"
                "        working-directory: services/agent-service/frontend\n"
                "        run: npm ci --ignore-scripts=false\n",
                block,
            )
            self.assertIn(
                "      - name: Install locked Chromium runtime\n"
                "        timeout-minutes: 2\n"
                "        working-directory: services/agent-service/frontend\n",
                block,
            )
            for runtime_marker in (
                'test "${PLAYWRIGHT_BROWSERS_PATH}" = "/ms-playwright"',
                "import { chromium } from 'playwright';",
                "chromium.launch({ headless: true })",
                "quality-browser-runtime",
            ):
                self.assertIn(runtime_marker, block)

            self.assertNotIn("playwright install --with-deps", block)
            self.assertNotIn("playwright install-deps", block)


if __name__ == "__main__":
    unittest.main()
