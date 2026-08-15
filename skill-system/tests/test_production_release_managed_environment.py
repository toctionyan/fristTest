from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "run_production_release.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "production_release_managed_environment_regression", RUNNER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionReleaseManagedEnvironmentTests(unittest.TestCase):
    def test_release_plan_preserves_virtualenv_launcher_identity(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            target = workspace / "target.md"
            target.write_text("target\n", encoding="utf-8")
            base_python = root / "base-python"
            base_python.write_text("#!/bin/sh\n", encoding="utf-8")
            base_python.chmod(0o755)
            agent_python = (
                workspace
                / "services"
                / "agent-service"
                / ".venv"
                / "bin"
                / "python"
            )
            agent_python.parent.mkdir(parents=True)
            agent_python.symlink_to(base_python)

            plan = runner.build_release_plan(
                workspace_root=workspace,
                target_path=target,
                evidence_dir=root / "evidence",
                output_dir=root / "output",
                artifact_name="customer_agent_workspace_v20_17_production_closed",
                python_executable=agent_python,
            )

            self.assertEqual(plan["quality_command"][0], str(agent_python.absolute()))
            self.assertEqual(plan["artifact_command"][0], str(agent_python.absolute()))
            self.assertTrue(Path(plan["quality_command"][0]).is_symlink())
            self.assertEqual(
                Path(plan["quality_command"][0]).resolve(), base_python.resolve()
            )

    def test_managed_quality_environment_preserves_protected_model_identity(self) -> None:
        runner = _load_runner()
        protected = {
            "OPENAI_API_KEY": "protected-model-key",
            "OPENAI_API_BASE": "https://api.deepseek.com",
            "OPENAI_MODEL": "deepseek-chat",
            "REAL_MODEL_CERTIFICATION_PROVIDER": "deepseek",
            "EMBEDDING_API_KEY": "protected-embedding-key",
            "EMBEDDING_API_BASE": "https://api.openai.com/v1",
            "EMBEDDING_MODEL": "text-embedding-3-small",
        }
        with tempfile.TemporaryDirectory() as temporary:
            recovery = Path(temporary) / "managed-postgres-recovery.json"
            environment = runner._compose_managed_quality_environment(
                protected,
                postgres_url="postgresql+psycopg://quality@127.0.0.1:55432/quality",
                agent_url="http://127.0.0.1:18000",
                business_url="http://127.0.0.1:19000",
                business_service_token="owned-test-token",
                recovery_evidence=recovery,
            )

        for key, value in protected.items():
            self.assertEqual(environment[key], value)
        self.assertEqual(environment["AGENT_TEST_URL"], "http://127.0.0.1:18000")
        self.assertEqual(environment["BUSINESS_TEST_URL"], "http://127.0.0.1:19000")
        self.assertEqual(
            environment["BUSINESS_SERVICE_BASE_URL"], "http://127.0.0.1:19000"
        )
        self.assertEqual(environment["BUSINESS_SERVICE_TOKEN"], "owned-test-token")
        self.assertEqual(environment["PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA"], "true")
        self.assertTrue(
            environment["AGENT_TEST_POSTGRES_URL"].startswith("postgresql+psycopg://")
        )
        self.assertEqual(environment["B16C_POSTGRES_RECOVERY_EVIDENCE"], str(recovery))
        self.assertNotIn("deterministic-canary-model", environment.values())


if __name__ == "__main__":
    unittest.main()
