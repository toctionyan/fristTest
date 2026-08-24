from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from concrete_host_bootstrap import (  # type: ignore  # noqa: E402
    CONCRETE_HOST_BOOTSTRAP_SCHEMA,
    ConcreteHostBootstrapError,
    ProjectCommandProfileRunner,
    build_orchestrator,
    seal_bootstrap,
    validate_bootstrap_declaration,
)
from project_initializer import (  # type: ignore  # noqa: E402
    ProjectInitializerError,
    initialize_concrete_host_project,
)
from starter_provider_bootstrap import (  # type: ignore  # noqa: E402
    build_concrete_starter_provider_registry,
)


def unsigned_bootstrap() -> dict[str, object]:
    return {
        "schema": CONCRETE_HOST_BOOTSTRAP_SCHEMA,
        "starter": {"starter_id": "customer-agent", "package_sha256": "a" * 64},
        "registration": {
            "path": ".harness/runtime/starter-registration.json",
            "sha256": "b" * 64,
        },
        "checkpointer": {
            "type": "sqlite",
            "path": ".harness/runtime/langgraph-checkpoints.sqlite3",
        },
        "providers": {
            "execution_profiles": {
                "test.run": ["test"],
                "quality.evaluate": ["quality"],
            },
            "process_timeout_seconds": 30,
            "github": None,
        },
        "authority": {
            "type": "repair-change-permit",
            "active_contract_path": "governance/active-change.json",
            "audit_root": ".harness/runtime/authority-checks",
            "generic_merge_authority": False,
        },
        "human_gate": {
            "type": "durable-local",
            "gate_root": ".harness/runtime/human-gates",
            "decision_root": ".harness/runtime/human-decisions",
            "authority_effect": False,
        },
        "scheduler": {
            "type": "durable-local-one-shot",
            "event_root": ".harness/runtime/external-events",
            "receipt_root": ".harness/runtime/external-wakeup-receipts",
            "lock_root": ".harness/runtime/external-wakeup-locks",
            "max_events_per_run": 100,
            "provider_polling": False,
            "authority_effect": False,
        },
        "runtime": {
            "session_root": ".harness/runtime/host-sessions",
            "taskrun_root": ".harness/taskruns",
            "workspace_fingerprint": None,
        },
        "policy": {
            "configuration_grants_write_authority": False,
            "configuration_completes_taskrun": False,
            "scheduler_is_authority": False,
            "external_event_completes_taskrun": False,
            "provider_polling": False,
            "automatic_merge": False,
            "completion_authority": "TaskRun",
            "authority_effect": False,
        },
    }


class ConcreteHostBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="concrete-host-bootstrap-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def _git_init(self) -> None:
        completed = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_bootstrap_is_closed_fingerprinted_and_path_bounded(self) -> None:
        sealed = seal_bootstrap(unsigned_bootstrap())
        self.assertEqual(validate_bootstrap_declaration(sealed), sealed)

        tampered = json.loads(json.dumps(sealed))
        tampered["runtime"]["session_root"] = "../sessions"
        with self.assertRaisesRegex(ConcreteHostBootstrapError, "bounded"):
            validate_bootstrap_declaration(tampered)

        tampered = json.loads(json.dumps(sealed))
        tampered["providers"]["process_timeout_seconds"] = 31
        with self.assertRaisesRegex(ConcreteHostBootstrapError, "fingerprint"):
            validate_bootstrap_declaration(tampered)

        unexpected = json.loads(json.dumps(sealed))
        unexpected["write_authority"] = "allow"
        with self.assertRaisesRegex(ConcreteHostBootstrapError, "not closed"):
            validate_bootstrap_declaration(unexpected)

        immutable = unsigned_bootstrap()
        immutable["checkpointer"]["path"] = (
            ".harness/starters/customer-agent/checkpoints.sqlite3"
        )
        with self.assertRaisesRegex(ConcreteHostBootstrapError, "immutable Starter"):
            seal_bootstrap(immutable)

    def test_project_command_runner_uses_sealed_argv_without_a_shell(self) -> None:
        marker = self.root / "must-not-exist"
        with mock.patch.dict(os.environ, {"PRIVATE_GITHUB_TOKEN": "secret"}):
            runner = ProjectCommandProfileRunner(
                workspace=self.root,
                commands={"test": f"{sys.executable} -c 'print(42)' ; touch {marker}"},
                timeout=30,
                denied_environment_variables=("PRIVATE_GITHUB_TOKEN",),
            )
        state = self.root / "state.json"
        result = runner("test", state_file=state)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(marker.exists())
        self.assertNotIn("PRIVATE_GITHUB_TOKEN", runner.environment)
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["authority_effect"], False)

    def test_local_only_provider_assembly_needs_no_github_token(self) -> None:
        self._git_init()
        assembly = build_concrete_starter_provider_registry(
            workspace=self.root,
            write_scope=("src/**",),
            allowed_profiles={
                "test.run": ("test",),
                "quality.evaluate": ("quality",),
            },
            github=None,
            process_runner=lambda profile, *, state_file: {"status": "PASS"},
        )
        self.assertEqual(
            assembly.provider_ids,
            ("local.workspace", "local.process", "local.git"),
        )
        self.assertFalse(assembly.policy["integration_configured"])
        self.assertFalse(assembly.policy["write_authority_granted"])

    def test_initializer_and_builtin_factory_assemble_real_runtime(self) -> None:
        self._git_init()
        result = initialize_concrete_host_project(
            project_workspace=self.root,
            registry_workspace=ROOT,
        )
        bootstrap = self.root / result["bootstrap"]
        self.assertTrue(bootstrap.is_file())
        self.assertTrue((self.root / ".harness/host-init.lock").is_file())
        self.assertEqual(result["factory"], "concrete_host_bootstrap:build_orchestrator")
        self.assertFalse(result["policy"]["configuration_grants_write_authority"])

        with mock.patch.dict(
            os.environ,
            {"HARNESS_HOST_BOOTSTRAP": str(bootstrap)},
            clear=True,
        ):
            orchestrator = build_orchestrator(host_id="codex")
        self.assertEqual(orchestrator.host_id, "codex")
        self.assertIsNotNone(orchestrator.write_authority_guard)
        self.assertTrue(
            orchestrator._concrete_bootstrap_policy["write_authority_injected"]
        )
        self.assertIsNotNone(orchestrator.human_gate_adapter)
        self.assertTrue(orchestrator._concrete_bootstrap_policy["human_gate_injected"])
        self.assertIsNotNone(orchestrator._concrete_wakeup_scheduler)
        self.assertTrue(orchestrator._concrete_bootstrap_policy["scheduler_injected"])
        self.assertFalse(orchestrator._concrete_bootstrap_policy["provider_polling"])
        self.assertFalse(
            orchestrator._concrete_bootstrap_policy["write_authority_currently_granted"]
        )
        self.assertFalse(orchestrator._concrete_bootstrap_policy["generic_merge_authority"])
        self.assertTrue(
            (self.root / ".harness/runtime/langgraph-checkpoints.sqlite3").is_file()
        )
        orchestrator._concrete_bootstrap_connection.close()

        with self.assertRaisesRegex(ProjectInitializerError, "refusing to overwrite"):
            initialize_concrete_host_project(
                project_workspace=self.root,
                registry_workspace=ROOT,
            )

    def test_failed_initializer_cleans_partial_installation(self) -> None:
        self._git_init()
        with self.assertRaisesRegex(ProjectInitializerError, "no declared command"):
            initialize_concrete_host_project(
                project_workspace=self.root,
                registry_workspace=ROOT,
                test_profiles=("missing-command",),
            )
        self.assertFalse((self.root / ".harness").exists())

    def test_configured_github_missing_token_blocks_factory(self) -> None:
        self._git_init()
        result = initialize_concrete_host_project(
            project_workspace=self.root,
            registry_workspace=ROOT,
            github_repository="owner/customer-agent",
            github_token_environment_variable="PRIVATE_GITHUB_TOKEN",
        )
        with mock.patch.dict(
            os.environ,
            {"HARNESS_HOST_BOOTSTRAP": str(self.root / result["bootstrap"])},
            clear=True,
        ):
            with self.assertRaisesRegex(Exception, "PRIVATE_GITHUB_TOKEN"):
                build_orchestrator(host_id="chatgpt")

    def test_root_cli_initializes_and_opens_one_durable_host_session(self) -> None:
        self._git_init()
        initialized = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "skillctl.py"),
                "authoring",
                "host-init",
                "--project-workspace",
                str(self.root),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        init_result = json.loads(initialized.stdout)
        self.assertEqual(init_result["status"], "PASS")

        request = self.root / "open.json"
        request.write_text(
            json.dumps(
                {
                    "schema": "starter-host-command@1",
                    "command_id": "open-1",
                    "host_id": "codex",
                    "operation": "OPEN",
                    "session_id": "session-1",
                    "expected_revision": None,
                    "payload": {"user_request": "检查客服 Agent 的整体问题"},
                    "authority_effect": False,
                }
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.update(
            {
                "HARNESS_HOST_FACTORY": init_result["factory"],
                "HARNESS_HOST_BOOTSTRAP": str(self.root / init_result["bootstrap"]),
            }
        )
        opened = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "skillctl.py"),
                "host",
                "--request",
                str(request),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(opened.returncode, 0, opened.stderr)
        response = json.loads(opened.stdout)
        self.assertEqual(response["status"], "PASS")
        self.assertEqual(response["session"]["phase"], "AWAITING_SELECTION")
        self.assertFalse(response["policy"]["write_authority_granted"])


if __name__ == "__main__":
    unittest.main()
