from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class Attempt3Stage2ValidationProbe(unittest.TestCase):
    def _run_pytest(self, *targets: str) -> None:
        env = dict(os.environ)
        pythonpath = [
            str(ROOT / "services/agent-service/src"),
            str(ROOT / "services/agent-service"),
            str(ROOT / "services/business-service"),
        ]
        if env.get("PYTHONPATH"):
            pythonpath.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        command = [sys.executable, "-B", "-m", "pytest", "-q", *targets]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=420,
        )
        print("\n[WP08 Attempt3 Stage2]", " ".join(command))
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr)
        self.assertEqual(completed.returncode, 0, "Stage2 pytest probe failed")

    def test_01_agent_semantic_strong_context_regressions(self) -> None:
        self._run_pytest(
            "services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py",
            "services/agent-service/tests/context/test_strong_context_case_execution.py",
            "services/agent-service/tests/context/test_conversation_regression_suite_execution.py",
            "services/agent-service/tests/runtime/test_goal_granularity_and_conditions.py",
            "services/agent-service/tests/runtime/test_semantic_reference_binding.py",
            "services/agent-service/tests/runtime/test_semantic_grounding_read.py",
            "services/agent-service/tests/runtime/test_similar_capability_rejection.py",
            "services/agent-service/tests/runtime/test_workflow_runtime.py",
            "services/agent-service/tests/runtime/test_wp08_attempt6_release_repairs.py",
        )

    def test_02_business_service_nonintegration_regressions(self) -> None:
        # PostgreSQL persistence tests intentionally fail when the protected DB
        # URLs are absent.  Stage 2 validates all non-integration Business tests;
        # the real PostgreSQL paths remain mandatory in the later protected gate.
        self._run_pytest(
            "-m",
            "not integration",
            "services/business-service/tests",
        )

    def test_03_report_exact_protected_baseline_delta(self) -> None:
        baseline_path = ROOT / "skill-system/registry/product-source-baseline.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        recorded = dict(baseline.get("files") or {})
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "services", "web", "contracts"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        tracked = sorted(
            value.decode("utf-8")
            for value in completed.stdout.split(b"\0")
            if value
        )
        current = {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in tracked
        }
        delta = {
            relative: {"recorded": recorded.get(relative), "current": current.get(relative)}
            for relative in sorted(set(recorded) | set(current))
            if recorded.get(relative) != current.get(relative)
        }
        print("\n[WP08 Attempt3 Stage3 baseline delta] " + json.dumps(delta, sort_keys=True))
        self.assertEqual(set(delta), {
            "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
            "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py",
        })


if __name__ == "__main__":
    unittest.main()
