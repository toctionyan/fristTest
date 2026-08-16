from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_ENV = {
    "SKILL_JUDGE_ROOT",
    "SKILL_JUDGE_TRUST_MODE",
}


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        f"quality_env_isolation_{name.replace('.', '_')}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualityTestRunnerJudgeEnvironmentIsolationTests(unittest.TestCase):
    def test_targeted_agent_pytest_strips_outer_controller_judge_binding(self) -> None:
        runner = _load_script("run_agent_pytest.py")
        self.assertEqual(set(runner.CONTROLLER_ONLY_ENV), CONTROLLER_ENV)

        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "SKILL_JUDGE_ROOT": "/trusted/control",
                "SKILL_JUDGE_TRUST_MODE": "external-readonly",
            },
            clear=False,
        ), mock.patch.object(runner, "_can_import", return_value=True):
            runtime = runner._runtime(Path(temporary))

        self.assertIsNotNone(runtime)
        _python, env = runtime
        self.assertTrue(CONTROLLER_ENV.isdisjoint(env))

    def test_standard_suite_strips_outer_controller_judge_binding_from_pytest(self) -> None:
        runner = _load_script("run_python_test_suites.py")
        self.assertEqual(set(runner.CONTROLLER_ONLY_ENV), CONTROLLER_ENV)
        captured: dict[str, str] = {}

        class FakeProcess:
            pid = 12345

            def wait(self, timeout=None):
                return 0

        def fake_popen(*_args, **kwargs):
            captured.update(kwargs["env"])
            return FakeProcess()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            junit = root / "evidence" / "junit" / "agent.xml"
            coverage = root / "evidence" / "coverage" / "agent.xml"
            with mock.patch.dict(
                os.environ,
                {
                    "SKILL_JUDGE_ROOT": "/trusted/control",
                    "SKILL_JUDGE_TRUST_MODE": "external-readonly",
                },
                clear=False,
            ), mock.patch.object(
                runner, "_locked_project_python", return_value=Path(sys.executable)
            ), mock.patch.object(runner.subprocess, "Popen", side_effect=fake_popen):
                result = runner._run(
                    root,
                    name="agent-service-pytest",
                    cwd="services/agent-service",
                    selector="not integration and not preprod",
                    junit_path=junit,
                    coverage_path=coverage,
                )

        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(CONTROLLER_ENV.isdisjoint(captured))


if __name__ == "__main__":
    unittest.main()
