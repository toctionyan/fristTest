from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
ROOT = SKILL_SYSTEM.parent
STARTER = SKILL_SYSTEM / "starters" / "customer-agent"
for search_path in (CONTROLLER, SKILL_SYSTEM):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from starter_host_cli import main as cli_main  # type: ignore  # noqa: E402
from starter_host_orchestrator import StarterHostOrchestrator  # type: ignore  # noqa: E402
from starter_host_transport import (  # type: ignore  # noqa: E402
    StarterHostCommandTransport,
    StarterHostTransportError,
    failure_response,
)
from starter_runtime import (  # type: ignore  # noqa: E402
    STARTER_HOST_SELECTION_SCHEMA,
    register_starter_runtime,
)
from workflow_dispatcher import ProviderAdapterRegistry  # type: ignore  # noqa: E402


class RecordingOrchestrator(StarterHostOrchestrator):
    host_id = "codex"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _record(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        return {
            "schema": "starter-host-session@1",
            "session_id": kwargs["session_id"],
            "host_id": self.host_id,
            "revision": 8,
            "next_action": {
                "schema": "starter-host-next-action@1",
                "kind": "TEST_NEXT_ACTION",
                "authority_effect": False,
            },
        }

    def open(self, **kwargs):
        return self._record("open", **kwargs)

    def read(self, session_id):
        return self._record("read", session_id=session_id)

    def select(self, **kwargs):
        return self._record("select", **kwargs)

    def confirm(self, **kwargs):
        return self._record("confirm", **kwargs)

    def start(self, **kwargs):
        return self._record("start", **kwargs)

    def submit_host_result(self, **kwargs):
        return self._record("submit_host_result", **kwargs)

    def resume_external(self, **kwargs):
        return self._record("resume_external", **kwargs)

    def resume_human(self, **kwargs):
        return self._record("resume_human", **kwargs)

    def reconcile(self, **kwargs):
        return self._record("reconcile", **kwargs)


class StarterHostTransportTest(unittest.TestCase):
    @staticmethod
    def command(operation: str, payload: dict[str, object], *, revision=7):
        return {
            "schema": "starter-host-command@1",
            "command_id": f"cmd-{operation.lower()}",
            "host_id": "codex",
            "operation": operation,
            "session_id": "session-1",
            "expected_revision": revision,
            "payload": payload,
            "authority_effect": False,
        }

    def test_all_operations_map_only_to_the_exact_orchestrator_method(self) -> None:
        cases = [
            (
                "OPEN",
                {"user_request": "检查客服 Agent 总体还有哪些问题"},
                None,
                "open",
                {"session_id": "session-1", "user_request": "检查客服 Agent 总体还有哪些问题"},
            ),
            ("READ", {}, None, "read", {"session_id": "session-1"}),
            (
                "SELECT",
                {"selection": {"schema": "selection", "selected_entrypoint": "overall_audit"}},
                7,
                "select",
                {
                    "session_id": "session-1",
                    "expected_revision": 7,
                    "selection": {"schema": "selection", "selected_entrypoint": "overall_audit"},
                },
            ),
            (
                "CONFIRM",
                {"confirmation": {"schema": "confirmation", "confirmed": True}},
                7,
                "confirm",
                {
                    "session_id": "session-1",
                    "expected_revision": 7,
                    "confirmation": {"schema": "confirmation", "confirmed": True},
                },
            ),
            (
                "START",
                {"target_ref": {"kind": "project", "ref": "customer-agent"}},
                7,
                "start",
                {
                    "session_id": "session-1",
                    "expected_revision": 7,
                    "target_ref": {"kind": "project", "ref": "customer-agent"},
                },
            ),
            (
                "SUBMIT_HOST_RESULT",
                {"result": {"execution_id": "execution-1"}},
                7,
                "submit_host_result",
                {
                    "session_id": "session-1",
                    "expected_revision": 7,
                    "result": {"execution_id": "execution-1"},
                },
            ),
            (
                "RESUME_EXTERNAL",
                {
                    "event": {"event": "ci.completed", "status": "success"},
                    "evidence_refs": ["ci:run:1"],
                    "correlation_ref": "run-1",
                },
                7,
                "resume_external",
                {
                    "session_id": "session-1",
                    "expected_revision": 7,
                    "event": {"event": "ci.completed", "status": "success"},
                    "evidence_refs": ["ci:run:1"],
                    "correlation_ref": "run-1",
                },
            ),
            (
                "RESUME_HUMAN",
                {
                    "decision": {"decision": "approve"},
                    "evidence_refs": ["human:decision:1"],
                },
                7,
                "resume_human",
                {
                    "session_id": "session-1",
                    "expected_revision": 7,
                    "decision": {"decision": "approve"},
                    "evidence_refs": ["human:decision:1"],
                },
            ),
            (
                "RECONCILE",
                {},
                7,
                "reconcile",
                {"session_id": "session-1", "expected_revision": 7},
            ),
        ]
        for operation, payload, revision, method, expected in cases:
            with self.subTest(operation=operation):
                orchestrator = RecordingOrchestrator()
                response = StarterHostCommandTransport(orchestrator).execute(
                    self.command(operation, payload, revision=revision)
                )
                self.assertEqual(orchestrator.calls, [(method, expected)])
                self.assertEqual(response["status"], "PASS")
                self.assertEqual(response["operation"], operation)
                self.assertEqual(response["session"]["session_id"], "session-1")
                self.assertEqual(response["next_action"], response["session"]["next_action"])
                self.assertFalse(response["policy"]["transport_is_authority"])
                self.assertFalse(response["policy"]["write_authority_granted"])
                self.assertFalse(response["policy"]["automatic_merge"])
                self.assertEqual(response["policy"]["completion_authority"], "TaskRun")

    def test_closed_commands_reject_malformed_or_authorizing_inputs_before_dispatch(self) -> None:
        base = self.command("READ", {}, revision=None)
        invalid = []
        row = copy.deepcopy(base)
        row["factory"] = "hostile:build"
        invalid.append(row)
        row = copy.deepcopy(base)
        row["operation"] = "AUTOMATIC_MERGE"
        invalid.append(row)
        row = copy.deepcopy(base)
        row["authority_effect"] = True
        invalid.append(row)
        row = copy.deepcopy(base)
        row["expected_revision"] = 0
        invalid.append(row)
        row = copy.deepcopy(base)
        row["host_id"] = "Codex"
        invalid.append(row)
        row = copy.deepcopy(base)
        row["operation"] = "read"
        invalid.append(row)
        row = copy.deepcopy(base)
        row["command_id"] = 17
        invalid.append(row)
        row = self.command("START", {}, revision=0)
        invalid.append(row)
        row = self.command(
            "RESUME_EXTERNAL",
            {"event": {"status": "success"}, "evidence_refs": [], "correlation_ref": "run-1"},
        )
        invalid.append(row)
        row = self.command(
            "RESUME_HUMAN",
            {"decision": {"decision": "approve"}, "evidence_refs": ["same", "same"]},
        )
        invalid.append(row)
        row = self.command(
            "RESUME_HUMAN",
            {"decision": {"decision": "approve"}, "evidence_refs": [17]},
        )
        invalid.append(row)

        for command in invalid:
            with self.subTest(command=command):
                orchestrator = RecordingOrchestrator()
                with self.assertRaises(StarterHostTransportError):
                    StarterHostCommandTransport(orchestrator).execute(command)
                self.assertEqual(orchestrator.calls, [])

    def test_factory_host_and_orchestrator_response_identity_must_match(self) -> None:
        command = self.command("READ", {}, revision=None)
        orchestrator = RecordingOrchestrator()
        orchestrator.host_id = "chatgpt"
        with self.assertRaisesRegex(StarterHostTransportError, "another Host"):
            StarterHostCommandTransport(orchestrator).execute(command)

        orchestrator = RecordingOrchestrator()
        orchestrator._record = lambda name, **kwargs: {
            "session_id": "other",
            "host_id": "codex",
            "next_action": {},
        }
        with self.assertRaisesRegex(StarterHostTransportError, "another Host session"):
            StarterHostCommandTransport(orchestrator).execute(command)

    def test_failure_response_never_reflects_untrusted_fields_or_authority(self) -> None:
        response = failure_response(
            {
                "command_id": "bad command secret-token",
                "host_id": "hostile",
                "operation": "AUTOMATIC_MERGE",
                "session_id": "../../secret",
            },
            code="INVALID_HOST_COMMAND",
            message="bounded failure",
        )
        self.assertEqual(response["status"], "BLOCKED")
        self.assertIsNone(response["command_id"])
        self.assertIsNone(response["host_id"])
        self.assertIsNone(response["operation"])
        self.assertIsNone(response["session_id"])
        self.assertFalse(response["authority_effect"])
        self.assertNotIn("secret-token", json.dumps(response))

    def test_transport_drives_real_orchestrator_open_read_and_exact_selection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="starter-host-transport-real-") as temp:
            project = Path(temp) / "project"
            package = project / ".harness/customer-agent"
            package.parent.mkdir(parents=True)
            shutil.copytree(STARTER, package)
            registration = project / ".harness/runtime/customer-agent.registration.json"
            register_starter_runtime(
                project_workspace=project,
                starter_directory=package,
                output=registration,
                registry_workspace=ROOT,
            )
            orchestrator = StarterHostOrchestrator(
                registry_workspace=ROOT,
                project_workspace=project,
                registration=registration,
                host_id="codex",
                provider_adapters=ProviderAdapterRegistry(),
                checkpointer=object(),
                workspace_fingerprint="transport-real-1",
            )
            transport = StarterHostCommandTransport(orchestrator)
            opened = transport.execute(
                self.command(
                    "OPEN",
                    {"user_request": "检查客服 Agent 总体还有哪些问题"},
                    revision=None,
                )
            )["session"]
            self.assertEqual(opened["phase"], "AWAITING_SELECTION")
            self.assertEqual(opened["next_action"]["kind"], "SELECT_EXACT_ENTRYPOINT")
            request = opened["selection_request"]
            selection = {
                "schema": STARTER_HOST_SELECTION_SCHEMA,
                "host_id": request["host_id"],
                "request_fingerprint_sha256": request["request_fingerprint_sha256"],
                "selected_entrypoint": "overall_audit",
                "authority_effect": False,
            }
            selected = transport.execute(
                self.command(
                    "SELECT",
                    {"selection": selection},
                    revision=opened["revision"],
                )
            )["session"]
            self.assertEqual(selected["phase"], "READY_TO_START")
            self.assertEqual(selected["next_action"]["kind"], "START_TASKRUN")
            read = transport.execute(
                self.command("READ", {}, revision=None)
            )["session"]
            self.assertEqual(read, selected)

    def test_cli_outputs_one_closed_json_and_redacts_factory_or_runtime_failures(self) -> None:
        command = self.command(
            "OPEN", {"user_request": "检查总体问题"}, revision=None
        )

        def loader(_spec):
            return lambda *, host_id: RecordingOrchestrator()

        original_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(command))
            output = io.StringIO()
            with redirect_stdout(output):
                result = cli_main(["--factory", "trusted.factory:build"], factory_loader=loader)
        finally:
            sys.stdin = original_stdin
        self.assertEqual(result, 0)
        response = json.loads(output.getvalue())
        self.assertEqual(response["status"], "PASS")

        def failing_loader(_spec):
            def fail(*, host_id):
                raise RuntimeError("secret-token-value")

            return fail

        try:
            sys.stdin = io.StringIO(json.dumps(command))
            output = io.StringIO()
            with redirect_stdout(output):
                result = cli_main(["--factory", "trusted.factory:build"], factory_loader=failing_loader)
        finally:
            sys.stdin = original_stdin
        self.assertEqual(result, 3)
        body = output.getvalue()
        self.assertNotIn("secret-token-value", body)
        self.assertEqual(json.loads(body)["error"]["code"], "HOST_ORCHESTRATION_BLOCKED")

    def test_cli_rejects_invalid_command_before_loading_trusted_factory(self) -> None:
        command = self.command("READ", {}, revision=None)
        command["factory"] = "hostile:build"
        calls = []

        def loader(spec):
            calls.append(spec)
            raise AssertionError("factory loader must not run")

        original_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(command))
            output = io.StringIO()
            with redirect_stdout(output):
                result = cli_main(
                    ["--factory", "trusted.factory:build"], factory_loader=loader
                )
        finally:
            sys.stdin = original_stdin
        self.assertEqual(result, 2)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")

    def test_root_skillctl_host_forwards_to_explicit_factory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="starter-host-cli-factory-") as temp:
            factory = Path(temp) / "host_fixture.py"
            factory.write_text(
                """
from starter_host_orchestrator import StarterHostOrchestrator

class Fake(StarterHostOrchestrator):
    host_id = "codex"
    def __init__(self):
        pass
    def open(self, *, session_id, user_request):
        return {
            "session_id": session_id,
            "host_id": self.host_id,
            "revision": 0,
            "next_action": {"kind": "SELECT_EXACT_ENTRYPOINT", "authority_effect": False},
        }

def build(*, host_id):
    if host_id != "codex":
        raise RuntimeError("wrong host")
    return Fake()
""".lstrip(),
                encoding="utf-8",
            )
            command = self.command(
                "OPEN", {"user_request": "检查总体问题"}, revision=None
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = temp
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "skillctl.py"),
                    "host",
                    "--factory",
                    "host_fixture:build",
                ],
                cwd=ROOT,
                input=json.dumps(command),
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
        self.assertEqual(
            completed.returncode, 0, f"stderr={completed.stderr}\nstdout={completed.stdout}"
        )
        response = json.loads(completed.stdout)
        self.assertEqual(response["status"], "PASS")
        self.assertEqual(response["next_action"]["kind"], "SELECT_EXACT_ENTRYPOINT")

    def test_schema_publishes_closed_request_response_and_fixed_policy(self) -> None:
        schema = json.loads(
            (SKILL_SYSTEM / "schemas/starter-host-command.schema.json").read_text(
                encoding="utf-8"
            )
        )
        command = schema["$defs"]["command"]
        response = schema["$defs"]["response"]
        self.assertFalse(command["additionalProperties"])
        self.assertFalse(response["additionalProperties"])
        policy = schema["$defs"]["policy"]["properties"]
        self.assertFalse(policy["write_authority_granted"]["const"])
        self.assertFalse(policy["automatic_merge"]["const"])
        self.assertEqual(policy["completion_authority"]["const"], "TaskRun")


if __name__ == "__main__":
    unittest.main()
