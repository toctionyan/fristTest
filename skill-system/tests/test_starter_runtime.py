from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
ROOT = SKILL_SYSTEM.parent
STARTER = SKILL_SYSTEM / "starters" / "customer-agent"
for search_path in (CONTROLLER, SKILL_SYSTEM):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from langgraph_workflow_runtime import (  # type: ignore  # noqa: E402
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    StepDispatchResult,
)
from starter_runtime import (  # type: ignore  # noqa: E402
    StarterRuntimeError,
    StarterWorkflowRuntime,
    load_starter_registration,
    parse_starter_command,
    register_starter_runtime,
    resolve_starter_entrypoint,
)
from skill_invocation import SkillInvocationError, canonical_skill_identity  # type: ignore  # noqa: E402
from starter_provider_bootstrap import (  # type: ignore  # noqa: E402
    GitHubPullRequestConfiguration,
    build_concrete_starter_provider_registry,
)
from task_run import TaskRunStore  # type: ignore  # noqa: E402
from workflow_dispatcher import (  # type: ignore  # noqa: E402
    ProviderAdapterRegistry,
    SkillHostResult,
)


class RecordingSkillHost:
    outcomes = {
        "customer-agent-audit": "findings",
        "customer-agent-standards-gate": "continue",
        "customer-agent-repair": "patched",
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, *, skill_name, request_class, step, state):
        self.calls.append(skill_name)
        outcome = self.outcomes.get(skill_name, "clean")
        content = json.dumps({"skill": skill_name, "outcome": outcome})
        return SkillHostResult(
            outcome=outcome,
            output_schema="starter-skill-output@1",
            output_content=content,
            output_evidence_ref=f"evidence:skill:{skill_name}",
            evidence_refs=(f"evidence:host:{skill_name}",),
            payload={"skill": skill_name},
        )


class GreenLocalProcessAdapter:
    provider_id = "local.process"
    provider_type = "executor"

    def invoke(self, *, binding, step, state):
        return StepDispatchResult(
            outcome="green",
            evidence_refs=(f"evidence:provider:{step.step_id}:green",),
        )


class GreenProviderAdapter:
    def __init__(self, provider_id: str, provider_type: str) -> None:
        self.provider_id = provider_id
        self.provider_type = provider_type

    def invoke(self, *, binding, step, state):
        if step.step_type == "external_wait" and not state.get("external_event"):
            return StepDispatchResult(
                outcome="pending",
                evidence_refs=("evidence:ci:pending",),
                external_wait={
                    "provider": self.provider_id,
                    "correlation_ref": "ci-run-2093",
                    "resume_event": "ci.completed",
                },
            )
        return StepDispatchResult(
            outcome="green",
            evidence_refs=(f"evidence:provider:{step.step_id}:green",),
        )


class AllowWriteGuard:
    def __init__(self) -> None:
        self.capabilities: list[str] = []

    def assert_allowed(self, *, binding, step, state) -> None:
        self.capabilities.append(binding.capability_id)


class MutatingRecordingSkillHost(RecordingSkillHost):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace

    def execute(self, *, skill_name, request_class, step, state):
        if skill_name == "customer-agent-repair":
            target = self.workspace / "src/fix.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("FIXED = True\n", encoding="utf-8")
        return super().execute(
            skill_name=skill_name,
            request_class=request_class,
            step=step,
            state=state,
        )


class RepositoryBackedGitHubTransport:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls = []
        self.create_payload = None

    def request(self, *, method, url, headers, payload):
        self.calls.append((method, url))
        if method == "POST":
            self.create_payload = dict(payload)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return {
            "number": 2094,
            "html_url": "https://github.com/owner/customer-agent/pull/2094",
            "draft": False,
            "base": {"ref": "main", "repo": {"full_name": "owner/customer-agent"}},
            "head": {
                "ref": "feat/runtime-provider-test",
                "sha": head_sha,
                "repo": {"full_name": "owner/customer-agent"},
            },
        }


class StarterRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="starter-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.package = self.project / ".harness/customer-agent"
        self.package.parent.mkdir(parents=True)
        shutil.copytree(STARTER, self.package)
        self.registration = self.project / ".harness/runtime/customer-agent.registration.json"
        register_starter_runtime(
            project_workspace=self.project,
            starter_directory=self.package,
            output=self.registration,
            registry_workspace=ROOT,
        )

    def load(self):
        return load_starter_registration(
            project_workspace=self.project,
            registration=self.registration,
            registry_workspace=ROOT,
        )

    def store(self, name: str) -> TaskRunStore:
        return TaskRunStore.open_or_create(
            self.project / ".harness/taskruns" / f"{name}.json",
            task_id=f"task-{name}",
            task_kind="customer-agent-starter",
            binding={"project": "customer-agent", "target": name},
            required_conditions=["workflow-evidence", "completion-policy"],
            current_workspace_fingerprint="fp-1",
        )

    def test_registration_seals_exact_workflows_skills_and_has_no_authority(self) -> None:
        loaded = self.load()
        self.assertEqual(set(loaded.payload["entrypoints"]), {
            "overall_audit", "module_audit", "architecture_review",
            "repair_and_prove", "repair_with_ci", "full_dev",
        })
        self.assertEqual(len(loaded.skill_paths), 7)
        self.assertFalse(loaded.payload["policy"]["registration_grants_write_authority"])
        self.assertFalse(loaded.payload["policy"]["automatic_merge"])
        self.assertTrue(all(path.name == "SKILL.md" for path in loaded.skill_paths.values()))

        read_route = resolve_starter_entrypoint(
            loaded, registry_workspace=ROOT, entrypoint="overall_audit"
        )
        write_route = resolve_starter_entrypoint(
            loaded, registry_workspace=ROOT, entrypoint="repair_with_ci"
        )
        self.assertEqual(
            read_route.effect_preview(registry_workspace=ROOT)["next_action"],
            "START_TASKRUN",
        )
        preview = write_route.effect_preview(registry_workspace=ROOT)
        self.assertEqual(preview["next_action"], "REQUIRE_WRITE_AUTHORITY")
        self.assertIn("workspace.write", preview["mutating_capabilities"])
        self.assertIn("ci.run.wait", preview["integration_effects"])
        self.assertFalse(preview["automatic_merge"])

    def test_authoring_and_invocation_clis_expose_registration_and_exact_route(self) -> None:
        second = Path(self.temp.name) / "cli-project"
        package = second / ".harness/customer-agent"
        package.parent.mkdir(parents=True)
        shutil.copytree(STARTER, package)
        registration = second / ".harness/runtime/customer-agent.registration.json"
        registered = subprocess.run(
            [
                sys.executable,
                "-B",
                "skillctl.py",
                "authoring",
                "starter-register",
                "--project-workspace",
                str(second),
                "--directory",
                ".harness/customer-agent",
                "--output",
                ".harness/runtime/customer-agent.registration.json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        self.assertEqual(json.loads(registered.stdout)["schema"], "harness-starter-runtime-registration@1")

        routed = subprocess.run(
            [
                sys.executable,
                "-B",
                "skillctl.py",
                "invoke",
                "--project-workspace",
                str(second),
                "--starter-registration",
                str(registration),
                "--starter-command",
                "/harness audit module src/router",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(routed.returncode, 0, routed.stderr or routed.stdout)
        route = json.loads(routed.stdout)
        self.assertEqual(route["mode"], "STARTER_WORKFLOW_BOUND")
        self.assertEqual(route["selected_workflow"], "customer-agent-module-audit-with-context")
        self.assertFalse(route["policy"]["route_executes_workflow"])
        self.assertFalse(route["policy"]["route_grants_write_authority"])

    def test_strict_commands_route_all_entrypoints_and_reject_ambiguous_write(self) -> None:
        commands = {
            "/harness audit all": "overall_audit",
            "/harness audit module src/router": "module_audit",
            "/harness architecture session boundaries": "architecture_review",
            "/harness repair finding-17 --local": "repair_and_prove",
            "/harness repair finding-17 --ci": "repair_with_ci",
            "/harness full-dev add escalation --ci": "full_dev",
        }
        for command, expected in commands.items():
            with self.subTest(command=command):
                self.assertEqual(parse_starter_command(command)["entrypoint"], expected)
        for invalid in (
            "/harness repair finding-17",
            "/harness repair finding-17 --local --ci",
            "please repair finding-17",
            "/harness full-dev add escalation",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(StarterRuntimeError):
                    parse_starter_command(invalid)

    def test_package_or_registration_drift_fails_closed(self) -> None:
        skill = self.package / "skill-implementations/customer-agent-audit/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nDrift.\n", encoding="utf-8")
        with self.assertRaisesRegex(StarterRuntimeError, "package digest drifted"):
            self.load()

        skill.write_text(skill.read_text(encoding="utf-8").removesuffix("\nDrift.\n"), encoding="utf-8")
        payload = json.loads(self.registration.read_text(encoding="utf-8"))
        payload["policy"]["automatic_merge"] = True
        self.registration.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(StarterRuntimeError, "fingerprint mismatch"):
            self.load()

    def test_injected_skill_path_cannot_escape_project_workspace(self) -> None:
        with self.assertRaisesRegex(SkillInvocationError, "workspace-relative and bounded"):
            canonical_skill_identity(
                self.project,
                "customer-agent-audit",
                canonical_skill_paths={"customer-agent-audit": "../SKILL.md"},
            )

    def test_read_only_entrypoint_runs_real_host_and_ends_in_validating(self) -> None:
        loaded = self.load()
        resolved = resolve_starter_entrypoint(
            loaded, registry_workspace=ROOT, entrypoint="overall_audit"
        )
        host = RecordingSkillHost()
        connection = sqlite3.connect(
            self.project / ".harness/checkpoints.sqlite", check_same_thread=False
        )
        self.addCleanup(connection.close)
        store = self.store("audit")
        runtime = StarterWorkflowRuntime(
            registry_workspace=ROOT,
            resolved=resolved,
            skill_host=host,
            provider_adapters=ProviderAdapterRegistry([GreenLocalProcessAdapter()]),
            checkpointer=SqliteSaver(connection),
            taskrun_store=store,
            workspace_fingerprint="fp-1",
        )
        result = runtime.start(target_ref={"kind": "workspace", "ref": "customer-agent"})

        self.assertEqual(result["runtime_state"]["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(store.payload["status"], "VALIDATING")
        self.assertEqual(
            store.payload["phase"], "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY"
        )
        self.assertEqual(host.calls, ["customer-agent-audit", "customer-agent-standards-gate"])
        self.assertTrue(
            (self.project / ".quality/skill-invocations/active.json").is_file()
        )
        self.assertFalse(result["policy"]["graph_end_completes_taskrun"])

    def test_mutating_skill_fails_closed_without_existing_write_guard(self) -> None:
        loaded = self.load()
        resolved = resolve_starter_entrypoint(
            loaded, registry_workspace=ROOT, entrypoint="repair_and_prove"
        )
        host = RecordingSkillHost()
        connection = sqlite3.connect(
            self.project / ".harness/write-checkpoints.sqlite", check_same_thread=False
        )
        self.addCleanup(connection.close)
        store = self.store("repair")
        runtime = StarterWorkflowRuntime(
            registry_workspace=ROOT,
            resolved=resolved,
            skill_host=host,
            provider_adapters=ProviderAdapterRegistry([GreenLocalProcessAdapter()]),
            checkpointer=SqliteSaver(connection),
            taskrun_store=store,
            workspace_fingerprint="fp-1",
        )
        result = runtime.start(target_ref={"kind": "finding", "ref": "finding-17"})

        self.assertEqual(result["runtime_state"]["runtime_status"], RUNTIME_STATUS_BLOCKED)
        self.assertEqual(store.payload["status"], "BLOCKED")
        self.assertEqual(host.calls, [])
        self.assertIn("write authority", result["runtime_state"]["runtime_error"])

    def test_ci_entrypoint_waits_and_resumes_same_taskrun_without_completing_it(self) -> None:
        resolved = resolve_starter_entrypoint(
            self.load(), registry_workspace=ROOT, entrypoint="repair_with_ci"
        )
        connection = sqlite3.connect(
            self.project / ".harness/ci-checkpoints.sqlite", check_same_thread=False
        )
        self.addCleanup(connection.close)
        store = self.store("ci")
        guard = AllowWriteGuard()
        runtime = StarterWorkflowRuntime(
            registry_workspace=ROOT,
            resolved=resolved,
            skill_host=RecordingSkillHost(),
            provider_adapters=ProviderAdapterRegistry(
                [
                    GreenProviderAdapter("local.process", "executor"),
                    GreenProviderAdapter("local.git", "executor"),
                    GreenProviderAdapter("github.code_review", "integration"),
                    GreenProviderAdapter("github.actions", "integration"),
                ]
            ),
            checkpointer=SqliteSaver(connection),
            taskrun_store=store,
            workspace_fingerprint="fp-1",
            write_authority_guard=guard,
        )
        waiting = runtime.start(target_ref={"kind": "finding", "ref": "finding-17"})
        state = waiting["runtime_state"]
        self.assertEqual(state["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL, state)
        self.assertEqual(store.payload["status"], "WAITING_EXTERNAL_RESULT")
        self.assertIn("workspace.write", guard.capabilities)
        self.assertIn("vcs.commit.create", guard.capabilities)
        self.assertIn("code_review.pull_request.create", guard.capabilities)

        with self.assertRaisesRegex(StarterRuntimeError, "does not match"):
            runtime.resume(
                state=state,
                external_event={
                    "event": "ci.completed",
                    "status": "success",
                    "correlation_ref": "ci-run-other",
                    "evidence_refs": ["event:ci.completed:ci-run-other"],
                },
                evidence_refs=("event:ci.completed:ci-run-other",),
                correlation_ref="ci-run-other",
            )
        self.assertEqual(store.payload["status"], "WAITING_EXTERNAL_RESULT")

        resumed = runtime.resume(
            state=state,
            external_event={
                "event": "ci.completed",
                "status": "success",
                "correlation_ref": "ci-run-2093",
                "evidence_refs": ["event:ci.completed:ci-run-2093"],
            },
            evidence_refs=("event:ci.completed:ci-run-2093",),
            correlation_ref="ci-run-2093",
        )
        self.assertEqual(resumed["runtime_state"]["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(store.payload["status"], "VALIDATING")
        self.assertNotEqual(store.payload["status"], "COMPLETED")
        phases = [row["phase"] for row in store.payload["checkpoints"]]
        self.assertIn("WORKFLOW_WAITING_EXTERNAL", phases)
        self.assertIn("WORKFLOW_RUNTIME_RESUMED", phases)

    def test_installed_ci_route_uses_real_git_and_exact_head_github_bridge(self) -> None:
        (self.project / ".gitignore").write_text(
            ".quality/\n.harness/taskruns/\n.harness/*.sqlite*\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-b", "main"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Harness Runtime Test"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "harness@example.invalid"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"],
            cwd=self.project,
            check=True,
            capture_output=True,
        )
        parent_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "switch", "-c", "feat/runtime-provider-test"],
            cwd=self.project,
            check=True,
            capture_output=True,
        )

        transport = RepositoryBackedGitHubTransport(self.project)
        assembly = build_concrete_starter_provider_registry(
            workspace=self.project,
            write_scope=("src/**", "tests/**"),
            allowed_profiles={
                "test.run": ("customer-agent-test",),
                "quality.evaluate": ("customer-agent-quality",),
            },
            github=GitHubPullRequestConfiguration(
                repository_full_name="owner/customer-agent",
                token="test-token",
            ),
            process_runner=lambda profile, *, state_file: {"status": "PASS"},
            github_transport=transport,
        )
        resolved = resolve_starter_entrypoint(
            self.load(), registry_workspace=ROOT, entrypoint="repair_with_ci"
        )
        connection = sqlite3.connect(
            self.project / ".harness/concrete-checkpoints.sqlite",
            check_same_thread=False,
        )
        self.addCleanup(connection.close)
        store = self.store("concrete-ci")
        guard = AllowWriteGuard()
        runtime = StarterWorkflowRuntime(
            registry_workspace=ROOT,
            resolved=resolved,
            skill_host=MutatingRecordingSkillHost(self.project),
            provider_adapters=assembly.registry,
            checkpointer=SqliteSaver(connection),
            taskrun_store=store,
            workspace_fingerprint="fp-1",
            write_authority_guard=guard,
        )
        waiting = runtime.start(
            target_ref={
                "kind": "finding",
                "ref": "finding-concrete-provider",
                "execution_profiles": {
                    "test.run": "customer-agent-test",
                    "quality.evaluate": "customer-agent-quality",
                },
                "publication_requests": {
                    "commit": {
                        "capability_id": "vcs.commit.create",
                        "expected_parent_sha": parent_sha,
                        "message": "fix: concrete provider runtime",
                        "changed_paths": ["src/fix.py"],
                    },
                    "create-pr": {
                        "capability_id": "code_review.pull_request.create",
                        "repository_full_name": "owner/customer-agent",
                        "base_branch": "main",
                        "head_branch": "feat/runtime-provider-test",
                        "title": "fix: concrete provider runtime",
                        "body": "Exact-head runtime proof",
                        "draft": False,
                        "from_steps": {
                            "head_sha": {"step_id": "commit", "path": "commit_sha"}
                        },
                    },
                },
                "external_handles": {
                    "ci.run.wait": {
                        "correlation_ref": "ci:customer-agent:2094",
                        "resume_event": "ci.completed",
                    }
                },
            }
        )

        state = waiting["runtime_state"]
        self.assertEqual(state["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL, state)
        self.assertEqual(store.payload["status"], "WAITING_EXTERNAL_RESULT")
        self.assertEqual(state["external_wait"]["provider"], "github.actions")
        self.assertEqual(transport.calls, [
            ("POST", "https://api.github.com/repos/owner/customer-agent/pulls"),
            ("GET", "https://api.github.com/repos/owner/customer-agent/pulls/2094"),
        ])
        commit_sha = state["step_results"]["commit"][-1]["payload"]["commit_sha"]
        self.assertNotEqual(commit_sha, parent_sha)
        self.assertEqual(
            state["step_results"]["create-pr"][-1]["payload"]["receipt"]["head_sha"],
            commit_sha,
        )
        self.assertFalse(assembly.policy["automatic_merge"])
        self.assertNotEqual(store.payload["status"], "COMPLETED")

    def test_in_memory_checkpointer_is_rejected(self) -> None:
        resolved = resolve_starter_entrypoint(
            self.load(), registry_workspace=ROOT, entrypoint="overall_audit"
        )
        with self.assertRaisesRegex(StarterRuntimeError, "durable"):
            StarterWorkflowRuntime(
                registry_workspace=ROOT,
                resolved=resolved,
                skill_host=RecordingSkillHost(),
                provider_adapters=ProviderAdapterRegistry([GreenLocalProcessAdapter()]),
                checkpointer=InMemorySaver(),
                taskrun_store=self.store("memory"),
                workspace_fingerprint="fp-1",
            )


if __name__ == "__main__":
    unittest.main()
